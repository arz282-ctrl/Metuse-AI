"""Authentication routes: email/password, logout, current user, and Supabase JWT session."""

from __future__ import annotations

import time
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
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
    decode_hs256_token,
    encode_hs256_token,
    exchange_google_code,
    exchange_meta_code,
    hash_password,
    resolve_jwt_secret,
    start_google_oauth,
    start_meta_oauth,
    verify_password,
)
from packages.auth.data_deletion import verify_meta_signed_request
from packages.shared.enums import AuditAction, WorkspaceType
from packages.shared.errors import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    ValidationDomainError,
)
from packages.shared.events import AuditEventPayload
from packages.tenancy import create_workspace, record_audit_log, rls_transaction

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


class SocialLoginStartResponse(BaseModel):
    authorization_url: str
    state: str
    mode: str


class SocialLoginCallbackRequest(BaseModel):
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=1, max_length=4096)
    redirect_uri: str | None = Field(default=None, max_length=512)
    mock_email: str | None = Field(default=None, max_length=320)
    mock_name: str | None = Field(default=None, max_length=200)


class SocialAuthTokenResponse(AuthTokenResponse):
    is_new_user: bool = False
    workspace_id: UUID | None = None


class MetaDataDeletionRequest(BaseModel):
    signed_request: str = Field(min_length=1, max_length=8192)


class MetaDataDeletionResponse(BaseModel):
    url: str
    confirmation_code: str


def _find_or_create_social_user(
    session: Session,
    *,
    provider: str,
    provider_subject: str,
    email: str,
    full_name: str | None,
    avatar_url: str | None,
    request_id: UUID,
) -> tuple[User, bool]:
    """Resolve a verified social identity to a user, linking or provisioning as needed."""
    identity = session.execute(
        select(UserIdentity).where(
            UserIdentity.provider == provider,
            UserIdentity.provider_subject == provider_subject,
        )
    ).scalar_one_or_none()
    if identity is not None:
        linked_user = session.execute(select(User).where(User.id == identity.user_id)).scalar_one()
        if not linked_user.is_active:
            raise AuthenticationError("This account has been deactivated")
        return linked_user, False

    normalized_email = email.strip().lower()
    existing_user = session.execute(
        select(User).where(User.email == normalized_email)
    ).scalar_one_or_none()
    if existing_user is not None:
        if not existing_user.is_active:
            raise AuthenticationError("This account has been deactivated")
        conflicting = session.execute(
            select(UserIdentity).where(
                UserIdentity.user_id == existing_user.id,
                UserIdentity.provider == provider,
            )
        ).scalar_one_or_none()
        if conflicting is not None:
            raise ConflictError(
                f"This account is already linked to a different {provider} identity"
            )
        session.add(
            UserIdentity(
                id=uuid4(),
                user_id=existing_user.id,
                provider=provider,
                provider_subject=provider_subject,
                token_secret_ref=None,
                metadata_json={"linked_via": "verified_social_email"},
            )
        )
        session.flush()
        return existing_user, False

    new_user = User(
        id=uuid4(),
        email=normalized_email,
        password_hash=None,
        full_name=(full_name or "").strip() or None,
        avatar_url=(avatar_url or "").strip() or None,
        is_active=True,
    )
    session.add(new_user)
    session.flush()
    session.add(
        UserIdentity(
            id=uuid4(),
            user_id=new_user.id,
            provider=provider,
            provider_subject=provider_subject,
            token_secret_ref=None,
            metadata_json={"source": f"{provider}_oauth"},
        )
    )
    session.flush()
    record_audit_log(
        session,
        AuditEventPayload(
            action=AuditAction.AUTH_REGISTER,
            workspace_id=None,
            actor_user_id=new_user.id,
            target_type="user",
            target_id=str(new_user.id),
            request_id=request_id,
            metadata={"provider": provider},
        ),
    )
    return new_user, True


def _anonymize_user_account(session: Session, user: User) -> None:
    """Irreversibly scrub PII and deactivate an account while preserving audit FK rows."""
    user.email = f"deleted-{user.id.hex}@deleted.local"
    user.password_hash = None
    user.full_name = None
    user.avatar_url = None
    user.is_active = False
    session.execute(delete(UserIdentity).where(UserIdentity.user_id == user.id))
    session.flush()


