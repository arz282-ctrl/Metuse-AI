"""Durable restart-safe video generation polling worker entrypoints."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from packages.tenancy.db import rls_transaction
from packages.video_gateway.contracts import VideoJobView
from packages.video_gateway.service import VideoGatewayService


def poll_workspace_video_jobs(
    session_factory: sessionmaker[Session],
    service: VideoGatewayService,
    *,
    workspace_id: UUID,
    limit: int = 20,
) -> list[VideoJobView]:
    """Poll pending video generation jobs for a single workspace inside an RLS transaction."""
    with rls_transaction(session_factory, user_id=None, workspace_id=workspace_id) as session:
        return service.poll_pending_jobs(session, workspace_id=workspace_id, limit=limit)


def run_video_generation_poll_cycle(
    session_factory: sessionmaker[Session],
    service: VideoGatewayService,
    *,
    workspace_ids: Iterable[UUID],
    limit_per_workspace: int = 20,
) -> dict[UUID, list[VideoJobView]]:
    """Run one restart-safe poll cycle across multiple workspaces with isolated RLS transactions."""
    results: dict[UUID, list[VideoJobView]] = {}
    seen: set[UUID] = set()
    for workspace_id in workspace_ids:
        if workspace_id in seen:
            continue
        seen.add(workspace_id)
        results[workspace_id] = poll_workspace_video_jobs(
            session_factory,
            service,
            workspace_id=workspace_id,
            limit=limit_per_workspace,
        )
    return results


__all__ = [
    "poll_workspace_video_jobs",
    "run_video_generation_poll_cycle",
]
