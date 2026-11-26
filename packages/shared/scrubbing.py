"""Recursive secret-redaction utilities for logs, errors, and audit metadata."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import SecretStr

REDACTED = "[REDACTED]"

_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "passwd",
        "pwd",
        "secret",
        "secret_key",
        "client_secret",
        "app_secret",
        "jwt_secret",
        "app_jwt_secret",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "session_token",
        "csrf_token",
        "api_key",
        "apikey",
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "jwt",
        "private_key",
        "signing_key",
        "meta_app_secret",
        "meta_webhook_verify_token",
        "s3_secret_key",
        "s3_access_key",
        "runway_api_key",
        "gemini_api_key",
        "kling_api_key",
        "muse_api_key",
        "supabase_service_role_key",
        "service_role_key",
        "supabase_secret_key",
        "cloudflare_api_token",
        "credentials",
        "credential",
    }
)

_SENSITIVE_SUFFIXES = (
    "_password",
    "_passwd",
    "_secret",
    "_secret_key",
    "_service_role_key",
    "_token",
    "_api_key",
    "_private_key",
    "_signing_key",
    "_credential",
    "_credentials",
)

_DSN_KEYS = frozenset(
    {
        "database_url",
        "app_database_url",
        "migration_database_url",
        "redis_url",
        "dsn",
        "connection_string",
    }
)

_BEARER_RE = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._\-~+/=]+")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_META_TOKEN_RE = re.compile(r"\bEAA[A-Za-z0-9]{12,}\b")
_PROVIDER_KEY_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|(?:sk|rk|pk)_(?:live|test|proj)_[A-Za-z0-9_-]{12,}"
    r"|sb_secret_[A-Za-z0-9_-]{12,}|cfut_[A-Za-z0-9_-]{16,}"
    r"|AIza[0-9A-Za-z\-_]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b"
)
_URL_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s\"'<>)}\]]+")
_HTTP_SCHEMES = frozenset({"http", "https", "ws", "wss"})
_HOST_PORT_RE = re.compile(r"^(?:[^:@/\s?#]+|\[[^\]]+\]):\d{1,5}$")
_AUTHORITY_HOST_RE = re.compile(r"@((?:\[[^\]]+\]|[^/\s@?#]+))(?=[/?#]|$)")
_INLINE_KV_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|access_token|refresh_token|id_token|"
    r"session_token|csrf_token|api_key|apikey|client_secret|app_secret|jwt_secret|"
    r"app_jwt_secret|meta_app_secret|s3_secret_key|s3_access_key|private_key|"
    r"signing_key)\s*([:=])\s*(?!\[REDACTED\](?:[\s&#,;'\")}\]]|$))([^\s&#,;'\")}\]]+)"
)


def _redact_url_token(match: re.Match[str]) -> str:
    """Redact only the password inside a URL authority without corrupting path/query '@'."""
    token = match.group(0)
    scheme_sep_idx = token.find("://")
    if scheme_sep_idx <= 0:
        return token

    scheme = token[:scheme_sep_idx].lower()
    prefix = token[: scheme_sep_idx + 3]
    rest = token[scheme_sep_idx + 3 :]
    if "@" not in rest:
        return token

    first_delim_idx = len(rest)
    for sep in ("/", "?", "#"):
        idx = rest.find(sep)
        if idx != -1 and idx < first_delim_idx:
            first_delim_idx = idx

    before_delim = rest[:first_delim_idx]
    delim_char = rest[first_delim_idx] if first_delim_idx < len(rest) else ""
    after_delim = rest[first_delim_idx:]

    if scheme in _HTTP_SCHEMES:
        if "@" not in before_delim:
            return token
        userinfo, host = before_delim.rsplit("@", 1)
        if ":" not in userinfo:
            return token
        username, _ = userinfo.split(":", 1)
        return f"{prefix}{username}:{REDACTED}@{host}{after_delim}"

    if "@" not in before_delim:
        if ":" not in before_delim or _HOST_PORT_RE.fullmatch(before_delim):
            return token
        host_match = _AUTHORITY_HOST_RE.search(rest)
        if host_match is None:
            return token
        userinfo = rest[: host_match.start()]
        if ":" not in userinfo:
            return token
        username, _ = userinfo.split(":", 1)
        host = host_match.group(1)
        suffix = rest[host_match.end() :]
        return f"{prefix}{username}:{REDACTED}@{host}{suffix}"

    if delim_char == "#" and "@" in after_delim:
        pre_query = after_delim.split("?", 1)[0]
        if "=" not in pre_query:
            host_match = _AUTHORITY_HOST_RE.search(rest, pos=first_delim_idx)
            if host_match is not None:
                userinfo = rest[: host_match.start()]
                if ":" in userinfo:
                    username, _ = userinfo.split(":", 1)
                    host = host_match.group(1)
                    suffix = rest[host_match.end() :]
                    return f"{prefix}{username}:{REDACTED}@{host}{suffix}"

    userinfo, host = before_delim.rsplit("@", 1)
    if ":" not in userinfo:
        return token
    username, _ = userinfo.split(":", 1)
    return f"{prefix}{username}:{REDACTED}@{host}{after_delim}"


def is_secret_ref_key(key: str) -> bool:
    """Return True if key is a safe secret-reference identifier (not a raw secret)."""
    lowered = key.strip().lower()
    return lowered == "secret_ref" or lowered.endswith("_secret_ref")


def is_sensitive_key(key: str) -> bool:
    """Determine whether a mapping key holds sensitive credential material."""
    lowered = key.strip().lower()
    if is_secret_ref_key(lowered):
        return False
    if lowered in _SENSITIVE_EXACT_KEYS:
        return True
    return any(lowered.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def scrub_text(text: str) -> str:
    """Redact credentials, tokens, JWTs, and DB DSN passwords from free text."""
    if not text:
        return text
    result = _URL_TOKEN_RE.sub(_redact_url_token, text)
    result = _BEARER_RE.sub(rf"\1 {REDACTED}", result)
    result = _JWT_RE.sub(REDACTED, result)
    result = _META_TOKEN_RE.sub(REDACTED, result)
    result = _PROVIDER_KEY_RE.sub(REDACTED, result)
    result = _INLINE_KV_RE.sub(rf"\1\2{REDACTED}", result)
    return result


def scrub_secrets(
    value: Any,
    *,
    max_depth: int = 16,
    _depth: int = 0,
    _seen: set[int] | None = None,
) -> Any:
    """Recursively redact sensitive keys and raw secret patterns from arbitrary data."""
    if _depth > max_depth:
        return "[MAX_DEPTH_EXCEEDED]"

    if isinstance(value, SecretStr):
        return REDACTED

    if isinstance(value, str):
        return scrub_text(value)

    if value is None or isinstance(value, (bool, int, float)):
        return value

    seen = set() if _seen is None else _seen
    obj_id = id(value)
    if obj_id in seen:
        return "[CIRCULAR_REFERENCE]"

    if isinstance(value, Mapping):
        seen.add(obj_id)
        try:
            scrubbed: dict[str, Any] = {}
            for raw_key, item_val in value.items():
                key_str = str(raw_key)
                lowered = key_str.lower()
                if is_sensitive_key(key_str):
                    scrubbed[key_str] = REDACTED
                elif lowered in _DSN_KEYS and isinstance(item_val, str):
                    scrubbed[key_str] = scrub_text(item_val)
                else:
                    scrubbed[key_str] = scrub_secrets(
                        item_val,
                        max_depth=max_depth,
                        _depth=_depth + 1,
                        _seen=seen,
                    )
            return scrubbed
        finally:
            seen.remove(obj_id)

    if isinstance(value, list):
        seen.add(obj_id)
        try:
            return [
                scrub_secrets(item, max_depth=max_depth, _depth=_depth + 1, _seen=seen)
                for item in value
            ]
        finally:
            seen.remove(obj_id)

    if isinstance(value, tuple):
        seen.add(obj_id)
        try:
            return tuple(
                scrub_secrets(item, max_depth=max_depth, _depth=_depth + 1, _seen=seen)
                for item in value
            )
        finally:
            seen.remove(obj_id)

    if isinstance(value, (set, frozenset)):
        seen.add(obj_id)
        try:
            return [
                scrub_secrets(item, max_depth=max_depth, _depth=_depth + 1, _seen=seen)
                for item in value
            ]
        finally:
            seen.remove(obj_id)

    return scrub_text(str(value))
