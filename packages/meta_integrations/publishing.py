"""Phase 6 Instagram Publishing State Machine & Insights Sync (Sections 8.5, 11, 14, 16).

Enforces:
- Mandatory human approval gate (`CreativeVersion.is_approved == True` and `qa_passed == True`)
- Backend Meta capability gating (`instagram_publish`, `instagram_insights`)
- Account moving publishing-limit checks (from API, never hardcoded blindly)
- Ambiguous-timeout remote-state verification before any retry (Acceptance #15: zero duplicates)
- Strict workspace/client attribution on all Insights snapshots (Acceptance #14 & #15)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import (
    Asset,
    AuditLog,
    Client,
    ContentCalendar,
    ContentItem,
    CreativeVersion,
    InsightsSnapshot,
    MetaConnection,
    PublishingJob,
    PublishingResult,
)
from packages.meta_integrations.secret_store import SecretStore
from packages.meta_integrations.service import MetaConnectionService
from packages.shared.config import BaseAppSettings
from packages.shared.enums import (
    AuditAction,
    ContentItemStatus,
    ErrorCode,
    PublishingJobStatus,
    PublishMediaType,
)
from packages.shared.errors import (
    ConflictError,
    ExternalServiceError,
    MetuseError,
    NotFoundError,
    ProviderDisabledError,
    ValidationDomainError,
)
from packages.storage.protocol import StorageBackend

MAX_CAROUSEL_ITEMS = 20


class ContentCalendarView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    name: str
    timezone: str
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


class ContentItemView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    calendar_id: UUID | None = None
    project_id: UUID | None = None
    creative_version_id: UUID | None = None
    meta_connection_id: UUID | None = None
    external_account_id: str
    media_type: PublishMediaType
    caption: str
    is_ai_generated: bool
    carousel_asset_ids: list[UUID] = Field(default_factory=list)
    status: ContentItemStatus
    scheduled_for: datetime | None = None
    published_at: datetime | None = None
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


class PublishingResultView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    publishing_job_id: UUID
    content_item_id: UUID
    external_account_id: str
    remote_container_id: str | None = None
    remote_media_id: str
    permalink: str | None = None
    verified_via_remote_check: bool
    published_at: datetime


class PublishingJobView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    content_item_id: UUID
    meta_connection_id: UUID
    actor_user_id: UUID
    status: PublishingJobStatus
    idempotency_key: str | None = None
    remote_container_id: str | None = None
    remote_media_id: str | None = None
    attempt_count: int
    is_ai_generated: bool
    account_limit_quota_used: int | None = None
    account_limit_quota_total: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    result: PublishingResultView | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class InsightsSnapshotView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    meta_connection_id: UUID
    external_account_id: str
    remote_media_id: str | None = None
    scope: str
    period: str
    impressions: int
    reach: int
    likes: int
    comments: int
    saves: int
    shares: int
    plays: int
    follower_count: int | None = None
    captured_at: datetime


class AnalyticsSummaryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    client_id: UUID | None = None
    snapshot_count: int
    total_impressions: int
    total_reach: int
    total_likes: int
    total_comments: int
    total_saves: int
    total_shares: int
    total_plays: int
    latest_follower_count: int | None = None
    snapshots: list[InsightsSnapshotView] = Field(default_factory=list)


@dataclass(frozen=True)
class PublishingLimitStatus:
    quota_usage: int
    quota_total: int


@dataclass(frozen=True)
class RemoteContainerStatus:
    container_id: str
    status_code: str  # FINISHED | IN_PROGRESS | EXPIRED | ERROR


@dataclass(frozen=True)
class RemotePublishResponse:
    remote_media_id: str
    permalink: str
    raw_response: dict[str, Any]


@dataclass(frozen=True)
class RemoteInsightsPayload:
    scope: str
    external_account_id: str
    remote_media_id: str | None
    period: str
    impressions: int
    reach: int
    likes: int
    comments: int
    saves: int
    shares: int
    plays: int
    follower_count: int | None = None
    raw_metrics: dict[str, Any] | None = None


class AmbiguousPublishTimeoutError(ExternalServiceError):
    """Raised when Meta publish call times out after remote state may already have succeeded."""


class MetaPublisherClient(Protocol):
    """Protocol for Meta Instagram container publishing & organic insights retrieval."""

    def check_publishing_limit(
        self, *, access_token: str, external_account_id: str
    ) -> PublishingLimitStatus: ...

    def create_container(
        self,
        *,
        access_token: str,
        external_account_id: str,
        media_type: PublishMediaType,
        media_url: str,
        caption: str,
        is_ai_generated: bool,
    ) -> str: ...

    def get_container_status(
        self, *, access_token: str, container_id: str
    ) -> RemoteContainerStatus: ...

    def publish_container(
        self,
        *,
        access_token: str,
        external_account_id: str,
        container_id: str,
    ) -> RemotePublishResponse: ...

    def find_published_media_for_container(
        self,
        *,
        access_token: str,
        external_account_id: str,
        container_id: str,
    ) -> RemotePublishResponse | None: ...

    def fetch_insights(
        self,
        *,
        access_token: str,
        external_account_id: str,
        remote_media_ids: list[str],
    ) -> list[RemoteInsightsPayload]: ...


class MockMetaPublisherClient:
    """$0 deterministic Meta Publisher & Insights simulator supporting timeout-dedupe tests."""

    def __init__(
        self,
        *,
        quota_usage: int = 3,
        quota_total: int = 100,
        simulate_ambiguous_timeout_once: bool = False,
        expire_container: bool = False,
    ) -> None:
        self._quota_usage = quota_usage
        self._quota_total = quota_total
        self._simulate_timeout_once = simulate_ambiguous_timeout_once
        self._expire_container = expire_container
        self._timed_out_containers: set[str] = set()
        self._published_by_container: dict[str, RemotePublishResponse] = {}
        self.publish_call_count = 0

    def check_publishing_limit(
        self, *, access_token: str, external_account_id: str
    ) -> PublishingLimitStatus:
        _ = (access_token, external_account_id)
        return PublishingLimitStatus(
            quota_usage=self._quota_usage + len(self._published_by_container),
            quota_total=self._quota_total,
        )

    def create_container(
        self,
        *,
        access_token: str,
        external_account_id: str,
        media_type: PublishMediaType,
        media_url: str,
        caption: str,
        is_ai_generated: bool,
    ) -> str:
        _ = (access_token, media_type, media_url, caption, is_ai_generated)
        return f"ig_cnt_{external_account_id}_{uuid4().hex[:10]}"

    def get_container_status(
        self, *, access_token: str, container_id: str
    ) -> RemoteContainerStatus:
        _ = access_token
        if self._expire_container:
            return RemoteContainerStatus(container_id=container_id, status_code="EXPIRED")
        return RemoteContainerStatus(container_id=container_id, status_code="FINISHED")

    def publish_container(
        self,
        *,
        access_token: str,
        external_account_id: str,
        container_id: str,
    ) -> RemotePublishResponse:
        _ = access_token
        self.publish_call_count += 1
        remote_id = f"ig_media_{container_id[-10:]}"
        resp = RemotePublishResponse(
            remote_media_id=remote_id,
            permalink=f"https://www.instagram.com/reel/{remote_id}/",
            raw_response={"id": remote_id, "account": external_account_id},
        )
        # Acceptance #15: Simulate Meta succeeding remotely while client hits ambiguous timeout!
        if self._simulate_timeout_once and container_id not in self._timed_out_containers:
            self._timed_out_containers.add(container_id)
            self._published_by_container[container_id] = resp
            raise AmbiguousPublishTimeoutError(
                "Ambiguous timeout waiting for Meta publish response; verify remote state first"
            )
        self._published_by_container[container_id] = resp
        return resp

    def find_published_media_for_container(
        self,
        *,
        access_token: str,
        external_account_id: str,
        container_id: str,
    ) -> RemotePublishResponse | None:
        _ = (access_token, external_account_id)
        return self._published_by_container.get(container_id)

    def fetch_insights(
        self,
        *,
        access_token: str,
        external_account_id: str,
        remote_media_ids: list[str],
    ) -> list[RemoteInsightsPayload]:
        _ = access_token
        snapshots = [
            RemoteInsightsPayload(
                scope="account",
                external_account_id=external_account_id,
                remote_media_id=None,
                period="days_28",
                impressions=18400,
                reach=12650,
                likes=1420,
                comments=94,
                saves=310,
                shares=185,
                plays=15200,
                follower_count=8420,
                raw_metrics={"source": "mock_graph_insights"},
            )
        ]
        for idx, media_id in enumerate(remote_media_ids):
            snapshots.append(
                RemoteInsightsPayload(
                    scope="media",
                    external_account_id=external_account_id,
                    remote_media_id=media_id,
                    period="lifetime",
                    impressions=4200 + idx * 500,
                    reach=3100 + idx * 350,
                    likes=380 + idx * 40,
                    comments=26 + idx * 3,
                    saves=92 + idx * 10,
                    shares=54 + idx * 5,
                    plays=4800 + idx * 600,
                    follower_count=None,
                    raw_metrics={"media_id": media_id},
                )
            )
        return snapshots


def _calendar_view(row: ContentCalendar) -> ContentCalendarView:
    return ContentCalendarView(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        name=row.name,
        timezone=row.timezone,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _content_view(row: ContentItem) -> ContentItemView:
    return ContentItemView(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        calendar_id=row.calendar_id,
        project_id=row.project_id,
        creative_version_id=row.creative_version_id,
        meta_connection_id=row.meta_connection_id,
        external_account_id=row.external_account_id,
        media_type=PublishMediaType(row.media_type),
        caption=row.caption,
        is_ai_generated=row.is_ai_generated,
        carousel_asset_ids=[UUID(item) for item in row.carousel_asset_ids],
        status=ContentItemStatus(row.status),
        scheduled_for=row.scheduled_for,
        published_at=row.published_at,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _result_view(row: PublishingResult) -> PublishingResultView:
    return PublishingResultView(
        id=row.id,
        publishing_job_id=row.publishing_job_id,
        content_item_id=row.content_item_id,
        external_account_id=row.external_account_id,
        remote_container_id=row.remote_container_id,
        remote_media_id=row.remote_media_id,
        permalink=row.permalink,
        verified_via_remote_check=row.verified_via_remote_check,
        published_at=row.published_at,
    )


def _job_view(row: PublishingJob, *, result: PublishingResult | None = None) -> PublishingJobView:
    return PublishingJobView(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        content_item_id=row.content_item_id,
        meta_connection_id=row.meta_connection_id,
        actor_user_id=row.actor_user_id,
        status=PublishingJobStatus(row.status),
        idempotency_key=row.idempotency_key,
        remote_container_id=row.remote_container_id,
        remote_media_id=row.remote_media_id,
        attempt_count=row.attempt_count,
        is_ai_generated=row.is_ai_generated,
        account_limit_quota_used=row.account_limit_quota_used,
        account_limit_quota_total=row.account_limit_quota_total,
        error_code=row.error_code,
        error_message=row.error_message,
        result=_result_view(result) if result else None,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _insight_view(row: InsightsSnapshot) -> InsightsSnapshotView:
    return InsightsSnapshotView(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        meta_connection_id=row.meta_connection_id,
        external_account_id=row.external_account_id,
        remote_media_id=row.remote_media_id,
        scope=row.scope,
        period=row.period,
        impressions=row.impressions,
        reach=row.reach,
        likes=row.likes,
        comments=row.comments,
        saves=row.saves,
        shares=row.shares,
        plays=row.plays,
        follower_count=row.follower_count,
        captured_at=row.captured_at,
    )


class PublishingAndInsightsService:
    """Orchestrates content calendars, approved-only idempotent publishing, and insights sync."""

    def __init__(
        self,
        *,
        settings: BaseAppSettings,
        meta_service: MetaConnectionService,
        secret_store: SecretStore,
        storage: StorageBackend,
        publisher: MetaPublisherClient,
    ) -> None:
        self._settings = settings
        self._meta = meta_service
        self._secrets = secret_store
        self._storage = storage
        self._publisher = publisher

    def create_calendar(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        name: str,
        client_id: UUID | None = None,
        timezone: str = "UTC",
    ) -> ContentCalendarView:
        if client_id is not None:
            client = session.execute(
                select(Client).where(Client.workspace_id == workspace_id, Client.id == client_id)
            ).scalar_one_or_none()
            if client is None:
                raise NotFoundError("Client not found", details={"client_id": str(client_id)})
        row = ContentCalendar(
            id=uuid4(),
            workspace_id=workspace_id,
            client_id=client_id,
            name=name.strip(),
            timezone=timezone.strip() or "UTC",
            created_by_user_id=actor_user_id,
        )
        session.add(row)
        session.flush()
        return _calendar_view(row)

    def list_calendars(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        client_id: UUID | None = None,
    ) -> list[ContentCalendarView]:
        stmt = select(ContentCalendar).where(ContentCalendar.workspace_id == workspace_id)
        if client_id is not None:
            stmt = stmt.where(ContentCalendar.client_id == client_id)
        rows = session.execute(stmt.order_by(ContentCalendar.created_at.desc())).scalars().all()
        return [_calendar_view(r) for r in rows]

    def create_content_item(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        meta_connection_id: UUID,
        external_account_id: str,
        creative_version_id: UUID,
        caption: str = "",
        media_type: PublishMediaType = PublishMediaType.REELS,
        is_ai_generated: bool = False,
        carousel_asset_ids: list[UUID] | None = None,
        client_id: UUID | None = None,
        calendar_id: UUID | None = None,
        request_id: UUID | None = None,
    ) -> ContentItemView:
        carousel_list = carousel_asset_ids or []
        if len(carousel_list) > MAX_CAROUSEL_ITEMS:
            raise ValidationDomainError(
                f"Instagram carousels support at most {MAX_CAROUSEL_ITEMS} items",
                details={"max_items": MAX_CAROUSEL_ITEMS, "received": len(carousel_list)},
            )

        cv = session.execute(
            select(CreativeVersion).where(
                CreativeVersion.workspace_id == workspace_id,
                CreativeVersion.id == creative_version_id,
            )
        ).scalar_one_or_none()
        if cv is None:
            raise NotFoundError(
                "Creative version not found",
                details={"creative_version_id": str(creative_version_id)},
            )

        conn = session.execute(
            select(MetaConnection).where(
                MetaConnection.workspace_id == workspace_id,
                MetaConnection.id == meta_connection_id,
            )
        ).scalar_one_or_none()
        if conn is None:
            raise NotFoundError(
                "Meta connection not found",
                details={"meta_connection_id": str(meta_connection_id)},
            )
        if external_account_id not in conn.instagram_account_ids:
            raise ValidationDomainError(
                "Target external_account_id is not linked to the selected Meta connection",
                details={"external_account_id": external_account_id},
            )

        item = ContentItem(
            id=uuid4(),
            workspace_id=workspace_id,
            client_id=client_id or cv.client_id,
            calendar_id=calendar_id,
            project_id=cv.project_id,
            creative_version_id=cv.id,
            meta_connection_id=conn.id,
            external_account_id=external_account_id,
            media_type=media_type.value,
            caption=caption,
            is_ai_generated=is_ai_generated,
            carousel_asset_ids=[str(aid) for aid in carousel_list],
            status=ContentItemStatus.DRAFT.value,
            created_by_user_id=actor_user_id,
        )
        session.add(item)
        session.flush()
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=item.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.CONTENT_ITEM_CREATED.value,
                target_type="content_item",
                target_id=str(item.id),
                request_id=request_id,
                metadata_json={"creative_version_id": str(cv.id), "media_type": media_type.value},
            )
        )
        session.flush()
        return _content_view(item)

    def list_content_items(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        client_id: UUID | None = None,
    ) -> list[ContentItemView]:
        stmt = select(ContentItem).where(ContentItem.workspace_id == workspace_id)
        if client_id is not None:
            stmt = stmt.where(ContentItem.client_id == client_id)
        rows = session.execute(stmt.order_by(ContentItem.created_at.desc())).scalars().all()
        return [_content_view(r) for r in rows]

    def _assert_content_approved_and_eligible(
        self, session: Session, *, workspace_id: UUID, item: ContentItem
    ) -> tuple[CreativeVersion, MetaConnection, Asset]:
        if item.creative_version_id is None or item.meta_connection_id is None:
            raise ValidationDomainError(
                "Content item must reference both a creative version and Meta connection"
            )
        cv = session.execute(
            select(CreativeVersion).where(
                CreativeVersion.workspace_id == workspace_id,
                CreativeVersion.id == item.creative_version_id,
            )
        ).scalar_one_or_none()
        if cv is None or not cv.qa_passed or not cv.is_approved:
            raise MetuseError(
                "Publishing requires an explicitly approved, QA-passed creative version",
                code=ErrorCode.CONTENT_APPROVAL_REQUIRED,
                status_code=403,
                details={"creative_version_id": str(item.creative_version_id)},
            )

        # Enforce backend Meta capability gate (instagram_publish)
        self._meta.assert_capability(
            session,
            workspace_id=workspace_id,
            connection_id=item.meta_connection_id,
            required_capability="instagram_publish",
        )
        conn = session.execute(
            select(MetaConnection).where(
                MetaConnection.workspace_id == workspace_id,
                MetaConnection.id == item.meta_connection_id,
            )
        ).scalar_one()
        asset = session.execute(
            select(Asset).where(
                Asset.workspace_id == workspace_id,
                Asset.id == cv.output_asset_id,
            )
        ).scalar_one()
        return cv, conn, asset

    def schedule_content_item(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        content_id: UUID,
        actor_user_id: UUID,
        scheduled_for: datetime,
        request_id: UUID | None = None,
    ) -> ContentItemView:
        item = session.execute(
            select(ContentItem).where(
                ContentItem.workspace_id == workspace_id, ContentItem.id == content_id
            )
        ).scalar_one_or_none()
        if item is None:
            raise NotFoundError("Content item not found", details={"content_id": str(content_id)})

        self._assert_content_approved_and_eligible(session, workspace_id=workspace_id, item=item)
        item.scheduled_for = scheduled_for
        item.status = ContentItemStatus.SCHEDULED.value
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=item.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.CONTENT_ITEM_SCHEDULED.value,
                target_type="content_item",
                target_id=str(item.id),
                request_id=request_id,
                metadata_json={"scheduled_for": scheduled_for.isoformat()},
            )
        )
        session.flush()
        return _content_view(item)

    def _record_published_success(
        self,
        session: Session,
        *,
        job: PublishingJob,
        item: ContentItem,
        actor_user_id: UUID,
        remote_resp: RemotePublishResponse,
        verified_via_remote_check: bool,
        request_id: UUID | None,
    ) -> PublishingJobView:
        now = datetime.now(UTC)
        job.remote_media_id = remote_resp.remote_media_id
        job.status = PublishingJobStatus.PUBLISHED.value
        job.error_code = None
        job.error_message = None
        job.completed_at = now

        item.status = ContentItemStatus.PUBLISHED.value
        item.published_at = now

        existing_res = session.execute(
            select(PublishingResult).where(
                PublishingResult.workspace_id == job.workspace_id,
                PublishingResult.external_account_id == item.external_account_id,
                PublishingResult.remote_media_id == remote_resp.remote_media_id,
            )
        ).scalar_one_or_none()
        if existing_res is None:
            existing_res = PublishingResult(
                id=uuid4(),
                workspace_id=job.workspace_id,
                client_id=job.client_id,
                publishing_job_id=job.id,
                content_item_id=item.id,
                external_account_id=item.external_account_id,
                remote_container_id=job.remote_container_id,
                remote_media_id=remote_resp.remote_media_id,
                permalink=remote_resp.permalink,
                verified_via_remote_check=verified_via_remote_check,
                raw_response=dict(remote_resp.raw_response),
                published_at=now,
            )
            session.add(existing_res)

        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=job.workspace_id,
                client_id=job.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.PUBLISHING_JOB_PUBLISHED.value,
                target_type="publishing_job",
                target_id=str(job.id),
                request_id=request_id,
                metadata_json={
                    "remote_media_id": remote_resp.remote_media_id,
                    "verified_via_remote_check": verified_via_remote_check,
                },
            )
        )
        session.flush()
        return _job_view(job, result=existing_res)

    def publish_content_item(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        content_id: UUID,
        actor_user_id: UUID,
        idempotency_key: str | None = None,
        request_id: UUID | None = None,
    ) -> PublishingJobView:
        """Execute Section 8.5 publishing state machine with zero-duplicate retry verification."""
        if self._settings.publishing_kill_switch:
            raise ProviderDisabledError(
                "Publishing is disabled via global PUBLISHING_KILL_SWITCH",
                details={"scope": "global"},
            )

        item = session.execute(
            select(ContentItem).where(
                ContentItem.workspace_id == workspace_id, ContentItem.id == content_id
            )
        ).scalar_one_or_none()
        if item is None:
            raise NotFoundError("Content item not found", details={"content_id": str(content_id)})

        _, conn, asset = self._assert_content_approved_and_eligible(
            session, workspace_id=workspace_id, item=item
        )
        access_token = self._secrets.get_secret(secret_ref=conn.token_secret_ref)

        # Idempotent retry handling (Acceptance #15)
        job: PublishingJob | None = None
        if idempotency_key:
            job = session.execute(
                select(PublishingJob).where(
                    PublishingJob.workspace_id == workspace_id,
                    PublishingJob.idempotency_key == idempotency_key,
                )
            ).scalar_one_or_none()
            if job is not None:
                if job.content_item_id != item.id:
                    raise ConflictError(
                        "Idempotency-Key already used for a different content item",
                        code=ErrorCode.IDEMPOTENCY_CONFLICT,
                        details={"idempotency_key": idempotency_key},
                    )
                if job.status == PublishingJobStatus.PUBLISHED.value:
                    res = session.execute(
                        select(PublishingResult).where(
                            PublishingResult.workspace_id == workspace_id,
                            PublishingResult.publishing_job_id == job.id,
                        )
                    ).scalar_one_or_none()
                    return _job_view(job, result=res)

        if job is None:
            job = PublishingJob(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=item.client_id,
                content_item_id=item.id,
                meta_connection_id=conn.id,
                actor_user_id=actor_user_id,
                status=PublishingJobStatus.VALIDATING.value,
                idempotency_key=idempotency_key,
                is_ai_generated=item.is_ai_generated,
                started_at=datetime.now(UTC),
            )
            session.add(job)
            session.flush()
            session.add(
                AuditLog(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    client_id=item.client_id,
                    actor_user_id=actor_user_id,
                    action=AuditAction.PUBLISHING_JOB_CREATED.value,
                    target_type="publishing_job",
                    target_id=str(job.id),
                    request_id=request_id,
                    metadata_json={"content_item_id": str(item.id)},
                )
            )

        # Step 8: ALWAYS verify remote state BEFORE retrying if a container already exists!
        if job.remote_container_id:
            job.status = PublishingJobStatus.VERIFYING_REMOTE.value
            existing_remote = self._publisher.find_published_media_for_container(
                access_token=access_token,
                external_account_id=item.external_account_id,
                container_id=job.remote_container_id,
            )
            if existing_remote is not None:
                return self._record_published_success(
                    session,
                    job=job,
                    item=item,
                    actor_user_id=actor_user_id,
                    remote_resp=existing_remote,
                    verified_via_remote_check=True,
                    request_id=request_id,
                )

        # Step 1-2: Check dynamic account publishing limit from Meta API
        limit_status = self._publisher.check_publishing_limit(
            access_token=access_token,
            external_account_id=item.external_account_id,
        )
        job.account_limit_quota_used = limit_status.quota_usage
        job.account_limit_quota_total = limit_status.quota_total
        if limit_status.quota_usage >= limit_status.quota_total:
            job.status = PublishingJobStatus.FAILED.value
            job.error_code = ErrorCode.PUBLISHING_LIMIT_EXCEEDED.value
            job.error_message = "Instagram account 24-hour publishing limit reached"
            job.completed_at = datetime.now(UTC)
            session.flush()
            raise MetuseError(
                "Instagram account moving 24-hour publishing limit reached",
                code=ErrorCode.PUBLISHING_LIMIT_EXCEEDED,
                status_code=429,
                details={
                    "quota_usage": limit_status.quota_usage,
                    "quota_total": limit_status.quota_total,
                },
            )

        # Step 3-4: Generate short-lived media URL & create container (if not already created)
        if not job.remote_container_id:
            job.status = PublishingJobStatus.CONTAINER_CREATING.value
            signed = self._storage.create_presigned_get_url(
                key=asset.storage_key,
                expires_in=600,
                download_filename=asset.filename,
            )
            job.remote_container_id = self._publisher.create_container(
                access_token=access_token,
                external_account_id=item.external_account_id,
                media_type=PublishMediaType(item.media_type),
                media_url=signed.url,
                caption=item.caption,
                is_ai_generated=item.is_ai_generated,
            )

        # Step 5: Check container readiness
        c_status = self._publisher.get_container_status(
            access_token=access_token, container_id=job.remote_container_id
        )
        if c_status.status_code == "EXPIRED":
            job.status = PublishingJobStatus.FAILED.value
            job.error_code = ErrorCode.CONTAINER_EXPIRED.value
            job.error_message = "Instagram publishing container expired before publication"
            job.completed_at = datetime.now(UTC)
            session.flush()
            raise MetuseError(
                "Instagram media container expired before publish",
                code=ErrorCode.CONTAINER_EXPIRED,
                status_code=409,
            )

        job.status = PublishingJobStatus.CONTAINER_READY.value
        item.status = ContentItemStatus.PUBLISHING.value

        # Step 6: Publish container (handle ambiguous timeout safely!)
        job.status = PublishingJobStatus.PUBLISHING.value
        job.attempt_count += 1
        session.flush()
        try:
            remote_resp = self._publisher.publish_container(
                access_token=access_token,
                external_account_id=item.external_account_id,
                container_id=job.remote_container_id,
            )
        except AmbiguousPublishTimeoutError as exc:
            job.status = PublishingJobStatus.VERIFYING_REMOTE.value
            job.error_code = ErrorCode.EXTERNAL_SERVICE_ERROR.value
            job.error_message = exc.message
            session.add(
                AuditLog(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    client_id=item.client_id,
                    actor_user_id=actor_user_id,
                    action=AuditAction.PUBLISHING_JOB_FAILED.value,
                    target_type="publishing_job",
                    target_id=str(job.id),
                    request_id=request_id,
                    metadata_json={"ambiguous_timeout": True},
                )
            )
            session.flush()
            return _job_view(job)

        return self._record_published_success(
            session,
            job=job,
            item=item,
            actor_user_id=actor_user_id,
            remote_resp=remote_resp,
            verified_via_remote_check=False,
            request_id=request_id,
        )

    def get_publishing_job(
        self, session: Session, *, workspace_id: UUID, job_id: UUID
    ) -> PublishingJobView:
        row = session.execute(
            select(PublishingJob).where(
                PublishingJob.workspace_id == workspace_id, PublishingJob.id == job_id
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Publishing job not found", details={"job_id": str(job_id)})
        res = session.execute(
            select(PublishingResult).where(
                PublishingResult.workspace_id == workspace_id,
                PublishingResult.publishing_job_id == row.id,
            )
        ).scalar_one_or_none()
        return _job_view(row, result=res)

    def sync_client_insights(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        client_id: UUID | None = None,
        meta_connection_id: UUID | None = None,
        request_id: UUID | None = None,
    ) -> list[InsightsSnapshotView]:
        """Sync organic account & media insights strictly attributed to workspace/client."""
        stmt = select(MetaConnection).where(MetaConnection.workspace_id == workspace_id)
        if meta_connection_id is not None:
            stmt = stmt.where(MetaConnection.id == meta_connection_id)
        elif client_id is not None:
            stmt = stmt.where(MetaConnection.client_id == client_id)
        conns = session.execute(stmt).scalars().all()
        if not conns:
            raise NotFoundError("No active Meta connection found for analytics sync")

        created: list[InsightsSnapshotView] = []
        for conn in conns:
            self._meta.assert_capability(
                session,
                workspace_id=workspace_id,
                connection_id=conn.id,
                required_capability="instagram_insights",
            )
            token = self._secrets.get_secret(secret_ref=conn.token_secret_ref)
            for ig_id in conn.instagram_account_ids:
                pub_results = (
                    session.execute(
                        select(PublishingResult.remote_media_id).where(
                            PublishingResult.workspace_id == workspace_id,
                            PublishingResult.external_account_id == ig_id,
                        )
                    )
                    .scalars()
                    .all()
                )
                payloads = self._publisher.fetch_insights(
                    access_token=token,
                    external_account_id=ig_id,
                    remote_media_ids=list(pub_results),
                )
                for p in payloads:
                    snap = InsightsSnapshot(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        client_id=client_id or conn.client_id,
                        meta_connection_id=conn.id,
                        external_account_id=p.external_account_id,
                        remote_media_id=p.remote_media_id,
                        scope=p.scope,
                        period=p.period,
                        impressions=p.impressions,
                        reach=p.reach,
                        likes=p.likes,
                        comments=p.comments,
                        saves=p.saves,
                        shares=p.shares,
                        plays=p.plays,
                        follower_count=p.follower_count,
                        metrics_json=dict(p.raw_metrics or {}),
                    )
                    session.add(snap)
                    session.flush()
                    created.append(_insight_view(snap))

        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.INSIGHTS_SYNCED.value,
                target_type="insights_snapshot",
                target_id=str(client_id) if client_id else str(workspace_id),
                request_id=request_id,
                metadata_json={"snapshots_created": len(created)},
            )
        )
        session.flush()
        return created

    def get_client_analytics(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        client_id: UUID | None = None,
        limit: int = 50,
    ) -> AnalyticsSummaryView:
        stmt = select(InsightsSnapshot).where(InsightsSnapshot.workspace_id == workspace_id)
        if client_id is not None:
            stmt = stmt.where(InsightsSnapshot.client_id == client_id)
        rows = (
            session.execute(
                stmt.order_by(InsightsSnapshot.captured_at.desc()).limit(max(1, min(limit, 200)))
            )
            .scalars()
            .all()
        )
        views = [_insight_view(r) for r in rows]
        latest_followers = next(
            (r.follower_count for r in rows if r.follower_count is not None), None
        )
        return AnalyticsSummaryView(
            workspace_id=workspace_id,
            client_id=client_id,
            snapshot_count=len(views),
            total_impressions=sum(r.impressions for r in rows),
            total_reach=sum(r.reach for r in rows),
            total_likes=sum(r.likes for r in rows),
            total_comments=sum(r.comments for r in rows),
            total_saves=sum(r.saves for r in rows),
            total_shares=sum(r.shares for r in rows),
            total_plays=sum(r.plays for r in rows),
            latest_follower_count=latest_followers,
            snapshots=views,
        )
