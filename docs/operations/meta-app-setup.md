# Meta App Setup, Unified Login & App Review Guide

This document defines the required Meta App configuration, permission matrix, and App Review steps for Metuse AI (`IMPLEMENTATION.md` Sections 2 & 8).

---

## 1. Strict Product Separation

Never share credentials across separate Meta products:
1. **Meta Model API / Muse (`MUSE_API_KEY`):** Reasoning, image generation/editing, transcription, segmentation (`packages/muse_adapter/`).
2. **Meta Graph / Instagram Platform (`META_APP_ID`, `META_APP_SECRET`, `META_CONFIG_ID`):** Unified Login, Instagram Professional publishing, insights, comments, messages (`packages/meta_integrations/`).
3. **Meta Marketing API (Phase 7):** Read-only ads discovery & approved campaign execution.

---

## 2. Meta App Prerequisites

1. **Business Portfolio Ownership:** The Meta App must be owned by a verified Meta Business Portfolio.
2. **Instagram API with Unified Login (Facebook Login for Business):**
   - Create a **Facebook Login for Business Configuration** and record its `META_CONFIG_ID`.
   - Allowlist exact OAuth callback URLs in `META_REDIRECT_URI` / `META_ALLOWED_REDIRECT_URIS` (never wildcards).
3. **Webhooks Configuration:**
   - Callback URL: `https://<api-host>/api/v1/workspaces/{workspace_id}/meta/webhooks`
   - Verify Token: `META_WEBHOOK_VERIFY_TOKEN`
   - Signature verification uses `X-Hub-Signature-256` with `META_APP_SECRET` (constant-time HMAC-SHA256).
4. **Token Security:**
   - Exchange authorization codes strictly server-to-server.
   - Store tokens exclusively inside the server-side `SecretStore` (`META_SECRET_ENCRYPTION_KEY` / KMS). PostgreSQL `meta_connections.token_secret_ref` enforces `CHECK (token_secret_ref LIKE 'secret://%')` so raw tokens can never be persisted in DB rows.

---

## 3. Minimal Permission Matrix

| Capability | Permission | Creator Organic | Agency w/ Ads | Access Tier |
|---|---|---|---|---|
| Read IG profile/media | `instagram_basic` | Required | Required | Standard (internal testers) -> Advanced (App Review) |
| Publish Reels/posts | `instagram_content_publish` | Required | Required | Standard -> Advanced |
| Account & media insights | `instagram_manage_insights` | Required | Required | Standard -> Advanced |
| Comment moderation | `instagram_manage_comments` | Optional | Optional | Standard -> Advanced |
| Direct messages | `instagram_manage_messages` | Optional | Optional | Standard -> Advanced |
| Read ad accounts/insights | `ads_read` | Not required | Optional | Standard -> Advanced |
| Manage ads (approved only) | `ads_management` | Not required | Optional (Phase 7) | Standard -> Advanced |
| Business portfolio assets | `business_management` | Not required | Required | Standard -> Advanced |

---

## 4. App Review & Live Mode Checklist (External Clients)

Do **not** claim external-client support until all items below are completed:
- [ ] Standard Access tested with internal App Role testers only (`META_CONNECTIONS_MODE=live`).
- [ ] Business Verification completed inside Meta Business Settings.
- [ ] Screen recordings captured showing exact end-to-end UX for each requested permission (consent screen, asset selection, personal-account eligibility message, disconnect flow).
- [ ] Privacy Policy URL, Terms of Service URL, and Data Deletion Callback verified.
- [ ] Advanced Access approved for every requested permission and App switched to **Live** mode.
