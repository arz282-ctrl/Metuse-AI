# Phase 6 Completion Report — Publishing & Insights

**Branch:** `feat/phase-6-publishing-and-insights`
**Migration:** `0007_phase5_meta_connections` -> `0008_phase6_publishing_and_insights`
**Monthly Cloud Spend:** `$0/month` (Supabase Free + Render Free + Cloudflare Pages Free + Sentry Free)

---

## 1. Delivered Capabilities

### 1.1 Database Schema & Tenant Isolation (`database/migrations/versions/0008_phase6_publishing_and_insights.py`, `database/models.py`)
- Additive, reversible Alembic migration applied to live Supabase PostgreSQL 17.12:
  - `content_calendars`: client-scoped planning calendars with timezone and composite `(workspace_id, client_id)` foreign key.
  - `content_items`: approval-gated content items bound to `(workspace_id, creative_version_id)` and `(workspace_id, connected_asset_id)` with `media_type`, `status`, `scheduled_for`, caption, carousel items, and approval snapshot metadata.
  - `publishing_jobs`: execution tracking table with partial unique idempotency index `uq_publishing_jobs_ws_idempotency` on `(workspace_id, idempotency_key) WHERE idempotency_key IS NOT NULL`, `container_id`, `container_status`, `remote_state_checked_at`, and `attempt_count`.
  - `publishing_results`: immutable publish receipts with unique `(workspace_id, connected_asset_id, remote_media_id)` constraint (`uq_publishing_results_remote_media`), permalink, and raw response snapshot.
  - `insights_snapshots`: account- and media-level analytics snapshots attributed to `(workspace_id, client_id, connected_asset_id)` with optional `(workspace_id, publishing_result_id)` link.
- Applied `updated_at` triggers, least-privilege `metuse_app` CRUD grants, and `ENABLE` + `FORCE ROW LEVEL SECURITY` policies across all 5 tables.

### 1.2 Shared Contracts, Config & RBAC
- `packages/shared/enums.py`: added `ContentItemStatus`, `PublishMediaType`, `PublishingJobStatus`, Phase 6 `AuditAction` entries (`CONTENT_ITEM_CREATED`, `CONTENT_ITEM_SCHEDULED`, `PUBLISHING_JOB_CREATED`, `PUBLISHING_JOB_PUBLISHED`, `PUBLISHING_JOB_FAILED`, `INSIGHTS_SYNCED`), and `ErrorCode` values (`CONTENT_APPROVAL_REQUIRED`, `PUBLISHING_LIMIT_EXCEEDED`, `CONTAINER_EXPIRED`).
- `packages/shared/config.py`: added `publishing_kill_switch: bool = False` (exposed in `safe_summary()`).
- `packages/auth/rbac.py`: added `publishing:*`, `publishing:read`, `publishing:write`, `analytics:*`, `analytics:read`, and `analytics:sync` permissions.

### 1.3 Publishing Engine, Duplicate Prevention & Insights Sync (`packages/meta_integrations/publishing.py`, `workers/meta_publishing/`, `workers/analytics/`)
- **Mandatory Approval & QA Gate:** Refuses scheduling and publishing unless the linked `CreativeVersion` has `is_approved = True` and `qa_passed = True` (`403 CONTENT_APPROVAL_REQUIRED`).
- **Backend Capability & Safety Enforcement:** Verifies `instagram_publish` / `instagram_insights` Meta capability flags, carousel item ceiling (max 20 items), dynamic 24-hour publishing rate limit check, and `PUBLISHING_KILL_SWITCH`.
- **Idempotency & Ambiguous Timeout Duplicate Prevention (Section 8.5 / Acceptance `#15`):**
  - Deduplicates retries via `Idempotency-Key` (rejecting mismatched payloads with `409 Conflict`).
  - Executes Instagram container creation -> container readiness polling -> publish, and on an ambiguous publish timeout **checks remote Meta media state before ever retrying**, resolving the existing remote media ID instead of creating a duplicate post.
  - Marks expired containers cleanly (`CONTAINER_EXPIRED`).
- **Workers:**
  - `workers/meta_publishing/__init__.py`: `execute_due_scheduled_publishes` runs due scheduled content items safely under workspace RLS and records remote media IDs (`Acceptance #14`).
  - `workers/analytics/__init__.py`: `sync_workspace_insights` syncs account- and media-level insights into `insights_snapshots` with client attribution.

### 1.4 FastAPI Endpoints & Next.js `/publishing` UI
- `apps/api/routes/publishing.py` (wired in `apps/api/deps.py` and `apps/api/main.py`):
  - `GET/POST /api/v1/calendars`
  - `GET/POST /api/v1/content`
  - `POST /api/v1/content/{content_item_id}/schedule`
  - `POST /api/v1/content/{content_item_id}/publish` (supports `Idempotency-Key`)
  - `GET /api/v1/publishing-jobs/{job_id}`
  - `GET /api/v1/clients/{client_id}/analytics` & `POST /api/v1/clients/{client_id}/analytics/sync`
  - `GET /api/v1/analytics` & `POST /api/v1/analytics/sync`
- `apps/web/src/lib/publishing-api.ts`, `apps/web/src/app/(app)/publishing/page.tsx`, and `apps/web/src/components/layout/app-shell.tsx`:
  - Full Publishing & Insights workspace UI: content calendars, approval-gated content item creation, scheduling, immediate idempotent publish, job status inspector, and client/workspace insights overview & sync controls.

---

## 2. Verification Results

| Check | Result |
|---|---|
| `ruff format --check .` | Passed (144 files formatted) |
| `ruff check .` | Passed (0 errors) |
| `mypy apps packages database workers tests` | Passed (116 source files, 0 issues) |
| `pytest tests/unit -q` | Passed (`57 passed in 1.60s`) |
| `alembic heads` | `0008_phase6_publishing_and_insights (head)` |
| Live Supabase `alembic upgrade head` | Applied cleanly (`0007_phase5_meta_connections` -> `0008_phase6_publishing_and_insights`) |
| Live Supabase `pytest tests/integration/test_phase6_publishing_rls.py -v` | Passed (`2 passed in 28.73s`): 5-table `FORCE RLS`, unapproved publish blocked (`403`), Acceptance `#14` scheduled worker publish + remote ID stored, Acceptance `#15` ambiguous timeout remote-state verification (strictly 1 publish call, zero duplicates), client-attributed insights sync, and cross-tenant 0-row `FORCE RLS` isolation |
| `pnpm --dir apps/web lint` | Passed (0 errors) |
| `pnpm --dir apps/web typecheck` | Passed (0 errors) |
| `pnpm --dir apps/web build` | Passed (`15/15` static routes exported including `/publishing`) |
