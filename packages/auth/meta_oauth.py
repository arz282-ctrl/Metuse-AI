"""Meta identity OAuth2 start/callback adapter (live + dev/test mock mode).

Strictly scoped to Phase 1 user identity sign-in (`user_identities`, scopes `email,public_profile`).
Never couples to Phase 5 `MetaConnection` / Business Integration System User tokens.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from packages.auth.jwt import decode_hs256_token, encode_hs256_token, resolve_jwt_secret
from packages.auth.sessions import SessionStore
from packages.shared.config import BaseAppSettings
from packages.shared.errors import BadRequestError, ExternalServiceError

_META_AUTHORIZE_URL = "https://www.facebook.com/v22.0/dialog/oauth"
_META_TOKEN_URL = "https://graph.facebook.com/v22.0/oauth/access_token"  # noqa: S105
_META_ME_URL = "https://graph.facebook.com/v22.0/me"
_META_IDENTITY_SCOPES = "email,public_profile"
_META_MOCK_ALLOWED_ENVS = frozenset({"development", "test"})


@dataclass(frozen=True)
class MetaLoginStartResult:
    """Authorization URL and CSRF state returned by Meta login start."""

    authorization_url: str
    state: str
    mode: str


@dataclass(frozen=True)
class MetaUserIdentityResult:
    """Verified Meta user profile and opaque secret reference (never raw token)."""

    provider_subject: str
    email: str
    full_name: str
    token_secret_ref: str
    mode: str


def resolve_meta_auth_mode(settings: BaseAppSettings) -> str:
    """Determine Meta OAuth execution mode; block mock mode outside development/test."""
    mode = settings.meta_auth_mode.strip().lower() or "mock_dev"
    app_env = settings.app_env.strip().lower()
    if mode == "mock_dev" and app_env not in _META_MOCK_ALLOWED_ENVS:
        raise ExternalServiceError(
            "Meta mock authentication mode is disabled outside development/test environments"
        )
    if mode not in {"mock_dev", "live"}:
        raise BadRequestError(f"Unsupported META_AUTH_MODE '{mode}'")
    return mode


def _resolve_allowlisted_redirect_uri(
    settings: BaseAppSettings,
    redirect_uri: str | None,
) -> str:
    allowed = settings.get_meta_allowed_redirect_uris()
    if not allowed:
        raise BadRequestError("Meta redirect URI allowlist is not configured")
    candidate = (redirect_uri or settings.meta_redirect_uri).strip()
    if not candidate:
        raise BadRequestError("Meta redirect URI is not configured")
    if candidate not in allowed:
        raise BadRequestError("Meta redirect URI is not in the configured allowlist")
    return candidate


def start_meta_oauth(
    settings: BaseAppSettings,
    *,
    redirect_uri: str | None = None,
    session_store: SessionStore | None = None,
) -> MetaLoginStartResult:
    """Create CSRF-protected OAuth state and Meta hosted identity login URL."""
    mode = resolve_meta_auth_mode(settings)
    jwt_secret = resolve_jwt_secret(settings)
    target_redirect = _resolve_allowlisted_redirect_uri(settings, redirect_uri)

    state_nonce = secrets.token_urlsafe(24)
    if session_store is not None:
        session_store.store_oauth_nonce(state_nonce, ttl_seconds=600)

    state_token = encode_hs256_token(
        {
            "nonce": state_nonce,
            "redirect_uri": target_redirect,
            "mode": mode,
        },
        secret=jwt_secret,
        ttl_seconds=600,
        token_use="meta_oauth_state",  # noqa: S106
    )

    if mode == "mock_dev":
        params = urlencode(
            {
                "code": f"mock-meta-code-{state_nonce[:12]}",
                "state": state_token,
            }
        )
        separator = "&" if "?" in target_redirect else "?"
        return MetaLoginStartResult(
            authorization_url=f"{target_redirect}{separator}{params}",
            state=state_token,
            mode=mode,
        )

    app_id = settings.meta_app_id.strip()
    if not app_id:
        raise ExternalServiceError("META_APP_ID must be configured for live Meta login")

    query = urlencode(
        {
            "client_id": app_id,
            "redirect_uri": target_redirect,
            "state": state_token,
            "scope": _META_IDENTITY_SCOPES,
            "response_type": "code",
        }
    )
    return MetaLoginStartResult(
        authorization_url=f"{_META_AUTHORIZE_URL}?{query}",
        state=state_token,
        mode=mode,
    )


def verify_meta_oauth_state(
    settings: BaseAppSettings,
    state: str,
    *,
    session_store: SessionStore | None = None,
) -> dict[str, str]:
    """Validate signed CSRF state parameter and optionally consume its single-use nonce."""
    jwt_secret = resolve_jwt_secret(settings)
    claims = decode_hs256_token(state, secret=jwt_secret, expected_use="meta_oauth_state")
    redirect_uri = str(claims.get("redirect_uri", "")).strip()
    mode = str(claims.get("mode", "")).strip()
    nonce = str(claims.get("nonce", "")).strip()
    if not redirect_uri or not nonce or mode not in {"mock_dev", "live"}:
        raise BadRequestError("Invalid Meta OAuth state claims")
    if redirect_uri not in settings.get_meta_allowed_redirect_uris():
        raise BadRequestError("Meta OAuth state redirect URI is not allowlisted")
    if session_store is not None and not session_store.consume_oauth_nonce(nonce):
        raise BadRequestError("Meta OAuth state has expired or was already consumed")
    return {"redirect_uri": redirect_uri, "mode": mode, "nonce": nonce}


def exchange_meta_code(
    settings: BaseAppSettings,
    *,
    code: str,
    state: str,
    mock_email: str | None = None,
    mock_name: str | None = None,
    http_client: httpx.Client | None = None,
    session_store: SessionStore | None = None,
) -> MetaUserIdentityResult:
    """Exchange Meta OAuth authorization code server-side and return secret ref only."""
    if not code or not code.strip():
        raise BadRequestError("Missing Meta OAuth authorization code")
    mode = resolve_meta_auth_mode(settings)
    state_claims = verify_meta_oauth_state(settings, state, session_store=session_store)
    if state_claims["mode"] != mode:
        raise BadRequestError("Meta OAuth state mode mismatch")

    cleaned_code = code.strip()
    if mode == "mock_dev":
        subject_digest = hashlib.sha256(cleaned_code.encode("utf-8")).hexdigest()[:16]
        if mock_email and mock_email.strip():
            email = mock_email.strip().lower()
            subject = f"meta-{hashlib.sha256(email.encode('utf-8')).hexdigest()[:16]}"
        else:
            subject = f"meta-dev-{subject_digest}"
            email = f"{subject}@meta.local"
        full_name = (mock_name or "Meta Verified User").strip() or "Meta Verified User"
        token_ref = f"env://meta-oauth/{subject}/{secrets.token_hex(8)}"
        return MetaUserIdentityResult(
            provider_subject=subject,
            email=email,
            full_name=full_name,
            token_secret_ref=token_ref,
            mode=mode,
        )

    app_id = settings.meta_app_id.strip()
    app_secret = settings.meta_app_secret.get_secret_value().strip()
    if not app_id or not app_secret:
        raise ExternalServiceError(
            "META_APP_ID and META_APP_SECRET must be configured for live Meta login"
        )

    client = http_client or httpx.Client(timeout=10.0)
    should_close = http_client is None
    try:
        token_resp = client.get(
            _META_TOKEN_URL,
            params={
                "client_id": app_id,
                "client_secret": app_secret,
                "redirect_uri": state_claims["redirect_uri"],
                "code": cleaned_code,
            },
        )
        if token_resp.status_code != 200:
            raise ExternalServiceError("Meta token exchange failed")
        token_data = token_resp.json()
        raw_access_token = str(token_data.get("access_token", "")).strip()
        if not raw_access_token:
            raise ExternalServiceError("Meta token exchange did not return an access token")

        me_resp = client.get(
            _META_ME_URL,
            params={"fields": "id,name,email", "access_token": raw_access_token},
        )
        if me_resp.status_code != 200:
            raise ExternalServiceError("Meta profile lookup failed")
        me_data = me_resp.json()
        subject = str(me_data.get("id", "")).strip()
        if not subject:
            raise ExternalServiceError("Meta profile response missing user id")

        email = str(me_data.get("email") or f"meta-{subject}@meta.local").strip().lower()
        full_name = str(me_data.get("name") or "Meta User").strip()
        token_fingerprint = hashlib.sha256(raw_access_token.encode("utf-8")).hexdigest()[:16]
        token_ref = f"secret://meta-login/{subject}/{token_fingerprint}"
        return MetaUserIdentityResult(
            provider_subject=subject,
            email=email,
            full_name=full_name,
            token_secret_ref=token_ref,
            mode=mode,
        )
    except httpx.HTTPError as exc:
        raise ExternalServiceError("Meta OAuth network request failed") from exc
    finally:
        if should_close:
            client.close()
