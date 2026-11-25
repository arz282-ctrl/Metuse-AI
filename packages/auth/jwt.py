"""HS256 JWT signing and verification for sessions and OAuth CSRF state."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from uuid import uuid4

from packages.shared.config import BaseAppSettings
from packages.shared.errors import AuthenticationError

_DEV_FALLBACK_JWT_SECRET = "metuse-dev-only-jwt-secret-not-for-prod"  # noqa: S105


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode((segment + padding).encode("ascii"))


def resolve_jwt_secret(settings: BaseAppSettings) -> str:
    """Resolve JWT signing secret; fail closed in non-development environments."""
    configured = settings.app_jwt_secret.get_secret_value().strip()
    if configured:
        return configured
    if settings.is_dev_env():
        return _DEV_FALLBACK_JWT_SECRET
    raise RuntimeError("APP_JWT_SECRET must be configured in non-development environments")


def encode_hs256_token(
    claims: dict[str, Any],
    *,
    secret: str,
    ttl_seconds: int,
    token_use: str = "session",
) -> str:
    """Encode an HS256 JWT with standard iat, exp, jti, and token_use claims."""
    if not secret:
        raise ValueError("JWT signing secret must not be empty")
    now = int(time.time())
    payload = {
        **claims,
        "iat": now,
        "exp": now + max(1, ttl_seconds),
        "jti": str(uuid4()),
        "token_use": token_use,
    }
    header_b64 = _b64url_encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8")
    )
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    )
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(sig)}"


def decode_hs256_token(
    token: str,
    *,
    secret: str,
    expected_use: str = "session",
) -> dict[str, Any]:
    """Verify signature, expiration, and token_use of an HS256 JWT."""
    if not token or not secret:
        raise AuthenticationError("Authentication token is missing or invalid")
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise AuthenticationError("Authentication token is malformed")

    header_b64, payload_b64, sig_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        provided_sig = _b64url_decode(sig_b64)
    except Exception as exc:
        raise AuthenticationError("Authentication token is invalid") from exc

    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise AuthenticationError("Unsupported token algorithm")
    if not isinstance(payload, dict):
        raise AuthenticationError("Authentication token payload is invalid")

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(provided_sig, expected_sig):
        raise AuthenticationError("Authentication token signature verification failed")

    exp = payload.get("exp")
    if not isinstance(exp, int | float) or int(time.time()) >= int(exp):
        raise AuthenticationError("Authentication token has expired")

    if payload.get("token_use") != expected_use:
        raise AuthenticationError("Authentication token has unexpected purpose")

    return payload
