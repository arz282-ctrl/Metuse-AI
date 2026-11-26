"""Tenant-scoped Client CRUD routes."""

from __future__ import annotations

from typing import Annotated, Any
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
from packages.shared.enums import ClientStatus
from packages.shared.schemas import ClientOut
from packages.tenancy import (
    create_client,
    get_client,
    list_clients,
    tenant_transaction,
    update_client,
)

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/clients", tags=["clients"])


class CreateClientRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=64)
    status: ClientStatus = ClientStatus.ACTIVE
    metadata: dict[str, Any] | None = None


class UpdateClientRequest(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    status: ClientStatus | None = None
    metadata: dict[str, Any] | None = None


@router.get("", response_model=list[ClientOut])
def get_workspace_clients(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> list[ClientOut]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        return list_clients(session, ctx)


@router.post("", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
def post_workspace_client(
    workspace_id: UUID,
    body: CreateClientRequest,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> ClientOut:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        return create_client(
            session,
            ctx,
            name=body.name,
            slug=body.slug,
            status=body.status,
            metadata=body.metadata,
        )


@router.get("/{client_id}", response_model=ClientOut)
def get_workspace_client(
    workspace_id: UUID,
    client_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> ClientOut:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        return get_client(session, ctx, client_id=client_id)


@router.patch("/{client_id}", response_model=ClientOut)
def patch_workspace_client(
    workspace_id: UUID,
    client_id: UUID,
    body: UpdateClientRequest,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> ClientOut:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        return update_client(
            session,
            ctx,
            client_id=client_id,
            name=body.name,
            status=body.status,
            metadata=body.metadata,
        )
