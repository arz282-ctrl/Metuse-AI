"""Isolated cloud media editing worker entrypoints."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from packages.media_engine.contracts import CreativeVersionView, RenderJobView
from packages.media_engine.service import CloudEditingService
from packages.shared.enums import RenderKind
from packages.tenancy.db import rls_transaction


def execute_render_in_worker(
    session_factory: sessionmaker[Session],
    service: CloudEditingService,
    *,
    workspace_id: UUID,
    actor_user_id: UUID,
    project_id: UUID,
    render_kind: RenderKind,
    editing_plan_id: UUID | None = None,
    idempotency_key: str | None = None,
) -> tuple[RenderJobView, CreativeVersionView | None]:
    """Run an isolated render job inside a workspace-scoped RLS transaction."""
    with rls_transaction(
        session_factory,
        user_id=actor_user_id,
        workspace_id=workspace_id,
    ) as session:
        return service.render_project(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            project_id=project_id,
            render_kind=render_kind,
            editing_plan_id=editing_plan_id,
            idempotency_key=idempotency_key,
        )


__all__ = ["execute_render_in_worker"]
