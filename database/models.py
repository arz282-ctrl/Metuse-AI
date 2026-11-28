"""SQLAlchemy 2.0 ORM metadata for Metuse AI PostgreSQL schema."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all Metuse AI database models."""


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class UserIdentity(Base):
    __tablename__ = "user_identities"
    __table_args__ = (
        CheckConstraint(
            "provider IN ('email', 'meta', 'supabase')", name="ck_user_identities_provider"
        ),
        UniqueConstraint(
            "provider", "provider_subject", name="uq_user_identities_provider_subject"
        ),
        UniqueConstraint("user_id", "provider", name="uq_user_identities_user_provider"),
        Index("ix_user_identities_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_subject: Mapped[str] = mapped_column(Text, nullable=False)
    token_secret_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint(
            "name IN ('agency_owner', 'agency_admin', 'agency_member', "
            "'creator_owner', 'client_reviewer', 'client_viewer')",
            name="ck_roles_name",
        ),
        CheckConstraint("scope IN ('workspace', 'client')", name="ck_roles_scope"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    permissions: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint("workspace_type IN ('agency', 'creator')", name="ck_workspaces_type"),
        CheckConstraint("max_members BETWEEN 1 AND 5", name="ck_workspaces_max_members"),
        Index("ix_workspaces_created_by_user_id", "created_by_user_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    workspace_type: Mapped[str] = mapped_column(Text, nullable=False)
    max_members: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'invited', 'suspended', 'removed')",
            name="ck_workspace_memberships_status",
        ),
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_memberships_ws_user"),
        Index("ix_workspace_memberships_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'paused', 'archived')", name="ck_clients_status"),
        UniqueConstraint("workspace_id", "slug", name="uq_clients_workspace_slug"),
        UniqueConstraint("workspace_id", "id", name="uq_clients_workspace_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ClientMembership(Base):
    __tablename__ = "client_memberships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_client_memberships_ws_client",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "user_id"],
            ["workspace_memberships.workspace_id", "workspace_memberships.user_id"],
            name="fk_client_memberships_ws_member",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id", "client_id", "user_id", name="uq_client_memberships_ws_client_user"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    client_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    role_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class BrandProfile(Base):
    __tablename__ = "brand_profiles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_brand_profiles_ws_client",
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "client_id", name="uq_brand_profiles_ws_client"),
        UniqueConstraint("workspace_id", "client_id", "id", name="uq_brand_profiles_ws_client_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    client_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    brand_name: Mapped[str] = mapped_column(Text, nullable=False)
    tone_of_voice: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    color_palette: Mapped[list[Any] | dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    guidelines: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class BrandAsset(Base):
    __tablename__ = "brand_assets"
    __table_args__ = (
        CheckConstraint(
            "asset_type IN ('logo', 'palette', 'font', 'guideline', 'product_reference', 'other')",
            name="ck_brand_assets_type",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_brand_assets_size_bytes"),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_brand_assets_ws_client",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id", "brand_profile_id"],
            ["brand_profiles.workspace_id", "brand_profiles.client_id", "brand_profiles.id"],
            name="fk_brand_assets_ws_client_brand",
            ondelete="CASCADE",
        ),
        UniqueConstraint("storage_bucket", "storage_key", name="uq_brand_assets_bucket_key"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    client_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    brand_profile_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    asset_type: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_bucket: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_workspace_created", "workspace_id", "created_at"),
        Index("ix_audit_logs_actor_created", "actor_user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class SkillDefinition(Base):
    __tablename__ = "skill_definitions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    skill_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class SkillVersion(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("skill_definition_id", "version", name="uq_skill_versions_def_version"),
        Index("ix_skill_versions_def_active", "skill_definition_id", "is_active"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    skill_definition_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("skill_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class WorkerJob(Base):
    __tablename__ = "worker_jobs"
    __table_args__ = (
        CheckConstraint(
            "task_type IN ('research', 'writing', 'seo_aeo', 'media_plan')",
            name="ck_worker_jobs_task_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'needs_review')",
            name="ck_worker_jobs_status",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_worker_jobs_ws_client",
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_worker_jobs_ws_id"),
        Index("ix_worker_jobs_ws_created", "workspace_id", "created_at"),
        Index("ix_worker_jobs_ws_client", "workspace_id", "client_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    actor_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    brand_profile_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    task_type: Mapped[str] = mapped_column(Text, nullable=False)
    skill_id: Mapped[str] = mapped_column(Text, nullable=False)
    skill_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_output_schema: Mapped[str] = mapped_column(Text, nullable=False)
    input_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    output_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    warnings: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ModelRun(Base):
    __tablename__ = "model_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('succeeded', 'failed', 'rate_limited')",
            name="ck_model_runs_status",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "worker_job_id"],
            ["worker_jobs.workspace_id", "worker_jobs.id"],
            name="fk_model_runs_ws_job",
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_model_runs_ws_id"),
        Index("ix_model_runs_ws_created", "workspace_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    worker_job_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    capability: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    input_units: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    output_units: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        CheckConstraint("input_units >= 0 AND output_units >= 0", name="ck_usage_records_units"),
        ForeignKeyConstraint(
            ["workspace_id", "worker_job_id"],
            ["worker_jobs.workspace_id", "worker_jobs.id"],
            name="fk_usage_records_ws_job",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "video_job_id"],
            ["video_generation_jobs.workspace_id", "video_generation_jobs.id"],
            name="fk_usage_records_ws_video_job",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "render_job_id"],
            ["render_jobs.workspace_id", "render_jobs.id"],
            name="fk_usage_records_ws_render_job",
            ondelete="SET NULL",
        ),
        Index("ix_usage_records_ws_period", "workspace_id", "billing_period"),
        Index("ix_usage_records_ws_client_period", "workspace_id", "client_id", "billing_period"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    worker_job_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    video_job_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    render_job_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    model_run_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    capability: Mapped[str] = mapped_column(Text, nullable=False)
    input_units: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    output_units: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, server_default=text("0")
    )
    billing_period: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class WorkspaceBudget(Base):
    __tablename__ = "workspace_budgets"
    __table_args__ = (
        CheckConstraint(
            "(monthly_unit_limit IS NULL OR monthly_unit_limit >= 0) "
            "AND (monthly_cost_limit_usd IS NULL OR monthly_cost_limit_usd >= 0)",
            name="ck_workspace_budgets_non_negative",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_workspace_budgets_ws_client",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    monthly_unit_limit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    monthly_cost_limit_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    provider_kill_switch: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ResearchReport(Base):
    __tablename__ = "research_reports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "worker_job_id"],
            ["worker_jobs.workspace_id", "worker_jobs.id"],
            name="fk_research_reports_ws_job",
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_research_reports_ws_id"),
        UniqueConstraint("worker_job_id", name="uq_research_reports_job_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    worker_job_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    inferences: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    confidence_overall: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, server_default=text("0.500")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ResearchSource(Base):
    __tablename__ = "research_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "research_report_id"],
            ["research_reports.workspace_id", "research_reports.id"],
            name="fk_research_sources_ws_report",
            ondelete="CASCADE",
        ),
        Index("ix_research_sources_report", "research_report_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    research_report_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    published_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, server_default=text("0.500")
    )
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'active', 'archived')", name="ck_projects_status"),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_projects_ws_client",
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_projects_ws_id"),
        UniqueConstraint("workspace_id", "slug", name="uq_projects_ws_slug"),
        Index("ix_projects_ws_client", "workspace_id", "client_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(
            "asset_kind IN ('image', 'video', 'audio', 'document')",
            name="ck_assets_kind",
        ),
        CheckConstraint(
            "source_type IN ('upload', 'video_generation', 'brand', 'render')",
            name="ck_assets_source_type",
        ),
        CheckConstraint(
            "status IN ('ready', 'pending', 'deleted')",
            name="ck_assets_status",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_assets_size_bytes"),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_assets_ws_client",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_assets_ws_project",
            ondelete="SET NULL",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_assets_ws_id"),
        UniqueConstraint("storage_bucket", "storage_key", name="uq_assets_bucket_key"),
        Index("ix_assets_ws_client_created", "workspace_id", "client_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    project_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    asset_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    storage_bucket: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ready'"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class VideoProviderModel(Base):
    __tablename__ = "video_provider_models"
    __table_args__ = (
        CheckConstraint(
            "quality_tier IN ('standard', 'high')",
            name="ck_video_provider_models_quality",
        ),
        CheckConstraint("cost_per_second_usd >= 0", name="ck_video_provider_models_cost"),
        UniqueConstraint("provider", "model", name="uq_video_provider_models_provider_model"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    modes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    aspect_ratios: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    durations_seconds: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    resolutions: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    max_reference_images: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    audio_generation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    webhook_support: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    regions: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    cost_per_second_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, server_default=text("0")
    )
    quality_tier: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'standard'")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class VideoGenerationJob(Base):
    __tablename__ = "video_generation_jobs"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('text_to_video', 'image_to_video')",
            name="ck_video_generation_jobs_mode",
        ),
        CheckConstraint(
            "status IN ('queued', 'validating', 'submitted', 'processing', "
            "'succeeded', 'failed', 'cancelled')",
            name="ck_video_generation_jobs_status",
        ),
        CheckConstraint(
            "progress_pct BETWEEN 0 AND 100",
            name="ck_video_generation_jobs_progress",
        ),
        CheckConstraint("duration_seconds > 0", name="ck_video_generation_jobs_duration"),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_video_generation_jobs_ws_client",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_video_generation_jobs_ws_project",
            ondelete="SET NULL",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_video_generation_jobs_ws_id"),
        Index("ix_video_generation_jobs_ws_created", "workspace_id", "created_at"),
        Index("ix_video_generation_jobs_ws_status", "workspace_id", "status"),
        Index("ix_video_generation_jobs_provider_job", "provider", "provider_job_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    project_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    actor_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    provider_job_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(Text, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution: Mapped[str] = mapped_column(Text, nullable=False)
    audio_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    brand_constraints: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    provider_preference: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'auto'")
    )
    request_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    input_asset_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    output_asset_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    failure_category: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    poll_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    actual_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class VideoProviderEvent(Base):
    __tablename__ = "video_provider_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('state_transition', 'webhook', 'poll', 'cancel', 'error')",
            name="ck_video_provider_events_type",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "video_job_id"],
            ["video_generation_jobs.workspace_id", "video_generation_jobs.id"],
            name="fk_video_provider_events_ws_job",
            ondelete="CASCADE",
        ),
        Index(
            "ix_video_provider_events_ws_job_created", "workspace_id", "video_job_id", "created_at"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    video_job_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_event_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    from_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class MediaMetadata(Base):
    __tablename__ = "media_metadata"
    __table_args__ = (
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_media_meta_duration"),
        CheckConstraint("width IS NULL OR width > 0", name="ck_media_meta_width"),
        CheckConstraint("height IS NULL OR height > 0", name="ck_media_meta_height"),
        ForeignKeyConstraint(
            ["workspace_id", "asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_media_metadata_ws_asset",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "proxy_asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_media_metadata_ws_proxy_asset",
            ondelete="SET NULL",
        ),
        UniqueConstraint("workspace_id", "asset_id", name="uq_media_metadata_ws_asset"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    proxy_asset_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    container: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_codec: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bitrate_bps: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    has_audio: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    probe_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class EditingPlan(Base):
    __tablename__ = "editing_plans"
    __table_args__ = (
        CheckConstraint("version_number > 0", name="ck_editing_plans_version"),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_editing_plans_ws_client",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_editing_plans_ws_project",
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_editing_plans_ws_id"),
        UniqueConstraint(
            "workspace_id", "project_id", "version_number", name="uq_editing_plans_project_version"
        ),
        Index("ix_editing_plans_ws_project", "workspace_id", "project_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    project_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'1'"))
    plan_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    plan_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class RenderJob(Base):
    __tablename__ = "render_jobs"
    __table_args__ = (
        CheckConstraint("render_kind IN ('preview', 'final')", name="ck_render_jobs_kind"),
        CheckConstraint(
            "status IN ('queued', 'validating', 'rendering', 'qa_checking', "
            "'succeeded', 'qa_failed', 'failed', 'cancelled')",
            name="ck_render_jobs_status",
        ),
        CheckConstraint("cpu_time_ms >= 0", name="ck_render_jobs_cpu_time"),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_render_jobs_ws_client",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_render_jobs_ws_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "editing_plan_id"],
            ["editing_plans.workspace_id", "editing_plans.id"],
            name="fk_render_jobs_ws_plan",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "output_asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_render_jobs_ws_output_asset",
            ondelete="SET NULL",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_render_jobs_ws_id"),
        Index("ix_render_jobs_ws_project_created", "workspace_id", "project_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    project_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    editing_plan_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    render_kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    renderer: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'mock'"))
    command_manifest: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    output_asset_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    qa_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    qa_report: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpu_time_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class CreativeVersion(Base):
    __tablename__ = "creative_versions"
    __table_args__ = (
        CheckConstraint("render_kind IN ('preview', 'final')", name="ck_creative_versions_kind"),
        CheckConstraint("version_number > 0", name="ck_creative_versions_version"),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_creative_versions_ws_client",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_creative_versions_ws_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "editing_plan_id"],
            ["editing_plans.workspace_id", "editing_plans.id"],
            name="fk_creative_versions_ws_plan",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "render_job_id"],
            ["render_jobs.workspace_id", "render_jobs.id"],
            name="fk_creative_versions_ws_render",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "output_asset_id"],
            ["assets.workspace_id", "assets.id"],
            name="fk_creative_versions_ws_asset",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_creative_versions_ws_id"),
        UniqueConstraint(
            "workspace_id",
            "project_id",
            "version_number",
            name="uq_creative_versions_project_version",
        ),
        Index("ix_creative_versions_ws_project", "workspace_id", "project_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    project_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    editing_plan_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    render_job_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    output_asset_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    render_kind: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    qa_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    qa_report: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "approval_type IN ('strategy', 'creative', 'publishing', "
            "'campaign_execution', 'budget_change')",
            name="ck_approvals_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'revision_requested', 'invalidated')",
            name="ck_approvals_status",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_approvals_ws_client",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_approvals_ws_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "creative_version_id"],
            ["creative_versions.workspace_id", "creative_versions.id"],
            name="fk_approvals_ws_version",
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_approvals_ws_id"),
        Index("ix_approvals_ws_status", "workspace_id", "status"),
        Index("ix_approvals_ws_project", "workspace_id", "project_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    project_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    creative_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    approval_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    target_version_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    decision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    invalidated_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class RevisionRequest(Base):
    __tablename__ = "revision_requests"
    __table_args__ = (
        CheckConstraint("timecode_ms IS NULL OR timecode_ms >= 0", name="ck_revisions_timecode"),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_revision_requests_ws_client",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_revision_requests_ws_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "creative_version_id"],
            ["creative_versions.workspace_id", "creative_versions.id"],
            name="fk_revision_requests_ws_version",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "approval_id"],
            ["approvals.workspace_id", "approvals.id"],
            name="fk_revision_requests_ws_approval",
            ondelete="SET NULL",
        ),
        Index("ix_revision_requests_ws_project", "workspace_id", "project_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    project_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    creative_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    approval_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    requested_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    timecode_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class MetaConnection(Base):
    __tablename__ = "meta_connections"
    __table_args__ = (
        CheckConstraint(
            "connection_type IN ('agency_business', 'creator_professional', "
            "'page_only', 'ads_only')",
            name="ck_meta_connections_type",
        ),
        CheckConstraint(
            "status IN ('active', 'reconnect_required', 'revoked', 'disconnected')",
            name="ck_meta_connections_status",
        ),
        CheckConstraint(
            "token_secret_ref LIKE 'secret://%'",
            name="ck_meta_connections_secret_ref_scheme",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_meta_connections_ws_client",
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_meta_connections_ws_id"),
        Index("ix_meta_connections_ws_client", "workspace_id", "client_id"),
        Index("ix_meta_connections_ws_status", "workspace_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    connection_type: Mapped[str] = mapped_column(Text, nullable=False)
    meta_business_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_id: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    token_secret_ref: Mapped[str] = mapped_column(Text, nullable=False)
    token_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'system_user'")
    )
    granted_scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    instagram_account_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    page_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    ad_account_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    capabilities: Mapped[dict[str, bool]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconnect_required_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reconnect_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class MetaConnectedAsset(Base):
    __tablename__ = "meta_connected_assets"
    __table_args__ = (
        CheckConstraint(
            "asset_type IN ('instagram_professional', 'facebook_page', 'ad_account')",
            name="ck_meta_connected_assets_type",
        ),
        CheckConstraint(
            "instagram_account_type IS NULL OR instagram_account_type IN ('business', 'creator')",
            name="ck_meta_connected_assets_ig_type",
        ),
        CheckConstraint(
            "status IN ('active', 'revoked', 'moved')",
            name="ck_meta_connected_assets_status",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_meta_connected_assets_ws_client",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["meta_connections.workspace_id", "meta_connections.id"],
            name="fk_meta_connected_assets_ws_conn",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "connection_id",
            "asset_type",
            "external_asset_id",
            name="uq_meta_connected_assets_unique",
        ),
        Index("ix_meta_connected_assets_ws_conn", "workspace_id", "connection_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    connection_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    asset_type: Mapped[str] = mapped_column(Text, nullable=False)
    external_asset_id: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    instagram_account_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_business_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_selected: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class MetaWebhookEvent(Base):
    __tablename__ = "meta_webhook_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["meta_connections.workspace_id", "meta_connections.id"],
            name="fk_meta_webhook_events_ws_conn",
            ondelete="SET NULL",
        ),
        Index("ix_meta_webhook_events_ws_created", "workspace_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    provider_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    object_type: Mapped[str] = mapped_column(Text, nullable=False)
    event_field: Mapped[str] = mapped_column(Text, nullable=False)
    signature_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ContentCalendar(Base):
    __tablename__ = "content_calendars"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_content_calendars_ws_client",
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_content_calendars_ws_id"),
        Index("ix_content_calendars_ws_client", "workspace_id", "client_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'UTC'"))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ContentItem(Base):
    __tablename__ = "content_items"
    __table_args__ = (
        CheckConstraint(
            "media_type IN ('reels', 'image', 'video', 'carousel', 'story')",
            name="ck_content_items_media_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'scheduled', 'publishing', 'published', 'failed', 'cancelled')",
            name="ck_content_items_status",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_content_items_ws_client",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "calendar_id"],
            ["content_calendars.workspace_id", "content_calendars.id"],
            name="fk_content_items_ws_calendar",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_content_items_ws_project",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "creative_version_id"],
            ["creative_versions.workspace_id", "creative_versions.id"],
            name="fk_content_items_ws_version",
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "meta_connection_id"],
            ["meta_connections.workspace_id", "meta_connections.id"],
            name="fk_content_items_ws_meta_conn",
            ondelete="SET NULL",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_content_items_ws_id"),
        Index("ix_content_items_ws_scheduled", "workspace_id", "scheduled_for"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    calendar_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    project_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    creative_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    meta_connection_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    external_account_id: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'reels'"))
    caption: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    is_ai_generated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    carousel_asset_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class PublishingJob(Base):
    __tablename__ = "publishing_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'validating', 'container_creating', 'container_ready', "
            "'publishing', 'verifying_remote', 'published', 'failed', 'cancelled')",
            name="ck_publishing_jobs_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_publishing_jobs_attempts"),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_publishing_jobs_ws_client",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "content_item_id"],
            ["content_items.workspace_id", "content_items.id"],
            name="fk_publishing_jobs_ws_content",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "meta_connection_id"],
            ["meta_connections.workspace_id", "meta_connections.id"],
            name="fk_publishing_jobs_ws_conn",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_publishing_jobs_ws_id"),
        Index("ix_publishing_jobs_ws_content", "workspace_id", "content_item_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    content_item_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    meta_connection_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    remote_container_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    remote_media_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    is_ai_generated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    account_limit_quota_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    account_limit_quota_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class PublishingResult(Base):
    __tablename__ = "publishing_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_publishing_results_ws_client",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "publishing_job_id"],
            ["publishing_jobs.workspace_id", "publishing_jobs.id"],
            name="fk_publishing_results_ws_job",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "content_item_id"],
            ["content_items.workspace_id", "content_items.id"],
            name="fk_publishing_results_ws_content",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id",
            "external_account_id",
            "remote_media_id",
            name="uq_publishing_results_remote_media",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    publishing_job_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    content_item_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    external_account_id: Mapped[str] = mapped_column(Text, nullable=False)
    remote_container_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    remote_media_id: Mapped[str] = mapped_column(Text, nullable=False)
    permalink: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_via_remote_check: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    raw_response: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class InsightsSnapshot(Base):
    __tablename__ = "insights_snapshots"
    __table_args__ = (
        CheckConstraint("scope IN ('account', 'media')", name="ck_insights_snapshots_scope"),
        ForeignKeyConstraint(
            ["workspace_id", "client_id"],
            ["clients.workspace_id", "clients.id"],
            name="fk_insights_snapshots_ws_client",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "meta_connection_id"],
            ["meta_connections.workspace_id", "meta_connections.id"],
            name="fk_insights_snapshots_ws_conn",
            ondelete="CASCADE",
        ),
        Index(
            "ix_insights_snapshots_ws_client_captured",
            "workspace_id",
            "client_id",
            "captured_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    client_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    meta_connection_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    external_account_id: Mapped[str] = mapped_column(Text, nullable=False)
    remote_media_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    period: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'days_28'"))
    impressions: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    reach: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    likes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    comments: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    saves: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    shares: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    plays: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    follower_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
