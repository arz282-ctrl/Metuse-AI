"""Authentication, password KDF, HS256 JWT, RBAC, session revocation, and identity OAuth."""

from packages.auth.data_deletion import verify_meta_signed_request
from packages.auth.google_oauth import (
    GoogleLoginStartResult,
    GoogleUserIdentityResult,
    exchange_google_code,
    resolve_google_auth_mode,
    start_google_oauth,
    verify_google_oauth_state,
)
from packages.auth.jwt import decode_hs256_token, encode_hs256_token, resolve_jwt_secret
from packages.auth.meta_oauth import (
    MetaLoginStartResult,
    MetaUserIdentityResult,
    exchange_meta_code,
    resolve_meta_auth_mode,
    start_meta_oauth,
    verify_meta_oauth_state,
)
from packages.auth.passwords import hash_password, verify_password
from packages.auth.rbac import (
    ROLE_PERMISSIONS,
    get_permissions_for_role,
    has_permission,
    require_permission,
)
from packages.auth.sessions import (
    SESSION_COOKIE_NAME,
    CookieParams,
    InMemorySessionStore,
    RedisSessionStore,
    SessionStore,
    build_session_cookie_kwargs,
    remaining_ttl_seconds,
    resolve_cookie_secure,
)
from packages.auth.supabase_jwt import (
    SupabaseJwksCache,
    SupabaseJwtClaims,
    build_supabase_jwks_url,
    verify_supabase_jwt,
    verify_supabase_jwt_from_settings,
)

__all__ = [
    "ROLE_PERMISSIONS",
    "SESSION_COOKIE_NAME",
    "CookieParams",
    "GoogleLoginStartResult",
    "GoogleUserIdentityResult",
    "InMemorySessionStore",
    "MetaLoginStartResult",
    "MetaUserIdentityResult",
    "RedisSessionStore",
    "SessionStore",
    "SupabaseJwksCache",
    "SupabaseJwtClaims",
    "build_session_cookie_kwargs",
    "build_supabase_jwks_url",
    "decode_hs256_token",
    "encode_hs256_token",
    "exchange_google_code",
    "exchange_meta_code",
    "get_permissions_for_role",
    "has_permission",
    "hash_password",
    "remaining_ttl_seconds",
    "require_permission",
    "resolve_cookie_secure",
    "resolve_google_auth_mode",
    "resolve_jwt_secret",
    "resolve_meta_auth_mode",
    "start_google_oauth",
    "start_meta_oauth",
    "verify_google_oauth_state",
    "verify_meta_oauth_state",
    "verify_meta_signed_request",
    "verify_password",
    "verify_supabase_jwt",
    "verify_supabase_jwt_from_settings",
]
