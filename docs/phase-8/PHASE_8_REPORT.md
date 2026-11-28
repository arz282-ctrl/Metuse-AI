# Phase 8 Report — Social Auth (Meta/Google), Prompt Ads & Conversions API

**Branch:** main (direct)
**Migration:** `0009_phase7_ads_and_controlled_execution` -> `0010_phase8_social_auth_and_capi`
**Monthly Cloud Spend:** `$0/month` (unchanged; all new paths are mock-first)

---

## 1. Slice A — Meta/Google Signup & Login

### 1.1 Backend (`packages/auth/`, `apps/api/routes/auth.py`)
- Wired the previously dead `meta_oauth` module to real routes: `GET /api/v1/auth/meta/start`,
  `POST /api/v1/auth/meta/callback`. Meta identity endpoints bumped `v21.0` -> `v22.0` to match
  the Unified Login client.
- New `packages/auth/google_oauth.py` mirroring Meta guarantees: signed single-use state,
  allowlisted redirect URIs, mock mode blocked outside dev/test, live exchange requires a
  verified Google email. Routes: `GET /api/v1/auth/google/start`, `POST /api/v1/auth/google/callback`.
- Callback flow finds-or-creates the user: existing `(provider, subject)` signs in, matching
  verified email links a new identity (conflicting second identity rejected), otherwise a new
  password-less user is provisioned. New users get a Creator workspace auto-provisioned
  (with default client + brand profile), and the response carries `is_new_user` + `workspace_id`.
- Account deletion: `DELETE /api/v1/auth/account` (self-serve) and Meta's Data Deletion
  Request protocol `POST /api/v1/auth/meta/data-deletion` (HMAC-signed `signed_request`
  verification, PII scrub + deactivation, JWT confirmation code) with
  `GET /api/v1/auth/meta/data-deletion/status`.
- New public legal page `GET /legal/privacy-policy` (Meta App Review requirement).

### 1.2 Database (`0010_phase8_social_auth_and_capi`)
- `ck_user_identities_provider` extended with `'google'` (reversible).
- `ad_accounts.pixel_dataset_id` nullable column for Conversions API binding (reversible).

### 1.3 Web (`apps/web`)
- `Continue with Meta` / `Continue with Google` buttons on login + register (shared component).
- `/auth/meta/callback` and `/auth/google/callback` pages exchange code server-side, then route
  new users to `/connections` (grant business assets) and returning users to `/dashboard`.

## 2. Slice B — Prompt Ads, Asset Picker, Live Clients, CAPI

### 2.1 Asset picker
- Live discovery now imports ad accounts (`me/adaccounts`) when ads scopes are granted.
- `PATCH /meta/connections/{id}/assets` updates selection, recomputes connection ids +
  capabilities, and audits before/after (`meta.assets_updated`, `meta:write` only).
- `GET .../assets?include_deselected=true` powers the Connections page checkbox picker.

### 2.2 Live Marketing API client
- New `LiveMetaAdsClient` implementing the `MetaAdsClient` protocol: account/hierarchy reads,
  PAUSED-first campaign + ad-set creation, `[metuse:short]` name-marker idempotency for
  ambiguous-timeout recovery, CBO/ad-set-aware budget updates. `create_meta_ads_client()`
  selects mock (default) vs live from `meta_connections_mode`; `deps.py` no longer hardcodes mock.

### 2.3 Single-prompt campaign builder
- New schema `metuse.ad_campaign_plan.v1` (`AdCampaignPlanArtifact`) + mock Muse payload branch.
- `POST /ads/prompt-drafts`: validates the prompt, enforces the selected Page belongs to the ad
  account's connection, clamps AI budget to safety ceiling + spend cap, auto-attaches ready image
  assets when no creative refs are supplied, then runs the standard create + validate flow.
  Execution still requires explicit human approval. New Ads page card with account + Page selectors.

### 2.4 Conversions API
- New `packages/meta_integrations/capi.py`: SHA-256 email/phone hashing, `user_data` builder,
  mock + live (`POST /{pixel}/events`, `test_event_code` support) clients, tenant-scoped
  `CapiService` requiring `meta_ads_manage` and a bound pixel, PII-free audit (`ads.capi_event_sent`).
- Routes: `PATCH /ads/accounts/{id}` (bind pixel, `ads:write`), `POST /ads/capi/events`
  (test events `ads:write`, live events `ads:execute`). Ads page gained pixel + event panels.

## 3. Verification
- `ruff check` / `ruff format --check`: clean (118 -> 121 files).
- `mypy apps packages database tests` (strict): 0 issues.
- Unit: `84 passed` (63 existing + 21 new in `test_phase8_social_auth_and_capi.py`).
- Integration: 4 new tests in `test_phase8_social_auth_and_capi.py` (run in CI on PostgreSQL 16).
- Web: `pnpm lint`, `pnpm typecheck`, static export build — 18/18 routes (2 new callbacks).

## 4. Phase 8b — Production prerequisites (code-complete)

- **Persistent vault:** new `DbSecretStore` (AES-256-GCM envelopes in workspace-isolated
  `secret_vault`, migration `0011`, FORCE RLS). `SECRET_STORE_BACKEND=db` outside dev/test;
  in-memory store and missing vault keys fail fast at boot.
- **Deploy migrations:** `api-entrypoint.sh` runs `alembic upgrade head` (owner `DATABASE_URL`)
  before serving; `render.yaml` declares `DATABASE_URL`, Upstash `REDIS_URL`, vault key,
  and Meta/Google credential slots.
- **Redis sessions:** missing `REDIS_URL` outside dev/test fails fast at boot.
- **Live validation:** boot rejects live modes without credentials and mistyped modes.
- **Live OAuth completion:** new `/connections/callback` page decodes the workspace from state
  and completes Unified Login; connections start passes the explicit callback URI (must be
  allowlisted + registered in the Meta dashboard).
- **`pages_show_list`** added to requested scopes + permission matrix (live Page/IG discovery
  requires it).
- Owner runbook: `docs/operations/production-launch.md` (Meta App, Google client, business
  verification, App Review order + screencasts, pixels, Upstash, Render vars, flag order,
  verification, rollback).

## 5. Owner actions required to go live
1. Create the Meta App (Facebook Login product, exact redirect URIs, Live mode).
2. Create Google OAuth credentials (exact redirect URI).
3. Business verification + App Review for each permission tier.
4. Create pixel/dataset per ad account; bind via the Ads page.
5. Set `META_AUTH_MODE=live`, `GOOGLE_AUTH_MODE=live`, `META_CONNECTIONS_MODE=live` in production.
6. Production prerequisites (unchanged from audit): persistent secret store, migrations on deploy,
   Redis-backed sessions before public launch.