def _complete_social_login(
    *,
    provider: str,
    provider_subject: str,
    email: str,
    full_name: str | None,
    avatar_url: str | None,
    callback_action: AuditAction,
    response: Response,
    container: AppContainer,
    session_factory: sessionmaker[Session],
    request_id: UUID,
) -> SocialAuthTokenResponse:
    with rls_transaction(session_factory, user_id=None, workspace_id=None) as session:
        user, is_new = _find_or_create_social_user(
            session,
            provider=provider,
            provider_subject=provider_subject,
            email=email,
            full_name=full_name,
            avatar_url=avatar_url,
            request_id=request_id,
        )
        record_audit_log(
            session,
            AuditEventPayload(
                action=callback_action,
                workspace_id=None,
                actor_user_id=user.id,
                target_type="user",
                target_id=str(user.id),
                request_id=request_id,
                metadata={"provider": provider, "is_new_user": is_new},
            ),
        )
        user_id = user.id

    workspace_id: UUID | None = None
    if is_new:
        with rls_transaction(session_factory, user_id=user_id, workspace_id=None) as session:
            fresh_user = session.execute(select(User).where(User.id == user_id)).scalar_one()
            display = (fresh_user.full_name or "").strip() or fresh_user.email.split("@")[0]
        ws = create_workspace(
            session_factory,
            user_id=user_id,
            name=f"{display}'s Studio",
            workspace_type=WorkspaceType.CREATOR,
            request_id=request_id,
        )
        workspace_id = ws.id

    with rls_transaction(session_factory, user_id=user_id, workspace_id=None) as session:
        session_user = session.execute(select(User).where(User.id == user_id)).scalar_one()
        issued = _issue_session(response, container, session_user)
    return SocialAuthTokenResponse(
        **issued.model_dump(), is_new_user=is_new, workspace_id=workspace_id
    )


@router.get("/meta/start", response_model=SocialLoginStartResponse)
def meta_login_start(
    request: Request,
    container: Annotated[AppContainer, Depends(get_container)],
    redirect_uri: Annotated[str | None, Query(max_length=512)] = None,
) -> SocialLoginStartResponse:
    """Start Meta identity login; frontend redirects the user to the returned URL."""
    container.auth_rate_limiter.check(_client_key(request, "meta-start"))
    started = start_meta_oauth(
        container.settings,
        redirect_uri=redirect_uri,
        session_store=container.session_store,
    )
    return SocialLoginStartResponse(
        authorization_url=started.authorization_url,
        state=started.state,
        mode=started.mode,
    )


