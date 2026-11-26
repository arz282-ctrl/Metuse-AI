"""Tenant-scoped append-only audit log query routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from apps.api.deps import (
    AuthenticatedActor,
    get_current_actor,
    get_request_id,
    get_session_factory,
)
from packages.shared.schemas import AuditLogOut
from packages.tenancy import list_audit_logs, tenant_transaction

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/audit-logs", tags=["audit"])


class AuditLogListResponse(BaseModel):
    items: list[AuditLogOut]
    total: int


@router.get("", response_model=AuditLogListResponse)
def get_workspace_audit_logs(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    action: str | None = Query(default=None),
) -> AuditLogListResponse:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        items, total = list_audit_logs(session, ctx, limit=limit, offset=offset, action=action)
        return AuditLogListResponse(items=items, total=total)
