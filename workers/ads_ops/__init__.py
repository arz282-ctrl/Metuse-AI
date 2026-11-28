"""Background worker helpers for Phase 7 Meta Ads discovery & approved execution."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from packages.meta_integrations.ads import (
    AdExecutionResultView,
    AdsOperationsService,
    AdsSyncSummaryView,
)
from packages.tenancy import rls_transaction


def sync_workspace_ads(
    session_factory: sessionmaker[Session],
    *,
    service: AdsOperationsService,
    workspace_id: UUID,
    actor_user_id: UUID,
    client_id: UUID | None = None,
) -> AdsSyncSummaryView:
    """Sync read-only ad accounts and campaign/ad-set/ad hierarchy under workspace RLS."""
    with rls_transaction(
        session_factory, user_id=actor_user_id, workspace_id=workspace_id
    ) as session:
        return service.sync_ad_accounts_and_snapshots(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            client_id=client_id,
        )


def execute_approved_draft_worker(
    session_factory: sessionmaker[Session],
    *,
    service: AdsOperationsService,
    workspace_id: UUID,
    actor_user_id: UUID,
    draft_id: UUID,
    idempotency_key: str,
) -> AdExecutionResultView:
    """Execute a pre-approved ad campaign draft safely under tenant RLS."""
    with rls_transaction(
        session_factory, user_id=actor_user_id, workspace_id=workspace_id
    ) as session:
        return service.execute_draft(
            session,
            workspace_id=workspace_id,
            draft_id=draft_id,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
        )
