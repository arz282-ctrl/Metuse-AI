"""Phase 5 schema: meta_connections, meta_connected_assets, meta_webhook_events + FORCE RLS.

Revision ID: 0007_phase5_meta_connections
Revises: 0006_phase4_cloud_editing_and_approvals
Create Date: 2026-11-28 05:42:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_phase5_meta_connections"
down_revision: str | Sequence[str] | None = "0006_phase4_cloud_editing_and_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = (
    "meta_connections",
    "meta_connected_assets",
    "meta_webhook_events",
)


def upgrade() -> None:
    op.create_table(
        "meta_connections",
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
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("connection_type", sa.Text(), nullable=False),
        sa.Column("meta_business_id", sa.Text(), nullable=True),
        sa.Column("config_id", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("token_secret_ref", sa.Text(), nullable=False),
        sa.Column("token_type", sa.Text(), nullable=False, server_default=sa.text("'system_user'")),
        sa.Column(
            "granted_scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "instagram_account_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "page_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "ad_account_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconnect_required_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconnect_reason", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "connection_type IN ('agency_business', 'creator_professional', "
            "'page_only', 'ads_only')",
            name="ck_meta_connections_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'reconnect_required', 'revoked', 'disconnected')",
            name="ck_meta_connections_status",
        ),
        sa.CheckConstraint(
            "token_secret_ref LIKE 'secret://%'",
            name="ck_meta_connections_secret_ref_scheme",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_meta_connections_ws_client",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_meta_connections_ws_id"),
    )
    op.create_index(
        "ix_meta_connections_ws_client", "meta_connections", ["workspace_id", "client_id"]
    )
    op.create_index("ix_meta_connections_ws_status", "meta_connections", ["workspace_id", "status"])

    op.create_table(
        "meta_connected_assets",
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
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_type", sa.Text(), nullable=False),
        sa.Column("external_asset_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("instagram_account_type", sa.Text(), nullable=True),
        sa.Column("meta_business_id", sa.Text(), nullable=True),
        sa.Column("is_selected", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_eligible", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
        sa.CheckConstraint(
            "asset_type IN ('instagram_professional', 'facebook_page', 'ad_account')",
            name="ck_meta_connected_assets_type",
        ),
        sa.CheckConstraint(
            "instagram_account_type IS NULL OR instagram_account_type IN ('business', 'creator')",
            name="ck_meta_connected_assets_ig_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked', 'moved')",
            name="ck_meta_connected_assets_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_meta_connected_assets_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["meta_connections.workspace_id", "meta_connections.id"],
            name="fk_meta_connected_assets_ws_conn",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "connection_id",
            "asset_type",
            "external_asset_id",
            name="uq_meta_connected_assets_unique",
        ),
    )
    op.create_index(
        "ix_meta_connected_assets_ws_conn",
        "meta_connected_assets",
        ["workspace_id", "connection_id"],
    )

    op.create_table(
        "meta_webhook_events",
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
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_event_id", sa.Text(), nullable=False),
        sa.Column("object_type", sa.Text(), nullable=False),
        sa.Column("event_field", sa.Text(), nullable=False),
        sa.Column(
            "signature_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["meta_connections.workspace_id", "meta_connections.id"],
            name="fk_meta_webhook_events_ws_conn",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_meta_webhook_events_ws_created",
        "meta_webhook_events",
        ["workspace_id", "created_at"],
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_meta_webhook_events_ws_event_id
        ON public.meta_webhook_events (workspace_id, provider_event_id);
        """
    )

    for tbl in ("meta_connections", "meta_connected_assets"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{tbl}_updated_at
            BEFORE UPDATE ON public.{tbl}
            FOR EACH ROW
            EXECUTE FUNCTION public.set_updated_at();
            """
        )

    for tbl in _TENANT_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.{tbl} TO metuse_app;")
        op.execute(f"ALTER TABLE public.{tbl} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE public.{tbl} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY {tbl}_tenant_isolation ON public.{tbl}
            FOR ALL TO metuse_app
            USING (workspace_id = public.current_app_workspace_id())
            WITH CHECK (workspace_id = public.current_app_workspace_id());
            """
        )


def downgrade() -> None:
    for tbl in reversed(_TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {tbl}_tenant_isolation ON public.{tbl};")
        op.drop_table(tbl)
