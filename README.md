# Metuse AI

Cloud-first AI Social Growth & Media Operations Platform for agencies and creators: research, plan, create, edit, publish and measure social content from one tenant-aware system.

- Full specification: [implementation (1).md](<implementation (1).md>)
- Architecture decisions, risks, open questions: [docs/architecture/decisions.md](docs/architecture/decisions.md)
- Phased task plan: [docs/architecture/task-plan.md](docs/architecture/task-plan.md)
- Ucuts audit: [docs/architecture/ucuts-audit.md](docs/architecture/ucuts-audit.md)

## Architecture (summary)

Modular monolith plus isolated background workers:

- Web: Next.js + TypeScript + Tailwind + shadcn/ui
- API: FastAPI + Pydantic (`/api/v1`)
- Data: PostgreSQL (UUID PKs, row-level security), Redis, S3-compatible object storage
- Workflows: Temporal
- AI: Meta Muse adapter (reasoning/image/transcribe/segmentation), specialist workers (research, writing/SEO/AEO, media-pack)
- Video: unified text-to-video / image-to-video gateway (mock first)
- Editing: Ucuts adapter + sandboxed FFmpeg render workers
- Meta: Unified Login, Instagram Platform, Marketing API (separate adapters); publishing always requires human approval

## Repository layout

```text
apps/        web, api
workers/     orchestrator, research, writing_seo_aeo, media_pack, video_generation,
             media_editing, meta_publishing, analytics
packages/    shared, auth, tenancy, skill_runtime, ai_core, muse_adapter, video_gateway,
             media_engine, ucuts_adapter, meta_integrations, storage
skills/      router, research-pack, marketing-seo-pack, media-pack (reference data only)
infrastructure/  docker, cloud, temporal, monitoring
database/    migrations, seeds
docs/        architecture, api, security, operations
tests/       unit, integration, workflows, e2e
```

## Status

| Phase | Status |
|---|---|
| 0 - Reconnaissance, repo scaffold, decisions | Complete, pending review |
| 1 - Runnable foundation | Awaiting approval |
| 2-7 | Not started |

Secrets: never commit. Use `.env.example` as a template only.
