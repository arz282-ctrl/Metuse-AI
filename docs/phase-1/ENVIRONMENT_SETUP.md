# Environment Setup Guide — Metuse AI (Phase 1)

This guide standardizes local and cloud development for Metuse AI under the **Unified Hybrid Architecture** (Python/FastAPI + Alembic + Supabase Managed Infrastructure + Optional OrbStack/Colima Local Containers).

---

## 1. Toolchain Versions

| Tool | Required Version | Purpose |
|---|---|---|
| Python | `3.13+` (`>=3.13`) | Primary FastAPI backend, Alembic migrations, domain packages, pytest |
| `uv` | `>=0.5.0` | Reproducible Python virtualenv & lockfile (`uv.lock`) management |
| Node.js | `24.x` (`v24.22.3` verified) | Frontend (`apps/web`) toolchain when scaffolded |
| `pnpm` | `10.x` (`10.33.0` verified) | Frontend package manager |
| Container Runtime (Optional) | **OrbStack** or Colima | Only required if running local Postgres/Redis/MinIO/Temporal instead of hosted Supabase |

> **Docker Desktop is NOT required.** Prefer hosted Supabase PostgreSQL (free tier) when local containers would consume excessive memory.

---

## 2. Quickstart (Zero-Container Hosted Supabase Mode)

1. **Copy environment template (never commit `.env`):**
   ```bash
   cp .env.example .env
   ```
2. **Install Python dependencies:**
   ```bash
   uv sync
   ```
3. **Configure Supabase PostgreSQL in `.env`:**
   - `DATABASE_URL` — Supabase **Direct / Session** connection string (port `5432`, owner role) used ONLY by Alembic migrations.
   - `APP_DATABASE_URL` — Supabase **Transaction Pooler** (`pooler.supabase.com:6543`) or direct connection bound to the non-superuser **`metuse_app`** role (`NOSUPERUSER NOBYPASSRLS`). `packages/tenancy/db.py` automatically disables psycopg3 prepared statements (`prepare_threshold=None`) when a Supabase transaction pooler URL is detected.
   - `NEXT_PUBLIC_SUPABASE_URL` & `NEXT_PUBLIC_SUPABASE_ANON_KEY` — Public client config (safe for browser).
   - `SUPABASE_SERVICE_ROLE_KEY` & `SUPABASE_JWT_SECRET` — **Server-only secrets. NEVER expose in browser code or `NEXT_PUBLIC_*` variables.**
   - `APP_JWT_SECRET` — Random 32+ byte secret for FastAPI session tokens.
4. **Apply Alembic migrations (owner connection):**
   ```bash
   uv run alembic upgrade head
   ```
5. **Run FastAPI locally:**
   ```bash
   uv run uvicorn apps.api.main:app --reload --port 8000
   ```
6. **Verify health:**
   ```bash
   curl -fsS http://localhost:8000/healthz
   curl -fsS http://localhost:8000/readyz
   ```

---

## 3. Optional Local Container Mode (OrbStack / Colima)

If you want local Postgres 16, Redis 7, MinIO, and Temporal without cloud access:
```bash
docker compose -f infrastructure/docker/docker-compose.yml up -d --build
uv run alembic upgrade head
APP_ENV=development uv run python -m database.seeds.seed_dev
```
See [docs/operations/local-dev.md](../operations/local-dev.md) for port overrides (`POSTGRES_HOST_PORT=5433`, etc.).

---

## 4. Environment Variable Reference (`.env.example`)

| Variable | Scope | Secret? | Description |
|---|---|---|---|
| `APP_ENV` | Server | No | `development`, `test`, `staging`, or `production` |
| `DATABASE_URL` | Server (Migrations) | Yes | Owner PostgreSQL DSN for Alembic migrations |
| `APP_DATABASE_URL` | Server (Runtime) | Yes | Non-superuser `metuse_app` DSN (`NOBYPASSRLS`) |
| `REDIS_URL` | Server (Optional) | Yes | Redis DSN; falls back to `InMemorySessionStore` in dev/free-tier |
| `NEXT_PUBLIC_SUPABASE_URL` | Public + Server | No | Supabase project URL (`https://<ref>.supabase.co`) |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Public | No | Supabase publishable/anon key |
| `SUPABASE_SERVICE_ROLE_KEY` | **Server-only** | **Yes** | Privileged Supabase admin key (never used for normal user queries) |
| `SUPABASE_JWT_SECRET` | **Server-only** | **Yes** | HS256 JWT verification secret for Supabase Auth tokens |
| `APP_JWT_SECRET` | **Server-only** | **Yes** | FastAPI session JWT signing secret (fail-closed outside dev) |
| `CLOUDFLARE_ACCOUNT_ID` | CI / Ops | No | Cloudflare account ID for Pages/Workers deployment |
| `CLOUDFLARE_API_TOKEN` | CI / Ops | **Yes** | Scoped Cloudflare deployment token (stored in GitHub Secrets) |
| `SENTRY_DSN` | Server / Web | No | Optional Sentry free-tier error reporting DSN |
