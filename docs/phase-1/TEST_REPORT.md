# Phase 1 Test Report — Metuse AI

**Date:** 2026-11-26  
**Backend Environment:** macOS (Apple Silicon), Python `3.13.7` (`.venv`), `ruff 0.17.6`, `mypy 2.4.1`, `pytest 9.2.0`, `alembic 1.20.1`, `pyjwt 2.15.1`, `cryptography 50.0.2`, disposable **PostgreSQL 16.13** (`/tmp/metuse_pg_disposable`, destroyed cleanly after verification)  
**Frontend Environment:** Node.js `v24.22.3`, `pnpm 10.33.0`, Next.js `15.5.29`, TypeScript `5.9.3`, Tailwind CSS `4.3.5`, ESLint `9.39.5`

---

## 1. Passed Checks (All Executed & Verified)

| Check | Exact Command | Actual Result | Status |
|---|---|---|---|
| **Python Lint** | `.venv/bin/ruff check .` | `All checks passed!` | **PASS** |
| **Python Static Type Check (`strict`)** | `.venv/bin/mypy apps packages database tests` | `Success: no issues found in 50 source files` | **PASS** |
| **Python Unit Tests (38 tests)** | `.venv/bin/pytest tests/unit -q` | `38 passed, 3 warnings in 1.11s` | **PASS** |
| **Alembic Graph Head Check** | `.venv/bin/alembic heads` | `0003_supabase_compat (head)` | **PASS** |
| **Alembic Full Upgrade/Downgrade/Re-Upgrade (Disposable Postgres 16.13)** | `alembic upgrade head && alembic downgrade base && alembic upgrade head` | Clean `0001` → `0002` → `0003` → `base` → `0003` round-trip | **PASS** |
| **Live PostgreSQL 16 `FORCE RLS` & Tenancy Integration Suite (12 tests)** | `.venv/bin/pytest tests/integration -v` (disposable Postgres 16.13) | **`12 passed in 2.36s`** (covers `metuse_app` non-superuser role, fail-closed unset GUCs, 7-table cross-tenant isolation, composite FKs, immutable `audit_logs`, pre-workspace actor spoofing rejection, 5-member agency cap, 1-member creator cap, function `search_path` hardening, client membership scoping) | **PASS** |
| **Live End-to-End FastAPI + Exported Next.js Verification (6/6 E2E checks)** | `uvicorn apps.api.main:app` (`:8000`) + static `apps/web/out` (`:3000`) + live `httpx` suite | `E2E_1_FRONTEND_LOAD` (7/7 routes 200), `E2E_2_HEALTH_READY_DB` (`database=ok`, CORS), `E2E_3_UNAUTH_REJECTED` (401), `E2E_4_REGISTER_WORKSPACE_CLIENT_DASHBOARD`, `E2E_5_CROSS_TENANT_ISOLATION` (User B got 403 on User A's workspace/clients/audit logs), `E2E_6_LOGOUT_AND_REFRESH_INVALIDATION` (204 logout → 401 post-logout) | **PASS** |
| **Frontend Lint** | `cd apps/web && pnpm lint` | `eslint .` exited 0 (0 errors, 0 warnings) | **PASS** |
| **Frontend Typecheck** | `cd apps/web && pnpm typecheck` | `tsc --noEmit` exited 0 | **PASS** |
| **Frontend Production Build** | `cd apps/web && pnpm build` | `Compiled successfully`, `Generating static pages (10/10)`, `Exporting (2/2)` to `apps/web/out/` | **PASS** |

---

## 2. Real Bugs Caught & Fixed During Release Readiness Audit

1. **Alembic `version_num VARCHAR(32)` Truncation (`database/migrations/env.py`):** Revision `0002_harden_functions_search_path` (33 chars) failed on fresh PostgreSQL because Alembic defaults `alembic_version.version_num` to `VARCHAR(32)`. Fixed idempotently in `env.py` by ensuring `VARCHAR(128)` before `run_migrations()`.
2. **Creator 1-Member Cap Ordering (`packages/tenancy/workspaces.py`):** `add_workspace_member()` checked `require_permission("members:manage")` before checking `workspace_type == "creator"`, raising `403 PermissionDeniedError` instead of `409 WorkspaceMemberLimitExceededError` for `creator_owner`. Fixed ordering and verified `12/12` integration tests pass.

---

## 3. Failed Checks

- **None.**

---

## 4. Not Executed / Blocked External Checks

| Check | Reason |
|---|---|
| **Live Supabase PostgreSQL Migration & Auth E2E** | No live Supabase credentials in `.env` and live migrations withheld pending explicit approval |
| **Cloudflare Pages Preview Deploy** | No `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN` configured yet |
