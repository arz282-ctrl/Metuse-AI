# Local Development Runbook

This guide covers starting the local Metuse AI infrastructure stack, running PostgreSQL migrations and development-only seeds, and verifying lint, type checks, and RLS isolation tests.

## Prerequisites

- **Colima & Docker CLI** (macOS Apple Silicon / Intel) or Docker Desktop
- **`uv`** (`>= 0.5.0`) and Python **3.13**
- **Node.js 24** and **pnpm 10** (used for the web container / M4+)

> **Port Note (`5433` vs `5432`, plus Redis/Web overrides):** Local Docker Compose binds PostgreSQL to host port **`5433`** (`POSTGRES_HOST_PORT=5433`) by default to avoid colliding with a host Homebrew `postgresql@16` service on `5432`. If another local stack already occupies Redis `6379` or Web `3000`, override `REDIS_HOST_PORT` / `WEB_HOST_PORT` (for example `REDIS_HOST_PORT=6380 WEB_HOST_PORT=3001 docker compose -f infrastructure/docker/docker-compose.yml up -d --build`).

---

## 1. Environment Setup

Copy `.env.example` to `.env` (never commit `.env` or real secrets):

```bash
cp .env.example .env
```

Key local defaults in `.env.example`:
- `APP_ENV=development`
- `POSTGRES_HOST_PORT=5433`
- `DATABASE_URL=postgresql://metuse:change-me@localhost:5433/metuse` (owner connection used by Alembic migrations & dev seeds)
- `APP_DATABASE_URL=postgresql://metuse_app:change-me-app@localhost:5433/metuse` (non-superuser runtime connection with `NOSUPERUSER NOBYPASSRLS`)
- `S3_BUCKET=metuse-dev` (created privately with `anonymous set none` by `minio-init`)

---

## 2. Start Colima and Docker Compose Stack

```bash
# Start Colima VM if not already running
colima start

# Validate and start all local services (postgres, redis, minio, minio-init, temporal, api, web)
docker compose -f infrastructure/docker/docker-compose.yml up -d --build

# Check service health
docker compose -f infrastructure/docker/docker-compose.yml ps
```

Exposed local endpoints:
- **PostgreSQL 16:** `localhost:5433` (container `5432`, initializes `metuse_app` non-superuser role)
- **Redis 7:** `localhost:6379`
- **MinIO S3 API / Console:** `http://localhost:9000` / `http://localhost:9001` (private bucket `metuse-dev`)
- **Temporal Dev Server / UI:** `localhost:7233` / `http://localhost:8233`
- **API:** `http://localhost:8000/healthz`
- **Web:** `http://localhost:3000/`

---

## 3. Install Python Dependencies

```bash
uv sync
```

---

## 4. Run Database Migrations and Development Seeds

Alembic runs as the database owner (`DATABASE_URL`), while runtime services use `metuse_app` (`APP_DATABASE_URL`) so `FORCE ROW LEVEL SECURITY` is always enforced.

```bash
# Apply all migrations
uv run alembic upgrade head

# Verify clean rollback and re-apply (optional check)
uv run alembic downgrade base
uv run alembic upgrade head

# Seed development fixtures (idempotent; refuses to run if APP_ENV is production/staging)
APP_ENV=development uv run python -m database.seeds.seed_dev
```

---

## 5. Run Verification Checks

```bash
# Lint & static type checking
uv run ruff check .
uv run mypy packages database tests

# Unit & live PostgreSQL RLS / trigger integration tests
uv run pytest -v
```

---

## 6. Reset / Teardown

```bash
# Stop containers while preserving volumes
docker compose -f infrastructure/docker/docker-compose.yml down

# Full local reset (destroys local Postgres, Redis, MinIO, Temporal data)
docker compose -f infrastructure/docker/docker-compose.yml down -v
```
