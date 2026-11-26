"""Append-only audit log recording and tenant-scoped read queries."""

from __future__ import annotations

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from database.models import AuditLog
from packages.auth.rbac import require_permission
from packages.shared.context import TenantContext
from packages.shared.errors import WorkspaceContextRequiredError
from packages.shared.events import AuditEventPayload
from packages.shared.schemas import AuditLogOut


def _audit_to_out(row: AuditLog) -> AuditLogOut:
    return AuditLogOut(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        actor_user_id=row.actor_user_id,
        action=row.action,
        target_type=row.target_type,
        target_id=row.target_id,
        request_id=row.request_id,
        metadata=row.metadata_json,
        created_at=row.created_at,
    )


def record_audit_log(session: Session, payload: AuditEventPayload) -> AuditLog:
    """Insert a secret-scrubbed append-only audit record in the current transaction."""
    entry = AuditLog(
        workspace_id=payload.workspace_id,
        client_id=payload.client_id,
        actor_user_id=payload.actor_user_id,
        action=payload.action.value,
        target_type=payload.target_type,
        target_id=payload.target_id,
        request_id=payload.request_id,
        metadata_json=payload.metadata,
    )
    session.add(entry)
    session.flush()
    return entry


def list_audit_logs(
    session: Session,
    ctx: TenantContext,
    *,
    limit: int = 50,
    offset: int = 0,
    action: str | None = None,
) -> tuple[list[AuditLogOut], int]:
    """Query tenant-scoped audit logs (RLS enforced + RBAC checked)."""
    if ctx.workspace_id is None or ctx.role_name is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")
    require_permission(ctx.role_name, "audit:read")

    bounded_limit = max(1, min(int(limit), 200))
    bounded_offset = max(0, int(offset))

    base_stmt = select(AuditLog).where(AuditLog.workspace_id == ctx.workspace_id)
    count_stmt = (
        select(func.count()).select_from(AuditLog).where(AuditLog.workspace_id == ctx.workspace_id)
    )
    if action and action.strip():
        cleaned_action = action.strip()
        base_stmt = base_stmt.where(AuditLog.action == cleaned_action)
        count_stmt = count_stmt.where(AuditLog.action == cleaned_action)

    total = int(session.execute(count_stmt).scalar_one())
    rows = (
        session.execute(
            base_stmt.order_by(desc(AuditLog.created_at), desc(AuditLog.id))
            .limit(bounded_limit)
            .offset(bounded_offset)
        )
        .scalars()
        .all()
    )
    return [_audit_to_out(row) for row in rows], total
