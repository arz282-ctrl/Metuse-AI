# Phase 7 Completion & Full 18-Test Acceptance Report

**Branch:** `feat/phase-7-ads-and-acceptance`
**Migration:** `0008_phase6_publishing_and_insights` -> `0009_phase7_ads_and_controlled_execution`
**Monthly Cloud Spend:** `$0/month` (Supabase Free + Render Free + Cloudflare Pages Free + Sentry Free)

---

## 1. Phase 7 Delivered Capabilities — Ads Read & Controlled Execution

### 1.1 Database Schema & Tenant Isolation (`database/migrations/versions/0009_phase7_ads_and_controlled_execution.py`, `database/models.py`)
- Additive, reversible Alembic migration applied to live Supabase PostgreSQL 17.12:
  - `ad_accounts`: permission-checked ad account inventory bound to `(workspace_id, client_id)` and `(workspace_id, meta_connection_id)` with spend cap and amount spent tracking.
  - `ad_campaign_snapshots`, `ad_set_snapshots`, `ad_snapshots`: read-only campaign -> ad-set -> ad hierarchy and performance snapshots (`spend_cents`, `impressions`, `clicks`, `conversions`, `targeting_json`, `creative_summary_json`).
  - `ad_campaign_drafts`: version- and SHA-256 content-hash-bound campaign drafts with `validation_passed`, `validation_report`, `approval_id`, `remote_campaign_id`, and partial unique idempotency index `uq_ad_drafts_ws_idempotency`.
- Applied `updated_at` triggers, least-privilege `metuse_app` CRUD grants, and `ENABLE` + `FORCE ROW LEVEL SECURITY` across all 5 tables.

### 1.2 Shared Contracts, Config & RBAC
- `packages/shared/enums.py`: added `AdDraftStatus`, `AdObjective`, Phase 7 `AuditAction` entries (`ads.accounts_synced`, `ads.insights_synced`, `ads.draft_created`, `ads.draft_updated`, `ads.draft_validated`, `ads.draft_approval_requested`, `ads.campaign_executed`, `ads.budget_approval_requested`, `ads.budget_changed`, `ads.mutation_failed`), and `ErrorCode` values (`ADS_APPROVAL_REQUIRED`, `ADS_APPROVAL_STALE`, `ADS_DRAFT_VALIDATION_FAILED`, `ADS_KILL_SWITCH_ACTIVE`, `ADS_BUDGET_CONFIRMATION_REQUIRED`, `ADS_SPEND_CAP_EXCEEDED`).
- `packages/shared/config.py`: added `ads_kill_switch: bool = False` and `ads_max_daily_budget_cents: int = 500_000` (both exposed in `safe_summary()`).
- `packages/auth/rbac.py`: added `ads:*` for owner/admin roles, `ads:read` + `ads:write` (no `ads:execute`) for `agency_member`, and `ads:read` for `client_reviewer`/`client_viewer` to preserve separation of duties.

### 1.3 Ads Engine, Workers, FastAPI Routes & `/ads` Console
- `packages/meta_integrations/ads.py` & `workers/ads_ops/__init__.py`:
  - Strictly separated from organic publishing.
  - Read-only discovery enforces backend `meta_ads_read` capability and only imports explicitly selected `AD_ACCOUNT` assets.
  - Draft edits bump `version_number`, recompute SHA-256 `content_hash`, reset validation, and automatically invalidate any pending/approved `campaign_execution` approval.
  - Campaign execution and budget mutations enforce `ADS_KILL_SWITCH`, backend `meta_ads_manage` capability, explicit version/hash-bound human approval (`campaign_execution` / `budget_change`), explicit human confirmation flag on budget changes (never auto-increases spend), remote-state re-read before retrying ambiguous timeouts, and append-only `AuditLog` entries with exact `before`/`after` values and actor attribution.
- `apps/api/routes/ads.py` (wired in `apps/api/deps.py` and `apps/api/main.py`):
  - `GET /ads/accounts`, `POST /ads/sync`, `GET /ads/campaigns`
  - `GET/POST /ads/drafts`, `PATCH /ads/drafts/{draft_id}`, `POST /ads/drafts/{draft_id}/validate`
  - `POST /ads/drafts/{draft_id}/request-approval`, `POST /ads/drafts/{draft_id}/execute` (`Idempotency-Key`)
  - `POST /ads/campaigns/{campaign_id}/budget/request-approval`, `POST /ads/campaigns/{campaign_id}/budget/apply`
