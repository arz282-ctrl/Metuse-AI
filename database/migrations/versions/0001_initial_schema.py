"""Initial 10-table UUID schema, metuse_app role, FORCE RLS, audit immutability, and 5-member cap.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-25 11:35:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RLS_TABLES = (
    "workspaces",
    "workspace_memberships",
    "clients",
    "client_memberships",
    "brand_profiles",
    "brand_assets",
    "audit_logs",
)

_UPDATED_AT_TABLES = (
    "users",
    "user_identities",
    "workspaces",
    "workspace_memberships",
    "clients",
    "client_memberships",
    "brand_profiles",
)


def upgrade() -> None:
    # 1. Ensure non-superuser runtime role exists (idempotent fallback when postgres-init absent)
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'metuse_app') THEN
                CREATE ROLE metuse_app WITH
                    LOGIN
                    NOSUPERUSER
                    NOCREATEDB
                    NOCREATEROLE
                    NOBYPASSRLS
                    NOREPLICATION
                    PASSWORD 'change-me-app';
            END IF;
        END
        $$;
        """
    )

    # 2. Helper functions for fail-closed RLS GUC parsing & triggers
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.current_app_workspace_id()
        RETURNS uuid
        LANGUAGE sql
        STABLE
        AS $$
            SELECT NULLIF(current_setting('app.current_workspace_id', true), '')::uuid;
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.current_app_user_id()
        RETURNS uuid
        LANGUAGE sql
        STABLE
        AS $$
            SELECT NULLIF(current_setting('app.current_user_id', true), '')::uuid;
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.set_updated_at()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$;
        """
    )

    # 3. Core 10 UUID-PK tables
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "user_identities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_subject", sa.Text(), nullable=False),
        sa.Column("token_secret_ref", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("provider IN ('email', 'meta')", name="ck_user_identities_provider"),
        sa.UniqueConstraint(
            "provider", "provider_subject", name="uq_user_identities_provider_subject"
        ),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_identities_user_provider"),
    )
    op.create_index("ix_user_identities_user_id", "user_identities", ["user_id"])

    op.create_table(
        "roles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "permissions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "name IN ('agency_owner', 'agency_admin', 'agency_member', "
            "'creator_owner', 'client_reviewer', 'client_viewer')",
            name="ck_roles_name",
        ),
        sa.CheckConstraint("scope IN ('workspace', 'client')", name="ck_roles_scope"),
    )

    op.create_table(
        "workspaces",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("workspace_type", sa.Text(), nullable=False),
        sa.Column("max_members", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("workspace_type IN ('agency', 'creator')", name="ck_workspaces_type"),
        sa.CheckConstraint("max_members BETWEEN 1 AND 5", name="ck_workspaces_max_members"),
    )
    op.create_index("ix_workspaces_created_by_user_id", "workspaces", ["created_by_user_id"])

    op.create_table(
        "workspace_memberships",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'invited', 'suspended', 'removed')",
            name="ck_workspace_memberships_status",
        ),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_memberships_ws_user"),
    )
    op.create_index("ix_workspace_memberships_user_id", "workspace_memberships", ["user_id"])

    op.create_table(
        "clients",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("status IN ('active', 'paused', 'archived')", name="ck_clients_status"),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_clients_workspace_slug"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_clients_workspace_id"),
    )

    op.create_table(
        "client_memberships",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_client_memberships_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_client_memberships_ws_member",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workspace_id", "client_id", "user_id", name="uq_client_memberships_ws_client_user"
        ),
    )

    op.create_table(
        "brand_profiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_name", sa.Text(), nullable=False),
        sa.Column("tone_of_voice", sa.Text(), nullable=True),
        sa.Column("target_audience", sa.Text(), nullable=True),
        sa.Column(
            "color_palette",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("guidelines", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_brand_profiles_ws_client",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "client_id", name="uq_brand_profiles_ws_client"),
        sa.UniqueConstraint(
            "workspace_id", "client_id", "id", name="uq_brand_profiles_ws_client_id"
        ),
    )

    op.create_table(
        "brand_assets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_type", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_bucket", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "asset_type IN ('logo', 'palette', 'font', 'guideline', 'product_reference', 'other')",
            name="ck_brand_assets_type",
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_brand_assets_size_bytes"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_brand_assets_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id", "brand_profile_id"],
            ["brand_profiles.workspace_id", "brand_profiles.client_id", "brand_profiles.id"],
            name="fk_brand_assets_ws_client_brand",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("storage_bucket", "storage_key", name="uq_brand_assets_bucket_key"),
    )

    op.create_table(
        "audit_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_audit_logs_workspace_created", "audit_logs", ["workspace_id", "created_at"])
    op.create_index("ix_audit_logs_actor_created", "audit_logs", ["actor_user_id", "created_at"])

    # 4. updated_at triggers
    for tbl in _UPDATED_AT_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{tbl}_set_updated_at
            BEFORE UPDATE ON public.{tbl}
            FOR EACH ROW
            EXECUTE FUNCTION public.set_updated_at();
            """
        )

    # 5. Atomic <= 5 workspace member cap trigger (SECURITY DEFINER + advisory & row lock)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.enforce_workspace_member_limit()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
            v_max_members integer;
            v_effective_max integer;
            v_active_count integer;
        BEGIN
            IF NEW.status = 'removed' THEN
                RETURN NEW;
            END IF;

            IF TG_OP = 'UPDATE'
               AND OLD.status <> 'removed'
               AND NEW.workspace_id IS NOT DISTINCT FROM OLD.workspace_id THEN
                RETURN NEW;
            END IF;

            PERFORM pg_advisory_xact_lock(hashtextextended(NEW.workspace_id::text, 0));

            SELECT max_members
            INTO v_max_members
            FROM public.workspaces
            WHERE id = NEW.workspace_id
            FOR UPDATE;

            v_effective_max := LEAST(COALESCE(v_max_members, 5), 5);

            SELECT COUNT(*)
            INTO v_active_count
            FROM public.workspace_memberships
            WHERE workspace_id = NEW.workspace_id
              AND status <> 'removed'
              AND id <> NEW.id;

            IF v_active_count >= v_effective_max THEN
                RAISE EXCEPTION
                    'WORKSPACE_MEMBER_LIMIT_EXCEEDED: workspace cannot exceed % members',
                    v_effective_max
                    USING ERRCODE = 'check_violation',
                          HINT = 'WORKSPACE_MEMBER_LIMIT_EXCEEDED',
                          DETAIL = 'WORKSPACE_MEMBER_LIMIT_EXCEEDED';
            END IF;

            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_workspace_memberships_member_limit
        BEFORE INSERT OR UPDATE ON public.workspace_memberships
        FOR EACH ROW
        EXECUTE FUNCTION public.enforce_workspace_member_limit();
        """
    )

    # 6. Append-only audit_logs triggers (ROW UPDATE/DELETE + STATEMENT TRUNCATE)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.prevent_audit_log_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only; % is prohibited', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_no_update_delete
        BEFORE UPDATE OR DELETE ON public.audit_logs
        FOR EACH ROW
        EXECUTE FUNCTION public.prevent_audit_log_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_no_truncate
        BEFORE TRUNCATE ON public.audit_logs
        FOR EACH STATEMENT
        EXECUTE FUNCTION public.prevent_audit_log_mutation();
        """
    )

    # 7. Enable + FORCE ROW LEVEL SECURITY on all 7 tenant/audit tables
    for tbl in _RLS_TABLES:
        op.execute(f"ALTER TABLE public.{tbl} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE public.{tbl} FORCE ROW LEVEL SECURITY;")

    # 7a. workspaces policies (supports INSERT ... RETURNING via created_by_user_id; fail-closed)
    op.execute(
        """
        CREATE POLICY workspaces_select_policy ON public.workspaces
        FOR SELECT
        USING (
            CASE
                WHEN public.current_app_workspace_id() IS NOT NULL THEN
                    id = public.current_app_workspace_id()
                WHEN public.current_app_user_id() IS NOT NULL THEN (
                    created_by_user_id = public.current_app_user_id()
                    OR EXISTS (
                        SELECT 1
                        FROM public.workspace_memberships wm
                        WHERE wm.workspace_id = workspaces.id
                          AND wm.user_id = public.current_app_user_id()
                          AND wm.status <> 'removed'
                    )
                )
                ELSE false
            END
        );
        """
    )
    op.execute(
        """
        CREATE POLICY workspaces_insert_policy ON public.workspaces
        FOR INSERT
        WITH CHECK (
            public.current_app_user_id() IS NOT NULL
            AND created_by_user_id = public.current_app_user_id()
            AND (
                public.current_app_workspace_id() IS NULL
                OR id = public.current_app_workspace_id()
            )
        );
        """
    )
    op.execute(
        """
        CREATE POLICY workspaces_update_policy ON public.workspaces
        FOR UPDATE
        USING (
            public.current_app_workspace_id() IS NOT NULL
            AND id = public.current_app_workspace_id()
        )
        WITH CHECK (
            public.current_app_workspace_id() IS NOT NULL
            AND id = public.current_app_workspace_id()
        );
        """
    )
    op.execute(
        """
        CREATE POLICY workspaces_delete_policy ON public.workspaces
        FOR DELETE
        USING (
            public.current_app_workspace_id() IS NOT NULL
            AND id = public.current_app_workspace_id()
        );
        """
    )

    # 7b. workspace_memberships policies (strictly non-recursive; never queries workspaces)
    op.execute(
        """
        CREATE POLICY workspace_memberships_select_policy ON public.workspace_memberships
        FOR SELECT
        USING (
            CASE
                WHEN public.current_app_workspace_id() IS NOT NULL THEN
                    workspace_id = public.current_app_workspace_id()
                WHEN public.current_app_user_id() IS NOT NULL THEN
                    user_id = public.current_app_user_id()
                ELSE false
            END
        );
        """
    )
    op.execute(
        """
        CREATE POLICY workspace_memberships_insert_policy ON public.workspace_memberships
        FOR INSERT
        WITH CHECK (
            public.current_app_workspace_id() IS NOT NULL
            AND workspace_id = public.current_app_workspace_id()
        );
        """
    )
    op.execute(
        """
        CREATE POLICY workspace_memberships_update_policy ON public.workspace_memberships
        FOR UPDATE
        USING (
            public.current_app_workspace_id() IS NOT NULL
            AND workspace_id = public.current_app_workspace_id()
        )
        WITH CHECK (
            public.current_app_workspace_id() IS NOT NULL
            AND workspace_id = public.current_app_workspace_id()
        );
        """
    )
    op.execute(
        """
        CREATE POLICY workspace_memberships_delete_policy ON public.workspace_memberships
        FOR DELETE
        USING (
            public.current_app_workspace_id() IS NOT NULL
            AND workspace_id = public.current_app_workspace_id()
        );
        """
    )

    # 7c. Standard tenant-scoped tables (clients, client_memberships, brand_profiles, brand_assets)
    for tbl in ("clients", "client_memberships", "brand_profiles", "brand_assets"):
        op.execute(
            f"""
            CREATE POLICY {tbl}_tenant_policy ON public.{tbl}
            FOR ALL
            USING (
                public.current_app_workspace_id() IS NOT NULL
                AND workspace_id = public.current_app_workspace_id()
            )
            WITH CHECK (
                public.current_app_workspace_id() IS NOT NULL
                AND workspace_id = public.current_app_workspace_id()
            );
            """
        )

    # 7d. audit_logs split SELECT / INSERT policies (supports pre-workspace NULL & fails closed)
    op.execute(
        """
        CREATE POLICY audit_logs_select_policy ON public.audit_logs
        FOR SELECT
        USING (
            (
                public.current_app_workspace_id() IS NOT NULL
                AND workspace_id = public.current_app_workspace_id()
            )
            OR (
                public.current_app_workspace_id() IS NULL
                AND public.current_app_user_id() IS NOT NULL
                AND workspace_id IS NULL
                AND actor_user_id = public.current_app_user_id()
            )
        );
        """
    )
    op.execute(
        """
        CREATE POLICY audit_logs_insert_policy ON public.audit_logs
        FOR INSERT
        WITH CHECK (
            (
                workspace_id IS NOT NULL
                AND workspace_id = public.current_app_workspace_id()
                AND (
                    actor_user_id IS NULL
                    OR public.current_app_user_id() IS NULL
                    OR actor_user_id = public.current_app_user_id()
                )
            )
            OR (
                public.current_app_workspace_id() IS NULL
                AND workspace_id IS NULL
                AND (
                    actor_user_id IS NULL
                    OR (
                        public.current_app_user_id() IS NOT NULL
                        AND actor_user_id = public.current_app_user_id()
                    )
                )
            )
        );
        """
    )

    # 8. Least-privilege grants for metuse_app
    op.execute("GRANT USAGE ON SCHEMA public TO metuse_app;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
        "public.users, public.user_identities, public.workspaces, "
        "public.workspace_memberships, public.clients, public.client_memberships, "
        "public.brand_profiles, public.brand_assets TO metuse_app;"
    )
    op.execute("GRANT SELECT ON TABLE public.roles TO metuse_app;")
    op.execute("GRANT SELECT, INSERT ON TABLE public.audit_logs TO metuse_app;")
    op.execute(
        "REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.audit_logs FROM metuse_app, PUBLIC;"
    )


