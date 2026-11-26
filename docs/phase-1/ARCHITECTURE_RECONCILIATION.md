# Phase 1 Architecture Reconciliation Report & Migration Plan

**Date:** 2026-11-26  
**Repository:** `/Users/ahmedxriyaz/Desktop/Metuse AI`  
**Branch:** `feat/phase-1-runnable-foundation` (`feae0f4`, ahead of `main` `754ad02`)  
**Adopted Strategy:** **Unified Hybrid Architecture** (Single Python/FastAPI API + Single Alembic-Managed 10-Table Schema + Supabase Managed Infrastructure Layer + Thin Web Client + GitHub Actions CI/CD)

---

## 1. Complete Working Tree Inspection (9 Modified + 14 Untracked Files)

All existing and uncommitted work on `feat/phase-1-runnable-foundation` was inspected line-by-line and is **preserved untouched**:

### Modified Tracked Files (9)
| File | What Changed Locally | Keep / Status |
|---|---|---|
| `.env.example` | Added `S3_INTERNAL_ENDPOINT_URL`, `S3_PUBLIC_ENDPOINT_URL`, `SESSION_COOKIE_SECURE`, `APP_CORS_ORIGINS`, `META_AUTH_MODE` | **Preserve**; extend additively with Supabase/Cloudflare/Sentry placeholders |
| `infrastructure/docker/docker-compose.yml` | Wired dual S3 endpoints, cookie security, CORS, and Meta auth env vars into `api` service | **Preserve**; remains optional for local OrbStack/Colima dev |
| `packages/auth/__init__.py` | Exported `sessions.py` helpers (`RedisSessionStore`, `InMemorySessionStore`, cookie helpers) and hardened `meta_oauth` symbols | **Preserve** |
| `packages/auth/jwt.py` | Minor refinement in HS256 JWT helper | **Preserve** |
| `packages/auth/meta_oauth.py` | Enforced `mock_dev` strictly to `development`/`test`, exact redirect URI allowlist, and single-use OAuth nonce consumption | **Preserve** |
| `packages/auth/rbac.py` | Refined permission matching (`members:manage`, `brand:write` implies `brand:read`) | **Preserve** |
| `packages/shared/config.py` | Added dual S3 endpoint helpers, CORS allowlist parser, Meta redirect URI allowlist, `session_cookie_secure` | **Preserve**; extend additively with optional Supabase/Cloudflare/Sentry settings |
| `tests/integration/test_m1_db_rls.py` | Added pre-workspace audit actor spoofing tests & hardened RLS assertions | **Preserve** |
| `tests/unit/test_shared.py` | Updated config/scrubbing unit test assertions | **Preserve** |

### Untracked Files (14)
| File(s) | Purpose | Keep / Status |
|---|---|---|
| `database/migrations/versions/0002_harden_functions_search_path.py` | Hardens all 5 PostgreSQL functions with `SET search_path = pg_catalog, public` and reaffirms 5-member cap trigger | **Preserve** (canonical Alembic HEAD) |
| `packages/auth/sessions.py` | `SessionStore` protocol, `RedisSessionStore` (fail-closed), `InMemorySessionStore`, HttpOnly/Secure/SameSite=Lax cookie builder | **Preserve** |
| `packages/storage/{__init__,keys,protocol,s3,validation}.py` | Tenant-prefixed S3/MinIO storage backend with dual internal/public SigV4 endpoints, path traversal guards, and magic-byte/SVG-XSS rejection | **Preserve** (directly compatible with Supabase Storage S3 API) |
| `packages/tenancy/{__init__,audit,brand,clients,db,workspaces}.py` | `SET LOCAL` GUC RLS transaction managers (`rls_transaction`, `tenant_transaction`), Agency/Creator workspace onboarding, 5-member cap, client CRUD, brand profile/asset upload, append-only audit logging | **Preserve** |
| `tests/integration/test_tenancy_services.py` | Live PostgreSQL integration tests for Step 1 function hardening and Batch A tenancy/storage services | **Preserve** |
| `tests/unit/test_auth_and_storage.py` | Unit tests for scrypt KDF, JWT revocation, Meta OAuth nonce/allowlist, S3 dual-endpoint presigning, magic-byte/SVG rejection | **Preserve** |

