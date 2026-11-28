# Production Launch Runbook — Meta App, Google, Review, Pixels, Flags

This is the human-operated companion to the code, which is already live-ready
(mock-first with fail-fast boot validation). Every step below requires the
owner's own Meta/Google accounts, business identity, or dashboard access —
it cannot be delegated to an agent.

**Canonical URLs** (replace `API`/`WEB` if your domains change):

| Slot | Preview | Production |
|---|---|---|
| `WEB` | `https://develop.metuse-ai-web.pages.dev` | `https://metuse-ai-web.pages.dev` |
| `API` | `https://metuse-ai-api-preview.onrender.com` | your production API origin |

---

## Step 1 — Meta App (30 min, you)

1. Go to [Meta for Developers](https://developers.facebook.com/apps/) → **Create App**.
2. Use case: **Authenticate and request data from users with Facebook Login**.
3. Add products: **Facebook Login**, **Webhooks**.
4. Settings → Basic:
   - App Domains: `develop.metuse-ai-web.pages.dev`, `metuse-ai-web.pages.dev`
   - Privacy Policy URL: `https://<API>/legal/privacy-policy`
   - Data Deletion URL: `https://<API>/api/v1/auth/meta/data-deletion`
   - Category: Business. Add icon + contact email.
5. Facebook Login → Settings → **Valid OAuth Redirect URIs** (all six, exact):
   - `https://develop.metuse-ai-web.pages.dev/auth/meta/callback`
   - `https://metuse-ai-web.pages.dev/auth/meta/callback`
   - `https://develop.metuse-ai-web.pages.dev/connections/callback`
   - `https://metuse-ai-web.pages.dev/connections/callback`
   - (`http://localhost:3000/...` variants stay dev-only; never add to prod app)
6. Copy **App ID** + **App Secret** into Render env (Step 6). Never commit them.

## Step 2 — Google OAuth client (15 min, you)

1. [Google Cloud Console](https://console.cloud.google.com/) → new project (or reuse) →
   **APIs & Services → OAuth consent screen**: External, app name `Metuse AI`,
   support email, scopes `openid email profile`, test users skipped (publish when ready).
2. **Credentials → Create OAuth client ID** (Web application). Authorized redirect URIs:
   - `https://develop.metuse-ai-web.pages.dev/auth/google/callback`
   - `https://metuse-ai-web.pages.dev/auth/google/callback`
3. Copy **Client ID** + **Client Secret** into Render env (Step 6).

## Step 3 — Business verification (1–5 days, you + Meta)

1. Meta App Dashboard → **App Review → Business verification** (requires a Business
   Portfolio / Business Manager you control).
2. Submit legal entity documents (registration/license + utility bill or bank statement).
3. Ads APIs (`ads_read`, `ads_management`) and `business_management` require completed
   verification before Advanced Access is granted. Track status in the dashboard.

## Step 4 — App Review permissions (you + Meta, plan screencasts)

Request in this order (each needs a written use case + click-through screencast):

| # | Permission | Powers | Screencast to record |
|---|---|---|---|
| 1 | `email`, `public_profile` | Meta signup/login | Login page → Continue with Meta → consent → dashboard |
| 2 | `pages_show_list` | Page + IG discovery | Connections → Creator Pro → consent → assets listed |
| 3 | `instagram_basic`, `instagram_content_publish`, `instagram_manage_insights` | Organic read/publish/insights | Studio → approve → Publishing → post appears on IG |
| 4 | `instagram_manage_comments`, `instagram_manage_messages` (optional) | Moderation | Connections capability badges |
| 5 | `ads_read` | Ad discovery + reporting | Connections (ads checkbox) → Ads → Sync → hierarchy |
| 6 | `ads_management`, `business_management` | Campaign execution | Ads → prompt draft → validate → approve → execute (PAUSED) |

Tips: record against **preview** with a test Page/IG Creator/ad account; keep each video
under 2 minutes; narrate why each permission is needed; submit 1–3 first, then 5–6 after
business verification lands.

## Step 5 — Pixels + Redis (30 min, you)

1. [Events Manager](https://business.facebook.com/events_manager2/) → Data Sources →
   **Create dataset** (Web) per ad account. Copy each **Dataset ID**.
2. In Metuse Ads page: save the Dataset ID on the matching ad account (pixel panel).
3. Events Manager → Test Events → copy a **test event code**; verify with the CAPI panel.
4. [Upstash](https://upstash.com/) → Create Redis database (free tier) → copy the
   `rediss://` URL into Render `REDIS_URL` (Step 6).

## Step 6 — Render env vars (10 min, you)

Dashboard → `metuse-ai-api-preview` (and prod service) → Environment. Paste values for
every `sync: false` key in `render.yaml`:

| Key | Value |
|---|---|
| `DATABASE_URL` | Supabase owner/pooler connection string (boot migrations) |
| `APP_DATABASE_URL` | `metuse_app` role connection string (runtime) |
| `REDIS_URL` | Upstash `rediss://…` URL |
| `META_APP_ID` / `META_APP_SECRET` | From Step 1 |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | From Step 2 |
| `SUPABASE_URL` / `SUPABASE_JWKS_URL` / `SUPABASE_PUBLISHABLE_KEY` / `SUPABASE_SECRET_KEY` | Supabase project settings |
| `SENTRY_DSN` | Sentry project DSN (optional) |
| `APP_CORS_ORIGINS` | Add your production `WEB` origin if it changes |
| `META_ALLOWED_REDIRECT_URIS` | `https://<WEB>/connections/callback` (prod + preview, comma-separated) |

`APP_JWT_SECRET` and `META_SECRET_ENCRYPTION_KEY` are `generateValue` (auto-created,
stable afterwards). **Never rotate the vault key** without re-encrypting `secret_vault`
— old tokens become unreadable.

## Step 7 — Flip the live flags (5 min, you, in this order)

Flip one, redeploy, verify boot logs, then the next:

1. `GOOGLE_AUTH_MODE=live` → test Google signup on preview.
2. `META_AUTH_MODE=live` → test Meta signup on preview.
3. `META_CONNECTIONS_MODE=live` → test Connections → Ads → Publishing on preview.

The API fails fast at boot with an explicit message if any live mode lacks credentials,
so a bad flip surfaces in deploy logs, not as silent breakage. Keep `*_MODE=mock_*`
locally — live flags belong only in deployed environments.

## Step 8 — Verify (15 min, you)

- `GET https://<API>/healthz` → `ok`; `GET /readyz` → database `ok`, session_store `ok`.
- Register via Meta + Google; new Creator workspace auto-created.
- Connections → Agency Business + ads checkbox → Sync → Ads hierarchy visible.
- Ads → prompt draft → validate → request approval → approve → execute → campaign PAUSED
  in Ads Manager (never Active on creation).
- CAPI panel → test event → appears in Events Manager Test Events.
- Publishing → schedule → approve → post appears on the test IG account.

## Rollback

- Flags: set any `*_MODE` back to `mock`/`mock_dev` and redeploy (instant, zero data loss).
- Bad migration: entrypoint aborts boot before serving traffic; fix forward with a new
  migration (never edit a landed migration).
- Vault key rotation is NOT supported yet — treat `META_SECRET_ENCRYPTION_KEY` as immutable.

## Known single-instance limits (free tier)

- Boot migrations assume one API instance (Render free). Before scaling past one,
  move migrations to a one-shot pre-deploy job.
- The auth rate limiter is per-process; Redis-backed limiting is a future upgrade.
