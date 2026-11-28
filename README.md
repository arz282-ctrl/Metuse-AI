# Metuse AI

Cloud-first AI Social Growth & Media Operations Platform for agencies and creators: research, plan, create, edit, publish and measure social content from one tenant-aware system.

- Full specification: [implementation.md](<implementation.md>)
- Phase 0 Architecture decisions, risks, open questions: [docs/architecture/decisions.md](docs/architecture/decisions.md)
- Phased task plan: [docs/architecture/task-plan.md](docs/architecture/task-plan.md)
- Ucuts audit: [docs/architecture/ucuts-audit.md](docs/architecture/ucuts-audit.md)
- Phase 1 Audit & Reconciliation: [docs/phase-1/PHASE_1_AUDIT.md](docs/phase-1/PHASE_1_AUDIT.md) · [docs/phase-1/ARCHITECTURE_RECONCILIATION.md](docs/phase-1/ARCHITECTURE_RECONCILIATION.md)

## Architecture (Unified Hybrid Foundation)

Single primary application API + single Alembic-managed canonical schema + Supabase managed infrastructure layer:

- **Primary API (`apps/api`):** FastAPI + Pydantic v2 (`/api/v1`)
- **Database (`database/`):** PostgreSQL 16 (Supabase Managed PostgreSQL or local Postgres 16), 10 canonical UUID-PK tables, Alembic migrations (`0001`–`0003`), `FORCE ROW LEVEL SECURITY` via non-superuser `metuse_app` role and transaction-local GUCs (`SET LOCAL`)
- **Authentication (`packages/auth`):** Email/password (`scrypt`), HS256 JWT sessions (`HttpOnly; Secure; SameSite=Lax`, server-side `jti` revocation), Meta identity OAuth, and additive Supabase Auth HS256 JWT verification
- **Storage (`packages/storage`):** S3-compatible private object storage (Supabase Storage S3 protocol or MinIO locally) with path-traversal and magic-byte/SVG-XSS validation
- **Web (`apps/web`):** Planned thin frontend client (Node 24 + pnpm 10; Cloudflare Pages/Workers compatible)
- **CI/CD (`.github/workflows`):** GitHub Actions CI (`ci.yml`), preview (`deploy-preview.yml`), and approval-gated production (`deploy-production.yml`)
- **Phases 2–7 Delivered:** Skill runtime + Meta Muse adapter, provider-independent video gateway, Ucuts + FFmpeg isolated render workers, Meta Graph/Instagram & Marketing API adapters, approval-gated publishing, and controlled ads execution (all currently mock-first; Temporal orchestration still planned)

## Repository Layout

```text
apps/            api (FastAPI), web (scaffold pending)
workers/         orchestrator, research, writing_seo_aeo, media_pack, video_generation,
                 media_editing, meta_publishing, analytics
packages/        shared, auth, tenancy, storage, skill_runtime, ai_core, muse_adapter,
                 video_gateway, media_engine, ucuts_adapter, meta_integrations
database/        models.py, migrations/ (Alembic), seeds/
infrastructure/  docker/ (optional OrbStack/Colima stack), cloud/, temporal/, monitoring/
.github/         workflows/ (ci.yml, deploy-preview.yml, deploy-production.yml)
docs/            architecture/, operations/, phase-1/
tests/           unit/, integration/, workflows/, e2e/
```

## Quickstart (Local & Supabase)

```bash
# 1. Configure environment (never commit .env)
cp .env.example .env

# 2. Install Python 3.13+ dependencies via uv
uv sync

# 3. Apply Alembic migrations (owner connection: DATABASE_URL)
uv run alembic upgrade head

# 4. Run FastAPI locally
uv run uvicorn apps.api.main:app --reload --port 8000
```

See [docs/phase-1/ENVIRONMENT_SETUP.md](docs/phase-1/ENVIRONMENT_SETUP.md) for hosted Supabase setup and [docs/operations/local-dev.md](docs/operations/local-dev.md) for optional local OrbStack/Colima containers.

## Verification & Testing

```bash
uv run ruff check .
uv run mypy apps packages database tests
uv run pytest tests/unit -v
uv run pytest tests/integration -v   # requires live PostgreSQL 16 (Supabase or local)
```

## Documentation (Phase 1)

- [PHASE_1_AUDIT.md](docs/phase-1/PHASE_1_AUDIT.md)
- [ARCHITECTURE_RECONCILIATION.md](docs/phase-1/ARCHITECTURE_RECONCILIATION.md)
- [ENVIRONMENT_SETUP.md](docs/phase-1/ENVIRONMENT_SETUP.md)
- [DATABASE_SCHEMA.md](docs/phase-1/DATABASE_SCHEMA.md)
- [SECURITY.md](docs/phase-1/SECURITY.md)
- [COST_AND_LIMITS.md](docs/phase-1/COST_AND_LIMITS.md)
- [DEPLOYMENT_GUIDE.md](docs/phase-1/DEPLOYMENT_GUIDE.md)
- [TEST_REPORT.md](docs/phase-1/TEST_REPORT.md)
- [IMPLEMENTATION_REPORT.md](docs/phase-1/IMPLEMENTATION_REPORT.md)

## Status

| Phase | Status |
|---|---|
| 0 - Reconnaissance, repo scaffold, decisions | Complete |
| 1 - Unified Hybrid Cloud Foundation & API | Complete ([report](docs/phase-1/PHASE_1_COMPLETION_REPORT.md)) |
| 2 - Skill runtime, Muse adapter, specialist workers | Complete ([report](docs/phase-2/PHASE_2_REPORT.md)) |
| 3 - Unified video generation gateway | Complete ([report](docs/phase-3/PHASE_3_REPORT.md)) |
| 4 - Cloud editing, Ucuts/FFmpeg, approvals | Complete ([report](docs/phase-4/PHASE_4_REPORT.md)) |
| 5 - Meta Unified Login & connections | Complete ([report](docs/phase-5/PHASE_5_REPORT.md)) |
| 6 - Approval-gated publishing & insights | Complete ([report](docs/phase-6/PHASE_6_REPORT.md)) |
| 7 - Ads read & controlled execution + 18-test acceptance | Complete ([report](docs/phase-7/PHASE_7_AND_ACCEPTANCE_REPORT.md)) |

Secrets: never commit. Use `.env.example` as a template only.
