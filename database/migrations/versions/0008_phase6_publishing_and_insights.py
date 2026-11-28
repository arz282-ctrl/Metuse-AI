"""Phase 6 schema: content calendars, items, publishing jobs/results, insights + FORCE RLS.

Revision ID: 0008_phase6_publishing_and_insights
Revises: 0007_phase5_meta_connections
Create Date: 2026-11-28 06:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_phase6_publishing_and_insights"
down_revision: str | Sequence[str] | None = "0007_phase5_meta_connections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = (
    "content_calendars",
    "content_items",
    "publishing_jobs",
    "publishing_results",
    "insights_snapshots",
)


def upgrade() -> None:
    op.create_table(
        "content_calendars",
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
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False, server_default=sa.text("'UTC'")),
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
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_content_calendars_ws_client",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_content_calendars_ws_id"),
    )
    op.create_index(
        "ix_content_calendars_ws_client", "content_calendars", ["workspace_id", "client_id"]
    )

    op.create_table(
        "content_items",
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
        sa.Column("calendar_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("creative_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("meta_connection_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("external_account_id", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False, server_default=sa.text("'reels'")),
        sa.Column("caption", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("is_ai_generated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "carousel_asset_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
            "media_type IN ('reels', 'image', 'video', 'carousel', 'story')",
            name="ck_content_items_media_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'scheduled', 'publishing', 'published', 'failed', 'cancelled')",
            name="ck_content_items_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_content_items_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "calendar_id"],
            ["content_calendars.workspace_id", "content_calendars.id"],
            name="fk_content_items_ws_calendar",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_content_items_ws_project",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "creative_version_id"],
            ["creative_versions.workspace_id", "creative_versions.id"],
            name="fk_content_items_ws_version",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "meta_connection_id"],
            ["meta_connections.workspace_id", "meta_connections.id"],
            name="fk_content_items_ws_meta_conn",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_content_items_ws_id"),
    )
    op.create_index(
        "ix_content_items_ws_scheduled", "content_items", ["workspace_id", "scheduled_for"]
    )

    op.create_table(
        "publishing_jobs",
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
        sa.Column("content_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("meta_connection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("remote_container_id", sa.Text(), nullable=True),
        sa.Column("remote_media_id", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_ai_generated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("account_limit_quota_used", sa.Integer(), nullable=True),
        sa.Column("account_limit_quota_total", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('queued', 'validating', 'container_creating', 'container_ready', "
            "'publishing', 'verifying_remote', 'published', 'failed', 'cancelled')",
            name="ck_publishing_jobs_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_publishing_jobs_attempts"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_publishing_jobs_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "content_item_id"],
            ["content_items.workspace_id", "content_items.id"],
            name="fk_publishing_jobs_ws_content",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "meta_connection_id"],
            ["meta_connections.workspace_id", "meta_connections.id"],
            name="fk_publishing_jobs_ws_conn",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_publishing_jobs_ws_id"),
    )
    op.create_index(
        "ix_publishing_jobs_ws_content", "publishing_jobs", ["workspace_id", "content_item_id"]
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_publishing_jobs_ws_idempotency
        ON public.publishing_jobs (workspace_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL;
        """
    )

    op.create_table(
        "publishing_results",
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
        sa.Column("publishing_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_account_id", sa.Text(), nullable=False),
        sa.Column("remote_container_id", sa.Text(), nullable=True),
        sa.Column("remote_media_id", sa.Text(), nullable=False),
        sa.Column("permalink", sa.Text(), nullable=True),
        sa.Column(
            "verified_via_remote_check",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "raw_response",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_publishing_results_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "publishing_job_id"],
            ["publishing_jobs.workspace_id", "publishing_jobs.id"],
            name="fk_publishing_results_ws_job",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "content_item_id"],
            ["content_items.workspace_id", "content_items.id"],
            name="fk_publishing_results_ws_content",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "external_account_id",
            "remote_media_id",
            name="uq_publishing_results_remote_media",
        ),
    )

    op.create_table(
        "insights_snapshots",
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
        sa.Column("remote_media_id", sa.Text(), nullable=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("period", sa.Text(), nullable=False, server_default=sa.text("'days_28'")),
        sa.Column("impressions", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("reach", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("likes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("comments", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("saves", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("shares", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("plays", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("follower_count", sa.BigInteger(), nullable=True),
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
        sa.CheckConstraint("scope IN ('account', 'media')", name="ck_insights_snapshots_scope"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_insights_snapshots_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "meta_connection_id"],
            ["meta_connections.workspace_id", "meta_connections.id"],
            name="fk_insights_snapshots_ws_conn",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_insights_snapshots_ws_client_captured",
        "insights_snapshots",
        ["workspace_id", "client_id", "captured_at"],
    )

    for tbl in ("content_calendars", "content_items", "publishing_jobs"):
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
