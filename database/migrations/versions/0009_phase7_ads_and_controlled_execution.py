"""Phase 7 schema: Meta Ads read snapshots, campaign drafts & controlled execution + FORCE RLS.

Revision ID: 0009_phase7_ads_and_controlled_execution
Revises: 0008_phase6_publishing_and_insights
Create Date: 2026-11-28 06:20:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_phase7_ads_and_controlled_execution"
down_revision: str | Sequence[str] | None = "0008_phase6_publishing_and_insights"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = (
    "ad_accounts",
    "ad_campaign_snapshots",
    "ad_set_snapshots",
    "ad_snapshots",
    "ad_campaign_drafts",
)


def upgrade() -> None:
    op.create_table(
        "ad_accounts",
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
        sa.Column("meta_connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_account_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("timezone_name", sa.Text(), nullable=False, server_default=sa.text("'UTC'")),
        sa.Column("account_status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("spend_cap_cents", sa.BigInteger(), nullable=True),
        sa.Column(
            "amount_spent_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
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
            "spend_cap_cents IS NULL OR spend_cap_cents >= 0",
            name="ck_ad_accounts_spend_cap",
        ),
        sa.CheckConstraint("amount_spent_cents >= 0", name="ck_ad_accounts_amount_spent"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_ad_accounts_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "meta_connection_id"],
            ["meta_connections.workspace_id", "meta_connections.id"],
            name="fk_ad_accounts_ws_conn",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_ad_accounts_ws_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "external_account_id",
            name="uq_ad_accounts_ws_external",
        ),
    )
    op.create_index("ix_ad_accounts_ws_client", "ad_accounts", ["workspace_id", "client_id"])

    op.create_table(
        "ad_campaign_snapshots",
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
        sa.Column("ad_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_campaign_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'PAUSED'")),
        sa.Column("daily_budget_cents", sa.BigInteger(), nullable=True),
        sa.Column("lifetime_budget_cents", sa.BigInteger(), nullable=True),
        sa.Column("spend_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("impressions", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("clicks", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("conversions", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "metrics_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "captured_at",
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
            "daily_budget_cents IS NULL OR daily_budget_cents >= 0",
            name="ck_ad_campaigns_daily_budget",
        ),
        sa.CheckConstraint(
            "lifetime_budget_cents IS NULL OR lifetime_budget_cents >= 0",
            name="ck_ad_campaigns_lifetime_budget",
        ),
        sa.CheckConstraint("spend_cents >= 0", name="ck_ad_campaigns_spend"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_ad_campaigns_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "ad_account_id"],
            ["ad_accounts.workspace_id", "ad_accounts.id"],
            name="fk_ad_campaigns_ws_account",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_ad_campaigns_ws_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "ad_account_id",
            "external_campaign_id",
            name="uq_ad_campaigns_ws_ext",
        ),
    )
    op.create_index(
        "ix_ad_campaigns_ws_account", "ad_campaign_snapshots", ["workspace_id", "ad_account_id"]
    )

    op.create_table(
        "ad_set_snapshots",
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
        sa.Column("ad_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_ad_set_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'PAUSED'")),
        sa.Column(
            "optimization_goal", sa.Text(), nullable=False, server_default=sa.text("'REACH'")
        ),
        sa.Column("daily_budget_cents", sa.BigInteger(), nullable=True),
        sa.Column(
            "targeting_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("spend_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("impressions", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("clicks", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "metrics_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "captured_at",
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
            "daily_budget_cents IS NULL OR daily_budget_cents >= 0",
            name="ck_ad_sets_daily_budget",
        ),
        sa.CheckConstraint("spend_cents >= 0", name="ck_ad_sets_spend"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_ad_sets_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "ad_account_id"],
            ["ad_accounts.workspace_id", "ad_accounts.id"],
            name="fk_ad_sets_ws_account",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "campaign_snapshot_id"],
            ["ad_campaign_snapshots.workspace_id", "ad_campaign_snapshots.id"],
            name="fk_ad_sets_ws_campaign",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_ad_sets_ws_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "ad_account_id",
            "external_ad_set_id",
            name="uq_ad_sets_ws_ext",
        ),
    )
    op.create_index(
        "ix_ad_sets_ws_campaign", "ad_set_snapshots", ["workspace_id", "campaign_snapshot_id"]
    )

    op.create_table(
        "ad_snapshots",
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
        sa.Column("ad_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ad_set_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_ad_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'PAUSED'")),
        sa.Column(
            "creative_summary_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("spend_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("impressions", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("clicks", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "metrics_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "captured_at",
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
        sa.CheckConstraint("spend_cents >= 0", name="ck_ad_snapshots_spend"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_ad_snapshots_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "ad_account_id"],
            ["ad_accounts.workspace_id", "ad_accounts.id"],
            name="fk_ad_snapshots_ws_account",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "ad_set_snapshot_id"],
            ["ad_set_snapshots.workspace_id", "ad_set_snapshots.id"],
            name="fk_ad_snapshots_ws_ad_set",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "ad_account_id",
            "external_ad_id",
            name="uq_ad_snapshots_ws_ext",
        ),
    )
    op.create_index(
        "ix_ad_snapshots_ws_ad_set", "ad_snapshots", ["workspace_id", "ad_set_snapshot_id"]
    )

    op.create_table(
        "ad_campaign_drafts",
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
        sa.Column("ad_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("daily_budget_cents", sa.BigInteger(), nullable=False),
        sa.Column(
            "targeting_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "creative_refs_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "validation_passed", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "validation_report",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("remote_campaign_id", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
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
            "status IN ('draft', 'validated', 'pending_approval', 'executed', 'failed')",
            name="ck_ad_drafts_status",
        ),
        sa.CheckConstraint("daily_budget_cents > 0", name="ck_ad_drafts_daily_budget"),
        sa.CheckConstraint("version_number > 0", name="ck_ad_drafts_version"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_ad_drafts_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "ad_account_id"],
            ["ad_accounts.workspace_id", "ad_accounts.id"],
            name="fk_ad_drafts_ws_account",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "approval_id"],
            ["approvals.workspace_id", "approvals.id"],
            name="fk_ad_drafts_ws_approval",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_ad_drafts_ws_id"),
    )
    op.create_index(
        "ix_ad_drafts_ws_account", "ad_campaign_drafts", ["workspace_id", "ad_account_id"]
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_ad_drafts_ws_idempotency
        ON public.ad_campaign_drafts (workspace_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL;
        """
    )

    for tbl in _TENANT_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{tbl}_updated_at
            BEFORE UPDATE ON public.{tbl}
            FOR EACH ROW
            EXECUTE FUNCTION public.set_updated_at();
            """
        )
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
