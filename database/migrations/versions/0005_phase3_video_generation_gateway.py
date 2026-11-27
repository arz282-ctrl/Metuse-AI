"""Phase 3 schema: projects, assets, video gateway jobs, events + FORCE RLS.

Revision ID: 0005_phase3_video_generation_gateway
Revises: 0004_phase2_ai_workers_and_usage
Create Date: 2026-11-27 21:45:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_phase3_video_generation_gateway"
down_revision: str | Sequence[str] | None = "0004_phase2_ai_workers_and_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = (
    "projects",
    "assets",
    "video_generation_jobs",
    "video_provider_events",
)


def upgrade() -> None:
    # 1. Global video provider model capability catalog (read-only for metuse_app)
    op.create_table(
        "video_provider_models",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "modes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "aspect_ratios",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "durations_seconds",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "resolutions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "max_reference_images",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "audio_generation",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "webhook_support",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "regions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "cost_per_second_usd",
            sa.Numeric(12, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "quality_tier",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'standard'"),
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
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
            "quality_tier IN ('standard', 'high')",
            name="ck_video_provider_models_quality",
        ),
        sa.CheckConstraint("cost_per_second_usd >= 0", name="ck_video_provider_models_cost"),
        sa.UniqueConstraint("provider", "model", name="uq_video_provider_models_provider_model"),
    )

    # 2. Tenant-owned projects table
    op.create_table(
        "projects",
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
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
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
        sa.CheckConstraint("status IN ('draft', 'active', 'archived')", name="ck_projects_status"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_projects_ws_client",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_projects_ws_id"),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_projects_ws_slug"),
    )
    op.create_index("ix_projects_ws_client", "projects", ["workspace_id", "client_id"])

    # 3. Tenant-owned private object storage asset catalog
    op.create_table(
        "assets",
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
        sa.Column("asset_kind", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("storage_bucket", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Numeric(10, 3), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'ready'")),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
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
            "asset_kind IN ('image', 'video', 'audio', 'document')",
            name="ck_assets_kind",
        ),
        sa.CheckConstraint(
            "source_type IN ('upload', 'video_generation', 'brand', 'render')",
            name="ck_assets_source_type",
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'pending', 'deleted')",
            name="ck_assets_status",
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_assets_size_bytes"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_assets_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_assets_ws_project",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_assets_ws_id"),
        sa.UniqueConstraint("storage_bucket", "storage_key", name="uq_assets_bucket_key"),
    )
    op.create_index(
        "ix_assets_ws_client_created", "assets", ["workspace_id", "client_id", "created_at"]
    )

    # 4. Tenant-owned video_generation_jobs
    op.create_table(
        "video_generation_jobs",
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
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("provider_job_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("progress_pct", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("aspect_ratio", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=False),
        sa.Column(
            "audio_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "brand_constraints",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "provider_preference",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'auto'"),
        ),
        sa.Column(
            "request_params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "input_asset_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "output_asset_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("failure_category", sa.Text(), nullable=True),
        sa.Column("provider_error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("poll_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("actual_cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
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
            "mode IN ('text_to_video', 'image_to_video')",
            name="ck_video_generation_jobs_mode",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'validating', 'submitted', 'processing', "
            "'succeeded', 'failed', 'cancelled')",
            name="ck_video_generation_jobs_status",
        ),
        sa.CheckConstraint(
            "progress_pct BETWEEN 0 AND 100",
            name="ck_video_generation_jobs_progress",
        ),
        sa.CheckConstraint("duration_seconds > 0", name="ck_video_generation_jobs_duration"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_video_generation_jobs_ws_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_video_generation_jobs_ws_project",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_video_generation_jobs_ws_id"),
    )
    op.create_index(
        "ix_video_generation_jobs_ws_created",
        "video_generation_jobs",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_video_generation_jobs_ws_status",
        "video_generation_jobs",
        ["workspace_id", "status"],
    )
    op.create_index(
        "ix_video_generation_jobs_provider_job",
        "video_generation_jobs",
        ["provider", "provider_job_id"],
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_video_generation_jobs_ws_idempotency
        ON public.video_generation_jobs (workspace_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL;
        """
    )

    # 5. Tenant-owned video_provider_events (durable audit trail + webhook deduplication)
    op.create_table(
        "video_provider_events",
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
        sa.Column("video_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_event_id", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=True),
        sa.Column(
            "payload",
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
        sa.CheckConstraint(
            "event_type IN ('state_transition', 'webhook', 'poll', 'cancel', 'error')",
            name="ck_video_provider_events_type",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "video_job_id"],
            ["video_generation_jobs.workspace_id", "video_generation_jobs.id"],
            name="fk_video_provider_events_ws_job",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_video_provider_events_ws_job_created",
        "video_provider_events",
        ["workspace_id", "video_job_id", "created_at"],
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_video_provider_events_dedupe
        ON public.video_provider_events (workspace_id, provider, provider_event_id)
        WHERE provider_event_id IS NOT NULL;
        """
    )

    # 6. Link usage_records optionally to video_generation_jobs (Acceptance #17)
    op.add_column(
        "usage_records",
        sa.Column("video_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_usage_records_ws_video_job",
        "usage_records",
        "video_generation_jobs",
        ["workspace_id", "video_job_id"],
        ["workspace_id", "id"],
        ondelete="SET NULL",
    )

    # 7. updated_at triggers
    for tbl in ("video_provider_models", "projects", "assets", "video_generation_jobs"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{tbl}_updated_at
            BEFORE UPDATE ON public.{tbl}
            FOR EACH ROW
            EXECUTE FUNCTION public.set_updated_at();
            """
        )

    # 8. Least-privilege grants to metuse_app
    op.execute("GRANT SELECT ON public.video_provider_models TO metuse_app;")
    for tbl in _TENANT_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.{tbl} TO metuse_app;")

    # 9. FORCE ROW LEVEL SECURITY on all 4 tenant-owned Phase 3 tables (fail-closed)
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
    op.drop_constraint("fk_usage_records_ws_video_job", "usage_records", type_="foreignkey")
    op.drop_column("usage_records", "video_job_id")

    for tbl in reversed(_TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {tbl}_tenant_isolation ON public.{tbl};")
        op.drop_table(tbl)

    op.drop_table("video_provider_models")
