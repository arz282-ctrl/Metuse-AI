"""Agency & Creator workspace onboarding and 5-member cap management services."""

from __future__ import annotations

import re
import secrets
from uuid import UUID, uuid4

from psycopg import errors as pg_errors
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from database.models import BrandProfile, Client, Role, User, Workspace, WorkspaceMembership
from packages.auth.rbac import require_permission
from packages.shared.context import TenantContext
from packages.shared.enums import (
    AuditAction,
    ClientStatus,
    MemberStatus,
    RoleName,
    WorkspaceType,
)
from packages.shared.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationDomainError,
    WorkspaceContextRequiredError,
    WorkspaceMemberLimitExceededError,
)
from packages.shared.events import AuditEventPayload
from packages.shared.schemas import MemberOut, MembershipSummaryOut, WorkspaceOut
from packages.tenancy.audit import record_audit_log
from packages.tenancy.db import rls_transaction, set_rls_gucs

_SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str, *, fallback: str = "item") -> str:
    """Normalize a display name into a lowercase URL-safe slug."""
    cleaned = _SLUG_NON_ALNUM_RE.sub("-", value.strip().lower()).strip("-")
    base = cleaned[:64] if cleaned else fallback
    return base


def _workspace_to_out(ws: Workspace) -> WorkspaceOut:
    return WorkspaceOut(
        id=ws.id,
        name=ws.name,
        slug=ws.slug,
        workspace_type=WorkspaceType(ws.workspace_type),
        max_members=ws.max_members,
        created_by_user_id=ws.created_by_user_id,
        created_at=ws.created_at,
        updated_at=ws.updated_at,
    )


def _get_role_by_name(session: Session, role_name: RoleName) -> Role:
    role = session.execute(select(Role).where(Role.name == role_name.value)).scalar_one_or_none()
    if role is None:
        raise NotFoundError(f"Canonical role '{role_name.value}' is not seeded")
    return role


def _is_member_limit_error(exc: IntegrityError) -> bool:
    if "WORKSPACE_MEMBER_LIMIT_EXCEEDED" in str(exc):
        return True
    orig = exc.orig
    if isinstance(orig, pg_errors.CheckViolation):
        return "WORKSPACE_MEMBER_LIMIT_EXCEEDED" in str(orig)
    return False


def create_workspace(
    session_factory: sessionmaker[Session],
    *,
    user_id: UUID,
    name: str,
    workspace_type: WorkspaceType,
    slug: str | None = None,
    max_members: int | None = None,
    request_id: UUID | None = None,
) -> WorkspaceOut:
    """Create an Agency or Creator workspace under non-superuser RLS.

    Creator workspaces require zero ad accounts and auto-provision a default
    personal-brand client + brand profile so all downstream creative workflows
    share one unified data model.
    """
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValidationDomainError("Workspace name must not be empty")

    if workspace_type == WorkspaceType.CREATOR:
        if max_members is not None and max_members != 1:
            raise ValidationDomainError("Creator workspaces support max_members=1 only")
        resolved_max = 1
        owner_role_name = RoleName.CREATOR_OWNER
    else:
        resolved_max = 5 if max_members is None else int(max_members)
        if resolved_max < 1 or resolved_max > 5:
            raise ValidationDomainError("Agency workspace max_members must be between 1 and 5")
        owner_role_name = RoleName.AGENCY_OWNER

    if slug is not None and slug.strip():
        resolved_slug = slugify(slug, fallback="workspace")
    else:
        resolved_slug = f"{slugify(cleaned_name, fallback='workspace')}-{secrets.token_hex(3)}"

    req_id = request_id or uuid4()

    with rls_transaction(session_factory, user_id=user_id, workspace_id=None) as session:
        owner_role = _get_role_by_name(session, owner_role_name)
        ws = Workspace(
            id=uuid4(),
            name=cleaned_name,
            slug=resolved_slug,
            workspace_type=workspace_type.value,
            max_members=resolved_max,
            created_by_user_id=user_id,
        )
        try:
            with session.begin_nested():
                session.add(ws)
                session.flush()
        except IntegrityError as exc:
            raise ConflictError(f"Workspace slug '{resolved_slug}' is already in use") from exc

        # Promote transaction-local RLS context to the newly created workspace.
        set_rls_gucs(session, user_id=user_id, workspace_id=ws.id)

        membership = WorkspaceMembership(
            id=uuid4(),
            workspace_id=ws.id,
            user_id=user_id,
            role_id=owner_role.id,
            status=MemberStatus.ACTIVE.value,
        )
        session.add(membership)
        session.flush()

        default_client_id: UUID | None = None
        if workspace_type == WorkspaceType.CREATOR:
            default_client = Client(
                id=uuid4(),
                workspace_id=ws.id,
                name=cleaned_name,
                slug="primary",
                status=ClientStatus.ACTIVE.value,
                metadata_json={"is_creator_default": True, "requires_ad_account": False},
            )
            session.add(default_client)
            session.flush()
            default_client_id = default_client.id

            default_brand = BrandProfile(
                id=uuid4(),
                workspace_id=ws.id,
                client_id=default_client.id,
                brand_name=cleaned_name,
                color_palette=[],
            )
            session.add(default_brand)
            session.flush()

        record_audit_log(
            session,
            AuditEventPayload(
                action=AuditAction.WORKSPACE_CREATED,
                workspace_id=ws.id,
                client_id=default_client_id,
                actor_user_id=user_id,
                target_type="workspace",
                target_id=str(ws.id),
                request_id=req_id,
                metadata={
                    "workspace_type": workspace_type.value,
                    "max_members": resolved_max,
                    "requires_ad_account": False,
                },
            ),
        )
        return _workspace_to_out(ws)


