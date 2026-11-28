"""Organic Meta insights sync worker entrypoint."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from packages.meta_integrations.publishing import InsightsSnapshotView, PublishingAndInsightsService
from packages.tenancy.db import rls_transaction


def sync_workspace_insights(
    session_factory: sessionmaker[Session],
    service: PublishingAndInsightsService,
    *,
    workspace_id: UUID,
    actor_user_id: UUID,
    client_id: UUID | None = None,
) -> list[InsightsSnapshotView]:
    """Run an isolated workspace-scoped insights sync inside an RLS transaction."""
    with rls_transaction(
        session_factory, user_id=actor_user_id, workspace_id=workspace_id
    ) as session:
        return service.sync_client_insights(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            client_id=client_id,
        )


__all__ = ["sync_workspace_insights"]