---

## 2. Conflict & Compatibility Matrix (Existing Codebase vs. Phase 1 PRD)

| Area | Existing Codebase (`feat/phase-1-runnable-foundation`) | Phase 1 PRD Requirement | Conflict? | Unified Hybrid Resolution |
|---|---|---|---|---|
| **Primary Backend API** | Python 3.13 + FastAPI (`apps/api/` currently `.gitkeep`) + `packages/*` | Backend API + Supabase integration | None if FastAPI stays primary | **Keep Python/FastAPI as the sole application API.** Wire existing `packages/auth`, `packages/tenancy`, `packages/storage` into `apps/api/`. |
| **Database Engine & Migrations** | PostgreSQL 16 + SQLAlchemy 2.0 + Alembic (`0001`, `0002`) | Supabase PostgreSQL + versioned migrations | Low | **Keep Alembic as the single source of truth.** Target Supabase PostgreSQL via `DATABASE_URL` (direct/session port `5432`, owner role for migrations) and `APP_DATABASE_URL` (non-superuser `metuse_app` role, `NOBYPASSRLS`, transaction pooler `6543` compatible with `prepare_threshold=None`). |
| **Schema Model** | 10 tables: `users`, `user_identities`, `roles`, `workspaces`, `workspace_memberships`, `clients`, `client_memberships`, `brand_profiles`, `brand_assets`, `audit_logs` | 6 tables: `profiles`, `organizations`, `organization_members`, `roles`, `clients`, `audit_logs` | **Naming/duplication conflict if both created** | **Do NOT create a parallel 6-table schema.** Map PRD concepts directly onto the canonical 10-table schema (see Section 3). Add `avatar_url` to `users` and `'supabase'` to `user_identities.provider` via additive, reversible Alembic migration `0003`. |
| **Row Level Security** | `FORCE ROW LEVEL SECURITY` on 7 tenant tables via `metuse_app` role + transaction-local GUCs (`app.current_workspace_id`, `app.current_user_id`) | Mandatory RLS on all customer-owned tables + cross-tenant isolation tests | Compatible (superior defense-in-depth) | **Preserve GUC-based `FORCE RLS` via `metuse_app`**. FastAPI enforces JWT auth + resolves active membership + binds `set_config(..., is_local=true)` per transaction. Compatible with Supabase PgBouncer Transaction Mode. |
| **Authentication** | Email+password (`scrypt`) + HS256 JWT + `SessionStore` + Meta identity OAuth (`user_identities`) | Supabase Auth (register, login, logout, session persistence, password reset) | Needs clean bridge | **One unified auth flow in FastAPI:** Support native FastAPI auth AND additive Supabase Auth JWT verification (`packages/auth/supabase_jwt.py`). When `SUPABASE_URL` + `SUPABASE_JWT_SECRET` are configured, FastAPI verifies Supabase-issued JWTs and maps `sub` → `user_identities(provider='supabase')` → `users.id` (auto-provisioning the `users` row on first login). |
| **File Storage** | S3-compatible (`packages/storage/s3.py`, MinIO locally) | Supabase Storage initially | None | **Zero rewrite needed.** Supabase Storage exposes an S3-compatible endpoint (`https://<ref>.supabase.co/storage/v1/s3`) directly consumable by `S3StorageBackend`. |
| **Session Store / Redis** | `RedisSessionStore` + `InMemorySessionStore` | Free-tier-first; no paid Redis in Phase 1 | Minor | Default to `InMemorySessionStore` when `REDIS_URL` is unset/empty in free-tier single-instance deployments; use `RedisSessionStore` when local Docker/Upstash Redis is configured. |
| **Frontend (`apps/web/`)** | Empty (`.gitkeep` only); Phase 0 docs & `web.Dockerfile` specify Next.js + TS + Tailwind + shadcn/ui on Node 24 / pnpm 10 | Frontend shell + auth + onboarding + Cloudflare Pages/Workers where compatible | None (no existing UI to conflict with) | Preserve `apps/web/` structure and Node 24 / pnpm 10 toolchain. Keep frontend strictly a thin UI client talking to FastAPI `/api/v1` (and optionally Supabase Auth). Evaluate Cloudflare Pages/Workers compatibility before finalizing adapter. |
| **Containers** | `infrastructure/docker/docker-compose.yml` (Colima/Docker) | Do not require Docker Desktop; OrbStack optional; prefer hosted Supabase | None | **Preserve `docker-compose.yml` untouched** as an optional local dev stack; support zero-container local dev against hosted Supabase PostgreSQL. |
| **CI/CD** | None (`.github/` absent) | `ci.yml`, `deploy-preview.yml`, `deploy-production.yml` with approval gate | Missing | Add all 3 workflows covering Python (`ruff`, `mypy`, `pytest tests/unit`, Alembic check) + Web + Cloudflare deployment gates. |