def list_user_workspaces(
    session_factory: sessionmaker[Session],
    *,
    user_id: UUID,
) -> list[MembershipSummaryOut]:
    """List active workspaces for a user using non-recursive user-scoped RLS."""
    with rls_transaction(session_factory, user_id=user_id, workspace_id=None) as session:
        stmt = (
            select(
                WorkspaceMembership.workspace_id,
                Workspace.name,
                Workspace.workspace_type,
                Role.name.label("role_name"),
                WorkspaceMembership.status,
            )
            .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
            .join(Role, Role.id == WorkspaceMembership.role_id)
            .where(
                WorkspaceMembership.user_id == user_id,
                WorkspaceMembership.status == MemberStatus.ACTIVE.value,
            )
            .order_by(Workspace.created_at.asc())
        )
        rows = session.execute(stmt).all()
        return [
            MembershipSummaryOut(
                workspace_id=row.workspace_id,
                workspace_name=row.name,
                workspace_type=WorkspaceType(row.workspace_type),
                role_name=RoleName(row.role_name),
                status=MemberStatus(row.status),
            )
            for row in rows
        ]


def get_current_workspace(session: Session, ctx: TenantContext) -> WorkspaceOut:
    """Fetch the active workspace under RLS."""
    if ctx.workspace_id is None or ctx.role_name is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")
    require_permission(ctx.role_name, "workspace:read")

    ws = session.execute(
        select(Workspace).where(Workspace.id == ctx.workspace_id)
    ).scalar_one_or_none()
    if ws is None:
        raise NotFoundError("Workspace not found")
    return _workspace_to_out(ws)


def list_workspace_members(session: Session, ctx: TenantContext) -> list[MemberOut]:
    """List workspace memberships within the active RLS workspace."""
    if ctx.workspace_id is None or ctx.role_name is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")
    require_permission(ctx.role_name, "workspace:read")

    stmt = (
        select(WorkspaceMembership, User, Role)
        .join(User, User.id == WorkspaceMembership.user_id)
        .join(Role, Role.id == WorkspaceMembership.role_id)
        .where(WorkspaceMembership.workspace_id == ctx.workspace_id)
        .order_by(WorkspaceMembership.created_at.asc())
    )
    rows = session.execute(stmt).all()
    return [
        MemberOut(
            id=wm.id,
            workspace_id=wm.workspace_id,
            user_id=wm.user_id,
            email=user.email,
            full_name=user.full_name,
            role_name=RoleName(role.name),
            status=MemberStatus(wm.status),
            created_at=wm.created_at,
            updated_at=wm.updated_at,
        )
        for wm, user, role in rows
    ]


