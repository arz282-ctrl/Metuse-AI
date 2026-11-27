# Phase 4 Completion Report — Cloud Editing & Rendering Environment

**Branch:** `feat/phase-4-cloud-editing-and-approvals` (targeting `main` via PR #4)  
**Spec Reference:** `implementation (1).md` (Sections 7, 9, 10, 11, 14, 15, 16, 17) & `docs/architecture/ucuts-audit.md`  
**Cloud Budget Impact:** `$0/month` (Cloud render workers default to deterministic `RENDER_MODE=mock` on Render Free Preview; Supabase Free + Render Free + Cloudflare Pages Free)

---

## 1. Delivered Capabilities

### 1.1 Database Schema & Tenant Isolation (`database/migrations/versions/0006_phase4_cloud_editing_and_approvals.py`)
- Additive, cleanly reversible Alembic migration (`0005_phase3_video_generation_gateway -> 0006_phase4_cloud_editing_and_approvals`) applied to live Supabase PostgreSQL 17.12:
  - 6 tenant-owned tables with composite tenant FKs, `updated_at` triggers, least-privilege `metuse_app` CRUD grants, and `ENABLE` + `FORCE ROW LEVEL SECURITY`:
    - `media_metadata` (verified probe metadata + lightweight proxy asset link)
    - `editing_plans` (versioned Section 7.3 JSONB timeline plans + SHA-256 hash)
    - `render_jobs` (`preview` / `final`, idempotency partial unique index, command manifest, QA verdict/report, CPU/cost tracking)
    - `creative_versions` (immutable rendered output artifacts bound to plan, job, asset, QA status, and approval flag)
    - `approvals` (Section 9 version-bound human approvals storing `target_version_sha256` and invalidation metadata)
    - `revision_requests` (timecode-aware creative revision notes linked to creative versions and approvals)
  - Linked `usage_records.render_job_id` to `render_jobs` for unified Acceptance `#17` cost attribution.

### 1.2 Pinned Ucuts & Allowlisted FFmpeg Adapter (`packages/ucuts_adapter/`)
- `pin.py`: loads and verifies `UCUTS_PIN.json` (commit `15469eeb1c327650d46731815486725085dd733d`, `scripts/ucuts_pipeline.py`).
- `translator.py`:
  - `build_ucuts_cli_argv`: strictly uses verified `--sections` flag (never the erroneous `--reels` flag), rejects remote URLs, `--cookies-from-browser`, and shell metacharacters.
  - `build_ffmpeg_render_argv`: translates Section 7.3 `TimelinePlan` into an explicit allowlisted `list[str]` argv array (`libx264`, `yuv420p`, `aac 192k`, `loudnorm -14 LUFS`, `+faststart`, zero `shell=True`).

### 1.3 Cloud Media Engine, Automated QA & Version-Bound Approvals (`packages/media_engine/`, `workers/media_editing/`)
- **Upload Validation & Media Probe (`probe.py`):** Magic-byte + container verification (`probe_media_bytes`), preserving original uploads untouched while generating separate lightweight proxy assets.
- **Automated Section 7.5 Media QA Gate (`qa.py`):** Validates decodability/playability, duration bounds (`±500ms`), exact canvas dimensions & aspect ratio, audio presence & non-clipping, black/frozen frame ratios, caption timing & safe margins, CTA end placement, and referenced asset integrity — rejecting corrupt or wrong-aspect-ratio renders with `qa_failed` / `MEDIA_QA_FAILED` (Acceptance `#12`).
- **Render Backends (`renderer.py`):** `$0` deterministic `MockRenderBackend` (default) and `IsolatedFfmpegRenderBackend` (`shell=False`, isolated temp dir, minimal env, timeout, and output size cap).
- **Cloud Editing & Approvals Service (`service.py`):** Full presigned upload URL + complete upload + project analyze + versioned timeline plan + idempotent preview/final render + pre-flight workspace/client budget & kill-switch enforcement + version-bound approvals that **automatically invalidate** when an approved project's editing plan is modified (Acceptance `#11`, `#13`, `#17`).

### 1.4 FastAPI Routes & Next.js Cloud Studio (`apps/api/`, `apps/web/`)
- **Editing API (`apps/api/routes/editing.py`):**
  - `POST /api/v1/workspaces/{workspace_id}/assets/upload-url`
  - `POST /api/v1/workspaces/{workspace_id}/assets/complete-upload`
  - `GET /api/v1/workspaces/{workspace_id}/projects/{project_id}`
  - `POST /api/v1/workspaces/{workspace_id}/projects/{project_id}/analyze`
  - `POST/GET /api/v1/workspaces/{workspace_id}/projects/{project_id}/editing-plan(s)`
  - `POST /api/v1/workspaces/{workspace_id}/projects/{project_id}/render-preview` (`Idempotency-Key`)
  - `POST /api/v1/workspaces/{workspace_id}/projects/{project_id}/render-final` (`Idempotency-Key`)
  - `GET /api/v1/workspaces/{workspace_id}/render-jobs/{job_id}`
  - `GET /api/v1/workspaces/{workspace_id}/projects/{project_id}/versions`
- **Approvals API (`apps/api/routes/approvals.py`):**
  - `GET/POST /api/v1/workspaces/{workspace_id}/approvals`
  - `POST /api/v1/workspaces/{workspace_id}/approvals/{approval_id}/approve`
  - `POST /api/v1/workspaces/{workspace_id}/approvals/{approval_id}/reject`
  - `POST /api/v1/workspaces/{workspace_id}/approvals/{approval_id}/request-revision`
- **RBAC (`packages/auth/rbac.py`):** Added `editing:*` and `approvals:*` / `approvals:decide` across canonical workspace and reviewer roles.
- **Web UI (`apps/web/src/app/(app)/studio/page.tsx`):** Added static-export `/studio` ("Cloud Studio") page for project timeline setup, preview/final rendering, QA status inspection, revision requests, and hash-bound approvals.

---

## 2. Verification Summary

| Check | Command | Result |
|---|---|---|
| Python formatting | `ruff format --check .` | Passed (135 files) |
| Python linting | `ruff check .` | Passed (0 errors) |
| Python static typing | `mypy apps packages database workers tests` | Passed (99 source files, 0 issues) |
| Unit tests (Phase 0–4) | `pytest tests/unit -q` | Passed (`52 passed in 1.55s`) |
| Alembic migration head | `alembic heads` | `0006_phase4_cloud_editing_and_approvals (head)` |
| Live Supabase migration | `alembic upgrade head` | Applied `0005 -> 0006` cleanly |
| Live Supabase Phase 4 `FORCE RLS` & E2E tests | `pytest tests/integration/test_phase4_editing_rls.py -v` | Passed (`2 passed in 25.71s`) |
| Frontend lint & typecheck | `pnpm --dir apps/web lint && pnpm --dir apps/web typecheck` | Passed (0 errors) |
| Frontend static export | `NEXT_PUBLIC_API_URL=https://metuse-ai-api-preview.onrender.com pnpm --dir apps/web build` | Passed (`13/13` static pages including `/studio`) |
| Secret hygiene scan | `grep` across Phase 4 files | Clean (`0` leaks) |
| Acceptance criteria covered | `#11` (Cloud editing), `#12` (Media QA), `#13` (Approval binding), `#17` (Cost attribution) | Verified in unit + live Supabase integration suites |

---

## 3. Deployment & Next Phase

- **Cloudflare Pages Preview:** Deployed updated `apps/web/out` (including `/studio`) to `https://develop.metuse-ai-web.pages.dev/studio`.
- **Render Preview API:** Automatically deploys Phase 4 editing & approval endpoints once PR #4 is merged into `main`.
- **Recommended Next Phase:** **Phase 5 — Meta Account Connection** (Unified Login start/callback, server-side code exchange, secret-reference token storage, Creator vs Agency eligibility & personal IG blocking with conversion guidance, capability matrix, and signed Meta webhooks).
