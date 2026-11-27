# Phase 2 Completion Report — Skill Runtime, Meta Muse Adapter & Specialist Workers

**Branch:** `feat/phase-2-skill-runtime-and-workers` (targeting `main` via PR #2)  
**Spec Reference:** `implementation (1).md` (Sections 4.5, 5.2, 6, 8, 9, 10, 12, 16, 17, 18) & `docs/architecture/task-plan.md`  
**Cloud Budget Impact:** `$0/month` (Meta Muse runs in deterministic `mock` mode by default until a real `MUSE_API_KEY` is supplied; Supabase Free + Render Free + Cloudflare Pages Free)

---

## 1. Delivered Capabilities

### 1.1 Database Schema & Tenant Isolation (`database/migrations/versions/0004_phase2_ai_workers_and_usage.py`)
- Additive, cleanly reversible Alembic migration (`0003_supabase_compat -> 0004_phase2_ai_workers_and_usage`) applied to live Supabase PostgreSQL 17.12:
  - Global skill metadata tables: `skill_definitions`, `skill_versions` (least-privilege `SELECT` for `metuse_app`).
  - Tenant-scoped tables: `worker_jobs`, `model_runs`, `usage_records`, `workspace_budgets`, `research_reports`, `research_sources`.
  - Composite tenant foreign keys `(workspace_id, client_id)` and `(workspace_id, job_id)` preventing cross-tenant joins at the schema layer.
  - Partial unique indexes:
    - `uq_worker_jobs_ws_idempotency` on `(workspace_id, idempotency_key) WHERE idempotency_key IS NOT NULL`
    - `uq_workspace_budgets_default` on `(workspace_id) WHERE client_id IS NULL`
    - `uq_workspace_budgets_ws_client` on `(workspace_id, client_id) WHERE client_id IS NOT NULL`
  - `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY` on all 6 tenant tables with `metuse_app` CRUD grants and `updated_at` triggers.

### 1.2 Allowlisted Skill Runtime & Deterministic Router (`packages/skill_runtime/`)
- `SkillRegistry` loads **only** the 3 allowlisted vendored packs (`research-pack`, `marketing-seo-pack`, `media-pack`) with SHA-256 content hashes and wraps prompt references in `<untrusted_skill_reference>` boundaries.
- `route_task()` deterministically maps `WorkerTaskType` to its exact allowlisted pack, schema version, and Muse capability; unknown or unapproved task types raise `INVALID_TASK_TYPE`.

### 1.3 Capability-Based Meta Muse Adapter (`packages/muse_adapter/`)
- `MuseCapability` routing supports `reasoning_tool_use`, `reasoning_structured_output`, `image_generation`, `image_edit`, `audio_transcription`, and `segmentation`.
- Explicitly rejects `"video_generation"` with `CapabilityNotSupportedError` (HTTP `422`, `CAPABILITY_NOT_SUPPORTED`) — video generation is deferred to the Phase 3 Unified Video Gateway.
- `MockMuseAdapter`: `$0` deterministic offline outputs matching versioned Pydantic contracts.
- `LiveMuseAdapter`: OpenAI-compatible `https://api.meta.ai/v1` client with `X-Meta-Privacy-Mode: standard_no_training`, bounded `429` exponential backoff + `Retry-After` + jitter, and global `MUSE_KILL_SWITCH` enforcement (`503 PROVIDER_DISABLED`).

### 1.4 AI Core, Guardrails & Specialist Workers (`packages/ai_core/`, `workers/`)
- Section 4.5 `WorkerJobEnvelope` & `WorkerResult` contracts and versioned schemas:
  - `metuse.research_report.v1` (`workers/research`)
  - `metuse.writing_package.v1` (`workers/writing_seo_aeo`)
  - `metuse.seo_aeo_package.v1` (`workers/writing_seo_aeo`)
  - `metuse.media_plan.v1` (`workers/media_pack`)
- Untrusted-input prompt compiler isolates user/client inputs and skill references from system instructions.
- Pre-flight `WorkspaceBudget` cap enforcement (`429 BUDGET_EXCEEDED`), global kill switch (`503 PROVIDER_DISABLED`), `Idempotency-Key` deduplication and payload-mismatch conflict (`409`), full `ModelRun` + `UsageRecord` (workspace + client cost attribution) + `ResearchReport`/`ResearchSource` persistence, and `AuditLog` entries.

### 1.5 API Endpoints & Frontend Workspace (`apps/api/`, `apps/web/`)
- **Brand Profile API (`apps/api/routes/brand.py`):**
  - `GET /api/v1/workspaces/{workspace_id}/clients/{client_id}/brand`
  - `PUT /api/v1/workspaces/{workspace_id}/clients/{client_id}/brand`
- **Specialist Worker, Usage & Budget API (`apps/api/routes/jobs.py`):**
  - `GET /api/v1/workspaces/{workspace_id}/skills`
  - `POST /api/v1/workspaces/{workspace_id}/clients/{client_id}/research-jobs`
  - `POST /api/v1/workspaces/{workspace_id}/clients/{client_id}/writing-jobs`
  - `POST /api/v1/workspaces/{workspace_id}/clients/{client_id}/seo-aeo-jobs`
  - `POST /api/v1/workspaces/{workspace_id}/clients/{client_id}/media-plan-jobs`
  - `GET /api/v1/workspaces/{workspace_id}/jobs`
  - `GET /api/v1/workspaces/{workspace_id}/jobs/{job_id}`
  - `GET /api/v1/workspaces/{workspace_id}/usage`
  - `PUT /api/v1/workspaces/{workspace_id}/budgets`
- **RBAC (`packages/auth/rbac.py`):** Added `jobs:*`, `jobs:read`, `jobs:write`, `usage:read`, `budgets:*`, `budgets:manage` across canonical workspace roles.
- **Web UI (`apps/web/src/app/(app)/jobs/page.tsx`):** Added `/jobs` ("AI Workers") page to dispatch all 4 specialist jobs with `Idempotency-Key`, inspect structured JSON outputs, and track billing-period usage and cost totals.
- **Container Packaging (`infrastructure/docker/api.Dockerfile`, `pyproject.toml`):** Included `workers/` and `skills/` in wheel packaging and Docker runtime image.

---

## 2. Verification Summary

| Check | Command | Result |
|---|---|---|
| Python formatting | `ruff format --check .` | Passed (93 files) |
| Python linting | `ruff check .` | Passed (0 errors) |
| Python static typing | `mypy apps packages database workers tests` | Passed (70 source files, 0 issues) |
| Unit tests (Phase 0–2) | `pytest tests/unit -q` | Passed (`45 passed`) |
| Alembic migration head | `alembic heads` | `0004_phase2_ai_workers_and_usage (head)` |
| Live Supabase migration | `alembic upgrade head` | Applied `0003_supabase_compat -> 0004_phase2_ai_workers_and_usage` cleanly |
| Live Supabase Phase 2 `FORCE RLS` & worker tests | `pytest tests/integration/test_phase2_rls_and_jobs.py -v` | Passed (`2 passed in 19.38s`) |
| Frontend lint & typecheck | `pnpm --dir apps/web lint && pnpm --dir apps/web typecheck` | Passed (0 errors) |
| Frontend static export | `NEXT_PUBLIC_API_URL=https://metuse-ai-api-preview.onrender.com pnpm --dir apps/web build` | Passed (11/11 static pages including `/jobs`) |
| Acceptance criteria covered | `#7` (Research worker), `#8` (Writing/SEO/AEO), `#17` (Cost attribution), `#18` (Deterministic allowlisted skill routing) | Verified in unit + live Supabase integration suites |

---

## 3. Deployment & Next Phase

- **Cloudflare Pages Preview:** Deployed updated `apps/web/out` (with `/jobs`) to `https://develop.metuse-ai-web.pages.dev`.
- **Render Preview API:** Automatically builds and deploys Phase 2 routes + `workers/` + `skills/` once PR #2 is merged into `main` (runs in `$0` `MUSE_MODE=mock` by default until `MUSE_API_KEY` + `MUSE_MODE=live` are configured in Render env vars).
- **Recommended Next Phase:** **Phase 3 — Unified Video Generation Gateway** (`Kling 3.0`, `Seedance 2.0`, `Veo 3.1`, `Sora 2` adapters, async render job polling, webhook ingestion, R2 media storage integration, and media cost/budget enforcement).