---

## 3. Canonical Schema Reconciliation Map (No Parallel Tables)

| Phase 1 PRD Concept | Canonical Metuse Table | Status & Delta |
|---|---|---|
| `profiles` | `public.users` | Existing (`id`, `email`, `password_hash`, `full_name`, `is_active`, `created_at`, `updated_at`). **Additive delta (`0003`):** add nullable `avatar_url TEXT`. |
| `organizations` | `public.workspaces` | Existing (`id`, `name`, `slug`, `workspace_type`, `max_members`, `created_by_user_id`, `created_at`, `updated_at`). Supports both `agency` (up to 5 members) and `creator` (1 member). |
| `organization_members` | `public.workspace_memberships` | Existing (`id`, `workspace_id`, `user_id`, `role_id`, `status`, `created_at`, `updated_at`). Enforces atomic `<= max_members` cap trigger. |
| `roles` | `public.roles` | Existing (`id`, `name`, `scope`, `description`, `permissions`, `created_at`) with 6 canonical roles (`agency_owner`, `agency_admin`, `agency_member`, `creator_owner`, `client_reviewer`, `client_viewer`). |
| `clients` | `public.clients` | Existing (`id`, `workspace_id`, `name`, `slug`, `status`, `metadata`, `created_at`, `updated_at`). |
| `audit_logs` | `public.audit_logs` | Existing (`id`, `workspace_id`, `client_id`, `actor_user_id`, `action`, `target_type`, `target_id`, `request_id`, `metadata`, `created_at`). Immutable append-only trigger + split RLS. |
| *(Preserved Phase 1+ tables)* | `public.user_identities`, `public.client_memberships`, `public.brand_profiles`, `public.brand_assets` | **Preserved untouched.** `user_identities.provider` check constraint expanded additively in `0003` from `('email', 'meta')` to `('email', 'meta', 'supabase')`. |

---

## 4. Proposed Additive, Reversible Migration Plan (`0003_supabase_compat.py`)

> **Status:** Proposed — will NOT be executed against any production/live database without explicit approval.

- **Revision:** `0003_supabase_compat` (down_revision: `0002_harden_functions_search_path`)
- **Upgrade (100% additive, non-destructive):**
  1. `ALTER TABLE public.users ADD COLUMN IF NOT EXISTS avatar_url TEXT NULL;`
  2. Replace check constraint `ck_user_identities_provider` to allow `provider IN ('email', 'meta', 'supabase')`.
  3. Add partial/lookup index `ix_users_email_lower` (`LOWER(email)`) if helpful for case-insensitive Supabase identity reconciliation.
