"""Tenant-scoped Version-Bound Approval & Revision endpoints (Sections 9 & 11)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
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
from packages.media_engine.contracts import ApprovalView, RevisionRequestView
from packages.shared.context import TenantContext
from packages.shared.enums import ApprovalStatus, ApprovalType
from packages.shared.errors import WorkspaceAccessDeniedError
from packages.tenancy import tenant_transaction

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/approvals", tags=["approvals"])


class CreateApprovalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    creative_version_id: UUID
    approval_type: ApprovalType = ApprovalType.CREATIVE


class ApproveDecisionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(default=None, max_length=2000)


class RejectDecisionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str = Field(min_length=2, max_length=2000)


class RequestRevisionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str = Field(min_length=2, max_length=2000)
    timecode_ms: int | None = Field(default=None, ge=0)


class RevisionDecisionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval: ApprovalView
    revision_request: RevisionRequestView


def _require_ws_perm(ctx: TenantContext, permission: str) -> None:
    if ctx.role_name is None:
        raise WorkspaceAccessDeniedError("Access to the requested workspace is denied")
    require_permission(ctx.role_name, permission)


@router.get("", response_model=list[ApprovalView])
def get_approvals(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    project_id: Annotated[UUID | None, Query()] = None,
    approval_status: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
) -> list[ApprovalView]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "approvals:read")
        return container.editing_service.list_approvals(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            status=approval_status,
        )


@router.post("", response_model=ApprovalView, status_code=status.HTTP_201_CREATED)
def post_create_approval(
    workspace_id: UUID,
    body: CreateApprovalPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> ApprovalView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "approvals:write")
        return container.editing_service.create_approval(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            creative_version_id=body.creative_version_id,
            approval_type=body.approval_type,
            request_id=request_id,
        )


@router.post("/{approval_id}/approve", response_model=ApprovalView)
def post_approve(
    workspace_id: UUID,
    approval_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    body: ApproveDecisionPayload | None = None,
) -> ApprovalView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "approvals:decide")
        return container.editing_service.approve(
            session,
            workspace_id=workspace_id,
            approval_id=approval_id,
            actor_user_id=actor.user_id,
            notes=body.notes if body else None,
            request_id=request_id,
        )


@router.post("/{approval_id}/reject", response_model=ApprovalView)
def post_reject(
    workspace_id: UUID,
    approval_id: UUID,
    body: RejectDecisionPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> ApprovalView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "approvals:decide")
        return container.editing_service.reject(
            session,
            workspace_id=workspace_id,
            approval_id=approval_id,
            actor_user_id=actor.user_id,
            notes=body.notes,
            request_id=request_id,
        )


@router.post("/{approval_id}/request-revision", response_model=RevisionDecisionOut)
def post_request_revision(
    workspace_id: UUID,
    approval_id: UUID,
    body: RequestRevisionPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> RevisionDecisionOut:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "approvals:decide")
        approval, revision = container.editing_service.request_revision(
            session,
            workspace_id=workspace_id,
            approval_id=approval_id,
            actor_user_id=actor.user_id,
            notes=body.notes,
            timecode_ms=body.timecode_ms,
            request_id=request_id,
        )
        return RevisionDecisionOut(approval=approval, revision_request=revision)
