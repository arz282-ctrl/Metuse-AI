"""Tenant-scoped Cloud Editing, Upload Validation, Timeline & Render endpoints (Section 11)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
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
from packages.media_engine.contracts import (
    CreativeVersionView,
    EditingPlanView,
    MediaMetadataView,
    RenderJobView,
    TimelinePlan,
    UploadUrlResponse,
)
from packages.shared.context import TenantContext
from packages.shared.enums import AssetKind, RenderKind
from packages.shared.errors import ValidationDomainError, WorkspaceAccessDeniedError
from packages.tenancy import tenant_transaction
from packages.video_gateway.contracts import AssetView, ProjectView

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}", tags=["editing"])


class CreateUploadUrlPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=3, max_length=128)
    size_bytes: int = Field(gt=0)
    asset_kind: AssetKind = AssetKind.VIDEO
    client_id: UUID | None = None
    project_id: UUID | None = None


class CompleteUploadPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: UUID


class CompleteUploadOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset: AssetView
    metadata: MediaMetadataView


class RenderTriggerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    editing_plan_id: UUID | None = None
    expect_audio: bool = True


class RenderTriggerOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    render_job: RenderJobView
    creative_version: CreativeVersionView | None = None


def _require_ws_perm(ctx: TenantContext, permission: str) -> None:
    if ctx.role_name is None:
        raise WorkspaceAccessDeniedError("Access to the requested workspace is denied")
    require_permission(ctx.role_name, permission)


@router.post(
    "/assets/upload-url", response_model=UploadUrlResponse, status_code=status.HTTP_201_CREATED
)
def post_asset_upload_url(
    workspace_id: UUID,
    body: CreateUploadUrlPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> UploadUrlResponse:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "assets:write")
        return container.editing_service.create_upload_url(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            filename=body.filename,
            mime_type=body.mime_type,
            size_bytes=body.size_bytes,
            asset_kind=body.asset_kind,
            client_id=body.client_id,
            project_id=body.project_id,
            request_id=request_id,
        )


@router.post("/assets/complete-upload", response_model=CompleteUploadOut)
def post_asset_complete_upload(
    workspace_id: UUID,
    body: CompleteUploadPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> CompleteUploadOut:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "assets:write")
        asset, metadata = container.editing_service.complete_upload(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            asset_id=body.asset_id,
            request_id=request_id,
        )
        return CompleteUploadOut(asset=asset, metadata=metadata)


@router.get("/projects/{project_id}", response_model=ProjectView)
def get_project_detail(
    workspace_id: UUID,
    project_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> ProjectView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "projects:read")
        return container.editing_service.get_project(
            session, workspace_id=workspace_id, project_id=project_id
        )


@router.post("/projects/{project_id}/analyze", response_model=list[MediaMetadataView])
def post_analyze_project(
    workspace_id: UUID,
    project_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> list[MediaMetadataView]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "editing:write")
        return container.editing_service.analyze_project(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            project_id=project_id,
            request_id=request_id,
        )


@router.post(
    "/projects/{project_id}/editing-plan",
    response_model=EditingPlanView,
    status_code=status.HTTP_201_CREATED,
)
def post_editing_plan(
    workspace_id: UUID,
    project_id: UUID,
    body: TimelinePlan,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> EditingPlanView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "editing:write")
        return container.editing_service.create_editing_plan(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            project_id=project_id,
            plan=body,
            request_id=request_id,
        )


@router.get("/projects/{project_id}/editing-plans", response_model=list[EditingPlanView])
def get_editing_plans(
    workspace_id: UUID,
    project_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> list[EditingPlanView]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "editing:read")
        return container.editing_service.list_editing_plans(
            session, workspace_id=workspace_id, project_id=project_id
        )


def _trigger_render(
    *,
    workspace_id: UUID,
    project_id: UUID,
    render_kind: RenderKind,
    body: RenderTriggerPayload,
    actor: AuthenticatedActor,
    session_factory: sessionmaker[Session],
    container: AppContainer,
    request_id: UUID,
    idempotency_key: str | None,
) -> RenderTriggerOut:
    cleaned_key = idempotency_key.strip() if idempotency_key else None
    if cleaned_key and len(cleaned_key) > 128:
        raise ValidationDomainError("Idempotency-Key must not exceed 128 characters")
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "editing:write")
        job, version = container.editing_service.render_project(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            project_id=project_id,
            render_kind=render_kind,
            editing_plan_id=body.editing_plan_id,
            idempotency_key=cleaned_key,
            expect_audio=body.expect_audio,
            request_id=request_id,
        )
        return RenderTriggerOut(render_job=job, creative_version=version)


@router.post(
    "/projects/{project_id}/render-preview",
    response_model=RenderTriggerOut,
    status_code=status.HTTP_201_CREATED,
)
def post_render_preview(
    workspace_id: UUID,
    project_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    body: RenderTriggerPayload | None = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RenderTriggerOut:
    return _trigger_render(
        workspace_id=workspace_id,
        project_id=project_id,
        render_kind=RenderKind.PREVIEW,
        body=body or RenderTriggerPayload(),
        actor=actor,
        session_factory=session_factory,
        container=container,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )


@router.post(
    "/projects/{project_id}/render-final",
    response_model=RenderTriggerOut,
    status_code=status.HTTP_201_CREATED,
)
def post_render_final(
    workspace_id: UUID,
    project_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    body: RenderTriggerPayload | None = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RenderTriggerOut:
    return _trigger_render(
        workspace_id=workspace_id,
        project_id=project_id,
        render_kind=RenderKind.FINAL,
        body=body or RenderTriggerPayload(),
        actor=actor,
        session_factory=session_factory,
        container=container,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )


@router.get("/render-jobs/{job_id}", response_model=RenderJobView)
def get_render_job(
    workspace_id: UUID,
    job_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> RenderJobView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "editing:read")
        return container.editing_service.get_render_job(
            session, workspace_id=workspace_id, job_id=job_id
        )


@router.get("/projects/{project_id}/versions", response_model=list[CreativeVersionView])
def get_project_versions(
    workspace_id: UUID,
    project_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> list[CreativeVersionView]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "editing:read")
        return container.editing_service.list_project_versions(
            session, workspace_id=workspace_id, project_id=project_id
        )