def downgrade() -> None:
    # Revoke role privileges before dropping tables
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'metuse_app') THEN
                REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM metuse_app;
                REVOKE USAGE ON SCHEMA public FROM metuse_app;
            END IF;
        END
        $$;
        """
    )

    # Drop audit_logs triggers & policies
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_no_truncate ON public.audit_logs;")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_no_update_delete ON public.audit_logs;")
    op.execute("DROP POLICY IF EXISTS audit_logs_insert_policy ON public.audit_logs;")
    op.execute("DROP POLICY IF EXISTS audit_logs_select_policy ON public.audit_logs;")

    # Drop tenant policies on standard tenant tables
    for tbl in ("brand_assets", "brand_profiles", "client_memberships", "clients"):
        op.execute(f"DROP POLICY IF EXISTS {tbl}_tenant_policy ON public.{tbl};")

    # Drop workspace_memberships triggers & policies
    op.execute(
        "DROP TRIGGER IF EXISTS trg_workspace_memberships_member_limit "
        "ON public.workspace_memberships;"
    )
    for op_name in ("delete", "update", "insert", "select"):
        op.execute(
            f"DROP POLICY IF EXISTS workspace_memberships_{op_name}_policy "
            "ON public.workspace_memberships;"
        )

    # Drop workspaces policies
    for op_name in ("delete", "update", "insert", "select"):
        op.execute(f"DROP POLICY IF EXISTS workspaces_{op_name}_policy ON public.workspaces;")

    # Disable RLS and drop updated_at triggers
    for tbl in _RLS_TABLES:
        op.execute(f"ALTER TABLE IF EXISTS public.{tbl} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE IF EXISTS public.{tbl} DISABLE ROW LEVEL SECURITY;")

    for tbl in _UPDATED_AT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{tbl}_set_updated_at ON public.{tbl};")

    # Drop tables in reverse dependency order
    op.drop_index("ix_audit_logs_actor_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_workspace_created", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("brand_assets")
    op.drop_table("brand_profiles")
    op.drop_table("client_memberships")
    op.drop_table("clients")
    op.drop_index("ix_workspace_memberships_user_id", table_name="workspace_memberships")
    op.drop_table("workspace_memberships")
    op.drop_index("ix_workspaces_created_by_user_id", table_name="workspaces")
    op.drop_table("workspaces")
    op.drop_table("roles")
    op.drop_index("ix_user_identities_user_id", table_name="user_identities")
    op.drop_table("user_identities")
    op.drop_table("users")

    # Drop functions
    op.execute("DROP FUNCTION IF EXISTS public.prevent_audit_log_mutation();")
    op.execute("DROP FUNCTION IF EXISTS public.enforce_workspace_member_limit();")
    op.execute("DROP FUNCTION IF EXISTS public.set_updated_at();")
    op.execute("DROP FUNCTION IF EXISTS public.current_app_user_id();")
    op.execute("DROP FUNCTION IF EXISTS public.current_app_workspace_id();")
