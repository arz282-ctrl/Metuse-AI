"""Phase 2 schema: skills, worker_jobs, model_runs, usage, budgets, research + FORCE RLS.

Revision ID: 0004_phase2_ai_workers_and_usage
Revises: 0003_supabase_compat
Create Date: 2026-11-27 16:35:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_phase2_ai_workers_and_usage"
down_revision: str | Sequence[str] | None = "0003_supabase_compat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_TABLES = (
    "worker_jobs",
    "model_runs",
    "usage_records",
    "workspace_budgets",
    "research_reports",
    "research_sources",
)


def upgrade() -> None:
    # 1. Global catalog tables (read-only for metuse_app)
    op.create_table(
        "skill_definitions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("skill_id", sa.Text(), nullable=False, unique=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
        "skill_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "skill_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("skill_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("skill_definition_id", "version", name="uq_skill_versions_def_version"),
    )
    op.create_index(
        "ix_skill_versions_def_active", "skill_versions", ["skill_definition_id", "is_active"]
    )

    # 2. Tenant-owned Phase 2 tables
    op.create_table(
        "worker_jobs",
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
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("brand_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_type", sa.Text(), nullable=False),
        sa.Column("skill_id", sa.Text(), nullable=False),
        sa.Column("skill_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("requested_output_schema", sa.Text(), nullable=False),
        sa.Column(
            "input_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("output_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
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
            "task_type IN ('research', 'writing', 'seo_aeo', 'media_plan')",
            name="ck_worker_jobs_task_type",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'needs_review')",
            name="ck_worker_jobs_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_worker_jobs_ws_client",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_worker_jobs_ws_id"),
    )
    op.create_index("ix_worker_jobs_ws_created", "worker_jobs", ["workspace_id", "created_at"])
    op.create_index("ix_worker_jobs_ws_client", "worker_jobs", ["workspace_id", "client_id"])
    op.execute(
        """
        CREATE UNIQUE INDEX uq_worker_jobs_ws_idempotency
        ON public.worker_jobs (workspace_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL;
        """
    )

    op.create_table(
        "model_runs",
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
        sa.Column("worker_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("input_units", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("output_units", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed', 'rate_limited')",
            name="ck_model_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "worker_job_id"],
            ["worker_jobs.workspace_id", "worker_jobs.id"],
            name="fk_model_runs_ws_job",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_model_runs_ws_id"),
    )
    op.create_index("ix_model_runs_ws_created", "model_runs", ["workspace_id", "created_at"])

    op.create_table(
        "usage_records",
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
        sa.Column("worker_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("input_units", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("output_units", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "estimated_cost_usd",
            sa.Numeric(12, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("billing_period", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("input_units >= 0 AND output_units >= 0", name="ck_usage_records_units"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "worker_job_id"],
            ["worker_jobs.workspace_id", "worker_jobs.id"],
            name="fk_usage_records_ws_job",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_usage_records_ws_period", "usage_records", ["workspace_id", "billing_period"]
    )
    op.create_index(
        "ix_usage_records_ws_client_period",
        "usage_records",
        ["workspace_id", "client_id", "billing_period"],
    )

    op.create_table(
        "workspace_budgets",
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
        sa.Column("monthly_unit_limit", sa.BigInteger(), nullable=True),
        sa.Column("monthly_cost_limit_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column(
            "provider_kill_switch",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
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
            "(monthly_unit_limit IS NULL OR monthly_unit_limit >= 0) "
            "AND (monthly_cost_limit_usd IS NULL OR monthly_cost_limit_usd >= 0)",
            name="ck_workspace_budgets_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_workspace_budgets_ws_client",
            ondelete="CASCADE",
        ),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_workspace_budgets_default
        ON public.workspace_budgets (workspace_id)
        WHERE client_id IS NULL;
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_workspace_budgets_ws_client
        ON public.workspace_budgets (workspace_id, client_id)
        WHERE client_id IS NOT NULL;
        """
    )

    op.create_table(
        "research_reports",
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
        sa.Column("worker_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "findings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "inferences",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "confidence_overall",
            sa.Numeric(4, 3),
            nullable=False,
            server_default=sa.text("0.500"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "worker_job_id"],
            ["worker_jobs.workspace_id", "worker_jobs.id"],
            name="fk_research_reports_ws_job",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "id", name="uq_research_reports_ws_id"),
        sa.UniqueConstraint("worker_job_id", name="uq_research_reports_job_id"),
    )

    op.create_table(
        "research_sources",
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
        sa.Column("research_report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("published_date", sa.Text(), nullable=True),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column(
            "confidence",
            sa.Numeric(4, 3),
            nullable=False,
            server_default=sa.text("0.500"),
        ),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "research_report_id"],
            ["research_reports.workspace_id", "research_reports.id"],
            name="fk_research_sources_ws_report",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_research_sources_report", "research_sources", ["research_report_id"])

    # 3. updated_at triggers
    for tbl in ("skill_definitions", "worker_jobs", "workspace_budgets"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{tbl}_updated_at
            BEFORE UPDATE ON public.{tbl}
            FOR EACH ROW
            EXECUTE FUNCTION public.set_updated_at();
            """
        )

    # 4. Least-privilege grants to metuse_app
    op.execute("GRANT SELECT ON public.skill_definitions, public.skill_versions TO metuse_app;")
    for tbl in _TENANT_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.{tbl} TO metuse_app;")

    # 5. FORCE ROW LEVEL SECURITY on all 6 tenant-owned Phase 2 tables (fail-closed)
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
    for tbl in reversed(_TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {tbl}_tenant_isolation ON public.{tbl};")
        op.drop_table(tbl)

    op.drop_table("skill_versions")
    op.drop_table("skill_definitions")
