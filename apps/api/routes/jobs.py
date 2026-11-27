"""Tenant-scoped specialist AI worker job, skill catalog, budget, and usage endpoints."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from apps.api.deps import (
    AppContainer,
    AuthenticatedActor,
    get_container,
    get_current_actor,
    get_request_id,
    get_session_factory,
)
from packages.ai_core import (
    UsageSummaryOut,
    WorkerJobOut,
    WorkspaceBudgetOut,
    execute_specialist_job,
    get_usage_summary,
    get_worker_job,
    list_worker_jobs,
    upsert_workspace_budget,
)
from packages.shared.enums import WorkerTaskType
from packages.tenancy import tenant_transaction

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}", tags=["ai-jobs"])


class SpecialistJobRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=400)
    constraints: dict[str, Any] = Field(default_factory=dict)
    input_asset_ids: list[UUID] = Field(default_factory=list)


class UpsertBudgetRequest(BaseModel):
    client_id: UUID | None = None
    monthly_unit_limit: int | None = Field(default=None, ge=0)
    monthly_cost_limit_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    provider_kill_switch: bool = False


class SkillCatalogItemOut(BaseModel):
    skill_id: str
    version: str
    display_name: str
    description: str
    content_sha256: str


def _dispatch_job(
    *,
    workspace_id: UUID,
    client_id: UUID,
    task_type: WorkerTaskType,
    body: SpecialistJobRequest,
    idempotency_key: str | None,
    actor: AuthenticatedActor,
    session_factory: sessionmaker[Session],
    container: AppContainer,
    request_id: UUID,
) -> WorkerJobOut:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        return execute_specialist_job(
            session,
            ctx,
            client_id=client_id,
            task_type=task_type,
            topic=body.topic,
            constraints=body.constraints,
            input_asset_ids=body.input_asset_ids,
            idempotency_key=idempotency_key,
            skill_registry=container.skill_registry,
            muse_adapter=container.muse_adapter,
        )


@router.get("/skills", response_model=list[SkillCatalogItemOut])
def list_available_skills(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> list[SkillCatalogItemOut]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ):
        return [
            SkillCatalogItemOut(
                skill_id=doc.skill_id,
                version=doc.version,
                display_name=doc.display_name,
                description=doc.description,
                content_sha256=doc.content_sha256,
            )
            for doc in container.skill_registry.list_skills()
        ]


@router.post(
    "/clients/{client_id}/research-jobs",
    response_model=WorkerJobOut,
    status_code=status.HTTP_201_CREATED,
)
def post_research_job(
    workspace_id: UUID,
    client_id: UUID,
    body: SpecialistJobRequest,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> WorkerJobOut:
    return _dispatch_job(
        workspace_id=workspace_id,
        client_id=client_id,
        task_type=WorkerTaskType.RESEARCH,
        body=body,
        idempotency_key=idempotency_key,
        actor=actor,
        session_factory=session_factory,
        container=container,
        request_id=request_id,
    )


@router.post(
    "/clients/{client_id}/writing-jobs",
    response_model=WorkerJobOut,
    status_code=status.HTTP_201_CREATED,
)
def post_writing_job(
    workspace_id: UUID,
    client_id: UUID,
    body: SpecialistJobRequest,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> WorkerJobOut:
    return _dispatch_job(
        workspace_id=workspace_id,
        client_id=client_id,
        task_type=WorkerTaskType.WRITING,
        body=body,
        idempotency_key=idempotency_key,
        actor=actor,
        session_factory=session_factory,
        container=container,
        request_id=request_id,
    )


@router.post(
    "/clients/{client_id}/seo-aeo-jobs",
    response_model=WorkerJobOut,
    status_code=status.HTTP_201_CREATED,
)
def post_seo_aeo_job(
    workspace_id: UUID,
    client_id: UUID,
    body: SpecialistJobRequest,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> WorkerJobOut:
    return _dispatch_job(
        workspace_id=workspace_id,
        client_id=client_id,
        task_type=WorkerTaskType.SEO_AEO,
        body=body,
        idempotency_key=idempotency_key,
        actor=actor,
        session_factory=session_factory,
        container=container,
        request_id=request_id,
    )


@router.post(
    "/clients/{client_id}/media-plan-jobs",
    response_model=WorkerJobOut,
    status_code=status.HTTP_201_CREATED,
)
def post_media_plan_job(
    workspace_id: UUID,
    client_id: UUID,
    body: SpecialistJobRequest,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> WorkerJobOut:
    return _dispatch_job(
        workspace_id=workspace_id,
        client_id=client_id,
        task_type=WorkerTaskType.MEDIA_PLAN,
        body=body,
        idempotency_key=idempotency_key,
        actor=actor,
        session_factory=session_factory,
        container=container,
        request_id=request_id,
    )


@router.get("/jobs", response_model=list[WorkerJobOut])
def get_workspace_jobs(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    client_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> list[WorkerJobOut]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        return list_worker_jobs(session, ctx, client_id=client_id, limit=limit)


@router.get("/jobs/{job_id}", response_model=WorkerJobOut)
def get_workspace_job(
    workspace_id: UUID,
    job_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> WorkerJobOut:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        return get_worker_job(session, ctx, job_id=job_id)


@router.get("/usage", response_model=UsageSummaryOut)
def get_workspace_usage(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    client_id: Annotated[UUID | None, Query()] = None,
    billing_period: Annotated[str | None, Query()] = None,
) -> UsageSummaryOut:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        return get_usage_summary(
            session,
            ctx,
            client_id=client_id,
            billing_period=billing_period,
        )


@router.put("/budgets", response_model=WorkspaceBudgetOut)
def put_workspace_budget(
    workspace_id: UUID,
    body: UpsertBudgetRequest,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> WorkspaceBudgetOut:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        return upsert_workspace_budget(
            session,
            ctx,
            client_id=body.client_id,
            monthly_unit_limit=body.monthly_unit_limit,
            monthly_cost_limit_usd=body.monthly_cost_limit_usd,
            provider_kill_switch=body.provider_kill_switch,
        )
