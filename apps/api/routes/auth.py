"""Authentication routes: email/password, logout, current user, and Supabase JWT session."""

from __future__ import annotations

import time
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.deps import (
    AppContainer,
    AuthenticatedActor,
    get_container,
    get_current_actor,
    get_request_id,
    get_session_factory,
)
from database.models import User, UserIdentity
from packages.auth import (
    SESSION_COOKIE_NAME,
    build_session_cookie_kwargs,
    encode_hs256_token,
    hash_password,
    resolve_jwt_secret,
    verify_password,
)
from packages.shared.enums import AuditAction
from packages.shared.errors import AuthenticationError, ConflictError, ValidationDomainError
from packages.shared.events import AuditEventPayload
from packages.tenancy import record_audit_log, rls_transaction

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)


class AuthUserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str | None
    avatar_url: str | None
    auth_source: str


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int
    user: AuthUserResponse


def _client_key(request: Request, suffix: str) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{suffix}:{host}"


def _issue_session(
    response: Response,
    container: AppContainer,
    user: User,
) -> AuthTokenResponse:
    secret = resolve_jwt_secret(container.settings)
    ttl = container.settings.jwt_expires_seconds
    token = encode_hs256_token(
        {"sub": str(user.id), "email": user.email},
        secret=secret,
        ttl_seconds=ttl,
        token_use="session",  # noqa: S106
    )
    cookie_kwargs = build_session_cookie_kwargs(container.settings, max_age=ttl)
    response.set_cookie(key=SESSION_COOKIE_NAME, value=token, **cookie_kwargs)
    return AuthTokenResponse(
        access_token=token,
        expires_in=ttl,
        user=AuthUserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            avatar_url=user.avatar_url,
            auth_source="metuse_jwt",
        ),
    )


@router.post("/register", response_model=AuthTokenResponse, status_code=status.HTTP_201_CREATED)
def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    container: Annotated[AppContainer, Depends(get_container)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> AuthTokenResponse:
    """Register a new user account, create email identity, and issue an HttpOnly session."""
    container.auth_rate_limiter.check(_client_key(request, "register"))
    normalized_email = body.email.strip().lower()
    if "@" not in normalized_email:
        raise ValidationDomainError("A valid email address is required")

    user_id = uuid4()
    pw_hash = hash_password(body.password)

    with rls_transaction(session_factory, user_id=user_id, workspace_id=None) as session:
        existing = session.execute(
            select(User).where(User.email == normalized_email)
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError("An account with this email already exists")

        user = User(
            id=user_id,
            email=normalized_email,
            password_hash=pw_hash,
            full_name=body.full_name.strip() if body.full_name and body.full_name.strip() else None,
            is_active=True,
        )
        session.add(user)
        session.flush()

        identity = UserIdentity(
            id=uuid4(),
            user_id=user.id,
            provider="email",
            provider_subject=normalized_email,
            metadata_json={},
        )
        session.add(identity)
        record_audit_log(
            session,
            AuditEventPayload(
                action=AuditAction.AUTH_REGISTER,
                workspace_id=None,
                actor_user_id=user.id,
                target_type="user",
                target_id=str(user.id),
                request_id=request_id,
                metadata={"provider": "email"},
            ),
        )
        return _issue_session(response, container, user)


@router.post("/login", response_model=AuthTokenResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    container: Annotated[AppContainer, Depends(get_container)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> AuthTokenResponse:
    """Authenticate an email/password user and issue a signed session cookie + Bearer token."""
    container.auth_rate_limiter.check(_client_key(request, "login"))
    normalized_email = body.email.strip().lower()

    with rls_transaction(session_factory, user_id=None, workspace_id=None) as session:
        user = session.execute(
            select(User).where(User.email == normalized_email)
        ).scalar_one_or_none()
        if (
            user is None
            or not user.is_active
            or not user.password_hash
            or not verify_password(body.password, user.password_hash)
        ):
            record_audit_log(
                session,
                AuditEventPayload(
                    action=AuditAction.AUTH_LOGIN_FAILED,
                    workspace_id=None,
                    actor_user_id=None,
                    target_type="auth",
                    target_id=None,
                    request_id=request_id,
                    metadata={"reason": "invalid_credentials"},
                ),
            )
            raise AuthenticationError("Invalid email or password")

        return _issue_session(response, container, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    container: Annotated[AppContainer, Depends(get_container)],
) -> None:
    """Revoke current Metuse session jti and clear the session cookie."""
    if actor.jti:
        ttl = max(1, (actor.expires_at or int(time.time()) + 60) - int(time.time()))
        container.session_store.revoke_jti(actor.jti, ttl_seconds=ttl)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


@router.get("/me", response_model=AuthUserResponse)
def me(
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> AuthUserResponse:
    """Return the authenticated user's profile (works for Metuse JWT and Supabase JWT)."""
    with rls_transaction(session_factory, user_id=actor.user_id, workspace_id=None) as session:
        user = session.execute(select(User).where(User.id == actor.user_id)).scalar_one_or_none()
        if user is None:
            raise AuthenticationError("Authenticated user record not found")
        return AuthUserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            avatar_url=user.avatar_url,
            auth_source=actor.auth_source,
        )
