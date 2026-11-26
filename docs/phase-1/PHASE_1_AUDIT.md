# Phase 1 Repository Audit — Metuse AI

**Date:** 2026-11-26  
**Repository Path:** `/Users/ahmedxriyaz/Desktop/Metuse AI`  
**Git Remote:** `origin` → `https://github.com/arz282-ctrl/Metuse-AI.git`  
**Current Branch:** `feat/phase-1-runnable-foundation` (tracking `origin/feat/phase-1-runnable-foundation`)  
**HEAD Commit:** `feae0f4` (`feat(phase-1): core infrastructure, RLS database schema, auth package, and integration test suite`)  
**Base Commit (`main`):** `754ad02` (`chore(phase-0): workspace reconnaissance, architecture decisions, skill vendoring, and repo scaffold`)

---

## 1. Git State Summary

The repository is currently on `feat/phase-1-runnable-foundation` (1 commit ahead of `main`) with **uncommitted local work in progress**:

- **Modified tracked files (9 files, `+231 / -75` lines):**
  - `.env.example`
  - `infrastructure/docker/docker-compose.yml`
  - `packages/auth/__init__.py`
  - `packages/auth/jwt.py`
  - `packages/auth/meta_oauth.py`
  - `packages/auth/rbac.py`
  - `packages/shared/config.py`
  - `tests/integration/test_m1_db_rls.py`
  - `tests/unit/test_shared.py`
- **Untracked files (14 files):**
  - `database/migrations/versions/0002_harden_functions_search_path.py`
  - `packages/auth/sessions.py`
  - `packages/storage/__init__.py`, `keys.py`, `protocol.py`, `s3.py`, `validation.py`
  - `packages/tenancy/__init__.py`, `audit.py`, `brand.py`, `clients.py`, `db.py`, `workspaces.py`
  - `tests/integration/test_tenancy_services.py`
  - `tests/unit/test_auth_and_storage.py`

> **Action Note:** None of these existing or uncommitted files will be overwritten or discarded without explicit approval.

---

## 2. Existing Architecture

The repository was scaffolded in Phase 0 and partially implemented in early Phase 1 around a **Python 3.13 modular monolith + isolated workers** architecture:

| Layer | Current Implementation | Status |
|---|---|---|
| **Python Packaging & Tooling** | `pyproject.toml`, `uv.lock`, `alembic.ini`, `ruff`, `mypy --strict`, `pytest` | Implemented |
| **Database ORM & Migrations** | SQLAlchemy 2.0 (`database/models.py`) + Alembic (`0001_initial_schema.py`, `0002_harden_functions_search_path.py`) | Implemented (PostgreSQL 16 + GUC-based RLS) |
| **Database Schema** | 10 UUID-PK tables: `users`, `user_identities`, `roles`, `workspaces`, `workspace_memberships`, `clients`, `client_memberships`, `brand_profiles`, `brand_assets`, `audit_logs` | Implemented |
| **Row Level Security** | `FORCE ROW LEVEL SECURITY` on 7 tenant tables via non-superuser `metuse_app` role and session GUCs (`app.current_workspace_id`, `app.current_user_id`), immutable `audit_logs` trigger, atomic 5-member workspace cap trigger | Implemented |
| **Shared Contracts** | `packages/shared/` (`BaseAppSettings`, enums, error envelopes, secret scrubbing, domain events, schemas) | Implemented |
| **Auth Package** | `packages/auth/` (scrypt password KDF, HS256 JWT, RBAC for 6 canonical roles, `InMemorySessionStore` / `RedisSessionStore`, Meta OAuth with `mock_dev` + redirect allowlist) | Implemented |
| **Tenancy Services** | `packages/tenancy/` (RLS transaction wrappers, workspace/client/brand/audit services) | Implemented (uncommitted) |
| **Storage Abstraction** | `packages/storage/` (`StorageBackend` protocol, `S3StorageBackend` dual-endpoint presigning, `InMemoryStorageBackend`, path traversal + magic-byte XSS/SVG guards) | Implemented (uncommitted) |
| **Local Container Stack** | `infrastructure/docker/docker-compose.yml` (Postgres 16, Redis 7, MinIO, Temporal, `api`, `web` shells) | Implemented |
| **Vendored Skills & Pins** | `skills/` (Mr-Agents packs + router reference + attribution), `packages/ucuts_adapter/UCUTS_PIN.json` | Completed in Phase 0 |
| **Web Frontend (`apps/web/`)** | Empty (`.gitkeep` only) | **Not started** |
| **API App (`apps/api/`)** | Empty (`.gitkeep` only) | **Not started** |
| **Workers (`workers/*`)** | Empty scaffold directories | Planned for Phase 2+ |