def add_workspace_member(
    session: Session,
    ctx: TenantContext,
    *,
    email: str,
    role_name: RoleName,
    full_name: str | None = None,
    status: MemberStatus = MemberStatus.ACTIVE,
) -> MemberOut:
    """Add or invite a workspace member, enforcing the atomic <=5 member cap as HTTP 409."""
    if ctx.workspace_id is None or ctx.role_name is None or ctx.user_id is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")

    ws = session.execute(
        select(Workspace).where(Workspace.id == ctx.workspace_id)
    ).scalar_one_or_none()
    if ws is None:
        raise NotFoundError("Workspace not found")
    if ws.workspace_type == WorkspaceType.CREATOR.value and ctx.role_name == RoleName.CREATOR_OWNER:
        raise WorkspaceMemberLimitExceededError(
            "Creator workspaces are limited to 1 member (the owner)",
            details={"workspace_id": str(ws.id), "max_members": ws.max_members},
        )
    require_permission(ctx.role_name, "members:manage")

    normalized_email = email.strip().lower()
    if not normalized_email or "@" not in normalized_email:
        raise ValidationDomainError("A valid member email address is required")
    if status == MemberStatus.REMOVED:
        raise ValidationDomainError("Cannot add a new member with status 'removed'")

    if ws.workspace_type == WorkspaceType.CREATOR.value:
        raise WorkspaceMemberLimitExceededError(
            "Creator workspaces are limited to 1 member (the owner)",
            details={"workspace_id": str(ws.id), "max_members": ws.max_members},
        )
    if role_name == RoleName.CREATOR_OWNER:
        raise ValidationDomainError("Role 'creator_owner' is invalid for agency workspaces")
    if role_name == RoleName.AGENCY_OWNER and ctx.role_name != RoleName.AGENCY_OWNER:
        raise PermissionDeniedError("Only an agency owner can assign the agency_owner role")

    target_role = _get_role_by_name(session, role_name)
    user = session.execute(select(User).where(User.email == normalized_email)).scalar_one_or_none()
    if user is None:
        user = User(
            id=uuid4(),
            email=normalized_email,
            password_hash=None,
            full_name=full_name.strip() if full_name and full_name.strip() else None,
            is_active=True,
        )
        session.add(user)
        session.flush()

    membership = WorkspaceMembership(
        id=uuid4(),
        workspace_id=ctx.workspace_id,
        user_id=user.id,
        role_id=target_role.id,
        status=status.value,
    )
    try:
        with session.begin_nested():
            session.add(membership)
            session.flush()
    except IntegrityError as exc:
        if _is_member_limit_error(exc):
            raise WorkspaceMemberLimitExceededError(
                f"Workspace has reached the maximum limit of {ws.max_members} members",
                details={"workspace_id": str(ws.id), "max_members": ws.max_members},
            ) from exc
        raise ConflictError("User is already a member of this workspace") from exc

    record_audit_log(
        session,
        AuditEventPayload(
            action=AuditAction.WORKSPACE_MEMBER_ADDED,
            workspace_id=ctx.workspace_id,
            actor_user_id=ctx.user_id,
            target_type="workspace_membership",
            target_id=str(membership.id),
            request_id=ctx.request_id,
            metadata={
                "member_user_id": str(user.id),
                "role_name": role_name.value,
                "status": status.value,
            },
        ),
    )
    return MemberOut(
        id=membership.id,
        workspace_id=membership.workspace_id,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role_name=role_name,
        status=status,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
    )


def update_workspace_member(
    session: Session,
    ctx: TenantContext,
    *,
    member_id: UUID,
    role_name: RoleName | None = None,
    status: MemberStatus | None = None,
) -> MemberOut:
    """Update a member's role or status while preserving owner invariants and member cap."""
    if ctx.workspace_id is None or ctx.role_name is None or ctx.user_id is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")
    require_permission(ctx.role_name, "members:manage")

    row = session.execute(
        select(WorkspaceMembership, User, Role)
        .join(User, User.id == WorkspaceMembership.user_id)
        .join(Role, Role.id == WorkspaceMembership.role_id)
        .where(
            WorkspaceMembership.workspace_id == ctx.workspace_id,
            WorkspaceMembership.id == member_id,
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Workspace member not found")

    membership, user, current_role = row
    next_role = current_role

    if role_name is not None:
        if role_name == RoleName.CREATOR_OWNER:
            raise ValidationDomainError("Cannot assign creator_owner role")
        if role_name == RoleName.AGENCY_OWNER and ctx.role_name != RoleName.AGENCY_OWNER:
            raise PermissionDeniedError("Only an agency owner can assign the agency_owner role")
        next_role = _get_role_by_name(session, role_name)

    if current_role.name == RoleName.AGENCY_OWNER.value:
        losing_owner = next_role.name != RoleName.AGENCY_OWNER.value or (
            status is not None and status != MemberStatus.ACTIVE
        )
        if losing_owner:
            other_owners = session.execute(
                select(WorkspaceMembership.id)
                .join(Role, Role.id == WorkspaceMembership.role_id)
                .where(
                    WorkspaceMembership.workspace_id == ctx.workspace_id,
                    WorkspaceMembership.id != membership.id,
                    WorkspaceMembership.status == MemberStatus.ACTIVE.value,
                    Role.name == RoleName.AGENCY_OWNER.value,
                )
            ).all()
            if not other_owners:
                raise ConflictError("Workspace must retain at least one active agency_owner")

    membership.role_id = next_role.id
    if status is not None:
        membership.status = status.value

    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError as exc:
        if _is_member_limit_error(exc):
            raise WorkspaceMemberLimitExceededError(
                "Workspace has reached the maximum member limit",
                details={"workspace_id": str(ctx.workspace_id)},
            ) from exc
        raise ConflictError("Unable to update workspace member") from exc

    audit_action = (
        AuditAction.WORKSPACE_MEMBER_REMOVED
        if status == MemberStatus.REMOVED
        else AuditAction.WORKSPACE_MEMBER_UPDATED
    )
    record_audit_log(
        session,
        AuditEventPayload(
            action=audit_action,
            workspace_id=ctx.workspace_id,
            actor_user_id=ctx.user_id,
            target_type="workspace_membership",
            target_id=str(membership.id),
            request_id=ctx.request_id,
            metadata={
                "member_user_id": str(user.id),
                "role_name": next_role.name,
                "status": membership.status,
            },
        ),
    )
    return MemberOut(
        id=membership.id,
        workspace_id=membership.workspace_id,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role_name=RoleName(next_role.name),
        status=MemberStatus(membership.status),
        created_at=membership.created_at,
        updated_at=membership.updated_at,
    )
