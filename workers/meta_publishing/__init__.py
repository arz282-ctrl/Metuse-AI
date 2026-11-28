"""Scheduled Meta publishing worker entrypoint."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from database.models import ContentItem
from packages.meta_integrations.publishing import PublishingAndInsightsService, PublishingJobView
from packages.shared.enums import ContentItemStatus
from packages.tenancy.db import rls_transaction


def execute_due_scheduled_publishes(
    session_factory: sessionmaker[Session],
    service: PublishingAndInsightsService,
    *,
    workspace_id: UUID,
    now: datetime | None = None,
) -> list[PublishingJobView]:
    """Publish all due approved scheduled content items inside a workspace RLS transaction."""
    cutoff = now or datetime.now(UTC)
    results: list[PublishingJobView] = []
    with rls_transaction(session_factory, user_id=None, workspace_id=workspace_id) as session:
        due_items = (
            session.execute(
                select(ContentItem).where(
                    ContentItem.workspace_id == workspace_id,
                    ContentItem.status == ContentItemStatus.SCHEDULED.value,
                    ContentItem.scheduled_for.is_not(None),
                    ContentItem.scheduled_for <= cutoff,
                )
            )
            .scalars()
            .all()
        )
        for item in due_items:
            results.append(
                service.publish_content_item(
                    session,
                    workspace_id=workspace_id,
                    content_id=item.id,
                    actor_user_id=item.created_by_user_id,
                    idempotency_key=f"scheduled-{item.id}",
                )
            )
    return results


__all__ = ["execute_due_scheduled_publishes"]
