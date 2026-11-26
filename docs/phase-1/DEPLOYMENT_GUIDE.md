# Deployment Guide — Metuse AI (Phase 1)

---

## 1. Unified Hybrid Deployment Topology

| Component | Platform | Compatibility & Cost |
|---|---|---|
| **Web Frontend (`apps/web`)** | **Cloudflare Pages** (Next.js static export `output: "export"` → `apps/web/out`) | **100% Free-Tier Compatible** — unlimited static requests, 500 builds/mo, zero Workers 10ms CPU risk, native `_headers` security headers |
| **Primary API (`apps/api`)** | Python 3.13 / FastAPI container (`infrastructure/docker/api.Dockerfile`) | FastAPI is Python ASGI (not Cloudflare Pages/Workers); run locally (`uvicorn`) or on a free-tier Python container host (Render / Fly.io / Koyeb) |
| **Database, Auth & Storage** | **Supabase (Free Tier)** | Managed PostgreSQL 16 (Alembic migrations on `:5432`, `metuse_app` runtime on Supavisor `:6543`), Supabase Auth (HS256 + JWKS), S3-compatible Storage |

---

## 2. Cloudflare Pages Preview & Production Setup

Config files:
- `apps/web/next.config.ts` (`output: "export"`, `images: { unoptimized: true }`)
- `apps/web/public/_headers` (Cloudflare Pages security headers: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy`, `Strict-Transport-Security`)
- `infrastructure/cloudflare/wrangler.jsonc` (`pages_build_output_dir: "../../apps/web/out"`)

### Manual Preview Deploy (once credentials exist):
```bash
cd apps/web
NEXT_PUBLIC_API_URL=https://<your-preview-api> pnpm build
npx wrangler pages deploy out --project-name=metuse-ai-web --branch=develop
```

### Automated GitHub Actions Deployment:
1. Protect `main` and `develop` branches requiring `CI` (`python-quality-and-tests`, `postgres-rls-integration`, `web-quality-and-build`).
2. Create GitHub Environments:
   - `preview` (`develop` branch → `.github/workflows/deploy-preview.yml`)
   - `production` (`main` branch → `.github/workflows/deploy-production.yml` with **Required reviewers** enabled).
3. Add `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` as Environment Secrets and `PREVIEW_API_URL` as an Environment Variable.

---

## 3. FastAPI Preview API Hosting (Free Tier — Render Blueprint)

`render.yaml` configures a zero-cost (`plan: free`) Docker web service using `infrastructure/docker/api.Dockerfile`:

1. Open [Render Dashboard](https://dashboard.render.com/) → **New → Blueprint** → select `arz282-ctrl/Metuse-AI`.
2. Enter the prompted secret env vars (`APP_DATABASE_URL` using the Supabase `metuse_app` `:6543` Supavisor pooler URL, `SUPABASE_URL`, `SUPABASE_JWKS_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`). `APP_JWT_SECRET` is auto-generated.
3. Verify `GET https://<your-render-service>.onrender.com/healthz` and `/readyz` (`database: ok`).
4. Set GitHub `preview` environment variable `PREVIEW_API_URL=https://<your-render-service>.onrender.com` and rebuild/deploy Cloudflare Pages (`apps/web/out`) so the live preview UI talks to the live API.

---

## 4. Supabase Provisioning & Migration Runbook

1. Set `DATABASE_URL` (direct `:5432`, owner role) and `APP_DATABASE_URL` (Supavisor `:6543`, `metuse_app` non-superuser role) in `.env`.
2. Set `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` (server-only), and `SUPABASE_JWT_SECRET` (optional if using asymmetric JWKS).
3. After explicit approval, apply migrations:
   ```bash
   uv run alembic upgrade head
   ```
4. Verify `GET /healthz` and `GET /readyz`.
