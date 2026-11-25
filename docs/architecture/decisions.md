# Architecture Decisions - Phase 0

Date: 2026-11-25. Source of truth: `implementation (1).md`.

## 1. Workspace and asset inventory

| Item | State |
|---|---|
| Project dir | `/Users/ahmedxriyaz/Desktop/Metuse AI` - contained only `implementation (1).md` (53,343 bytes, untouched) |
| Git remote | https://github.com/arz282-ctrl/Metuse-AI.git - empty before Phase 0 |
| Google Drive | Folder `Metuse AI`, ID `15-EirfsWj4NbuLWyoE-aTyuFLeCPiKaH` (appears under "Shared with me"; owner arz4dev@gmail.com; contains `implementation.md`) |
| Ucuts | `/Users/ahmedxriyaz/Desktop/Ucuts`, commit `15469eeb1c327650d46731815486725085dd733d`, MIT. See `ucuts-audit.md` |
| Mr-Agents | `/Users/ahmedxriyaz/.gemini/antigravity/scratch/Mr-Agents`, commit `b6e7abbf49103ba924a40aa0338fd4df2590a976`; 3 pack files vendored to `skills/` |
| Router archive | `Mr-Agents-Assistant-Router.skill` not found locally; rules derived from spec (`skills/router/ROUTER_REFERENCE.md`) |
| Toolchain | Node 24.22.3, npm 11.19.1, pnpm 10.33.0, Python 3.13.7, Git 2.54.0, Docker 29.10.0, Compose 5.8.0, FFmpeg 8.1.1 |
| Existing code | None. New repository; no project instructions or package scripts to preserve |

## 2. Stack decisions

| Layer | Decision |
|---|---|
| Shape | Modular monolith + isolated background workers |
| Web | Next.js, TypeScript, Tailwind, shadcn/ui |
| API | FastAPI, Pydantic, `/api/v1`, idempotency keys |
| Database | PostgreSQL, UUID PKs, row-level security + app authorization |
| Cache | Redis |
| Storage | S3-compatible interface; MinIO locally; cloud adapter later |
| Workflows | Temporal (self-hosted dev server locally) |
| AI | Typed internal orchestrator; Meta Muse adapter at `https://api.meta.ai/v1`; mock adapter for tests |
| Video | Provider-independent gateway; mock first; Runway Dev / Gemini Omni Flash / Kling candidates; never the shut-down Veo 3.1 Preview IDs |
| Editing | Ucuts adapter (allowlisted ops) + FFmpeg in isolated, non-root, network-less workers |
| Meta | Unified Login; separate adapters for Muse, Graph/Instagram, Marketing API; secrets in secret manager, only refs in DB |
| Approvals | Mandatory human approval for publish/spend; version-bound |

## 3. Risk register

| Risk | Mitigation |
|---|---|
| Cross-tenant data leak | RLS + API authorization + dedicated isolation tests from Phase 1 |
| Secret exposure | Secret manager, log scrubbing, bundle/log secret-scan test |
| Meta App Review / personal IG accounts | Eligibility UX, capability matrix, Standard access for internal testing only |
| Video provider churn | Capability registry, provider interface, mock provider |
| Prompt injection (web, uploads, skills) | Untrusted-data framing, schema-validated outputs, no LLM shell commands |
| Render worker needs model download | Bake `face_detection_yunet_2023mar.onnx` into image |
| Archived upstream deps (stable-ts, demucs) | Pin exact versions |
| Skill license caveats | See `skills/ATTRIBUTION.md` |
| Duplicate publishing | Idempotency + remote-state verification before retry |
| Cost overruns | Budgets, pre-job estimates, kill switches |

## 4. Open decisions (spec Section 19)

| # | Decision | Recorded default | Status |
|---|---|---|---|
| 1 | Existing repo vs empty | Empty; new repo | Resolved |
| 2 | Cloud provider/region | Undecided; keep behind interfaces | Needs owner input |
| 3 | Temporal hosting | Self-hosted locally; Cloud vs self-hosted later | Default recorded - confirm |
| 4 | First video provider | Mock first; Runway Dev if account/terms pass | Needs account access |
| 5 | Meta app ownership / App Review | Unknown | Needs owner input |
| 6 | First Meta release scope | Instagram organic publishing only | Default recorded - confirm |
| 7 | Client review by | Agency staff and client users (roles configurable) | Default recorded - confirm |
| 8 | Media retention | Originals until deleted; previews 30 days (placeholder) | Default recorded - confirm |
| 9 | Creator pricing | Free/internal initially | Default recorded - confirm |

## 5. Proposed Phase 1 file-change plan

- `infrastructure/docker/docker-compose.yml` (Postgres, Redis, MinIO, Temporal, api, web)
- `database/migrations/` (Alembic): identity, tenancy, brand, audit; RLS policies; dev-only seeds in `database/seeds/`
- `packages/shared/`, `packages/auth/`, `packages/tenancy/`, `packages/storage/`
- `apps/api/` (FastAPI shell, health, workspaces/clients/brand endpoints)
- `apps/web/` (Next.js shell, sign-in, workspace onboarding for agency/creator)
- `.github/workflows/ci.yml` (lint, type, test, build)
- `tests/integration/test_tenancy_isolation.py`
- `docs/operations/local-dev.md`

No dependency is added until Phase 1 approval; each addition will be justified first.