- **Downgrade (cleanly reversible):**
  1. Drop `ix_users_email_lower`.
  2. Restore `ck_user_identities_provider` to `provider IN ('email', 'meta')` (safe when no `'supabase'` rows exist).
  3. `ALTER TABLE public.users DROP COLUMN IF EXISTS avatar_url;`
- **Supabase PostgreSQL Role Setup Note:**
  - On hosted Supabase, `0001_initial_schema.py` creates the `metuse_app` role (`NOSUPERUSER NOBYPASSRLS`) if absent. Because Supabase's default `postgres` user can create non-superuser roles (or if restricted, the DBA creates `metuse_app` once via SQL Editor with `GRANT USAGE ON SCHEMA public` and `GRANT SELECT, INSERT, UPDATE, DELETE` on application tables), `APP_DATABASE_URL` connects as `metuse_app` so `FORCE ROW LEVEL SECURITY` is enforced identically on Supabase and local Postgres.

---

## 5. Safe & Necessary Next Implementation Steps

### Safe to implement now (Additive, zero breaking changes to existing code):
1. **Environment & Config Standardization:**
   - Add optional Supabase (`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_JWT_SECRET`, `SUPABASE_SERVICE_ROLE_KEY`), Cloudflare (`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`), and `SENTRY_DSN` placeholders to `.env.example` and `packages/shared/config.py` (strictly keeping `SUPABASE_SERVICE_ROLE_KEY` server-only and scrubbed from logs).
   - Add Supabase PgBouncer transaction-pooler compatibility (`prepare_threshold=None` when pooler URL detected) in `packages/tenancy/db.py`.
2. **Additive Supabase Auth JWT Bridge (`packages/auth/supabase_jwt.py`):**
   - Implement pure-Python/HS256 Supabase JWT verifier (`verify_supabase_jwt`) that validates `iss`, `aud="authenticated"`, `exp`, and `sub` without replacing existing `packages/auth/jwt.py` or `passwords.py`.
   - Add unit tests proving valid Supabase JWT verification, expired/tampered token rejection, and fail-closed behavior when unconfigured.
3. **Primary FastAPI Application (`apps/api/`):**
   - Implement the FastAPI app shell (`apps/api/main.py`, `deps.py`, `routes/health.py`, `routes/auth.py`, `routes/workspaces.py`, `routes/clients.py`, `routes/audit.py`) wired directly to existing `packages/auth`, `packages/tenancy`, `packages/storage`, and `packages/shared`.
   - Security middleware: strict CORS allowlist, security headers, request IDs, scrubbed error responses, and in-memory rate limiting for auth endpoints.
   - Comprehensive FastAPI `TestClient` tests verifying auth, workspace onboarding, cross-tenant denial, RBAC privilege escalation denial, and env validation.
4. **GitHub Actions CI/CD (`.github/workflows/`):**
   - `ci.yml` (ruff, mypy, pytest unit tests, Alembic migration graph check)
   - `deploy-preview.yml` (`develop` branch)
   - `deploy-production.yml` (`main` branch with `production` environment manual approval gate)
5. **Phase 1 Documentation Suite (`docs/phase-1/`):**
   - `ENVIRONMENT_SETUP.md`, `DATABASE_SCHEMA.md`, `SECURITY.md`, `COST_AND_LIMITS.md`, `DEPLOYMENT_GUIDE.md`, `TEST_REPORT.md`, `IMPLEMENTATION_REPORT.md`, and updated `README.md`.

### Requires Explicit User Approval Before Executing:
1. Applying Alembic migration `0003_supabase_compat` to a live Supabase PostgreSQL instance (and providing Supabase connection credentials via local `.env` / GitHub Secrets).
2. Scaffolding `apps/web/` (Next.js vs another frontend framework — currently `.gitkeep` only, while Phase 0 docs specify Next.js + Tailwind + shadcn/ui).
3. Committing or pushing changes on `feat/phase-1-runnable-foundation`.
