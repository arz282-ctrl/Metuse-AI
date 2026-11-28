"""Tenant-scoped Content Calendar, Publishing, and Organic Insights routes (Section 11)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from apps.api.deps import (
    AppContainer,
    AuthenticatedActor,
    get_container,
    get_current_actor,
    get_request_id,
    get_session_factory,
)
from packages.auth import require_permission
from packages.meta_integrations.publishing import (
    AnalyticsSummaryView,
    ContentCalendarView,
    ContentItemView,
    InsightsSnapshotView,
    PublishingJobView,
)
from packages.shared.context import TenantContext
from packages.shared.enums import PublishMediaType
from packages.shared.errors import ValidationDomainError, WorkspaceAccessDeniedError
from packages.tenancy import tenant_transaction

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}", tags=["publishing", "analytics"])


class CreateCalendarPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    client_id: UUID | None = None
    timezone: str = Field(default="UTC", max_length=64)


class CreateContentItemPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta_connection_id: UUID
    external_account_id: str = Field(min_length=1, max_length=128)
    creative_version_id: UUID
    caption: str = Field(default="", max_length=2200)
    media_type: PublishMediaType = PublishMediaType.REELS
    is_ai_generated: bool = False
    carousel_asset_ids: list[UUID] = Field(default_factory=list)
    client_id: UUID | None = None
    calendar_id: UUID | None = None


class ScheduleContentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheduled_for: datetime


def _require_ws_perm(ctx: TenantContext, permission: str) -> None:
    if ctx.role_name is None:
        raise WorkspaceAccessDeniedError("Access to the requested workspace is denied")
    require_permission(ctx.role_name, permission)


@router.post("/calendars", response_model=ContentCalendarView, status_code=status.HTTP_201_CREATED)
def post_calendar(
    workspace_id: UUID,
    body: CreateCalendarPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> ContentCalendarView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "publishing:write")
        return container.publishing_service.create_calendar(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            name=body.name,
            client_id=body.client_id,
            timezone=body.timezone,
        )


@router.get("/calendars", response_model=list[ContentCalendarView])
def get_calendars(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    client_id: Annotated[UUID | None, Query()] = None,
) -> list[ContentCalendarView]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "publishing:read")
        return container.publishing_service.list_calendars(
            session, workspace_id=workspace_id, client_id=client_id
        )


@router.post("/content", response_model=ContentItemView, status_code=status.HTTP_201_CREATED)
def post_content_item(
    workspace_id: UUID,
    body: CreateContentItemPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> ContentItemView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "publishing:write")
        return container.publishing_service.create_content_item(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            meta_connection_id=body.meta_connection_id,
            external_account_id=body.external_account_id,
            creative_version_id=body.creative_version_id,
            caption=body.caption,
            media_type=body.media_type,
            is_ai_generated=body.is_ai_generated,
            carousel_asset_ids=body.carousel_asset_ids,
            client_id=body.client_id,
            calendar_id=body.calendar_id,
            request_id=request_id,
        )


@router.get("/content", response_model=list[ContentItemView])
def get_content_items(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    client_id: Annotated[UUID | None, Query()] = None,
) -> list[ContentItemView]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "publishing:read")
        return container.publishing_service.list_content_items(
            session, workspace_id=workspace_id, client_id=client_id
        )


@router.post("/content/{content_id}/schedule", response_model=ContentItemView)
def post_schedule_content(
    workspace_id: UUID,
    content_id: UUID,
    body: ScheduleContentPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> ContentItemView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "publishing:write")
        return container.publishing_service.schedule_content_item(
            session,
            workspace_id=workspace_id,
            content_id=content_id,
            actor_user_id=actor.user_id,
            scheduled_for=body.scheduled_for,
            request_id=request_id,
        )


@router.post(
    "/content/{content_id}/publish",
    response_model=PublishingJobView,
    status_code=status.HTTP_201_CREATED,
)
def post_publish_content(
    workspace_id: UUID,
    content_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PublishingJobView:
    cleaned_key = idempotency_key.strip() if idempotency_key else None
    if cleaned_key and len(cleaned_key) > 128:
        raise ValidationDomainError("Idempotency-Key must not exceed 128 characters")
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "publishing:write")
        return container.publishing_service.publish_content_item(
            session,
            workspace_id=workspace_id,
            content_id=content_id,
            actor_user_id=actor.user_id,
            idempotency_key=cleaned_key,
            request_id=request_id,
        )


@router.get("/publishing-jobs/{job_id}", response_model=PublishingJobView)
def get_publishing_job(
    workspace_id: UUID,
    job_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> PublishingJobView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "publishing:read")
        return container.publishing_service.get_publishing_job(
            session, workspace_id=workspace_id, job_id=job_id
        )


@router.get("/clients/{client_id}/analytics", response_model=AnalyticsSummaryView)
def get_client_analytics(
    workspace_id: UUID,
    client_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> AnalyticsSummaryView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "analytics:read")
        return container.publishing_service.get_client_analytics(
            session, workspace_id=workspace_id, client_id=client_id
        )


@router.post("/clients/{client_id}/analytics/sync", response_model=list[InsightsSnapshotView])
def post_client_analytics_sync(
    workspace_id: UUID,
    client_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> list[InsightsSnapshotView]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "analytics:sync")
        return container.publishing_service.sync_client_insights(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            client_id=client_id,
            request_id=request_id,
        )


@router.get("/analytics", response_model=AnalyticsSummaryView)
def get_workspace_analytics(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> AnalyticsSummaryView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "analytics:read")
        return container.publishing_service.get_client_analytics(
            session, workspace_id=workspace_id, client_id=None
        )


@router.post("/analytics/sync", response_model=list[InsightsSnapshotView])
def post_workspace_analytics_sync(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> list[InsightsSnapshotView]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "analytics:sync")
        return container.publishing_service.sync_client_insights(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            client_id=None,
            request_id=request_id,
        )
