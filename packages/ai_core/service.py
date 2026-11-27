"""Tenant-scoped AI worker job execution, idempotency, budget enforcement, and usage ledger."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from database.models import (
    BrandProfile,
    ModelRun,
    ResearchReport,
    ResearchSource,
    UsageRecord,
    WorkerJob,
    WorkspaceBudget,
)
from packages.ai_core.contracts import (
    UsageBreakdownItem,
    UsageSummaryOut,
    WorkerJobEnvelope,
    WorkerJobOut,
    WorkspaceBudgetOut,
)
from packages.auth.rbac import require_permission
from packages.muse_adapter import MuseAdapter
from packages.shared.context import TenantContext
from packages.shared.enums import (
    AuditAction,
    ErrorCode,
    WorkerJobStatus,
    WorkerTaskType,
)
from packages.shared.errors import (
    BudgetExceededError,
    ConflictError,
    NotFoundError,
    ProviderDisabledError,
    ValidationDomainError,
    WorkspaceContextRequiredError,
)
from packages.shared.events import AuditEventPayload
from packages.shared.scrubbing import scrub_secrets
from packages.skill_runtime import SkillRegistry, route_task
from packages.tenancy.audit import record_audit_log
from packages.tenancy.clients import CLIENT_SCOPED_ROLES, assert_client_readable
from workers.media_pack import run_media_pack_worker
from workers.research import run_research_worker
from workers.writing_seo_aeo import run_writing_seo_aeo_worker


def _current_billing_period() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _job_to_out(row: WorkerJob) -> WorkerJobOut:
    return WorkerJobOut(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        actor_user_id=row.actor_user_id,
        brand_profile_id=row.brand_profile_id,
        task_type=WorkerTaskType(row.task_type),
        skill_id=row.skill_id,
        skill_version=row.skill_version,
        status=WorkerJobStatus(row.status),
        idempotency_key=row.idempotency_key,
        requested_output_schema=row.requested_output_schema,
        input_payload=row.input_payload,
        output_payload=row.output_payload,
        warnings=list(row.warnings or []),
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


def _budget_to_out(row: WorkspaceBudget) -> WorkspaceBudgetOut:
    return WorkspaceBudgetOut(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        monthly_unit_limit=row.monthly_unit_limit,
        monthly_cost_limit_usd=row.monthly_cost_limit_usd,
        provider_kill_switch=row.provider_kill_switch,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def enforce_workspace_budget(
    session: Session,
    *,
    workspace_id: UUID,
    client_id: UUID | None = None,
    billing_period: str | None = None,
) -> None:
    """Enforce kill switch and monthly unit/cost budgets before executing any model call."""
    period = billing_period or _current_billing_period()

    budgets = (
        session.execute(
            select(WorkspaceBudget).where(
                WorkspaceBudget.workspace_id == workspace_id,
                (WorkspaceBudget.client_id.is_(None)) | (WorkspaceBudget.client_id == client_id),
            )
        )
        .scalars()
        .all()
    )
    if not budgets:
        return

    for budget in budgets:
        if budget.provider_kill_switch:
            raise ProviderDisabledError(
                "AI provider execution is disabled by workspace/client kill switch",
                details={"workspace_id": str(workspace_id), "client_id": str(budget.client_id)},
            )

        usage_stmt = select(
            func.coalesce(func.sum(UsageRecord.input_units + UsageRecord.output_units), 0),
            func.coalesce(func.sum(UsageRecord.estimated_cost_usd), Decimal("0")),
        ).where(
            UsageRecord.workspace_id == workspace_id,
            UsageRecord.billing_period == period,
        )
        if budget.client_id is not None:
            usage_stmt = usage_stmt.where(UsageRecord.client_id == budget.client_id)

        used_units_raw, used_cost_raw = session.execute(usage_stmt).one()
        used_units = int(used_units_raw or 0)
        used_cost = Decimal(str(used_cost_raw or 0))

        if budget.monthly_unit_limit is not None and used_units >= budget.monthly_unit_limit:
            raise BudgetExceededError(
                f"Monthly AI unit budget ({budget.monthly_unit_limit}) reached for period {period}",
                details={"billing_period": period, "used_units": used_units},
            )
        if budget.monthly_cost_limit_usd is not None and used_cost >= budget.monthly_cost_limit_usd:
            raise BudgetExceededError(
                f"Monthly AI estimated cost budget (${budget.monthly_cost_limit_usd}) reached "
                f"for period {period}",
                details={"billing_period": period, "used_cost_usd": str(used_cost)},
            )


def execute_specialist_job(
    session: Session,
    ctx: TenantContext,
    *,
    client_id: UUID,
    task_type: WorkerTaskType,
    topic: str,
    constraints: dict[str, Any] | None = None,
    input_asset_ids: list[UUID] | None = None,
    idempotency_key: str | None = None,
    skill_registry: SkillRegistry,
    muse_adapter: MuseAdapter,
) -> WorkerJobOut:
    """Create, route, execute, validate, and attribute a tenant-scoped specialist worker job."""
    if ctx.workspace_id is None or ctx.role_name is None or ctx.user_id is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")
    require_permission(ctx.role_name, "jobs:write")

    cleaned_topic = topic.strip()
    if not cleaned_topic:
        raise ValidationDomainError("Job topic must not be empty")

    client = assert_client_readable(session, ctx, client_id)

    cleaned_idem = idempotency_key.strip() if idempotency_key and idempotency_key.strip() else None
    if cleaned_idem is not None:
        existing = session.execute(
            select(WorkerJob).where(
                WorkerJob.workspace_id == ctx.workspace_id,
                WorkerJob.idempotency_key == cleaned_idem,
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.task_type != task_type.value or existing.client_id != client_id:
                raise ConflictError(
                    "Idempotency key was already used for a different task or client",
                    code=ErrorCode.IDEMPOTENCY_CONFLICT,
                )
            return _job_to_out(existing)

    route = route_task(task_type)
    skill_doc = skill_registry.get_skill(route.skill_id, version=route.skill_version)

    # Enforce budget & kill switch BEFORE creating/executing the model call
    enforce_workspace_budget(session, workspace_id=ctx.workspace_id, client_id=client_id)

    brand = session.execute(
        select(BrandProfile).where(
            BrandProfile.workspace_id == ctx.workspace_id,
            BrandProfile.client_id == client.id,
        )
    ).scalar_one_or_none()

    brand_context: dict[str, Any] = {
        "brand_name": brand.brand_name if brand else client.name,
        "tone_of_voice": brand.tone_of_voice if brand else None,
        "target_audience": brand.target_audience if brand else None,
        "color_palette": brand.color_palette if brand else [],
        "guidelines": brand.guidelines if brand else None,
    }

    safe_constraints_raw = scrub_secrets(constraints or {})
    safe_constraints = safe_constraints_raw if isinstance(safe_constraints_raw, dict) else {}
    now = datetime.now(UTC)

    job = WorkerJob(
        id=uuid4(),
        workspace_id=ctx.workspace_id,
        client_id=client.id,
        actor_user_id=ctx.user_id,
        brand_profile_id=brand.id if brand else None,
        task_type=route.task_type.value,
        skill_id=skill_doc.skill_id,
        skill_version=skill_doc.version,
        status=WorkerJobStatus.RUNNING.value,
        idempotency_key=cleaned_idem,
        requested_output_schema=route.requested_output_schema,
        input_payload={
            "topic": cleaned_topic,
            "constraints": safe_constraints,
            "input_asset_ids": [str(a) for a in (input_asset_ids or [])],
        },
        started_at=now,
    )
    session.add(job)
    session.flush()

    record_audit_log(
        session,
        AuditEventPayload(
            action=AuditAction.WORKER_JOB_CREATED,
            workspace_id=ctx.workspace_id,
            client_id=client.id,
            actor_user_id=ctx.user_id,
            target_type="worker_job",
            target_id=str(job.id),
            request_id=ctx.request_id,
            metadata={
                "task_type": route.task_type.value,
                "skill_id": skill_doc.skill_id,
                "skill_version": skill_doc.version,
            },
        ),
    )

    envelope = WorkerJobEnvelope(
        job_id=job.id,
        workspace_id=ctx.workspace_id,
        client_id=client.id,
        actor_user_id=ctx.user_id,
        task_type=route.task_type,
        input_asset_ids=list(input_asset_ids or []),
        brand_profile_id=brand.id if brand else None,
        skill_id=skill_doc.skill_id,
        skill_version=skill_doc.version,
        constraints=safe_constraints,
        requested_output_schema=route.requested_output_schema,
    )

    research_artifact = None
    if route.task_type == WorkerTaskType.RESEARCH:
        worker_result, research_artifact, latency_ms, prompt_version = run_research_worker(
            envelope=envelope,
            topic=cleaned_topic,
            brand_context=brand_context,
            skill_doc=skill_doc,
            muse_adapter=muse_adapter,
        )
    elif route.task_type in {WorkerTaskType.WRITING, WorkerTaskType.SEO_AEO}:
        worker_result, latency_ms, prompt_version = run_writing_seo_aeo_worker(
            envelope=envelope,
            topic=cleaned_topic,
            brand_context=brand_context,
            skill_doc=skill_doc,
            muse_adapter=muse_adapter,
        )
    else:
        worker_result, latency_ms, prompt_version = run_media_pack_worker(
            envelope=envelope,
            topic=cleaned_topic,
            brand_context=brand_context,
            skill_doc=skill_doc,
            muse_adapter=muse_adapter,
        )

    est_cost = worker_result.usage.estimated_cost or Decimal("0")
    model_run = ModelRun(
        id=uuid4(),
        workspace_id=ctx.workspace_id,
        client_id=client.id,
        worker_job_id=job.id,
        provider=worker_result.usage.provider,
        model=worker_result.usage.model,
        capability=route.required_capability.value,
        prompt_version=prompt_version,
        schema_version=route.requested_output_schema,
        status="succeeded",
        latency_ms=latency_ms,
        input_units=worker_result.usage.input_units,
        output_units=worker_result.usage.output_units,
        estimated_cost_usd=est_cost,
        metadata_json={"worker": route.worker_name},
    )
    session.add(model_run)
    session.flush()

    usage_rec = UsageRecord(
        id=uuid4(),
        workspace_id=ctx.workspace_id,
        client_id=client.id,
        worker_job_id=job.id,
        model_run_id=model_run.id,
        provider=worker_result.usage.provider,
        model=worker_result.usage.model,
        capability=route.required_capability.value,
        input_units=worker_result.usage.input_units,
        output_units=worker_result.usage.output_units,
        estimated_cost_usd=est_cost,
        billing_period=_current_billing_period(),
    )
    session.add(usage_rec)

    if research_artifact is not None:
        report_row = ResearchReport(
            id=uuid4(),
            workspace_id=ctx.workspace_id,
            client_id=client.id,
            worker_job_id=job.id,
            topic=research_artifact.topic,
            summary=research_artifact.summary,
            findings=[f.model_dump(mode="json") for f in research_artifact.verified_findings],
            inferences=[inf.model_dump(mode="json") for inf in research_artifact.inferences],
            confidence_overall=Decimal(str(round(research_artifact.confidence_overall, 3))),
        )
        session.add(report_row)
        session.flush()
        for finding in research_artifact.verified_findings:
            session.add(
                ResearchSource(
                    id=uuid4(),
                    workspace_id=ctx.workspace_id,
                    research_report_id=report_row.id,
                    url=finding.source_url,
                    title=finding.source_title,
                    published_date=finding.published_date,
                    claim=finding.claim,
                    confidence=Decimal(str(round(finding.confidence, 3))),
                    is_verified=True,
                )
            )

    job.status = worker_result.status
    job.output_payload = worker_result.model_dump(mode="json")
    job.warnings = list(worker_result.warnings)
    job.completed_at = datetime.now(UTC)
    session.flush()

    record_audit_log(
        session,
        AuditEventPayload(
            action=AuditAction.WORKER_JOB_COMPLETED,
            workspace_id=ctx.workspace_id,
            client_id=client.id,
            actor_user_id=ctx.user_id,
            target_type="worker_job",
            target_id=str(job.id),
            request_id=ctx.request_id,
            metadata={
                "provider": worker_result.usage.provider,
                "model": worker_result.usage.model,
                "input_units": worker_result.usage.input_units,
                "output_units": worker_result.usage.output_units,
                "estimated_cost_usd": str(est_cost),
            },
        ),
    )
    return _job_to_out(job)


def get_worker_job(session: Session, ctx: TenantContext, *, job_id: UUID) -> WorkerJobOut:
    """Fetch a single tenant-scoped worker job under RLS and client membership checks."""
    if ctx.workspace_id is None or ctx.role_name is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")
    require_permission(ctx.role_name, "jobs:read")

    job = session.execute(
        select(WorkerJob).where(
            WorkerJob.workspace_id == ctx.workspace_id,
            WorkerJob.id == job_id,
        )
    ).scalar_one_or_none()
    if job is None:
        raise NotFoundError("Worker job not found")

    if ctx.role_name in CLIENT_SCOPED_ROLES and job.client_id is not None:
        assert_client_readable(session, ctx, job.client_id)

    return _job_to_out(job)


def list_worker_jobs(
    session: Session,
    ctx: TenantContext,
    *,
    client_id: UUID | None = None,
    limit: int = 25,
) -> list[WorkerJobOut]:
    """List recent tenant-scoped worker jobs (filtered by client if provided)."""
    if ctx.workspace_id is None or ctx.role_name is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")
    require_permission(ctx.role_name, "jobs:read")

    if client_id is not None:
        assert_client_readable(session, ctx, client_id)

    bounded = max(1, min(int(limit), 100))
    stmt = select(WorkerJob).where(WorkerJob.workspace_id == ctx.workspace_id)
    if client_id is not None:
        stmt = stmt.where(WorkerJob.client_id == client_id)
    rows = session.execute(stmt.order_by(desc(WorkerJob.created_at)).limit(bounded)).scalars().all()
    return [_job_to_out(r) for r in rows]


def get_usage_summary(
    session: Session,
    ctx: TenantContext,
    *,
    client_id: UUID | None = None,
    billing_period: str | None = None,
) -> UsageSummaryOut:
    """Aggregate workspace/client AI usage and estimated cost for a billing period."""
    if ctx.workspace_id is None or ctx.role_name is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")
    require_permission(ctx.role_name, "usage:read")

    if client_id is not None:
        assert_client_readable(session, ctx, client_id)

    period = (billing_period or _current_billing_period()).strip()
    stmt = (
        select(
            UsageRecord.provider,
            UsageRecord.model,
            UsageRecord.capability,
            func.coalesce(func.sum(UsageRecord.input_units), 0).label("in_u"),
            func.coalesce(func.sum(UsageRecord.output_units), 0).label("out_u"),
            func.coalesce(func.sum(UsageRecord.estimated_cost_usd), Decimal("0")).label("cost"),
            func.count(UsageRecord.id).label("runs"),
        )
        .where(
            UsageRecord.workspace_id == ctx.workspace_id,
            UsageRecord.billing_period == period,
        )
        .group_by(UsageRecord.provider, UsageRecord.model, UsageRecord.capability)
    )
    if client_id is not None:
        stmt = stmt.where(UsageRecord.client_id == client_id)

    rows = session.execute(stmt).all()
    breakdown = [
        UsageBreakdownItem(
            provider=str(r.provider),
            model=str(r.model),
            capability=str(r.capability),
            total_input_units=int(r.in_u or 0),
            total_output_units=int(r.out_u or 0),
            total_estimated_cost_usd=Decimal(str(r.cost or 0)),
            run_count=int(r.runs or 0),
        )
        for r in rows
    ]
    return UsageSummaryOut(
        workspace_id=ctx.workspace_id,
        client_id=client_id,
        billing_period=period,
        total_input_units=sum(b.total_input_units for b in breakdown),
        total_output_units=sum(b.total_output_units for b in breakdown),
        total_estimated_cost_usd=sum((b.total_estimated_cost_usd for b in breakdown), Decimal("0")),
        breakdown=breakdown,
    )


def upsert_workspace_budget(
    session: Session,
    ctx: TenantContext,
    *,
    client_id: UUID | None = None,
    monthly_unit_limit: int | None = None,
    monthly_cost_limit_usd: Decimal | None = None,
    provider_kill_switch: bool = False,
) -> WorkspaceBudgetOut:
    """Create or update a workspace-level or client-level AI usage budget & kill switch."""
    if ctx.workspace_id is None or ctx.role_name is None or ctx.user_id is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")
    require_permission(ctx.role_name, "budgets:manage")

    if client_id is not None:
        assert_client_readable(session, ctx, client_id)

    stmt = select(WorkspaceBudget).where(WorkspaceBudget.workspace_id == ctx.workspace_id)
    stmt = (
        stmt.where(WorkspaceBudget.client_id.is_(None))
        if client_id is None
        else stmt.where(WorkspaceBudget.client_id == client_id)
    )
    budget = session.execute(stmt).scalar_one_or_none()
    if budget is None:
        budget = WorkspaceBudget(
            id=uuid4(),
            workspace_id=ctx.workspace_id,
            client_id=client_id,
            monthly_unit_limit=monthly_unit_limit,
            monthly_cost_limit_usd=monthly_cost_limit_usd,
            provider_kill_switch=provider_kill_switch,
        )
        session.add(budget)
    else:
        budget.monthly_unit_limit = monthly_unit_limit
        budget.monthly_cost_limit_usd = monthly_cost_limit_usd
        budget.provider_kill_switch = provider_kill_switch
    session.flush()

    record_audit_log(
        session,
        AuditEventPayload(
            action=AuditAction.WORKSPACE_BUDGET_UPDATED,
            workspace_id=ctx.workspace_id,
            client_id=client_id,
            actor_user_id=ctx.user_id,
            target_type="workspace_budget",
            target_id=str(budget.id),
            request_id=ctx.request_id,
            metadata={
                "monthly_unit_limit": monthly_unit_limit,
                "monthly_cost_limit_usd": (
                    str(monthly_cost_limit_usd) if monthly_cost_limit_usd is not None else None
                ),
                "provider_kill_switch": provider_kill_switch,
            },
        ),
    )
    return _budget_to_out(budget)
