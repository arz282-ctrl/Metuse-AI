# Release Readiness & Deployment Architecture Audit — Metuse AI (Phase 1)

**Date:** 2026-11-26  
**Branch:** `feat/phase-1-runnable-foundation`

---

## 1. Authentication & Frontend Security Audit (Task 2)

### Verified Controls
- **Zero Server-Side Next.js Dependencies:** `apps/web/` exports cleanly via `output: "export"` (`apps/web/out/`, 10 static HTML pages). Zero Next.js API routes, zero server actions, zero `SUPABASE_SERVICE_ROLE_KEY` references in frontend code.
- **Server-Side Authorization Authority:** `ProtectedRoute` in `apps/web` is strictly a UX guard; **FastAPI enforces authentication (`get_current_actor`) and tenant authorization (`tenant_transaction` + `FORCE RLS` + RBAC) on every protected endpoint** (verified live: unauthenticated → `401`, cross-tenant → `403 WORKSPACE_ACCESS_DENIED`, insufficient role → `403 PERMISSION_DENIED`).
- **Dual JWT Verification:** FastAPI supports canonical Metuse HS256 sessions (with server-side `jti` revocation) AND Supabase Auth JWTs (both `HS256` and asymmetric JWKS `ES256`/`RS256`/`EdDSA` with key-rotation refresh).
- **Logout & Refresh Behavior:** `POST /api/v1/auth/logout` revokes the token `jti` server-side and clears the session cookie; subsequent `/api/v1/auth/me` or page refresh requests with that token receive `401` and force re-authentication.

### Security Finding — Static Export + Cross-Origin Session Storage
- **Finding (Medium, Expected for Phase 1 Static Export):** Because Cloudflare Pages (`*.pages.dev`) and a separate FastAPI API domain (`api.<domain>`) are cross-site unless hosted under the same apex domain, `SameSite=Lax` cookies are not sent on cross-site XHR requests; `apps/web/src/lib/api-client.ts` therefore stores the short-lived Bearer token in `window.sessionStorage` (cleared on tab close and logout).
- **Smallest Safe Solution for Production (Zero Architecture Rewrite):**
  1. Serve frontend at `app.<yourdomain>.com` and FastAPI at `api.<yourdomain>.com` (same-site apex domain) so `HttpOnly; Secure; SameSite=Lax` cookies work natively without exposing tokens to JS (`sessionStorage`), **and**
  2. Add a strict `Content-Security-Policy` header in `apps/web/public/_headers` before production launch to harden against XSS.

---

## 2. Deployment Architecture & Free-Tier-First Hosting Plan (Task 5)

| Layer | Recommended Platform | Free-Tier Limits & Caveats |
|---|---|---|
| **Frontend (`apps/web`)** | **Cloudflare Pages** (`apps/web/out` via `infrastructure/cloudflare/wrangler.jsonc`) | Unlimited static requests, 500 builds/mo, native `_headers` security headers, automatic HTTPS & preview URLs per branch. |
| **Primary API (`apps/api`)** | **Render Free Web Service** (Docker via `infrastructure/docker/api.Dockerfile`) *or* **Koyeb Free** | Free-tier Python ASGI container (512 MB RAM, auto-HTTPS). **Limitation:** Spins down after ~15 min idle (~30–50s cold start). Upgrade to Render Starter (\$7/mo) or Fly.io (\$5/mo) before production traffic. |
| **Database, Auth & Storage** | **Supabase Free Plan** | 500 MB PostgreSQL 16, 1 GB S3-compatible Storage, 50,000 MAU Auth. **Limitation:** Pauses after 7 days of inactivity; upgrade to Supabase Pro (\$25/mo) before production launch. |
| **Session Store** | `InMemorySessionStore` (Phase 1 single-instance) → Upstash Redis Free (Phase 2 multi-replica) | \$0/mo in Phase 1. Note: in-memory revocation resets on container restart (acceptable for single-instance dev/preview; enable `REDIS_URL` before multi-instance production). |
| **CI/CD** | **GitHub Actions** (`ci.yml`, `deploy-preview.yml`, `deploy-production.yml`) | 2,000 free mins/mo (private repo). Production deploy gated by `environment: production` manual approval. |

### Connectivity & Environment Matrix
- **Migrations (`DATABASE_URL`):** Supabase Direct/Session connection (`:5432`, owner role) — used ONLY for `alembic upgrade head`.
- **Runtime API (`APP_DATABASE_URL`):** Supabase Supavisor Transaction Pooler (`:6543`, `metuse_app` non-superuser role) — `packages/tenancy/db.py` automatically sets `prepare_threshold=None` and binds `set_config(..., is_local=true)` per transaction.
- **CORS (`APP_CORS_ORIGINS`):** Explicit comma-separated allowlist (e.g., `http://localhost:3000,https://metuse-ai-web.pages.dev,https://app.yourdomain.com`); wildcards are stripped.
- **Preview vs. Production:**
  - `develop` branch → Cloudflare Pages preview (`deploy-preview.yml`, skips cleanly if secrets absent).
  - `main` branch → Production (`deploy-production.yml`, requires manual GitHub Environment reviewer approval).
