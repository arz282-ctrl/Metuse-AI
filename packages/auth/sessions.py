"""Server-side JWT jti revocation, single-use OAuth nonce store, and cookie helpers."""

from __future__ import annotations

import threading
import time
from typing import Literal, Protocol, TypedDict

import redis
from redis.exceptions import RedisError

from packages.shared.config import BaseAppSettings
from packages.shared.errors import AuthenticationError, ServiceUnavailableError

SESSION_COOKIE_NAME = "metuse_session"
CSRF_COOKIE_NAME = "metuse_csrf"

_REVOKED_JTI_PREFIX = "metuse:auth:revoked_jti:"
_OAUTH_NONCE_PREFIX = "metuse:auth:oauth_nonce:"


class CookieParams(TypedDict):
    """Typed cookie options enforcing HttpOnly; Secure; SameSite=Lax."""

    httponly: bool
    secure: bool
    samesite: Literal["lax", "strict", "none"]
    path: str
    max_age: int


class RedisLike(Protocol):
    """Minimal structural Redis contract for strict mypy compatibility."""

    def set(self, name: str, value: str, *, ex: int | None = None) -> object: ...

    def exists(self, *names: str) -> object: ...

    def getdel(self, name: str) -> object: ...

    def ping(self) -> object: ...


class SessionStore(Protocol):
    """Server-side revocation and one-time OAuth state nonce store."""

    def revoke_jti(self, jti: str, *, ttl_seconds: int) -> None: ...

    def is_revoked(self, jti: str) -> bool: ...

    def store_oauth_nonce(self, nonce: str, *, ttl_seconds: int = 600) -> None: ...

    def consume_oauth_nonce(self, nonce: str) -> bool: ...

    def check_health(self) -> bool: ...


class RedisSessionStore:
    """Redis-backed session revocation and single-use OAuth nonce store (fail-closed)."""

    def __init__(self, client: RedisLike) -> None:
        self._client = client

    @classmethod
    def from_url(cls, redis_url: str) -> RedisSessionStore:
        """Create a RedisSessionStore from a Redis DSN."""
        client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=3.0,
            socket_timeout=3.0,
        )
        return cls(client)

    def revoke_jti(self, jti: str, *, ttl_seconds: int) -> None:
        cleaned = jti.strip()
        if not cleaned:
            raise AuthenticationError("Cannot revoke empty session identifier")
        ttl = max(1, int(ttl_seconds))
        try:
            self._client.set(f"{_REVOKED_JTI_PREFIX}{cleaned}", "1", ex=ttl)
        except RedisError as exc:
            raise ServiceUnavailableError("Session revocation store is unavailable") from exc

    def is_revoked(self, jti: str) -> bool:
        cleaned = jti.strip()
        if not cleaned:
            return True
        try:
            result = self._client.exists(f"{_REVOKED_JTI_PREFIX}{cleaned}")
        except RedisError as exc:
            raise ServiceUnavailableError("Session revocation store is unavailable") from exc
        return bool(result)

    def store_oauth_nonce(self, nonce: str, *, ttl_seconds: int = 600) -> None:
        cleaned = nonce.strip()
        if not cleaned:
            raise ValueError("OAuth nonce must not be empty")
        try:
            self._client.set(f"{_OAUTH_NONCE_PREFIX}{cleaned}", "1", ex=max(1, int(ttl_seconds)))
        except RedisError as exc:
            raise ServiceUnavailableError("Session store is unavailable") from exc

    def consume_oauth_nonce(self, nonce: str) -> bool:
        cleaned = nonce.strip()
        if not cleaned:
            return False
        try:
            value = self._client.getdel(f"{_OAUTH_NONCE_PREFIX}{cleaned}")
        except RedisError as exc:
            raise ServiceUnavailableError("Session store is unavailable") from exc
        return value is not None

    def check_health(self) -> bool:
        try:
            return bool(self._client.ping())
        except RedisError:
            return False


class InMemorySessionStore:
    """Thread-safe in-memory session store strictly for unit tests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._revoked: dict[str, float] = {}
        self._nonces: dict[str, float] = {}

    def _prune(self, now: float) -> None:
        self._revoked = {k: exp for k, exp in self._revoked.items() if exp > now}
        self._nonces = {k: exp for k, exp in self._nonces.items() if exp > now}

    def revoke_jti(self, jti: str, *, ttl_seconds: int) -> None:
        cleaned = jti.strip()
        if not cleaned:
            raise AuthenticationError("Cannot revoke empty session identifier")
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._revoked[cleaned] = now + max(1, int(ttl_seconds))

    def is_revoked(self, jti: str) -> bool:
        cleaned = jti.strip()
        if not cleaned:
            return True
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            return cleaned in self._revoked

    def store_oauth_nonce(self, nonce: str, *, ttl_seconds: int = 600) -> None:
        cleaned = nonce.strip()
        if not cleaned:
            raise ValueError("OAuth nonce must not be empty")
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._nonces[cleaned] = now + max(1, int(ttl_seconds))

    def consume_oauth_nonce(self, nonce: str) -> bool:
        cleaned = nonce.strip()
        if not cleaned:
            return False
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            exp = self._nonces.pop(cleaned, None)
            return exp is not None and exp > now

    def check_health(self) -> bool:
        return True


def resolve_cookie_secure(settings: BaseAppSettings) -> bool:
    """Force Secure=True outside dev; default True in dev (localhost is a secure context)."""
    if not settings.is_dev_env():
        return True
    return settings.session_cookie_secure


def build_session_cookie_kwargs(
    settings: BaseAppSettings,
    *,
    max_age: int | None = None,
) -> CookieParams:
    """Build HttpOnly; Secure; SameSite=Lax cookie attributes for web sessions."""
    return CookieParams(
        httponly=True,
        secure=resolve_cookie_secure(settings),
        samesite="lax",
        path="/",
        max_age=settings.jwt_expires_seconds if max_age is None else max_age,
    )


def remaining_ttl_seconds(claims: dict[str, object]) -> int:
    """Compute remaining seconds until JWT expiration for jti blocklist TTL."""
    raw_exp = claims.get("exp")
    if isinstance(raw_exp, int | float):
        return max(1, int(raw_exp) - int(time.time()))
    return 1
