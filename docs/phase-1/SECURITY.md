# Security Architecture & Checklist — Metuse AI (Phase 1)

---

## 1. Security Controls Implemented

| Control | Implementation | Verification |
|---|---|---|
| **Tenant Isolation (RLS)** | `FORCE ROW LEVEL SECURITY` on all 7 tenant-scoped tables via non-superuser `metuse_app` (`NOBYPASSRLS`) and fail-closed transaction-local GUCs (`set_config(..., is_local=true)`) | `tests/integration/test_m1_db_rls.py`, `tests/integration/test_tenancy_services.py` |
| **Supabase JWT Verification (HS256 + Asymmetric JWKS)** | `packages/auth/supabase_jwt.py` uses `PyJWT` + `cryptography`: supports `HS256` (secret) and `ES256`/`RS256`/`EdDSA` via thread-safe `SupabaseJwksCache` with automatic key-rotation refresh. Enforces signature verification, `alg` allowlist (rejects `alg=none`), `aud="authenticated"`, `iss`, `exp`/`nbf`/`iat`, and UUID `sub`. Never trusts unverified claims. | `test_supabase_hs256_jwt_valid_and_rejection_cases`, `test_supabase_asymmetric_jwks_verification_and_key_rotation` |
| **Native Auth & Sessions** | `scrypt` password KDF, HS256 JWT with `jti` revocation (`InMemorySessionStore` / `RedisSessionStore`), `HttpOnly; Secure; SameSite=Lax` cookies, Meta identity OAuth (`mock_dev` blocked outside `development`/`test`, single-use nonce, strict redirect allowlist) | `tests/unit/test_auth_and_storage.py` |
| **Privilege Escalation Prevention** | Users cannot self-modify roles; `members:manage` required; only `agency_owner` can assign `agency_owner`; last active `agency_owner` protected | `packages/tenancy/workspaces.py`, `test_fastapi_endpoints_security_cors_rbac_and_supabase_jwks_auth` |
| **Immutable Audit Logging** | Append-only `audit_logs` with `prevent_audit_log_mutation()` trigger blocking `UPDATE`/`DELETE`/`TRUNCATE` even for DB owner | `tests/integration/test_m1_db_rls.py` |
| **Secret Management & Scrubbing** | Pydantic `SecretStr` + `scrub_secrets()` on logs/errors; `apps/web/src/lib/env.ts` exposes ONLY `NEXT_PUBLIC_*` variables (zero service-role keys in frontend) | `test_supabase_pooler_detection_and_secret_scrubbing` |
| **HTTP & Edge Security Headers** | FastAPI middleware (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, `X-Request-Id`), strict non-wildcard CORS allowlist, auth rate limiter, plus Cloudflare Pages `apps/web/public/_headers` (including HSTS) | `test_fastapi_endpoints_security_cors_rbac_and_supabase_jwks_auth` |
| **Storage Security** | Tenant-prefixed keys, path traversal guards, magic-byte MIME verification, SVG/HTML XSS rejection, private bucket + short-lived SigV4 presigned URLs | `packages/storage/`, `tests/unit/test_auth_and_storage.py` |

---

## 2. Never-Log & Never-Expose Policy

Forbidden in logs, error payloads, URLs, Git, and frontend bundles:
- Access/refresh tokens, passwords, password hashes
- `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `APP_JWT_SECRET`
- `META_APP_SECRET`, `CLOUDFLARE_API_TOKEN`, payment secrets