---

## 3. Existing Completed Functionality & Working Integrations

- **Phase 0 Deliverables (Complete):**
  - Repository initialization, GitHub remote (`arz282-ctrl/Metuse-AI`), `.gitignore`, `.env.example`.
  - Architecture decisions (`docs/architecture/decisions.md`), phased task plan (`docs/architecture/task-plan.md`), Ucuts audit (`docs/architecture/ucuts-audit.md`), and `packages/ucuts_adapter/UCUTS_PIN.json`.
  - Vendored Mr-Agents skill packs in `skills/`.
- **Early Phase 1 Deliverables (Implemented on `feat/phase-1-runnable-foundation`):**
  - 10-table PostgreSQL schema with `FORCE ROW LEVEL SECURITY`, hardened function `search_path`, audit log immutability, and 5-member cap trigger.
  - Python domain packages (`packages/shared`, `packages/auth`, `packages/tenancy`, `packages/storage`).
  - Comprehensive unit tests (`tests/unit/test_shared.py`, `tests/unit/test_auth_and_storage.py`) and live PostgreSQL RLS/tenancy integration tests (`tests/integration/test_m1_db_rls.py`, `tests/integration/test_tenancy_services.py`).
  - Local Docker Compose stack and runbook (`docs/operations/local-dev.md`).

---

## 4. Missing Infrastructure (Phase 1 PRD Gap Analysis)

Compared against the **Phase 1 Cloud Foundation PRD (Supabase + Cloudflare + Next.js + GitHub Actions)**:

1. **Web Application (`apps/web/`):** No Next.js / TypeScript / Tailwind application, no `package.json`, no environment validation, no auth pages, no onboarding flow, no protected dashboard routes.
2. **Supabase Integration (`supabase/` + SDK):**
   - No `supabase/` directory (`supabase/config.toml`, `supabase/migrations/`, `supabase/seed.sql`).
   - No `@supabase/supabase-js` or `@supabase/ssr` client configuration (browser client, server client, privileged service-role client, middleware session refresh).
   - No Supabase Auth integration (`auth.users` → `public.profiles` trigger, registration, login, logout, password reset).
   - No Supabase-native `auth.uid()` RLS policies for the 6 required cloud foundation tables (`profiles`, `organizations`, `organization_members`, `roles`, `clients`, `audit_logs`).
   - No generated TypeScript database types.
3. **Cloudflare Deployment Foundation (`infrastructure/cloudflare/`):**
   - No Cloudflare Pages / Workers adapter (`@opennextjs/cloudflare` or `wrangler.jsonc`), no security headers configuration (`_headers` / Next.js headers), no preview/production deployment config.
4. **GitHub Actions CI/CD (`.github/workflows/`):**
   - `.github/` does not exist. Missing `ci.yml`, `deploy-preview.yml`, and `deploy-production.yml` (with manual approval gate and OIDC/secret protection).
5. **Phase 1 Documentation Suite (`docs/phase-1/`):**
   - Missing `IMPLEMENTATION_REPORT.md`, `DEPLOYMENT_GUIDE.md`, `ENVIRONMENT_SETUP.md`, `DATABASE_SCHEMA.md`, `SECURITY.md`, `TEST_REPORT.md`, `COST_AND_LIMITS.md`, and updated root `README.md`.
