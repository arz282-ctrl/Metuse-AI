"""Google identity OAuth2 start/callback adapter (live + dev/test mock mode).

Strictly scoped to user identity sign-in (`user_identities`, scopes
`openid email profile`). Never couples to Meta business assets or ad accounts.
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

_GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105
_GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
_GOOGLE_IDENTITY_SCOPES = "openid email profile"
_GOOGLE_MOCK_ALLOWED_ENVS = frozenset({"development", "test"})


@dataclass(frozen=True)
class GoogleLoginStartResult:
    """Authorization URL and CSRF state returned by Google login start."""

    authorization_url: str
    state: str
    mode: str


@dataclass(frozen=True)
class GoogleUserIdentityResult:
    """Verified Google user profile and opaque secret reference (never raw token)."""

    provider_subject: str
    email: str
    full_name: str
    avatar_url: str | None
    token_secret_ref: str
    mode: str


def resolve_google_auth_mode(settings: BaseAppSettings) -> str:
    """Determine Google OAuth execution mode; block mock mode outside development/test."""
    mode = settings.google_auth_mode.strip().lower() or "mock_dev"
    app_env = settings.app_env.strip().lower()
    if mode == "mock_dev" and app_env not in _GOOGLE_MOCK_ALLOWED_ENVS:
        raise ExternalServiceError(
            "Google mock authentication mode is disabled outside development/test environments"
        )
    if mode not in {"mock_dev", "live"}:
        raise BadRequestError(f"Unsupported GOOGLE_AUTH_MODE '{mode}'")
    return mode


def _resolve_allowlisted_redirect_uri(
    settings: BaseAppSettings,
    redirect_uri: str | None,
) -> str:
    allowed = settings.get_google_allowed_redirect_uris()
    if not allowed:
        raise BadRequestError("Google redirect URI allowlist is not configured")
    candidate = (redirect_uri or settings.google_redirect_uri).strip()
    if not candidate:
        raise BadRequestError("Google redirect URI is not configured")
    if candidate not in allowed:
        raise BadRequestError("Google redirect URI is not in the configured allowlist")
    return candidate


def start_google_oauth(
    settings: BaseAppSettings,
    *,
    redirect_uri: str | None = None,
    session_store: SessionStore | None = None,
) -> GoogleLoginStartResult:
    """Create CSRF-protected OAuth state and Google hosted identity login URL."""
    mode = resolve_google_auth_mode(settings)
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
        token_use="google_oauth_state",  # noqa: S106
    )

    if mode == "mock_dev":
        params = urlencode(
            {
                "code": f"mock-google-code-{state_nonce[:12]}",
                "state": state_token,
            }
        )
        separator = "&" if "?" in target_redirect else "?"
        return GoogleLoginStartResult(
            authorization_url=f"{target_redirect}{separator}{params}",
            state=state_token,
            mode=mode,
        )

    client_id = settings.google_client_id.strip()
    if not client_id:
        raise ExternalServiceError("GOOGLE_CLIENT_ID must be configured for live Google login")

    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": target_redirect,
            "state": state_token,
            "scope": _GOOGLE_IDENTITY_SCOPES,
            "response_type": "code",
            "access_type": "online",
        }
    )
    return GoogleLoginStartResult(
        authorization_url=f"{_GOOGLE_AUTHORIZE_URL}?{query}",
        state=state_token,
        mode=mode,
    )


def verify_google_oauth_state(
    settings: BaseAppSettings,
    state: str,
    *,
    session_store: SessionStore | None = None,
) -> dict[str, str]:
    """Validate signed CSRF state parameter and optionally consume its single-use nonce."""
    jwt_secret = resolve_jwt_secret(settings)
    claims = decode_hs256_token(state, secret=jwt_secret, expected_use="google_oauth_state")
    redirect_uri = str(claims.get("redirect_uri", "")).strip()
    mode = str(claims.get("mode", "")).strip()
    nonce = str(claims.get("nonce", "")).strip()
    if not redirect_uri or not nonce or mode not in {"mock_dev", "live"}:
        raise BadRequestError("Invalid Google OAuth state claims")
    if redirect_uri not in settings.get_google_allowed_redirect_uris():
        raise BadRequestError("Google OAuth state redirect URI is not allowlisted")
    if session_store is not None and not session_store.consume_oauth_nonce(nonce):
        raise BadRequestError("Google OAuth state has expired or was already consumed")
    return {"redirect_uri": redirect_uri, "mode": mode, "nonce": nonce}


def exchange_google_code(
    settings: BaseAppSettings,
    *,
    code: str,
    state: str,
    mock_email: str | None = None,
    mock_name: str | None = None,
    http_client: httpx.Client | None = None,
    session_store: SessionStore | None = None,
) -> GoogleUserIdentityResult:
    """Exchange Google OAuth authorization code server-side and return secret ref only."""
    if not code or not code.strip():
        raise BadRequestError("Missing Google OAuth authorization code")
    mode = resolve_google_auth_mode(settings)
    state_claims = verify_google_oauth_state(settings, state, session_store=session_store)
    if state_claims["mode"] != mode:
        raise BadRequestError("Google OAuth state mode mismatch")

    cleaned_code = code.strip()
    if mode == "mock_dev":
        subject_digest = hashlib.sha256(cleaned_code.encode("utf-8")).hexdigest()[:16]
        if mock_email and mock_email.strip():
            email = mock_email.strip().lower()
            subject = f"google-{hashlib.sha256(email.encode('utf-8')).hexdigest()[:16]}"
        else:
            subject = f"google-dev-{subject_digest}"
            email = f"{subject}@google.local"
        full_name = (mock_name or "Google Verified User").strip() or "Google Verified User"
        token_ref = f"env://google-oauth/{subject}/{secrets.token_hex(8)}"
        return GoogleUserIdentityResult(
            provider_subject=subject,
            email=email,
            full_name=full_name,
            avatar_url=None,
            token_secret_ref=token_ref,
            mode=mode,
        )

    client_id = settings.google_client_id.strip()
    client_secret = settings.google_client_secret.get_secret_value().strip()
    if not client_id or not client_secret:
        raise ExternalServiceError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be configured for live Google login"
        )

    client = http_client or httpx.Client(timeout=10.0)
    should_close = http_client is None
    try:
        token_resp = client.post(
            _GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": state_claims["redirect_uri"],
                "code": cleaned_code,
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            raise ExternalServiceError("Google token exchange failed")
        token_data = token_resp.json()
        raw_access_token = str(token_data.get("access_token", "")).strip()
        if not raw_access_token:
            raise ExternalServiceError("Google token exchange did not return an access token")

        me_resp = client.get(
            _GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {raw_access_token}"},
        )
        if me_resp.status_code != 200:
            raise ExternalServiceError("Google profile lookup failed")
        me_data = me_resp.json()
        subject = str(me_data.get("sub", "")).strip()
        if not subject:
            raise ExternalServiceError("Google profile response missing subject id")
        if me_data.get("email_verified") is not True:
            raise ExternalServiceError("Google account email is not verified")

        email = str(me_data.get("email") or f"google-{subject}@google.local").strip().lower()
        full_name = str(me_data.get("name") or "Google User").strip()
        avatar = str(me_data.get("picture") or "").strip() or None
        token_fingerprint = hashlib.sha256(raw_access_token.encode("utf-8")).hexdigest()[:16]
        token_ref = f"secret://google-login/{subject}/{token_fingerprint}"
        return GoogleUserIdentityResult(
            provider_subject=subject,
            email=email,
            full_name=full_name,
            avatar_url=avatar,
            token_secret_ref=token_ref,
            mode=mode,
        )
    except httpx.HTTPError as exc:
        raise ExternalServiceError("Google OAuth network request failed") from exc
    finally:
        if should_close:
            client.close()
