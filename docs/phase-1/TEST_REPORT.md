# Phase 1 Test Report — Metuse AI

**Date:** 2026-11-27  
**Backend Environment:** macOS (Apple Silicon), Python `3.13.7` (`.venv`), `ruff 0.17.6`, `mypy 2.4.1`, `pytest 9.2.0`, `alembic 1.20.1`, `pyjwt 2.15.1`, `cryptography 50.0.2`  
**Frontend Environment:** Node.js `v24.22.3`, `pnpm 10.33.0`, Next.js `15.5.29`, TypeScript `5.9.3`, Tailwind CSS `4.3.5`, ESLint `9.39.5`  
**Linked Supabase Project:** `<supabase-project-ref>` (redacted for public repo)

---

## 1. Passed Checks (All Executed & Verified)

| Check | Exact Command / Endpoint | Actual Result | Status |
|---|---|---|---|
| **Python Formatting** | `.venv/bin/ruff format --check .` | `73 files already formatted` | **PASS** |
| **Python Lint** | `.venv/bin/ruff check .` | `All checks passed!` | **PASS** |
| **Python Static Type Check (`strict`)** | `.venv/bin/mypy apps packages database tests` | `Success: no issues found in 50 source files` | **PASS** |
| **Python Unit Tests (39 tests)** | `.venv/bin/pytest tests/unit -q` | `39 passed, 3 warnings in 1.11s` (includes HS256 & asymmetric ES256/RS256 JWKS verification, key rotation, modern `SUPABASE_URL`/`SUPABASE_SECRET_KEY`/`SUPABASE_JWKS_URL` precedence & scrubbing, FastAPI auth/RBAC/CORS/security headers, pooler detection, Alembic linearity) | **PASS** |
| **Alembic Graph Head Check** | `.venv/bin/alembic heads` | `0003_supabase_compat (head)` | **PASS** |
| **Live Supabase PostgreSQL 17.12 Alembic Migrations (`0001`–`0003`)** | `.venv/bin/alembic upgrade head` (via IPv4 Supavisor Session Pooler `:5432`) | Applied `0001_initial_schema` → `0002_harden_functions_search_path` → `0003_supabase_compat`; 11 public tables + 6 canonical roles seeded; `metuse_app` connected over Supavisor Transaction Pooler `:6543` (`unset_guc_workspaces=0`) | **PASS** |
| **Live Supabase PostgreSQL 17.12 `FORCE RLS` & Tenancy Suite (12 tests)** | `.venv/bin/pytest tests/integration -v` (live Supabase `:5432` owner + `:6543` `metuse_app`) | **`12 passed in 88.90s`** (all 7 tenant tables isolated, `metuse_app` non-superuser verified, fail-closed unset GUCs, immutable `audit_logs`, actor spoofing blocked, 5-member agency & 1-member creator caps, composite FKs, hardened `search_path`) | **PASS** |
| **Live FastAPI E2E Against Live Supabase (`:6543` pooler)** | FastAPI `TestClient` against live Supabase DB | **`/readyz` HTTP 200** (`database=ok`, `session_store=ok`), register 2 users, create workspace + client + audit logs, cross-tenant User B **403** on workspace/clients/audit, logout revocation **401**, test rows cleaned up | **PASS** |
| **Live Supabase Auth Health** | `GET https://<project-ref>.supabase.co/auth/v1/health` | `HTTP 200` (`GoTrue v2.199.0`) | **PASS** |
| **Live Supabase JWKS & `SupabaseJwksCache`** | `SupabaseJwksCache(".../auth/v1/.well-known/jwks.json").get_signing_key(kid)` | `HTTP 200`, parsed live `ES256` `ECPublicKey` | **PASS** |
| **Live Supabase PostgREST Auth Check** | `GET https://<project-ref>.supabase.co/rest/v1/` (with `SUPABASE_SECRET_KEY`) | `HTTP 200` (authenticated cleanly) | **PASS** |
| **Live Supabase Storage Auth Check** | `GET https://<project-ref>.supabase.co/storage/v1/bucket` (with `SUPABASE_SECRET_KEY`) | `HTTP 200`, `[]` (0 buckets created yet) | **PASS** |
| **Alembic Full Round-Trip (Disposable Postgres 16.13, 2026-11-26)** | `alembic upgrade head && alembic downgrade base && alembic upgrade head` | Clean `0001` → `0002` → `0003` → `base` → `0003` round-trip | **PASS** |
| **Live PostgreSQL 16 `FORCE RLS` & Tenancy Integration Suite (12 tests, 2026-11-26)** | `.venv/bin/pytest tests/integration -v` (disposable Postgres 16.13) | `12 passed in 2.36s` | **PASS** |
| **Live End-to-End FastAPI + Exported Next.js Verification (6/6 E2E checks, 2026-11-26)** | `uvicorn apps.api.main:app` (`:8000`) + static `apps/web/out` (`:3000`) | 6/6 E2E flows passed (routes 200, `/readyz`, register/workspace/client/dashboard, cross-tenant 403, logout revocation 401) | **PASS** |
| **Frontend Lint** | `cd apps/web && pnpm lint` | `eslint .` exited 0 (0 errors, 0 warnings) | **PASS** |
| **Frontend Typecheck** | `cd apps/web && pnpm typecheck` | `tsc --noEmit` exited 0 | **PASS** |
| **Frontend Production Build** | `cd apps/web && pnpm build` | `Compiled successfully in 2.3s`, `Generating static pages (10/10)`, `Exporting (2/2)` to `apps/web/out/` | **PASS** |
| **Cloudflare Account & Token Verification** | `GET /client/v4/user/tokens/verify` & `/accounts` | `HTTP 200`, `active` (account ID redacted in public repo) | **PASS** |
| **Cloudflare Pages Preview Deploy (`develop`)** | `wrangler pages deploy apps/web/out --project-name=metuse-ai-web --branch=develop` + `curl -sI https://develop.metuse-ai-web.pages.dev/` | **`HTTP/2 200` LIVE** (39 files + `_headers` uploaded; verified `Strict-Transport-Security`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy`, `x-robots-tag: noindex` at `https://develop.metuse-ai-web.pages.dev` & `https://6be2d2e4.metuse-ai-web.pages.dev`) | **PASS** |
| **GitHub Environments & Cloudflare Secrets** | `gh api PUT repos/arz282-ctrl/Metuse-AI/environments/{preview,production}` + `gh secret set` | Both `preview` and `production` (with Required Reviewer `arz282-ctrl`) configured with `CLOUDFLARE_ACCOUNT_ID` & `CLOUDFLARE_API_TOKEN` | **PASS** |

---

## 2. Real Issues Caught & Fixed in Final Hardening Pass

1. **CI `ruff format --check .` Failures (`pyproject.toml` + `apps/api/deps.py`):** `ruff format` attempted to check `implementation (1).md` (untouched Phase 0 spec) and flagged line 108 in `apps/api/deps.py`. Added `"implementation (1).md"` to `[tool.ruff]` `exclude` and formatted `apps/api/deps.py`; `ruff format --check .` now passes (`73 files already formatted`).
2. **Deploy Workflows Missing Node/pnpm Setup (`.github/workflows/deploy-preview.yml`, `.github/workflows/deploy-production.yml`):** Added `pnpm/action-setup@v4` (v10.33.0) and `actions/setup-node@v4` (Node 24) to both Cloudflare deploy jobs and wired the production Cloudflare Pages deploy command behind the `environment: production` manual approval gate.
3. **Previous Audit Fixes (`54c6968`):** Widened `alembic_version.version_num` to `VARCHAR(128)` (`database/migrations/env.py`) and fixed `creator_owner` 1-member cap ordering (`packages/tenancy/workspaces.py`).

---

## 3. Failed / Blocked External Checks

| Check | Exact Observed Error / Blocker | Required Action |
|---|---|---|
| **GitHub Actions CI Runs (`#45826349839`, `#45826349476`)** | All 3 jobs failed in 2–3s with GitHub account annotation: `"The job was not started because your account is locked due to a billing issue."` | Resolve GitHub account billing/Actions lock in GitHub Settings → Billing, then re-run CI. |
| **Cloudflare Pages Production Deploy (`main`)** | Production `main` deploy intentionally withheld pending manual approval and hosted FastAPI `PRODUCTION_API_URL`. | Set `PREVIEW_API_URL` / `PRODUCTION_API_URL` in GitHub `preview` / `production` environments once FastAPI is hosted, then approve production deploy on `main`. |