6. **Environment & Toolchain Gaps:**
   - `.env.example` lacks `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `SENTRY_DSN`.
   - Local environment has Node `24.22.3`, `pnpm 10.33.0`, `uv`, `python3 3.13`, `gh`; `supabase`, `wrangler`, `docker`, and `orb` are not globally installed on `PATH` (Supabase CLI and Wrangler can run via `pnpm dlx` / `npx` when needed).

---

## 5. Existing Technical Debt & Architectural Divergence

1. **Critical Architectural Divergence (Requires Owner Confirmation):**
   - The existing `feat/phase-1-runnable-foundation` branch implements **custom Python/FastAPI auth + Alembic 10-table `workspaces` schema + GUC-based RLS + Docker/Redis/MinIO/Temporal**.
   - The new Phase 1 PRD specifies a **free-tier-first Supabase + Cloudflare + Next.js cloud foundation** (`profiles`, `organizations`, `organization_members`, `roles`, `clients`, `audit_logs`, Supabase Auth, `auth.uid()` RLS, Cloudflare Pages/Workers).
2. **Uncommitted Local Changes:** 9 modified and 14 untracked files on `feat/phase-1-runnable-foundation` are not yet committed.
3. **Empty Application Shells:** Both `apps/web/` and `apps/api/` contain only `.gitkeep`.
4. **No Live Cloud Credentials in Repo:** No `.env` file exists yet; Supabase project URL/keys and Cloudflare account credentials will need to be configured locally / in GitHub Secrets to run live cloud integration tests and deployments.

---

## 6. Dependencies to Preserve

- **Python Backend & Tooling:** `pyproject.toml`, `uv.lock`, `alembic.ini`, SQLAlchemy 2.0, Alembic, Pydantic v2, FastAPI, psycopg3, boto3, ruff, mypy, pytest.
- **Node Toolchain:** Node 24 (`v24.22.3`), `pnpm` (`10.33.0`).
- **Vendored Reference Data:** `skills/` and `packages/ucuts_adapter/UCUTS_PIN.json`.

---

## 7. Files That Must Not Be Overwritten

- `implementation (1).md` (Phase 0 source of truth)
- `docs/architecture/decisions.md`, `docs/architecture/task-plan.md`, `docs/architecture/ucuts-audit.md`
- `database/models.py`, `database/migrations/versions/0001_initial_schema.py`, `database/migrations/versions/0002_harden_functions_search_path.py`, `database/seeds/seed_dev.py`
- `packages/shared/*`, `packages/auth/*`, `packages/tenancy/*`, `packages/storage/*`, `packages/ucuts_adapter/UCUTS_PIN.json`
- `tests/unit/*`, `tests/integration/*`
- `skills/*`

---

## 8. Proposed Implementation Sequence

1. **Resolve Critical Architecture Alignment** (confirm Hybrid/Coexistent vs Unified schema strategy).
2. **Standardize Dev Environment:** Update `.env.example` (preserving all existing keys + adding Supabase/Cloudflare/Sentry placeholders), `.gitignore`, Node/pnpm version pins (`.nvmrc` / `package.json`).
3. **Supabase Database & RLS Foundation:** Create `supabase/config.toml`, `supabase/migrations/` (6-table multi-tenant schema, UUID PKs, UTC timestamps, indexes, `auth.users` → `profiles` trigger, organization creation RPC/workflow, strict `auth.uid()` RLS policies), and `supabase/seed.sql`.
4. **Next.js Frontend & Supabase Auth (`apps/web/`):** Scaffold Next.js (App Router, TypeScript, Tailwind CSS) with strict env validation (`zod`), separated browser/server/admin Supabase clients, typed database definitions, Auth flows (register, login, logout, password reset), onboarding (`Registration → Profile → Organization → Membership → Dashboard`), protected routes, and authenticated API routes with audit logging and security headers.
5. **Cloudflare Deployment Config (`infrastructure/cloudflare/`):** Configure Cloudflare Workers/Pages compatibility (`wrangler.jsonc` + `@opennextjs/cloudflare` or document any runtime constraint), security headers, and preview/production environments.
6. **GitHub Actions CI/CD (`.github/workflows/`):** Implement `ci.yml` (Python + Web lint/typecheck/test/build + SQL migration validation), `deploy-preview.yml` (`develop` branch), and `deploy-production.yml` (`main` branch with manual environment approval gate).
7. **Automated Verification & Security Testing:** Run unit tests, cross-tenant RLS isolation tests, env validation tests, lint/typecheck, and production build; record actual results in `docs/phase-1/TEST_REPORT.md`.
8. **Documentation & Handover:** Complete all 8 documents in `docs/phase-1/` and update root `README.md`.
