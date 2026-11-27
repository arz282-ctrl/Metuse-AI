"""Tenant-scoped Unified Video Generation Gateway endpoints and provider webhooks."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
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
from packages.shared.context import TenantContext
from packages.shared.enums import VideoGenerationMode
from packages.shared.errors import ValidationDomainError, WorkspaceAccessDeniedError
from packages.tenancy import rls_transaction, tenant_transaction
from packages.video_gateway.contracts import (
    NormalizedVideoRequest,
    VideoAudioConfig,
    VideoJobView,
    VideoModelCapability,
    VideoProviderDescriptor,
)

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/video", tags=["video"])


class CreateVideoJobPayload(BaseModel):
    """Provider-independent video generation request payload (Section 6.3)."""

    model_config = ConfigDict(extra="forbid")

    mode: VideoGenerationMode
    prompt: str = Field(min_length=3, max_length=4000)
    client_id: UUID | None = None
    project_id: UUID | None = None
    reference_asset_ids: list[UUID] = Field(default_factory=list)
    aspect_ratio: str = Field(default="9:16", min_length=3, max_length=16)
    duration_seconds: int = Field(default=5, ge=1, le=60)
    resolution: str = Field(default="720p", min_length=2, max_length=32)
    audio: VideoAudioConfig = Field(default_factory=VideoAudioConfig)
    brand_constraints: dict[str, Any] = Field(default_factory=dict)
    provider_preference: str = Field(default="auto", min_length=1, max_length=64)
    model_preference: str | None = Field(default=None, max_length=128)
    quality_preference: Literal["lowest_cost", "high_quality"] = "lowest_cost"
    idempotency_key: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_mode_references(self) -> CreateVideoJobPayload:
        if self.mode == VideoGenerationMode.IMAGE_TO_VIDEO and not self.reference_asset_ids:
            raise ValueError("image_to_video requires at least one reference_asset_id")
        return self


def _require_workspace_permission(ctx: TenantContext, permission: str) -> None:
    if ctx.role_name is None:
        raise WorkspaceAccessDeniedError("Access to the requested workspace is denied")
    require_permission(ctx.role_name, permission)


@router.get("/providers", response_model=list[VideoProviderDescriptor])
def get_video_providers(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> list[VideoProviderDescriptor]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (_, ctx):
        _require_workspace_permission(ctx, "video:read")
        return container.video_service.list_providers()


@router.get("/models", response_model=list[VideoModelCapability])
def get_video_models(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    enabled_only: Annotated[bool, Query()] = False,
) -> list[VideoModelCapability]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (_, ctx):
        _require_workspace_permission(ctx, "video:read")
        return container.video_service.list_models(enabled_only=enabled_only)


@router.post("/jobs", response_model=VideoJobView, status_code=status.HTTP_201_CREATED)
def post_video_job(
    workspace_id: UUID,
    body: CreateVideoJobPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> VideoJobView:
    header_key = idempotency_key.strip() if idempotency_key is not None else ""
    body_key = body.idempotency_key.strip() if body.idempotency_key is not None else ""
    resolved_idempotency_key = header_key or body_key or None
    if resolved_idempotency_key is not None and len(resolved_idempotency_key) > 128:
        raise ValidationDomainError(
            "Idempotency-Key must not exceed 128 characters",
            details={"max_length": 128},
        )

    normalized_request = NormalizedVideoRequest(
        workspace_id=workspace_id,
        client_id=body.client_id,
        project_id=body.project_id,
        mode=body.mode,
        prompt=body.prompt,
        reference_asset_ids=body.reference_asset_ids,
        aspect_ratio=body.aspect_ratio,
        duration_seconds=body.duration_seconds,
        resolution=body.resolution,
        audio=body.audio,
        brand_constraints=body.brand_constraints,
        provider_preference=body.provider_preference,
        model_preference=body.model_preference,
        quality_preference=body.quality_preference,
        idempotency_key=resolved_idempotency_key,
    )

    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_workspace_permission(ctx, "video:write")
        return container.video_service.create_job(
            session,
            actor_user_id=actor.user_id,
            request=normalized_request,
            request_id=request_id,
        )


@router.get("/jobs", response_model=list[VideoJobView])
def get_video_jobs(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    client_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[VideoJobView]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_workspace_permission(ctx, "video:read")
        return container.video_service.list_jobs(
            session,
            workspace_id=workspace_id,
            client_id=client_id,
            limit=limit,
        )


@router.get("/jobs/{job_id}", response_model=VideoJobView)
def get_video_job(
    workspace_id: UUID,
    job_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> VideoJobView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_workspace_permission(ctx, "video:read")
        return container.video_service.get_job(
            session,
            workspace_id=workspace_id,
            job_id=job_id,
        )


@router.post("/jobs/{job_id}/poll", response_model=VideoJobView)
def post_poll_video_job(
    workspace_id: UUID,
    job_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> VideoJobView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_workspace_permission(ctx, "video:write")
        return container.video_service.poll_job(
            session,
            workspace_id=workspace_id,
            job_id=job_id,
            actor_user_id=actor.user_id,
            request_id=request_id,
        )


@router.post("/jobs/{job_id}/cancel", response_model=VideoJobView)
def post_cancel_video_job(
    workspace_id: UUID,
    job_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> VideoJobView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_workspace_permission(ctx, "video:write")
        return container.video_service.cancel_job(
            session,
            workspace_id=workspace_id,
            job_id=job_id,
            actor_user_id=actor.user_id,
            request_id=request_id,
        )


@router.post("/webhooks/{provider}", response_model=VideoJobView)
async def post_video_provider_webhook(
    workspace_id: UUID,
    provider: str,
    request: Request,
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> VideoJobView:
    raw_body = await request.body()
    headers = dict(request.headers)
    with rls_transaction(
        session_factory,
        user_id=None,
        workspace_id=workspace_id,
    ) as session:
        return container.video_service.handle_webhook(
            session,
            workspace_id=workspace_id,
            provider_id=provider,
            raw_body=raw_body,
            headers=headers,
            request_id=request_id,
        )
