"""Phase 4 schema: cloud editing, render jobs, QA, versions, approvals + FORCE RLS.

Revision ID: 0006_phase4_cloud_editing_and_approvals
Revises: 0005_phase3_video_generation_gateway
Create Date: 2026-11-27 22:55:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_phase4_cloud_editing_and_approvals"
down_revision: str | Sequence[str] | None = "0005_phase3_video_generation_gateway"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = (
    "media_metadata",
    "editing_plans",
    "render_jobs",
    "creative_versions",
    "approvals",
    "revision_requests",
)


def upgrade() -> None:
    op.create_table(
        "media_metadata",
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
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proxy_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("container", sa.Text(), nullable=True),
        sa.Column("video_codec", sa.Text(), nullable=True),
        sa.Column("audio_codec", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("fps", sa.Numeric(8, 3), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("bitrate_bps", sa.BigInteger(), nullable=True),
        sa.Column("has_audio", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "probe_payload",
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
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_media_meta_duration"
        ),
        sa.CheckConstraint("width IS NULL OR width > 0", name="ck_media_meta_width"),
        sa.CheckConstraint("height IS NULL OR height > 0", name="ck_media_meta_height"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_media_metadata_ws_asset",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "proxy_asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_media_metadata_ws_proxy_asset",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("workspace_id", "asset_id", name="uq_media_metadata_ws_asset"),
    )

    op.create_table(
        "editing_plans",
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
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False, server_default=sa.text("'1'")),
        sa.Column(
            "plan_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("plan_sha256", sa.Text(), nullable=False),
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
        sa.CheckConstraint("version_number > 0", name="ck_editing_plans_version"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_editing_plans_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_editing_plans_ws_project",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_editing_plans_ws_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "project_id",
            "version_number",
            name="uq_editing_plans_project_version",
        ),
    )
    op.create_index("ix_editing_plans_ws_project", "editing_plans", ["workspace_id", "project_id"])

    op.create_table(
        "render_jobs",
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
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("editing_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("render_kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("renderer", sa.Text(), nullable=False, server_default=sa.text("'mock'")),
        sa.Column(
            "command_manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("output_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("qa_passed", sa.Boolean(), nullable=True),
        sa.Column(
            "qa_report",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("cpu_time_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=True),
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
        sa.CheckConstraint("render_kind IN ('preview', 'final')", name="ck_render_jobs_kind"),
        sa.CheckConstraint(
            "status IN ('queued', 'validating', 'rendering', 'qa_checking', "
            "'succeeded', 'qa_failed', 'failed', 'cancelled')",
            name="ck_render_jobs_status",
        ),
        sa.CheckConstraint("cpu_time_ms >= 0", name="ck_render_jobs_cpu_time"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_render_jobs_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_render_jobs_ws_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "editing_plan_id"],
            ["editing_plans.workspace_id", "editing_plans.id"],
            name="fk_render_jobs_ws_plan",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "output_asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_render_jobs_ws_output_asset",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_render_jobs_ws_id"),
    )
    op.create_index(
        "ix_render_jobs_ws_project_created",
        "render_jobs",
        ["workspace_id", "project_id", "created_at"],
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_render_jobs_ws_idempotency
        ON public.render_jobs (workspace_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL;
        """
    )

    op.create_table(
        "creative_versions",
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
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("editing_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("render_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("output_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("render_kind", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("qa_passed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "qa_report",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
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
        sa.CheckConstraint("render_kind IN ('preview', 'final')", name="ck_creative_versions_kind"),
        sa.CheckConstraint("version_number > 0", name="ck_creative_versions_version"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_creative_versions_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_creative_versions_ws_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "editing_plan_id"],
            ["editing_plans.workspace_id", "editing_plans.id"],
            name="fk_creative_versions_ws_plan",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "render_job_id"],
            ["render_jobs.workspace_id", "render_jobs.id"],
            name="fk_creative_versions_ws_render",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "output_asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_creative_versions_ws_asset",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_creative_versions_ws_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "project_id",
            "version_number",
            name="uq_creative_versions_project_version",
        ),
    )
    op.create_index(
        "ix_creative_versions_ws_project", "creative_versions", ["workspace_id", "project_id"]
    )

    op.create_table(
        "approvals",
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
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("creative_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approval_type", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_version_sha256", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "decided_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        sa.Column("invalidated_reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
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
            "approval_type IN ('strategy', 'creative', 'publishing', "
            "'campaign_execution', 'budget_change')",
            name="ck_approvals_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'revision_requested', 'invalidated')",
            name="ck_approvals_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_approvals_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_approvals_ws_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "creative_version_id"],
            ["creative_versions.workspace_id", "creative_versions.id"],
            name="fk_approvals_ws_version",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_approvals_ws_id"),
    )
    op.create_index("ix_approvals_ws_status", "approvals", ["workspace_id", "status"])
    op.create_index("ix_approvals_ws_project", "approvals", ["workspace_id", "project_id"])

    op.create_table(
        "revision_requests",
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
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("creative_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("timecode_ms", sa.Integer(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("timecode_ms IS NULL OR timecode_ms >= 0", name="ck_revisions_timecode"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_revision_requests_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_revision_requests_ws_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "creative_version_id"],
            ["creative_versions.workspace_id", "creative_versions.id"],
            name="fk_revision_requests_ws_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "approval_id"],
            ["approvals.workspace_id", "approvals.id"],
            name="fk_revision_requests_ws_approval",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_revision_requests_ws_project",
        "revision_requests",
        ["workspace_id", "project_id"],
    )

    # Link usage_records optionally to render_jobs (Acceptance #17)
    op.add_column(
        "usage_records",
        sa.Column("render_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_usage_records_ws_render_job",
        "usage_records",
        "render_jobs",
        ["workspace_id", "render_job_id"],
        ["workspace_id", "id"],
        ondelete="SET NULL",
    )

    # updated_at triggers
    for tbl in ("media_metadata", "render_jobs", "approvals"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{tbl}_updated_at
            BEFORE UPDATE ON public.{tbl}
            FOR EACH ROW
            EXECUTE FUNCTION public.set_updated_at();
            """
        )

    # Least-privilege grants to metuse_app
    for tbl in _TENANT_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.{tbl} TO metuse_app;")

    # FORCE ROW LEVEL SECURITY on all 6 tenant-owned Phase 4 tables (fail-closed)
    for tbl in _TENANT_TABLES:
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
    op.drop_constraint("fk_usage_records_ws_render_job", "usage_records", type_="foreignkey")
    op.drop_column("usage_records", "render_job_id")

    for tbl in reversed(_TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {tbl}_tenant_isolation ON public.{tbl};")
        op.drop_table(tbl)
