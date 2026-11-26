# Database Schema & Migration Reference — Metuse AI (Phase 1)

**Single Source of Truth:** Alembic (`database/migrations/versions/`) + SQLAlchemy 2.0 ORM (`database/models.py`).  
**Target Engine:** PostgreSQL 16 (Supabase Managed PostgreSQL or local Postgres 16).  
**Primary Keys:** UUID (`gen_random_uuid()`).  
**Timestamps:** UTC (`TIMESTAMP WITH TIME ZONE`, `server_default=now()`).

---

## 1. Canonical 10-Table Multi-Tenant Schema

No parallel 6-table organization schema was created. All Phase 1 PRD entities map cleanly onto the canonical 10-table schema:

| Table | PRD Equivalent | RLS Enabled (`FORCE`) | Purpose |
|---|---|---|---|
| `public.users` | `profiles` | App-scoped | Canonical user profile (`id`, `email`, `password_hash`, `full_name`, `avatar_url`, `is_active`, `created_at`, `updated_at`) |
| `public.user_identities` | Auth link | App-scoped | Links `users` to `'email'`, `'meta'`, or `'supabase'` identities (`provider`, `provider_subject`, `token_secret_ref`) |
| `public.roles` | `roles` | Read-only catalog | 6 canonical roles (`agency_owner`, `agency_admin`, `agency_member`, `creator_owner`, `client_reviewer`, `client_viewer`) with JSONB permissions |
| `public.workspaces` | `organizations` | **Yes (`FORCE RLS`)** | Multi-tenant agency (`max_members <= 5`) and creator (`max_members = 1`) tenants |
| `public.workspace_memberships` | `organization_members` | **Yes (`FORCE RLS`)** | User-to-workspace membership, role FK, lifecycle status, atomic 5-member cap trigger |
| `public.clients` | `clients` | **Yes (`FORCE RLS`)** | Tenant-scoped client accounts (`active`, `paused`, `archived`) with composite uniqueness `(workspace_id, id)` |
| `public.client_memberships` | Client RBAC | **Yes (`FORCE RLS`)** | Restricts `client_reviewer` and `client_viewer` roles to explicitly assigned clients via composite FKs |
| `public.brand_profiles` | Brand metadata | **Yes (`FORCE RLS`)** | Client brand voice, audience, color palette, guidelines (composite FK to `clients`) |
| `public.brand_assets` | Private media | **Yes (`FORCE RLS`)** | Tenant-prefixed private object storage metadata (S3 / Supabase Storage S3 API) |
| `public.audit_logs` | `audit_logs` | **Yes (`FORCE RLS`)** | Immutable append-only audit trail (`UPDATE`/`DELETE`/`TRUNCATE` blocked by trigger for both `metuse_app` and DB owner) |

---

## 2. Migration History (Linear Graph)

1. **`0001_initial_schema`** — Creates `metuse_app` (`NOSUPERUSER NOBYPASSRLS`), helper functions (`current_app_workspace_id()`, `current_app_user_id()`, `set_updated_at()`), all 10 UUID-PK tables, composite foreign keys, `FORCE ROW LEVEL SECURITY` on all 7 tenant tables, immutable `audit_logs` trigger, and atomic 5-member cap trigger.
2. **`0002_harden_functions_search_path`** — Hardens all 5 PostgreSQL functions with `SET search_path = pg_catalog, public` and reaffirms `BEFORE INSERT OR UPDATE` member-cap trigger coverage.
3. **`0003_supabase_compat`** *(Additive, Reversible — Ready for approval before applying to live Supabase)* — Adds nullable `avatar_url TEXT` to `public.users` and expands `ck_user_identities_provider` to `('email', 'meta', 'supabase')`.

---

## 3. Row Level Security & Connection Pooling Model

- **Runtime Role:** FastAPI connects strictly as `metuse_app` (`NOSUPERUSER`, `NOBYPASSRLS`, owns 0 tables).
- **Fail-Closed Default:** When `app.current_workspace_id` and `app.current_user_id` GUCs are unset, `SELECT` returns 0 rows and tenant writes fail with `InsufficientPrivilege`.
- **Transaction-Local Context:** `packages/tenancy/db.py` executes `SELECT set_config('app.current_user_id', ..., true)` and `SELECT set_config('app.current_workspace_id', ..., true)` inside every `session.begin()` transaction, explicitly clearing unset GUCs so pooled connections can never leak tenant state.
- **Supabase PgBouncer Transaction Mode (`:6543`):** Fully compatible because `set_config(..., is_local=true)` is transaction-scoped and `create_app_engine()` sets `prepare_threshold=None` automatically on Supabase pooler URLs.
