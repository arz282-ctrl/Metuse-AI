"""FastAPI dependency injection, unified auth extraction, and lightweight rate limiting."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from database.models import User, UserIdentity
from packages.auth import (
    SESSION_COOKIE_NAME,
    InMemorySessionStore,
    RedisSessionStore,
    SessionStore,
    SupabaseJwksCache,
    build_supabase_jwks_url,
    decode_hs256_token,
    resolve_jwt_secret,
    verify_supabase_jwt_from_settings,
)
from packages.media_engine import CloudEditingService, RenderBackend, create_render_backend
from packages.meta_integrations import (
    EncryptedSecretStore,
    MetaConnectionService,
    MetaGraphClient,
    SecretStore,
    create_meta_graph_client,
)
from packages.muse_adapter import MuseAdapter, create_muse_adapter
from packages.shared.config import BaseAppSettings
from packages.shared.enums import ErrorCode
from packages.shared.errors import AuthenticationError, MetuseError
from packages.skill_runtime import SkillRegistry
from packages.storage import InMemoryStorageBackend, S3StorageBackend, StorageBackend
from packages.tenancy import create_app_engine, create_session_factory, rls_transaction
from packages.video_gateway import (
    VideoGatewayRegistry,
    VideoGatewayService,
    build_default_video_registry,
)


@dataclass(frozen=True)
class AuthenticatedActor:
    """Verified user identity resolved from Metuse session token or Supabase JWT."""

    user_id: UUID
    auth_source: str
    jti: str | None = None
    expires_at: int | None = None


class InMemoryRateLimiter:
    """Sliding-window in-memory rate limiter for auth endpoints (zero external cost)."""

    def __init__(self, *, max_requests: int = 30, window_seconds: int = 60) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._lock = threading.Lock()
        self._buckets: dict[str, list[float]] = {}

    def check(self, key: str) -> None:
        now = time.monotonic()
        cutoff = now - self._window_seconds
        with self._lock:
            entries = [ts for ts in self._buckets.get(key, []) if ts > cutoff]
            if len(entries) >= self._max_requests:
                self._buckets[key] = entries
                raise MetuseError(
                    "Too many requests; please retry shortly",
                    code=ErrorCode.BAD_REQUEST,
                    status_code=429,
                )
            entries.append(now)
            self._buckets[key] = entries


@dataclass
class AppContainer:
    """Application-scoped dependency container stored on app.state."""

    settings: BaseAppSettings
    session_factory: sessionmaker[Session] | None
    session_store: SessionStore
    auth_rate_limiter: InMemoryRateLimiter
    skill_registry: SkillRegistry
    muse_adapter: MuseAdapter
    storage: StorageBackend
    video_registry: VideoGatewayRegistry
    video_service: VideoGatewayService
    editing_service: CloudEditingService
    secret_store: SecretStore
    meta_service: MetaConnectionService
    supabase_jwks_cache: SupabaseJwksCache | None = None

    @classmethod
    def from_settings(
        cls,
        settings: BaseAppSettings,
        *,
        session_factory: sessionmaker[Session] | None = None,
        session_store: SessionStore | None = None,
        supabase_jwks_cache: SupabaseJwksCache | None = None,
        skill_registry: SkillRegistry | None = None,
        muse_adapter: MuseAdapter | None = None,
        storage: StorageBackend | None = None,
        video_registry: VideoGatewayRegistry | None = None,
        render_backend: RenderBackend | None = None,
        meta_graph_client: MetaGraphClient | None = None,
        secret_store: SecretStore | None = None,
        enable_db: bool = True,
    ) -> AppContainer:
        resolved_factory = session_factory
        if resolved_factory is None and enable_db:
            if (
                settings.app_database_url is not None
                and settings.app_database_url.get_secret_value().strip()
            ):
                engine = create_app_engine(settings)
                resolved_factory = create_session_factory(engine)

        resolved_store: SessionStore
        if session_store is not None:
            resolved_store = session_store
        else:
            redis_dsn = settings.redis_url.get_secret_value().strip()
            if redis_dsn and not settings.is_dev_env():
                resolved_store = RedisSessionStore.from_url(redis_dsn)
            else:
                resolved_store = InMemorySessionStore()

        resolved_jwks = supabase_jwks_cache
        resolved_sb_url = (settings.supabase_url or settings.next_public_supabase_url).strip()
        resolved_jwks_url = settings.supabase_jwks_url.strip() or (
            build_supabase_jwks_url(resolved_sb_url) if resolved_sb_url else ""
        )
        if resolved_jwks is None and resolved_jwks_url:
            resolved_jwks = SupabaseJwksCache(resolved_jwks_url)

        resolved_storage: StorageBackend
        if storage is not None:
            resolved_storage = storage
        elif (
            settings.s3_access_key.get_secret_value().strip()
            and settings.s3_secret_key.get_secret_value().strip()
        ):
            resolved_storage = S3StorageBackend.from_settings(settings)
        else:
            resolved_storage = InMemoryStorageBackend(
                bucket=settings.s3_bucket,
                public_endpoint_url=settings.get_s3_public_endpoint_url(),
            )

        resolved_video_registry = video_registry or build_default_video_registry(settings)
        resolved_video_service = VideoGatewayService(
            settings=settings,
            registry=resolved_video_registry,
            storage=resolved_storage,
        )
        resolved_renderer = render_backend or create_render_backend(settings)
        resolved_editing_service = CloudEditingService(
            settings=settings,
            storage=resolved_storage,
            renderer=resolved_renderer,
        )
        resolved_secret_store = secret_store or EncryptedSecretStore.from_settings(settings)
        resolved_meta_client = meta_graph_client or create_meta_graph_client(settings)
        resolved_meta_service = MetaConnectionService(
            settings=settings,
            graph_client=resolved_meta_client,
            secret_store=resolved_secret_store,
        )

        return cls(
            settings=settings,
            session_factory=resolved_factory,
            session_store=resolved_store,
            auth_rate_limiter=InMemoryRateLimiter(max_requests=30, window_seconds=60),
            skill_registry=skill_registry or SkillRegistry(),
            muse_adapter=muse_adapter or create_muse_adapter(settings),
            storage=resolved_storage,
            video_registry=resolved_video_registry,
            video_service=resolved_video_service,
            editing_service=resolved_editing_service,
            secret_store=resolved_secret_store,
            meta_service=resolved_meta_service,
            supabase_jwks_cache=resolved_jwks,
        )


def get_container(request: Request) -> AppContainer:
    container: AppContainer = request.app.state.container
    return container


def get_settings(container: Annotated[AppContainer, Depends(get_container)]) -> BaseAppSettings:
    return container.settings


def get_session_factory(
    container: Annotated[AppContainer, Depends(get_container)],
) -> sessionmaker[Session]:
    if container.session_factory is None:
        raise AuthenticationError("Database connection is not configured for this API instance")
    return container.session_factory


def get_request_id(request: Request) -> UUID:
    raw = getattr(request.state, "request_id", None)
    if isinstance(raw, UUID):
        return raw
    return uuid4()


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
        return parts[1].strip()
    return None


def _resolve_or_provision_supabase_user(
    session_factory: sessionmaker[Session],
    *,
    subject: UUID,
    email: str | None,
    full_name: str | None,
    avatar_url: str | None,
) -> UUID:
    """Map a verified Supabase Auth subject to canonical public.users + user_identities."""
    with rls_transaction(session_factory, user_id=None, workspace_id=None) as session:
        identity = session.execute(
            select(UserIdentity).where(
                UserIdentity.provider == "supabase",
                UserIdentity.provider_subject == str(subject),
            )
        ).scalar_one_or_none()
        if identity is not None:
            return identity.user_id

        normalized_email = (email or f"supabase-{subject}@users.local").strip().lower()
        user = session.execute(
            select(User).where(User.email == normalized_email)
        ).scalar_one_or_none()
        if user is None:
            user = User(
                id=subject,
                email=normalized_email,
                password_hash=None,
                full_name=full_name,
                avatar_url=avatar_url,
                is_active=True,
            )
            session.add(user)
            session.flush()

        link = UserIdentity(
            id=uuid4(),
            user_id=user.id,
            provider="supabase",
            provider_subject=str(subject),
            token_secret_ref=None,
            metadata_json={"source": "supabase_jwt"},
        )
        session.add(link)
        session.flush()
        return user.id


def get_current_actor(
    container: Annotated[AppContainer, Depends(get_container)],
    authorization: Annotated[str | None, Header()] = None,
    session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> AuthenticatedActor:
    """Authenticate via Metuse HS256 session token (cookie/Bearer) or Supabase Auth JWT."""
    token = _extract_bearer_token(authorization) or session_cookie
    if not token:
        raise AuthenticationError("Authentication required")

    # 1. Try canonical Metuse HS256 session token first
    try:
        secret = resolve_jwt_secret(container.settings)
        claims = decode_hs256_token(token, secret=secret, expected_use="session")
        jti = str(claims.get("jti") or "")
        if jti and container.session_store.is_revoked(jti):
            raise AuthenticationError("Session has been revoked")
        user_id = UUID(str(claims["sub"]))
        exp_val = claims.get("exp")
        return AuthenticatedActor(
            user_id=user_id,
            auth_source="metuse_jwt",
            jti=jti or None,
            expires_at=int(exp_val) if isinstance(exp_val, int | float) else None,
        )
    except AuthenticationError:
        pass

    # 2. Fallback to Supabase Auth JWT verification when HS256 secret or JWKS URL is configured
    has_sb_secret = bool(container.settings.supabase_jwt_secret.get_secret_value().strip())
    if has_sb_secret or container.supabase_jwks_cache is not None:
        sb_claims = verify_supabase_jwt_from_settings(
            token,
            container.settings,
            jwks_cache=container.supabase_jwks_cache,
        )
        if container.session_factory is not None:
            resolved_uid = _resolve_or_provision_supabase_user(
                container.session_factory,
                subject=sb_claims.subject,
                email=sb_claims.email,
                full_name=sb_claims.full_name,
                avatar_url=sb_claims.avatar_url,
            )
        else:
            resolved_uid = sb_claims.subject
        return AuthenticatedActor(
            user_id=resolved_uid,
            auth_source="supabase_jwt",
            jti=None,
            expires_at=sb_claims.expires_at,
        )

    raise AuthenticationError("Invalid or expired authentication token")
