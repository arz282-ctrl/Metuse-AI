"""Supabase Auth JWT verification (HS256 + asymmetric JWKS with key rotation).

Supports both legacy HS256 Supabase JWT secrets and modern asymmetric Supabase JWKS
(ES256, RS256, EdDSA) with strict cryptographic signature verification, issuer/audience/
time-claim checks, UUID subject validation, and thread-safe JWKS caching with rotation.
Never trusts unverified JWT payloads.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import jwt
from jwt import InvalidTokenError, PyJWK, PyJWKSet

from packages.shared.config import BaseAppSettings
from packages.shared.errors import AuthenticationError

_ASYMMETRIC_ALGS = frozenset({"ES256", "RS256", "EdDSA"})
_SYMMETRIC_ALGS = frozenset({"HS256"})
_DEFAULT_LEEWAY_SECONDS = 5


@dataclass(frozen=True)
class SupabaseJwtClaims:
    """Verified identity claims extracted from a Supabase Auth access token."""

    subject: UUID
    email: str | None
    role: str
    full_name: str | None
    avatar_url: str | None
    expires_at: int
    algorithm: str


class SupabaseJwksCache:
    """Thread-safe JWKS cache with TTL and forced-refresh support for key rotation."""

    def __init__(
        self,
        jwks_url: str,
        *,
        ttl_seconds: int = 300,
        refresh_cooldown_seconds: float = 10.0,
        fetcher: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self._jwks_url = jwks_url.strip()
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._refresh_cooldown = max(0.0, float(refresh_cooldown_seconds))
        self._fetcher = fetcher or self._default_fetch
        self._lock = threading.Lock()
        self._keys_by_kid: dict[str, PyJWK] = {}
        self._fetched_at: float = 0.0

    @property
    def jwks_url(self) -> str:
        return self._jwks_url

    @staticmethod
    def _default_fetch(url: str) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            raise AuthenticationError("Unable to fetch Supabase JWKS keys") from exc
        if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
            raise AuthenticationError("Invalid Supabase JWKS payload")
        return data

    def _reload_locked(self, now: float) -> None:
        raw = self._fetcher(self._jwks_url)
        try:
            jwk_set = PyJWKSet.from_dict(raw)
        except Exception as exc:
            raise AuthenticationError("Malformed Supabase JWKS key set") from exc
        by_kid: dict[str, PyJWK] = {}
        for key in jwk_set.keys:
            kid = key.key_id
            if kid:
                by_kid[str(kid)] = key
        self._keys_by_kid = by_kid
        self._fetched_at = now

    def get_signing_key(self, kid: str) -> PyJWK:
        """Return signing key for `kid`, refreshing once on cache miss for key rotation."""
        cleaned_kid = kid.strip()
        if not cleaned_kid:
            raise AuthenticationError("Supabase token header is missing 'kid'")

        now = time.monotonic()
        with self._lock:
            is_stale = self._fetched_at == 0.0 or (now - self._fetched_at) >= self._ttl_seconds
            if is_stale:
                self._reload_locked(now)

            found = self._keys_by_kid.get(cleaned_kid)
            if found is not None:
                return found

            # Key rotation handling: force one refresh if outside cooldown window.
            if (now - self._fetched_at) >= self._refresh_cooldown:
                self._reload_locked(now)
                found = self._keys_by_kid.get(cleaned_kid)
                if found is not None:
                    return found

        raise AuthenticationError(f"Unknown Supabase signing key '{cleaned_kid}'")


def build_supabase_jwks_url(supabase_url: str) -> str:
    """Derive canonical Supabase JWKS URL from NEXT_PUBLIC_SUPABASE_URL."""
    base = supabase_url.strip().rstrip("/")
    return f"{base}/auth/v1/.well-known/jwks.json"


def _extract_verified_claims(payload: dict[str, Any], *, algorithm: str) -> SupabaseJwtClaims:
    raw_sub = str(payload.get("sub") or "").strip()
    try:
        subject = UUID(raw_sub)
    except ValueError as exc:
        raise AuthenticationError("Supabase token subject must be a valid UUID") from exc

    raw_exp = payload.get("exp")
    if not isinstance(raw_exp, int | float):
        raise AuthenticationError("Supabase token is missing a valid expiration claim")

    user_meta = payload.get("user_metadata")
    meta_dict = user_meta if isinstance(user_meta, dict) else {}
    raw_email = payload.get("email")
    email = (
        str(raw_email).strip().lower() if isinstance(raw_email, str) and raw_email.strip() else None
    )
    raw_name = meta_dict.get("full_name") or meta_dict.get("name")
    full_name = str(raw_name).strip() if isinstance(raw_name, str) and raw_name.strip() else None
    raw_avatar = meta_dict.get("avatar_url") or meta_dict.get("picture")
    avatar_url = (
        str(raw_avatar).strip() if isinstance(raw_avatar, str) and raw_avatar.strip() else None
    )

    return SupabaseJwtClaims(
        subject=subject,
        email=email,
        role=str(payload.get("role") or "authenticated"),
        full_name=full_name,
        avatar_url=avatar_url,
        expires_at=int(raw_exp),
        algorithm=algorithm,
    )


def verify_supabase_jwt(
    token: str,
    *,
    jwt_secret: str | None = None,
    jwks_cache: SupabaseJwksCache | None = None,
    expected_issuer: str | None = None,
    expected_audience: str = "authenticated",
    leeway_seconds: int = _DEFAULT_LEEWAY_SECONDS,
) -> SupabaseJwtClaims:
    """Cryptographically verify a Supabase Auth JWT (HS256 or asymmetric JWKS)."""
    cleaned_token = token.strip() if token else ""
    if not cleaned_token:
        raise AuthenticationError("Supabase access token is missing")

    try:
        header = jwt.get_unverified_header(cleaned_token)
    except InvalidTokenError as exc:
        raise AuthenticationError("Supabase access token header is malformed") from exc

    alg = str(header.get("alg") or "").strip()
    if alg not in _SYMMETRIC_ALGS and alg not in _ASYMMETRIC_ALGS:
        raise AuthenticationError(f"Unsupported Supabase token algorithm '{alg or 'none'}'")

    verify_key: Any
    allowed_algs: list[str]
    if alg in _SYMMETRIC_ALGS:
        cleaned_secret = (jwt_secret or "").strip()
        if not cleaned_secret:
            raise AuthenticationError("Supabase HS256 JWT verification is not configured")
        verify_key = cleaned_secret
        allowed_algs = ["HS256"]
    else:
        if jwks_cache is None:
            raise AuthenticationError("Supabase asymmetric JWKS verification is not configured")
        kid = str(header.get("kid") or "").strip()
        signing_jwk = jwks_cache.get_signing_key(kid)
        verify_key = signing_jwk.key
        allowed_algs = [alg]

    normalized_issuer = (
        expected_issuer.strip().rstrip("/") if expected_issuer and expected_issuer.strip() else None
    )

    try:
        payload = jwt.decode(
            cleaned_token,
            key=verify_key,
            algorithms=allowed_algs,
            audience=expected_audience,
            issuer=normalized_issuer,
            leeway=leeway_seconds,
            options={
                "require": ["exp", "sub", "aud"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": True,
                "verify_aud": True,
                "verify_iss": normalized_issuer is not None,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Supabase token has expired") from exc
    except jwt.ImmatureSignatureError as exc:
        raise AuthenticationError("Supabase token is not yet valid") from exc
    except jwt.InvalidAudienceError as exc:
        raise AuthenticationError("Supabase token audience is not authenticated") from exc
    except jwt.InvalidIssuerError as exc:
        raise AuthenticationError("Supabase token issuer mismatch") from exc
    except jwt.InvalidSignatureError as exc:
        raise AuthenticationError("Supabase token signature verification failed") from exc
    except InvalidTokenError as exc:
        raise AuthenticationError("Supabase access token verification failed") from exc

    if not isinstance(payload, dict):
        raise AuthenticationError("Supabase token payload is invalid")

    return _extract_verified_claims(payload, algorithm=alg)


def verify_supabase_jwt_from_settings(
    token: str,
    settings: BaseAppSettings,
    *,
    jwks_cache: SupabaseJwksCache | None = None,
) -> SupabaseJwtClaims:
    """Verify a Supabase Auth JWT using BaseAppSettings (HS256 secret and/or JWKS URL)."""
    secret = settings.supabase_jwt_secret.get_secret_value().strip() or None
    base_url = (settings.supabase_url or settings.next_public_supabase_url).strip().rstrip("/")
    expected_issuer = f"{base_url}/auth/v1" if base_url else None
    resolved_cache = jwks_cache
    jwks_url = settings.supabase_jwks_url.strip() or (
        build_supabase_jwks_url(base_url) if base_url else ""
    )
    if resolved_cache is None and jwks_url:
        resolved_cache = SupabaseJwksCache(jwks_url)
    return verify_supabase_jwt(
        token,
        jwt_secret=secret,
        jwks_cache=resolved_cache,
        expected_issuer=expected_issuer,
    )