- `apps/web/src/lib/ads-api.ts`, `apps/web/src/app/(app)/ads/page.tsx`, and `apps/web/src/components/layout/app-shell.tsx` (`Ads Ops` `/ads`, `Agency OS · Phase 7`).

---

## 2. Full 18-Test End-to-End Acceptance Verification Matrix (Section 16)

All **63 unit tests** and all **24 live Supabase PostgreSQL 17.12 `FORCE RLS` integration tests** passed cleanly in a single verification run (`pytest tests/unit -q` -> `63 passed`; `pytest tests/integration -v` -> `24 passed in 238.08s`):

| # | Acceptance Criterion | Verification Suite | Status |
|---|---|---|---|
| **1** | Agency isolation (Agency A cannot read Agency B's records) | `test_m1_db_rls.py`, `test_tenancy_services.py`, `test_phase2..7_*_rls.py` (all 7 phases) | **PASSED** |
| **2** | Creator onboarding works without an ad account | `test_tenancy_services.py::test_creator_onboarding_works_without_ad_account_and_caps_at_one` | **PASSED** |
| **3** | Personal Instagram account blocked with conversion guidance | `test_phase5_meta_rls.py` + `test_phase5_meta_connections.py` | **PASSED** |
| **4** | Professional Creator connects & exposes only granted capabilities | `test_phase5_meta_rls.py` | **PASSED** |
| **5** | Agency Meta connection exposes only selected assets | `test_phase5_meta_rls.py` | **PASSED** |
| **6** | Permission denial disables backend capability with reconnect guidance | `test_phase5_meta_rls.py` + `test_phase5_meta_connections.py` | **PASSED** |
| **7** | Research workflow returns evidence-backed report with URLs & confidence | `test_phase2_rls_and_jobs.py` + `test_phase2_workers_and_muse.py` | **PASSED** |
| **8** | SEO/AEO workflow returns structured content, metadata & answer passages | `test_phase2_rls_and_jobs.py` + `test_phase2_workers_and_muse.py` | **PASSED** |
| **9** | Text-to-video produces owned asset through unified gateway | `test_phase3_video_rls.py` + `test_phase3_video_gateway.py` | **PASSED** |
| **10** | Image-to-video produces owned asset through unified gateway | `test_phase3_video_rls.py` + `test_phase3_video_gateway.py` | **PASSED** |
| **11** | Cloud editing: upload -> preview -> revision -> final MP4 | `test_phase4_editing_rls.py` + `test_phase4_cloud_editing.py` | **PASSED** |
| **12** | Media QA rejects broken/corrupt/wrong-aspect-ratio output | `test_phase4_editing_rls.py` + `test_phase4_cloud_editing.py` | **PASSED** |
| **13** | Approval binding invalidates approval when creative/draft changes | `test_phase4_editing_rls.py` + `test_phase7_ads_rls.py` | **PASSED** |
| **14** | Approved content publishes and remote ID is stored | `test_phase6_publishing_rls.py` | **PASSED** |
| **15** | Ambiguous publish timeout does not create duplicate post on retry | `test_phase6_publishing_rls.py` | **PASSED** |
| **16** | Ads safety: zero campaign/budget mutations without explicit approval + confirmation + audit | `test_phase7_ads_rls.py` + `test_phase7_ads.py` | **PASSED** |
| **17** | Cost attribution across model, generation, and render jobs | `test_phase2_rls_and_jobs.py`, `test_phase3_video_rls.py`, `test_phase4_editing_rls.py` | **PASSED** |
| **18** | Automated secret scan across browser bundles (`apps/web/out/`), frontend source, settings & scrubbers | `test_acceptance_18_credential_scan.py` + `test_phase5_meta_rls.py` | **PASSED** |

---

## 3. Build, Lint & Quality Gate Summary

- `ruff format --check .`: **152 files formatted**
- `ruff check .`: **0 errors**
- `mypy apps packages database workers tests`: **123 source files, 0 issues**
- `alembic heads`: `0009_phase7_ads_and_controlled_execution (head)`
- `pnpm --dir apps/web lint` & `typecheck`: **0 errors**
- `NEXT_PUBLIC_API_URL=https://metuse-ai-api-preview.onrender.com pnpm --dir apps/web build`: **16/16 static routes exported** (including `/ads`)
