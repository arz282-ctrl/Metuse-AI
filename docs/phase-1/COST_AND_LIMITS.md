# Cost Control & Free-Tier Limits — Metuse AI (Phase 1)

**Target Phase 1 Mandatory Recurring Expenditure:** **$0/month** (free-tier-first, zero paid cloud compute, zero paid Redis, zero AWS/GCP services).

---

## 1. Service Free-Tier Limits & Upgrade Conditions

| Service | Free Tier Quotas | Inactivity / Suspension Rules | Billing Activation Required? | Upgrade Trigger |
|---|---|---|---|---|
| **Supabase (Free Plan)** | 2 active projects; 500 MB PostgreSQL database; 1 GB file storage; 5 GB cached egress; 50,000 MAU (Supabase Auth); 500k Edge Function invocations/mo | Projects pause after **7 days of inactivity** on Free plan | No | Upgrade to Pro (\$25/mo) before production launch (eliminates 7-day pause, adds daily backups & 8 GB DB) |
| **Cloudflare Pages / Workers (Free)** | Unlimited static requests on Pages; 500 builds/month; 100,000 Workers requests/day (10 ms CPU/req) | No inactivity pause | No (optional card only for paid Workers/R2) | Upgrade to Workers Paid (\$5/mo) if SSR CPU exceeds 10 ms/req or >100k req/day |
| **GitHub Actions (Free)** | 2,000 CI minutes/month (private repos) / unlimited (public repos); 500 MB artifact storage | Workflows disable after 60 days of repo inactivity | No | Optimize CI caching (`uv` cache) or upgrade Team if private CI minutes exceed 2,000/mo |
| **Sentry (Developer Free)** | 5,000 errors/month; 1 user | Drops events once monthly quota reached (no overage charges) | No | Upgrade to Team (\$26/mo) when multi-seat or higher event volume is needed |
| **FastAPI Hosting (Phase 1)** | Local dev (`uvicorn`) or free-tier container host (e.g. Render Free / Fly.io trial / Koyeb Free) | Free web services typically sleep after ~15 min idle | Varies by host | Move to dedicated container (\$5–\$7/mo) in Phase 2 when persistent workers start |

---

## 2. Phase 1 Cost Guardrails Implemented

- **No Paid Redis:** `apps/api/deps.py` defaults to `InMemorySessionStore` in dev/single-instance free-tier mode; `RedisSessionStore` is opt-in via `REDIS_URL`.
- **No Always-Running Workers:** Background workers (`workers/*`), Temporal, and GPU/video rendering are deferred to Phase 2+.
- **No Long-Running Supabase Edge Functions:** AI generation and video rendering will NOT run inside Supabase Edge Functions.
- **Connection Pooling:** `packages/tenancy/db.py` supports Supabase PgBouncer transaction mode (`:6543`, `prepare_threshold=None`) to stay well within Supabase Free connection limits.
- **Request Limits:** Sliding-window rate limiting on auth endpoints (`30 req/min` per IP) and bounded pagination (`limit <= 200`) on `audit_logs`.
- **Storage Hygiene:** Private bucket with strict file-size caps (`MAX_BRAND_ASSET_BYTES`) and short-lived presigned URLs (`60–900s`).
- **AI Usage Accounting Foundation:** Append-only `audit_logs` with `workspace_id`, `client_id`, `actor_user_id`, `request_id`, and JSONB `metadata` ready to anchor Phase 2 token/media cost ledgers.