@router.post("/meta/callback", response_model=SocialAuthTokenResponse)
def meta_login_callback(
    body: SocialLoginCallbackRequest,
    request: Request,
    response: Response,
    container: Annotated[AppContainer, Depends(get_container)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> SocialAuthTokenResponse:
    """Exchange a Meta authorization code, provision/link the user, and issue a session."""
    container.auth_rate_limiter.check(_client_key(request, "meta-callback"))
    if body.redirect_uri is not None and body.redirect_uri.strip():
        supplied = body.redirect_uri.strip()
        if supplied not in container.settings.get_meta_allowed_redirect_uris():
            raise BadRequestError("Meta redirect URI is not in the configured allowlist")
    identity = exchange_meta_code(
        container.settings,
        code=body.code,
        state=body.state,
        mock_email=body.mock_email,
        mock_name=body.mock_name,
        session_store=container.session_store,
    )
    return _complete_social_login(
        provider="meta",
        provider_subject=identity.provider_subject,
        email=identity.email,
        full_name=identity.full_name,
        avatar_url=None,
        callback_action=AuditAction.AUTH_META_LOGIN_CALLBACK,
        response=response,
        container=container,
        session_factory=session_factory,
        request_id=request_id,
    )


@router.get("/google/start", response_model=SocialLoginStartResponse)
def google_login_start(
    request: Request,
    container: Annotated[AppContainer, Depends(get_container)],
    redirect_uri: Annotated[str | None, Query(max_length=512)] = None,
) -> SocialLoginStartResponse:
    """Start Google identity login; frontend redirects the user to the returned URL."""
    container.auth_rate_limiter.check(_client_key(request, "google-start"))
    started = start_google_oauth(
        container.settings,
        redirect_uri=redirect_uri,
        session_store=container.session_store,
    )
    return SocialLoginStartResponse(
        authorization_url=started.authorization_url,
        state=started.state,
        mode=started.mode,
    )


@router.post("/google/callback", response_model=SocialAuthTokenResponse)
def google_login_callback(
    body: SocialLoginCallbackRequest,
    request: Request,
    response: Response,
    container: Annotated[AppContainer, Depends(get_container)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> SocialAuthTokenResponse:
    """Exchange a Google authorization code, provision/link the user, and issue a session."""
    container.auth_rate_limiter.check(_client_key(request, "google-callback"))
    if body.redirect_uri is not None and body.redirect_uri.strip():
        supplied = body.redirect_uri.strip()
        if supplied not in container.settings.get_google_allowed_redirect_uris():
            raise BadRequestError("Google redirect URI is not in the configured allowlist")
    identity = exchange_google_code(
        container.settings,
        code=body.code,
        state=body.state,
        mock_email=body.mock_email,
        mock_name=body.mock_name,
        session_store=container.session_store,
    )
    return _complete_social_login(
        provider="google",
        provider_subject=identity.provider_subject,
        email=identity.email,
        full_name=identity.full_name,
        avatar_url=identity.avatar_url,
        callback_action=AuditAction.AUTH_GOOGLE_LOGIN_CALLBACK,
        response=response,
        container=container,
        session_factory=session_factory,
        request_id=request_id,
    )


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_own_account(
    response: Response,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    container: Annotated[AppContainer, Depends(get_container)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> None:
    """Permanently scrub the authenticated user's PII and deactivate the account."""
    with rls_transaction(session_factory, user_id=actor.user_id, workspace_id=None) as session:
        user = session.execute(select(User).where(User.id == actor.user_id)).scalar_one_or_none()
        if user is None:
            raise AuthenticationError("Authenticated user record not found")
        _anonymize_user_account(session, user)
        record_audit_log(
            session,
            AuditEventPayload(
                action=AuditAction.AUTH_ACCOUNT_DELETED,
                workspace_id=None,
                actor_user_id=actor.user_id,
                target_type="user",
                target_id=str(actor.user_id),
                request_id=request_id,
                metadata={"initiated_by": "user"},
            ),
        )
    if actor.jti:
        ttl = max(1, (actor.expires_at or int(time.time()) + 60) - int(time.time()))
        container.session_store.revoke_jti(actor.jti, ttl_seconds=ttl)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


@router.post("/meta/data-deletion", response_model=MetaDataDeletionResponse)
def meta_data_deletion(
    body: MetaDataDeletionRequest,
    request: Request,
    container: Annotated[AppContainer, Depends(get_container)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> MetaDataDeletionResponse:
    """Handle Meta's Data Deletion Request callback: verify, scrub, return status URL."""
    container.auth_rate_limiter.check(_client_key(request, "meta-data-deletion"))
    payload = verify_meta_signed_request(container.settings, body.signed_request)
    meta_subject = str(payload.get("user_id") or "").strip()
    if not meta_subject:
        raise BadRequestError("Meta data deletion payload is missing user_id")

    with rls_transaction(session_factory, user_id=None, workspace_id=None) as session:
        identity = session.execute(
            select(UserIdentity).where(
                UserIdentity.provider == "meta",
                UserIdentity.provider_subject == meta_subject,
            )
        ).scalar_one_or_none()
        if identity is None:
            raise BadRequestError("No Metuse account is linked to this Meta user")
        user = session.execute(select(User).where(User.id == identity.user_id)).scalar_one()
        _anonymize_user_account(session, user)
        record_audit_log(
            session,
            AuditEventPayload(
                action=AuditAction.AUTH_ACCOUNT_DELETED,
                workspace_id=None,
                actor_user_id=user.id,
                target_type="user",
                target_id=str(user.id),
                request_id=request_id,
                metadata={"initiated_by": "meta_data_deletion_callback"},
            ),
        )
        deleted_user_id = user.id

    secret = resolve_jwt_secret(container.settings)
    confirmation_code = encode_hs256_token(
        {"sub": str(deleted_user_id), "meta_subject": meta_subject},
        secret=secret,
        ttl_seconds=7 * 24 * 3600,
        token_use="meta_deletion_status",  # noqa: S106
    )
    status_url = (
        f"{str(request.base_url).rstrip('/')}"
        f"/api/v1/auth/meta/data-deletion/status?code={confirmation_code}"
    )
    return MetaDataDeletionResponse(url=status_url, confirmation_code=confirmation_code)


@router.get("/meta/data-deletion/status")
def meta_data_deletion_status(
    container: Annotated[AppContainer, Depends(get_container)],
    code: Annotated[str, Query(min_length=1, max_length=4096)],
) -> dict[str, str]:
    """Return the status of a Meta data deletion request from its confirmation code."""
    secret = resolve_jwt_secret(container.settings)
    claims = decode_hs256_token(code, secret=secret, expected_use="meta_deletion_status")
    return {"status": "complete", "deleted_user_id": str(claims.get("sub", ""))}
