"""Workspace (organization) onboarding and membership management routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from apps.api.deps import (
    AuthenticatedActor,
    get_current_actor,
    get_request_id,
    get_session_factory,
)
from packages.shared.enums import MemberStatus, RoleName, WorkspaceType
from packages.shared.schemas import MemberOut, MembershipSummaryOut, WorkspaceOut
from packages.tenancy import (
    add_workspace_member,
    create_workspace,
    get_current_workspace,
    list_user_workspaces,
    list_workspace_members,
    tenant_transaction,
    update_workspace_member,
)

router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    workspace_type: WorkspaceType = WorkspaceType.AGENCY
    slug: str | None = Field(default=None, max_length=64)
    max_members: int | None = Field(default=None, ge=1, le=5)


class AddMemberRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role_name: RoleName
    full_name: str | None = Field(default=None, max_length=200)
    status: MemberStatus = MemberStatus.ACTIVE


class UpdateMemberRequest(BaseModel):
    role_name: RoleName | None = None
    status: MemberStatus | None = None


@router.get("", response_model=list[MembershipSummaryOut])
def get_workspaces(
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
) -> list[MembershipSummaryOut]:
    return list_user_workspaces(session_factory, user_id=actor.user_id)


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def post_workspace(
    body: CreateWorkspaceRequest,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> WorkspaceOut:
    return create_workspace(
        session_factory,
        user_id=actor.user_id,
        name=body.name,
        workspace_type=body.workspace_type,
        slug=body.slug,
        max_members=body.max_members,
        request_id=request_id,
    )


@router.get("/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> WorkspaceOut:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        return get_current_workspace(session, ctx)


@router.get("/{workspace_id}/members", response_model=list[MemberOut])
def get_members(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> list[MemberOut]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        return list_workspace_members(session, ctx)


@router.post(
    "/{workspace_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED
)
def post_member(
    workspace_id: UUID,
    body: AddMemberRequest,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> MemberOut:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        return add_workspace_member(
            session,
            ctx,
            email=body.email,
            role_name=body.role_name,
            full_name=body.full_name,
            status=body.status,
        )


@router.patch("/{workspace_id}/members/{member_id}", response_model=MemberOut)
def patch_member(
    workspace_id: UUID,
    member_id: UUID,
    body: UpdateMemberRequest,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> MemberOut:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        return update_workspace_member(
            session,
            ctx,
            member_id=member_id,
            role_name=body.role_name,
            status=body.status,
        )
