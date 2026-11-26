"""PostgreSQL runtime engine factory and transaction-scoped SET LOCAL RLS context."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from database.models import Role, WorkspaceMembership
from packages.shared.config import BaseAppSettings
from packages.shared.context import TenantContext
from packages.shared.enums import MemberStatus, RoleName
from packages.shared.errors import WorkspaceAccessDeniedError


def is_supabase_transaction_pooler_url(url: str) -> bool:
    """Detect Supabase PgBouncer transaction-mode pooler URLs (port 6543 / pooler host)."""
    lowered = url.lower()
    return "pooler.supabase.com" in lowered or ":6543/" in lowered


def create_app_engine(settings: BaseAppSettings) -> Engine:
    """Create a SQLAlchemy Engine bound strictly to the non-superuser metuse_app role."""
    url = settings.get_sqlalchemy_database_url(use_app_role=True)
    connect_args: dict[str, object] = {}
    if is_supabase_transaction_pooler_url(url):
        # psycopg3 prepared statements must be disabled for PgBouncer transaction pooling.
        connect_args["prepare_threshold"] = None
    return create_engine(url, pool_pre_ping=True, connect_args=connect_args, future=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a SQLAlchemy 2.0 session factory with expire_on_commit=False."""
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)


def set_rls_gucs(
    session: Session,
    *,
    user_id: UUID | None = None,
    workspace_id: UUID | None = None,
) -> None:
    """Bind transaction-local RLS GUCs via set_config(..., is_local=true).

    Always explicitly resets unset GUCs to '' so pooled connections cannot inherit
    stale tenant context from prior transactions.
    """
    session.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(user_id) if user_id is not None else ""},
    )
    session.execute(
        text("SELECT set_config('app.current_workspace_id', :wid, true)"),
        {"wid": str(workspace_id) if workspace_id is not None else ""},
    )


@contextmanager
def rls_transaction(
    session_factory: sessionmaker[Session],
    *,
    user_id: UUID | None = None,
    workspace_id: UUID | None = None,
) -> Iterator[Session]:
    """Execute a unit of work inside a single transaction with SET LOCAL RLS GUCs."""
    with session_factory() as session, session.begin():
        set_rls_gucs(session, user_id=user_id, workspace_id=workspace_id)
        yield session


def resolve_tenant_context(
    session: Session,
    *,
    user_id: UUID,
    workspace_id: UUID,
    request_id: UUID | None = None,
) -> TenantContext:
    """Verify active workspace membership under RLS and return immutable TenantContext."""
    set_rls_gucs(session, user_id=user_id, workspace_id=workspace_id)
    stmt = (
        select(Role.name)
        .join(WorkspaceMembership, WorkspaceMembership.role_id == Role.id)
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.status == MemberStatus.ACTIVE.value,
        )
    )
    role_name_raw = session.execute(stmt).scalar_one_or_none()
    if role_name_raw is None:
        raise WorkspaceAccessDeniedError("Access to the requested workspace is denied")

    return TenantContext(
        workspace_id=workspace_id,
        user_id=user_id,
        role_name=RoleName(role_name_raw),
        request_id=request_id or uuid4(),
    )


@contextmanager
def tenant_transaction(
    session_factory: sessionmaker[Session],
    *,
    user_id: UUID,
    workspace_id: UUID,
    request_id: UUID | None = None,
) -> Iterator[tuple[Session, TenantContext]]:
    """Open an RLS-scoped transaction after verifying active workspace membership."""
    with rls_transaction(session_factory, user_id=user_id, workspace_id=workspace_id) as session:
        ctx = resolve_tenant_context(
            session,
            user_id=user_id,
            workspace_id=workspace_id,
            request_id=request_id,
        )
        yield session, ctx
