"""Tenant-scoped Client CRUD with explicit client_memberships scoping for client roles."""

from __future__ import annotations

import secrets
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import Client, ClientMembership, Role
from packages.auth.rbac import require_permission
from packages.shared.context import TenantContext
from packages.shared.enums import AuditAction, ClientStatus, RoleName
from packages.shared.errors import (
    ConflictError,
    NotFoundError,
    ValidationDomainError,
    WorkspaceContextRequiredError,
)
from packages.shared.events import AuditEventPayload
from packages.shared.schemas import ClientOut
from packages.shared.scrubbing import scrub_secrets
from packages.tenancy.audit import record_audit_log
from packages.tenancy.workspaces import slugify

CLIENT_SCOPED_ROLES: frozenset[RoleName] = frozenset(
    {RoleName.CLIENT_REVIEWER, RoleName.CLIENT_VIEWER}
)


def _client_to_out(row: Client) -> ClientOut:
    return ClientOut(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        slug=row.slug,
        status=ClientStatus(row.status),
        metadata=row.metadata_json,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def assert_client_readable(session: Session, ctx: TenantContext, client_id: UUID) -> Client:
    """Load a client visible to ctx, enforcing client_memberships for client-scoped roles."""
    if ctx.workspace_id is None or ctx.role_name is None or ctx.user_id is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")

    if ctx.role_name in CLIENT_SCOPED_ROLES:
        require_permission(ctx.role_name, "client:read")
        stmt = (
            select(Client)
            .join(
                ClientMembership,
                (ClientMembership.workspace_id == Client.workspace_id)
                & (ClientMembership.client_id == Client.id),
            )
            .where(
                Client.workspace_id == ctx.workspace_id,
                Client.id == client_id,
                ClientMembership.user_id == ctx.user_id,
            )
        )
    else:
        require_permission(ctx.role_name, "clients:read")
        stmt = select(Client).where(
            Client.workspace_id == ctx.workspace_id,
            Client.id == client_id,
        )

    client = session.execute(stmt).scalar_one_or_none()
    if client is None:
        raise NotFoundError("Client not found")
    return client


def list_clients(session: Session, ctx: TenantContext) -> list[ClientOut]:
    """List clients in the active workspace, restricted by client_memberships for client roles."""
    if ctx.workspace_id is None or ctx.role_name is None or ctx.user_id is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")

    if ctx.role_name in CLIENT_SCOPED_ROLES:
        require_permission(ctx.role_name, "client:read")
        stmt = (
            select(Client)
            .join(
                ClientMembership,
                (ClientMembership.workspace_id == Client.workspace_id)
                & (ClientMembership.client_id == Client.id),
            )
            .where(
                Client.workspace_id == ctx.workspace_id,
                ClientMembership.user_id == ctx.user_id,
            )
            .order_by(Client.created_at.asc())
        )
    else:
        require_permission(ctx.role_name, "clients:read")
        stmt = (
            select(Client)
            .where(Client.workspace_id == ctx.workspace_id)
            .order_by(Client.created_at.asc())
        )

    rows = session.execute(stmt).scalars().all()
    return [_client_to_out(row) for row in rows]


def get_client(session: Session, ctx: TenantContext, *, client_id: UUID) -> ClientOut:
    """Get a single client by ID within tenant and client-membership boundaries."""
    return _client_to_out(assert_client_readable(session, ctx, client_id))


def create_client(
    session: Session,
    ctx: TenantContext,
    *,
    name: str,
    slug: str | None = None,
    status: ClientStatus = ClientStatus.ACTIVE,
    metadata: dict[str, Any] | None = None,
) -> ClientOut:
    """Create a tenant-scoped client record and append an audit log entry."""
    if ctx.workspace_id is None or ctx.role_name is None or ctx.user_id is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")
    require_permission(ctx.role_name, "clients:write")

    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValidationDomainError("Client name must not be empty")

    resolved_slug = (
        slugify(slug, fallback="client")
        if slug is not None and slug.strip()
        else f"{slugify(cleaned_name, fallback='client')}-{secrets.token_hex(3)}"
    )
    scrubbed_meta = scrub_secrets(metadata or {})
    safe_meta = scrubbed_meta if isinstance(scrubbed_meta, dict) else {}

    client = Client(
        id=uuid4(),
        workspace_id=ctx.workspace_id,
        name=cleaned_name,
        slug=resolved_slug,
        status=status.value,
        metadata_json=safe_meta,
    )
    try:
        with session.begin_nested():
            session.add(client)
            session.flush()
    except IntegrityError as exc:
        raise ConflictError(
            f"Client slug '{resolved_slug}' already exists in this workspace"
        ) from exc

    record_audit_log(
        session,
        AuditEventPayload(
            action=AuditAction.CLIENT_CREATED,
            workspace_id=ctx.workspace_id,
            client_id=client.id,
            actor_user_id=ctx.user_id,
            target_type="client",
            target_id=str(client.id),
            request_id=ctx.request_id,
            metadata={"slug": client.slug, "status": client.status},
        ),
    )
    return _client_to_out(client)


def update_client(
    session: Session,
    ctx: TenantContext,
    *,
    client_id: UUID,
    name: str | None = None,
    status: ClientStatus | None = None,
    metadata: dict[str, Any] | None = None,
) -> ClientOut:
    """Update a tenant-scoped client record and record an audit entry."""
    if ctx.workspace_id is None or ctx.role_name is None or ctx.user_id is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")
    require_permission(ctx.role_name, "clients:write")

    client = session.execute(
        select(Client).where(
            Client.workspace_id == ctx.workspace_id,
            Client.id == client_id,
        )
    ).scalar_one_or_none()
    if client is None:
        raise NotFoundError("Client not found")

    updated_fields: list[str] = []
    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise ValidationDomainError("Client name must not be empty")
        client.name = cleaned
        updated_fields.append("name")
    if status is not None:
        client.status = status.value
        updated_fields.append("status")
    if metadata is not None:
        scrubbed = scrub_secrets(metadata)
        client.metadata_json = scrubbed if isinstance(scrubbed, dict) else {}
        updated_fields.append("metadata")

    session.flush()
    record_audit_log(
        session,
        AuditEventPayload(
            action=AuditAction.CLIENT_UPDATED,
            workspace_id=ctx.workspace_id,
            client_id=client.id,
            actor_user_id=ctx.user_id,
            target_type="client",
            target_id=str(client.id),
            request_id=ctx.request_id,
            metadata={"updated_fields": updated_fields, "status": client.status},
        ),
    )
    return _client_to_out(client)


def assign_client_member(
    session: Session,
    ctx: TenantContext,
    *,
    client_id: UUID,
    user_id: UUID,
    role_name: RoleName,
) -> ClientMembership:
    """Assign an existing workspace member to a specific client with a client-scoped role."""
    if ctx.workspace_id is None or ctx.role_name is None or ctx.user_id is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")
    require_permission(ctx.role_name, "clients:write")

    if role_name not in CLIENT_SCOPED_ROLES:
        raise ValidationDomainError("Client assignments require client_reviewer or client_viewer")

    client = session.execute(
        select(Client).where(
            Client.workspace_id == ctx.workspace_id,
            Client.id == client_id,
        )
    ).scalar_one_or_none()
    if client is None:
        raise NotFoundError("Client not found")

    role = session.execute(select(Role).where(Role.name == role_name.value)).scalar_one_or_none()
    if role is None:
        raise NotFoundError(f"Canonical role '{role_name.value}' is not seeded")

    existing = session.execute(
        select(ClientMembership).where(
            ClientMembership.workspace_id == ctx.workspace_id,
            ClientMembership.client_id == client_id,
            ClientMembership.user_id == user_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.role_id = role.id
        session.flush()
        return existing

    cm = ClientMembership(
        id=uuid4(),
        workspace_id=ctx.workspace_id,
        client_id=client_id,
        user_id=user_id,
        role_id=role.id,
    )
    try:
        with session.begin_nested():
            session.add(cm)
            session.flush()
    except IntegrityError as exc:
        raise ConflictError(
            "User must belong to this workspace before being assigned to a client"
        ) from exc
    return cm
