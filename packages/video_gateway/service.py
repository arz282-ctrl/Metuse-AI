"""Durable Unified Video Generation Gateway service (Section 6, 10, 14, 16)."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import (
    Asset,
    AuditLog,
    Client,
    Project,
    UsageRecord,
    VideoGenerationJob,
    VideoProviderEvent,
    WorkspaceBudget,
)
from packages.shared.config import BaseAppSettings
from packages.shared.enums import (
    AssetKind,
    AssetSourceType,
    AuditAction,
    ErrorCode,
    VideoFailureCategory,
    VideoGenerationMode,
    VideoJobStatus,
)
from packages.shared.errors import (
    BudgetExceededError,
    ConflictError,
    ExternalServiceError,
    MetuseError,
    NotFoundError,
    ProviderDisabledError,
    ValidationDomainError,
)
from packages.shared.scrubbing import scrub_secrets, scrub_text
from packages.storage.keys import build_tenant_object_key, sanitize_filename, validate_storage_key
from packages.storage.protocol import PresignedDownloadUrl, StorageBackend
from packages.video_gateway.contracts import (
    AssetView,
    NormalizedVideoRequest,
    ProjectView,
    ProviderPollResult,
    ProviderReferenceImage,
    ProviderSubmissionRequest,
    VideoJobView,
    VideoModelCapability,
    VideoProviderDescriptor,
)
from packages.video_gateway.registry import VideoGatewayRegistry
from packages.video_gateway.state_machine import (
    TERMINAL_STATUSES,
    compute_next_poll_delay_seconds,
    validate_transition,
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    cleaned = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return cleaned[:48] or f"project-{uuid4().hex[:8]}"


def _current_billing_period(now: datetime | None = None) -> str:
    ref = now or datetime.now(UTC)
    return ref.strftime("%Y-%m")


def _project_to_view(row: Project) -> ProjectView:
    return ProjectView(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        name=row.name,
        slug=row.slug,
        status=row.status,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _asset_to_view(row: Asset) -> AssetView:
    return AssetView(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        project_id=row.project_id,
        asset_kind=AssetKind(row.asset_kind),
        source_type=AssetSourceType(row.source_type),
        filename=row.filename,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        storage_bucket=row.storage_bucket,
        storage_key=row.storage_key,
        duration_seconds=row.duration_seconds,
        width=row.width,
        height=row.height,
        sha256=row.sha256,
        status=row.status,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _job_to_view(row: VideoGenerationJob) -> VideoJobView:
    return VideoJobView(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        project_id=row.project_id,
        actor_user_id=row.actor_user_id,
        mode=VideoGenerationMode(row.mode),
        provider=row.provider,
        model=row.model,
        provider_job_id=row.provider_job_id,
        status=VideoJobStatus(row.status),
        progress_pct=row.progress_pct,
        idempotency_key=row.idempotency_key,
        prompt=row.prompt,
        aspect_ratio=row.aspect_ratio,
        duration_seconds=row.duration_seconds,
        resolution=row.resolution,
        provider_preference=row.provider_preference,
        input_asset_ids=[UUID(item) for item in row.input_asset_ids],
        output_asset_ids=[UUID(item) for item in row.output_asset_ids],
        failure_category=(
            VideoFailureCategory(row.failure_category) if row.failure_category else None
        ),
        provider_error_code=row.provider_error_code,
        error_message=row.error_message,
        poll_attempts=row.poll_attempts,
        next_poll_at=row.next_poll_at,
        estimated_cost_usd=row.estimated_cost_usd,
        actual_cost_usd=row.actual_cost_usd,
        submitted_at=row.submitted_at,
        completed_at=row.completed_at,
        cancelled_at=row.cancelled_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _request_fingerprint(request: NormalizedVideoRequest) -> dict[str, Any]:
    return {
        "mode": request.mode.value,
        "prompt": request.prompt,
        "client_id": str(request.client_id) if request.client_id else None,
        "project_id": str(request.project_id) if request.project_id else None,
        "aspect_ratio": request.aspect_ratio,
        "duration_seconds": request.duration_seconds,
        "resolution": request.resolution,
        "reference_asset_ids": [str(aid) for aid in request.reference_asset_ids],
        "provider_preference": request.provider_preference,
        "model_preference": request.model_preference,
        "quality_preference": request.quality_preference,
    }


def _safe_dict(payload: dict[str, Any]) -> dict[str, Any]:
    scrubbed = scrub_secrets(payload)
    return scrubbed if isinstance(scrubbed, dict) else {}


class VideoGatewayService:
    """Provider-independent video generation orchestrator with durable state and RLS isolation."""

    def __init__(
        self,
        *,
        settings: BaseAppSettings,
        registry: VideoGatewayRegistry,
        storage: StorageBackend,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._storage = storage

    def list_providers(self) -> list[VideoProviderDescriptor]:
        return self._registry.list_providers()

    def list_models(self, *, enabled_only: bool = False) -> list[VideoModelCapability]:
        return self._registry.list_models(enabled_only=enabled_only)

    def _assert_client_owned(
        self, session: Session, *, workspace_id: UUID, client_id: UUID
    ) -> None:
        client = session.execute(
            select(Client).where(Client.workspace_id == workspace_id, Client.id == client_id)
        ).scalar_one_or_none()
        if client is None:
            raise NotFoundError(
                "Client not found in the active workspace",
                details={"client_id": str(client_id)},
            )

    def _assert_project_owned(
        self, session: Session, *, workspace_id: UUID, project_id: UUID
    ) -> Project:
        project = session.execute(
            select(Project).where(Project.workspace_id == workspace_id, Project.id == project_id)
        ).scalar_one_or_none()
        if project is None:
            raise NotFoundError(
                "Project not found in the active workspace",
                details={"project_id": str(project_id)},
            )
        return project

    def _build_asset_storage_key(
        self,
        *,
        workspace_id: UUID,
        client_id: UUID | None,
        asset_id: UUID,
        filename: str,
        source_type: AssetSourceType,
    ) -> str:
        category = (
            "generated/video-scenes"
            if source_type == AssetSourceType.VIDEO_GENERATION
            else "uploads/originals"
        )
        if client_id is not None:
            return build_tenant_object_key(
                workspace_id=workspace_id,
                client_id=client_id,
                category=category,
                asset_id=asset_id,
                filename=filename,
            )
        safe_name = sanitize_filename(filename)
        key = (
            f"workspaces/{str(workspace_id).lower()}/{category}/{str(asset_id).lower()}/{safe_name}"
        )
        return validate_storage_key(key, expected_workspace_id=workspace_id)

    def create_project(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        name: str,
        client_id: UUID | None = None,
        slug: str | None = None,
        request_id: UUID | None = None,
    ) -> ProjectView:
        if client_id is not None:
            self._assert_client_owned(session, workspace_id=workspace_id, client_id=client_id)

        base_slug = _slugify(slug or name)
        existing = session.execute(
            select(Project).where(Project.workspace_id == workspace_id, Project.slug == base_slug)
        ).scalar_one_or_none()
        resolved_slug = f"{base_slug}-{uuid4().hex[:6]}" if existing is not None else base_slug

        project = Project(
            id=uuid4(),
            workspace_id=workspace_id,
            client_id=client_id,
            name=name.strip(),
            slug=resolved_slug,
            status="active",
            metadata_json={},
            created_by_user_id=actor_user_id,
        )
        session.add(project)
        session.flush()
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.PROJECT_CREATED.value,
                target_type="project",
                target_id=str(project.id),
                request_id=request_id,
                metadata_json={"slug": project.slug},
            )
        )
        session.flush()
        return _project_to_view(project)

    def list_projects(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        client_id: UUID | None = None,
    ) -> list[ProjectView]:
        stmt = select(Project).where(Project.workspace_id == workspace_id)
        if client_id is not None:
            stmt = stmt.where(Project.client_id == client_id)
        stmt = stmt.order_by(Project.created_at.desc())
        rows = session.execute(stmt).scalars().all()
        return [_project_to_view(row) for row in rows]

    def create_asset(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        filename: str,
        mime_type: str,
        asset_kind: AssetKind,
        data: bytes,
        client_id: UUID | None = None,
        project_id: UUID | None = None,
        source_type: AssetSourceType = AssetSourceType.UPLOAD,
        duration_seconds: Decimal | None = None,
        width: int | None = None,
        height: int | None = None,
        metadata: dict[str, Any] | None = None,
        request_id: UUID | None = None,
    ) -> AssetView:
        if not data:
            raise ValidationDomainError("Asset payload bytes must not be empty")
        if client_id is not None:
            self._assert_client_owned(session, workspace_id=workspace_id, client_id=client_id)
        if project_id is not None:
            self._assert_project_owned(session, workspace_id=workspace_id, project_id=project_id)

        asset_id = uuid4()
        storage_key = self._build_asset_storage_key(
            workspace_id=workspace_id,
            client_id=client_id,
            asset_id=asset_id,
            filename=filename,
            source_type=source_type,
        )
        obj_meta = self._storage.put_object_bytes(
            key=storage_key,
            data=data,
            content_type=mime_type,
        )
        digest = hashlib.sha256(data).hexdigest()

        asset = Asset(
            id=asset_id,
            workspace_id=workspace_id,
            client_id=client_id,
            project_id=project_id,
            asset_kind=asset_kind.value,
            source_type=source_type.value,
            filename=sanitize_filename(filename),
            mime_type=mime_type,
            size_bytes=obj_meta.size_bytes,
            storage_bucket=obj_meta.bucket,
            storage_key=obj_meta.key,
            duration_seconds=duration_seconds,
            width=width,
            height=height,
            sha256=digest,
            status="ready",
            metadata_json=_safe_dict(metadata or {}),
            created_by_user_id=actor_user_id,
        )
        session.add(asset)
        session.flush()
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.ASSET_CREATED.value,
                target_type="asset",
                target_id=str(asset.id),
                request_id=request_id,
                metadata_json={
                    "asset_kind": asset.asset_kind,
                    "source_type": asset.source_type,
                    "size_bytes": asset.size_bytes,
                },
            )
        )
        session.flush()
        return _asset_to_view(asset)

    def list_assets(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        client_id: UUID | None = None,
        asset_kind: AssetKind | None = None,
    ) -> list[AssetView]:
        stmt = select(Asset).where(Asset.workspace_id == workspace_id, Asset.status != "deleted")
        if client_id is not None:
            stmt = stmt.where(Asset.client_id == client_id)
        if asset_kind is not None:
            stmt = stmt.where(Asset.asset_kind == asset_kind.value)
        stmt = stmt.order_by(Asset.created_at.desc())
        rows = session.execute(stmt).scalars().all()
        return [_asset_to_view(row) for row in rows]

    def get_asset(self, session: Session, *, workspace_id: UUID, asset_id: UUID) -> AssetView:
        row = session.execute(
            select(Asset).where(
                Asset.workspace_id == workspace_id,
                Asset.id == asset_id,
                Asset.status != "deleted",
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Asset not found", details={"asset_id": str(asset_id)})
        return _asset_to_view(row)

    def create_asset_download_url(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        asset_id: UUID,
        expires_in: int = 300,
    ) -> PresignedDownloadUrl:
        asset = self.get_asset(session, workspace_id=workspace_id, asset_id=asset_id)
        return self._storage.create_presigned_get_url(
            key=asset.storage_key,
            expires_in=expires_in,
            download_filename=asset.filename,
        )

    def _enforce_budget_and_kill_switch(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        client_id: UUID | None,
        estimated_cost_usd: Decimal,
        duration_units: int,
    ) -> None:
        if self._settings.video_kill_switch:
            raise ProviderDisabledError(
                "Video generation gateway is disabled via global VIDEO_KILL_SWITCH",
                details={"scope": "global"},
            )

        ws_budget = session.execute(
            select(WorkspaceBudget).where(
                WorkspaceBudget.workspace_id == workspace_id,
                WorkspaceBudget.client_id.is_(None),
            )
        ).scalar_one_or_none()
        client_budget = (
            session.execute(
                select(WorkspaceBudget).where(
                    WorkspaceBudget.workspace_id == workspace_id,
                    WorkspaceBudget.client_id == client_id,
                )
            ).scalar_one_or_none()
            if client_id is not None
            else None
        )

        for label, budget in (("workspace", ws_budget), ("client", client_budget)):
            if budget is None:
                continue
            if budget.provider_kill_switch:
                raise ProviderDisabledError(
                    f"Video generation is disabled by {label} provider kill switch",
                    details={"scope": label},
                )

        period = _current_billing_period()
        for label, budget, scope_client_id in (
            ("workspace", ws_budget, None),
            ("client", client_budget, client_id),
        ):
            if budget is None:
                continue
            stmt = select(
                func.coalesce(func.sum(UsageRecord.input_units + UsageRecord.output_units), 0),
                func.coalesce(func.sum(UsageRecord.estimated_cost_usd), Decimal("0")),
            ).where(
                UsageRecord.workspace_id == workspace_id,
                UsageRecord.billing_period == period,
            )
            if scope_client_id is not None:
                stmt = stmt.where(UsageRecord.client_id == scope_client_id)
            used_units_raw, used_cost_raw = session.execute(stmt).one()
            used_units = int(used_units_raw)
            used_cost = Decimal(str(used_cost_raw))

            if (
                budget.monthly_unit_limit is not None
                and used_units + duration_units > budget.monthly_unit_limit
            ):
                raise BudgetExceededError(
                    f"Monthly {label} usage unit limit would be exceeded",
                    details={
                        "scope": label,
                        "billing_period": period,
                        "used_units": used_units,
                        "requested_units": duration_units,
                        "limit_units": budget.monthly_unit_limit,
                    },
                )
            if (
                budget.monthly_cost_limit_usd is not None
                and used_cost + estimated_cost_usd > budget.monthly_cost_limit_usd
            ):
                raise BudgetExceededError(
                    f"Monthly {label} cost budget would be exceeded",
                    details={
                        "scope": label,
                        "billing_period": period,
                        "used_cost_usd": str(used_cost),
                        "estimated_cost_usd": str(estimated_cost_usd),
                        "limit_cost_usd": str(budget.monthly_cost_limit_usd),
                    },
                )

    def _record_event(
        self,
        session: Session,
        *,
        job: VideoGenerationJob,
        event_type: str,
        from_status: VideoJobStatus | None,
        to_status: VideoJobStatus | None,
        payload: dict[str, Any] | None = None,
        provider_event_id: str | None = None,
    ) -> None:
        session.add(
            VideoProviderEvent(
                id=uuid4(),
                workspace_id=job.workspace_id,
                video_job_id=job.id,
                provider=job.provider,
                provider_event_id=provider_event_id,
                event_type=event_type,
                from_status=from_status.value if from_status else None,
                to_status=to_status.value if to_status else None,
                payload=_safe_dict(payload or {}),
            )
        )
        session.flush()

    def _resolve_reference_images(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        reference_asset_ids: list[UUID],
    ) -> list[ProviderReferenceImage]:
        refs: list[ProviderReferenceImage] = []
        for asset_id in reference_asset_ids:
            row = session.execute(
                select(Asset).where(
                    Asset.workspace_id == workspace_id,
                    Asset.id == asset_id,
                    Asset.status != "deleted",
                )
            ).scalar_one_or_none()
            if row is None:
                raise NotFoundError(
                    "Reference image asset not found in active workspace",
                    details={"asset_id": str(asset_id)},
                )
            if row.asset_kind != AssetKind.IMAGE.value:
                raise ValidationDomainError(
                    "Reference asset for image_to_video must have asset_kind='image'",
                    details={"asset_id": str(asset_id), "asset_kind": row.asset_kind},
                )
            signed = self._storage.create_presigned_get_url(
                key=row.storage_key,
                expires_in=300,
                download_filename=row.filename,
            )
            refs.append(
                ProviderReferenceImage(
                    asset_id=row.id,
                    signed_url=signed.url,
                    mime_type=row.mime_type,
                    filename=row.filename,
                )
            )
        return refs

    def create_job(
        self,
        session: Session,
        *,
        actor_user_id: UUID,
        request: NormalizedVideoRequest,
        request_id: UUID | None = None,
        auto_poll_if_sync: bool = False,
    ) -> VideoJobView:
        fingerprint = _request_fingerprint(request)
        if request.idempotency_key:
            existing = session.execute(
                select(VideoGenerationJob).where(
                    VideoGenerationJob.workspace_id == request.workspace_id,
                    VideoGenerationJob.idempotency_key == request.idempotency_key,
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing_fp = existing.request_params.get("fingerprint")
                if existing_fp != fingerprint:
                    raise ConflictError(
                        "Idempotency-Key already used with different video request parameters",
                        code=ErrorCode.IDEMPOTENCY_CONFLICT,
                        details={"idempotency_key": request.idempotency_key},
                    )
                return _job_to_view(existing)

        if request.client_id is not None:
            self._assert_client_owned(
                session, workspace_id=request.workspace_id, client_id=request.client_id
            )
        if request.project_id is not None:
            self._assert_project_owned(
                session, workspace_id=request.workspace_id, project_id=request.project_id
            )

        # Select eligible provider & model before persisting (fails with 422/503 if unsupported)
        provider, model_cap = self._registry.select_provider_and_model(request)
        estimated_cost = (
            model_cap.cost_per_second_usd * Decimal(request.duration_seconds)
        ).quantize(Decimal("0.000001"))
        self._enforce_budget_and_kill_switch(
            session,
            workspace_id=request.workspace_id,
            client_id=request.client_id,
            estimated_cost_usd=estimated_cost,
            duration_units=request.duration_seconds,
        )

        reference_images = self._resolve_reference_images(
            session,
            workspace_id=request.workspace_id,
            reference_asset_ids=request.reference_asset_ids,
        )

        job = VideoGenerationJob(
            id=uuid4(),
            workspace_id=request.workspace_id,
            client_id=request.client_id,
            project_id=request.project_id,
            actor_user_id=actor_user_id,
            mode=request.mode.value,
            provider=provider.provider_id,
            model=model_cap.model,
            provider_job_id=None,
            status=VideoJobStatus.QUEUED.value,
            progress_pct=0,
            idempotency_key=request.idempotency_key,
            prompt=request.prompt,
            aspect_ratio=request.aspect_ratio,
            duration_seconds=request.duration_seconds,
            resolution=request.resolution,
            audio_config=request.audio.model_dump(mode="json"),
            brand_constraints=_safe_dict(request.brand_constraints),
            provider_preference=request.provider_preference,
            request_params=_safe_dict({"fingerprint": fingerprint}),
            input_asset_ids=[str(aid) for aid in request.reference_asset_ids],
            output_asset_ids=[],
            estimated_cost_usd=estimated_cost,
        )
        session.add(job)
        session.flush()

        # Transition queued -> validating
        validate_transition(VideoJobStatus.QUEUED, VideoJobStatus.VALIDATING)
        job.status = VideoJobStatus.VALIDATING.value
        job.progress_pct = 5
        self._record_event(
            session,
            job=job,
            event_type="state_transition",
            from_status=VideoJobStatus.QUEUED,
            to_status=VideoJobStatus.VALIDATING,
            payload={"model": model_cap.model},
        )

        submission = ProviderSubmissionRequest(
            internal_job_id=job.id,
            workspace_id=job.workspace_id,
            client_id=job.client_id,
            mode=request.mode,
            model=model_cap.model,
            prompt=request.prompt,
            aspect_ratio=request.aspect_ratio,
            duration_seconds=request.duration_seconds,
            resolution=request.resolution,
            audio_generate=request.audio.generate,
            reference_images=reference_images,
            brand_constraints=_safe_dict(request.brand_constraints),
            idempotency_key=request.idempotency_key,
        )

        try:
            if request.mode == VideoGenerationMode.TEXT_TO_VIDEO:
                result = provider.create_text_to_video(submission)
            else:
                result = provider.create_image_to_video(submission)
        except MetuseError as exc:
            validate_transition(VideoJobStatus.VALIDATING, VideoJobStatus.FAILED)
            job.status = VideoJobStatus.FAILED.value
            job.failure_category = (
                VideoFailureCategory.CAPABILITY.value
                if exc.code == ErrorCode.CAPABILITY_NOT_SUPPORTED
                else VideoFailureCategory.PROVIDER_ERROR.value
            )
            job.provider_error_code = exc.code.value
            job.error_message = scrub_text(exc.message)
            job.completed_at = datetime.now(UTC)
            self._record_event(
                session,
                job=job,
                event_type="error",
                from_status=VideoJobStatus.VALIDATING,
                to_status=VideoJobStatus.FAILED,
                payload={"error_code": exc.code.value, "message": job.error_message},
            )
            session.add(
                AuditLog(
                    id=uuid4(),
                    workspace_id=job.workspace_id,
                    client_id=job.client_id,
                    actor_user_id=actor_user_id,
                    action=AuditAction.VIDEO_JOB_FAILED.value,
                    target_type="video_generation_job",
                    target_id=str(job.id),
                    request_id=request_id,
                    metadata_json={"provider": job.provider, "error_code": exc.code.value},
                )
            )
            session.flush()
            raise

        now = datetime.now(UTC)
        validate_transition(VideoJobStatus.VALIDATING, VideoJobStatus.SUBMITTED)
        job.status = VideoJobStatus.SUBMITTED.value
        job.provider_job_id = result.provider_job_id
        job.progress_pct = max(10, result.progress_pct)
        job.submitted_at = now
        job.next_poll_at = now + timedelta(
            seconds=compute_next_poll_delay_seconds(
                1,
                base_delay=self._settings.video_poll_base_delay_seconds,
                max_delay=self._settings.video_poll_max_delay_seconds,
            )
        )
        if result.estimated_cost_usd is not None:
            job.estimated_cost_usd = result.estimated_cost_usd
        self._record_event(
            session,
            job=job,
            event_type="state_transition",
            from_status=VideoJobStatus.VALIDATING,
            to_status=VideoJobStatus.SUBMITTED,
            payload={
                "provider_job_id": result.provider_job_id,
                "raw_metadata": result.raw_metadata,
            },
        )
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=job.workspace_id,
                client_id=job.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.VIDEO_JOB_CREATED.value,
                target_type="video_generation_job",
                target_id=str(job.id),
                request_id=request_id,
                metadata_json={
                    "mode": job.mode,
                    "provider": job.provider,
                    "model": job.model,
                    "provider_job_id": job.provider_job_id,
                    "estimated_cost_usd": str(job.estimated_cost_usd),
                },
            )
        )
        session.flush()

        if auto_poll_if_sync:
            return self.poll_job(
                session,
                workspace_id=job.workspace_id,
                job_id=job.id,
                actor_user_id=actor_user_id,
                request_id=request_id,
            )
        return _job_to_view(job)

    def list_jobs(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        client_id: UUID | None = None,
        limit: int = 50,
    ) -> list[VideoJobView]:
        stmt = select(VideoGenerationJob).where(VideoGenerationJob.workspace_id == workspace_id)
        if client_id is not None:
            stmt = stmt.where(VideoGenerationJob.client_id == client_id)
        stmt = stmt.order_by(VideoGenerationJob.created_at.desc()).limit(max(1, min(limit, 100)))
        rows = session.execute(stmt).scalars().all()
        return [_job_to_view(row) for row in rows]

    def _get_job_row(
        self, session: Session, *, workspace_id: UUID, job_id: UUID
    ) -> VideoGenerationJob:
        row = session.execute(
            select(VideoGenerationJob).where(
                VideoGenerationJob.workspace_id == workspace_id,
                VideoGenerationJob.id == job_id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(
                "Video generation job not found",
                details={"job_id": str(job_id)},
            )
        return row

    def get_job(self, session: Session, *, workspace_id: UUID, job_id: UUID) -> VideoJobView:
        return _job_to_view(self._get_job_row(session, workspace_id=workspace_id, job_id=job_id))

    def _finalize_succeeded_job(
        self,
        session: Session,
        *,
        job: VideoGenerationJob,
        poll_result: ProviderPollResult,
        actor_user_id: UUID | None,
        request_id: UUID | None,
    ) -> None:
        now = datetime.now(UTC)
        output_bytes = poll_result.output_bytes
        if not output_bytes:
            # Synthesize deterministic placeholder MP4 metadata stub if provider returned URL-only
            output_bytes = (
                b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom\x00\x00\x00\x08free"
                + f"metuse-ref:{job.provider}:{job.provider_job_id}".encode()
            )
        filename = poll_result.output_filename or f"{job.provider}-{job.id.hex[:12]}.mp4"
        asset_view = self.create_asset(
            session,
            workspace_id=job.workspace_id,
            actor_user_id=actor_user_id or job.actor_user_id,
            filename=filename,
            mime_type=poll_result.output_mime_type or "video/mp4",
            asset_kind=AssetKind.VIDEO,
            data=output_bytes,
            client_id=job.client_id,
            project_id=job.project_id,
            source_type=AssetSourceType.VIDEO_GENERATION,
            duration_seconds=poll_result.duration_seconds or Decimal(job.duration_seconds),
            width=poll_result.width,
            height=poll_result.height,
            metadata={
                "video_job_id": str(job.id),
                "provider": job.provider,
                "model": job.model,
                "provider_job_id": job.provider_job_id,
                "output_url": poll_result.output_url,
            },
            request_id=request_id,
        )

        job.output_asset_ids = [*job.output_asset_ids, str(asset_view.id)]
        job.progress_pct = 100
        job.completed_at = now
        job.next_poll_at = None
        final_cost = (
            poll_result.actual_cost_usd
            if poll_result.actual_cost_usd is not None
            else (job.estimated_cost_usd or Decimal("0.000000"))
        )
        job.actual_cost_usd = final_cost

        # Record Acceptance #17 cost attribution in usage_records
        session.add(
            UsageRecord(
                id=uuid4(),
                workspace_id=job.workspace_id,
                client_id=job.client_id,
                worker_job_id=None,
                video_job_id=job.id,
                model_run_id=None,
                provider=job.provider,
                model=job.model,
                capability=job.mode,
                input_units=job.duration_seconds,
                output_units=job.duration_seconds,
                estimated_cost_usd=final_cost,
                billing_period=_current_billing_period(now),
            )
        )
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=job.workspace_id,
                client_id=job.client_id,
                actor_user_id=actor_user_id or job.actor_user_id,
                action=AuditAction.VIDEO_JOB_COMPLETED.value,
                target_type="video_generation_job",
                target_id=str(job.id),
                request_id=request_id,
                metadata_json={
                    "provider": job.provider,
                    "model": job.model,
                    "output_asset_id": str(asset_view.id),
                    "actual_cost_usd": str(final_cost),
                },
            )
        )
        session.flush()

    def _apply_poll_result(
        self,
        session: Session,
        *,
        job: VideoGenerationJob,
        poll_result: ProviderPollResult,
        event_type: str,
        actor_user_id: UUID | None,
        request_id: UUID | None,
        provider_event_id: str | None = None,
    ) -> VideoJobView:
        current = VideoJobStatus(job.status)
        if current in TERMINAL_STATUSES:
            return _job_to_view(job)

        target = poll_result.status
        validate_transition(current, target)
        now = datetime.now(UTC)
        job.status = target.value
        job.progress_pct = max(job.progress_pct, poll_result.progress_pct)

        if target == VideoJobStatus.SUCCEEDED:
            self._finalize_succeeded_job(
                session,
                job=job,
                poll_result=poll_result,
                actor_user_id=actor_user_id,
                request_id=request_id,
            )
        elif target == VideoJobStatus.FAILED:
            job.failure_category = (
                poll_result.failure_category.value
                if poll_result.failure_category
                else VideoFailureCategory.PROVIDER_ERROR.value
            )
            job.provider_error_code = poll_result.provider_error_code
            job.error_message = (
                scrub_text(poll_result.error_message)
                if poll_result.error_message
                else "Video provider reported failure"
            )
            job.completed_at = now
            job.next_poll_at = None
            session.add(
                AuditLog(
                    id=uuid4(),
                    workspace_id=job.workspace_id,
                    client_id=job.client_id,
                    actor_user_id=actor_user_id or job.actor_user_id,
                    action=AuditAction.VIDEO_JOB_FAILED.value,
                    target_type="video_generation_job",
                    target_id=str(job.id),
                    request_id=request_id,
                    metadata_json={
                        "provider": job.provider,
                        "failure_category": job.failure_category,
                        "provider_error_code": job.provider_error_code,
                    },
                )
            )
        elif target == VideoJobStatus.CANCELLED:
            job.failure_category = VideoFailureCategory.CANCELLED.value
            job.cancelled_at = now
            job.completed_at = now
            job.next_poll_at = None
        else:
            job.poll_attempts += 1
            if job.poll_attempts >= self._settings.video_max_poll_attempts:
                validate_transition(target, VideoJobStatus.FAILED)
                job.status = VideoJobStatus.FAILED.value
                job.failure_category = VideoFailureCategory.TIMEOUT.value
                job.error_message = "Video generation exceeded maximum poll attempts"
                job.completed_at = now
                job.next_poll_at = None
                target = VideoJobStatus.FAILED
            else:
                delay = compute_next_poll_delay_seconds(
                    job.poll_attempts,
                    base_delay=self._settings.video_poll_base_delay_seconds,
                    max_delay=self._settings.video_poll_max_delay_seconds,
                )
                job.next_poll_at = now + timedelta(seconds=delay)

        self._record_event(
            session,
            job=job,
            event_type=event_type,
            from_status=current,
            to_status=target,
            provider_event_id=provider_event_id,
            payload={
                "progress_pct": job.progress_pct,
                "provider_error_code": poll_result.provider_error_code,
                "raw_metadata": poll_result.raw_metadata,
            },
        )
        session.flush()
        return _job_to_view(job)

    def poll_job(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        job_id: UUID,
        actor_user_id: UUID | None = None,
        request_id: UUID | None = None,
    ) -> VideoJobView:
        """Restart-safe poll of a single video job from PostgreSQL state."""
        job = self._get_job_row(session, workspace_id=workspace_id, job_id=job_id)
        if VideoJobStatus(job.status) in TERMINAL_STATUSES:
            return _job_to_view(job)
        if not job.provider_job_id:
            raise ExternalServiceError("Video job does not have a provider_job_id yet")

        provider = self._registry.get_provider(job.provider)
        poll_result = provider.get_job(job.provider_job_id)
        return self._apply_poll_result(
            session,
            job=job,
            poll_result=poll_result,
            event_type="poll",
            actor_user_id=actor_user_id,
            request_id=request_id,
        )

    def poll_pending_jobs(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        limit: int = 20,
    ) -> list[VideoJobView]:
        """Restart-safe batch poll of all non-terminal jobs in a workspace."""
        stmt = (
            select(VideoGenerationJob)
            .where(
                VideoGenerationJob.workspace_id == workspace_id,
                VideoGenerationJob.status.in_(
                    [VideoJobStatus.SUBMITTED.value, VideoJobStatus.PROCESSING.value]
                ),
            )
            .order_by(VideoGenerationJob.created_at.asc())
            .limit(max(1, min(limit, 100)))
        )
        jobs = session.execute(stmt).scalars().all()
        results: list[VideoJobView] = []
        for job in jobs:
            results.append(self.poll_job(session, workspace_id=workspace_id, job_id=job.id))
        return results

    def cancel_job(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        job_id: UUID,
        actor_user_id: UUID,
        request_id: UUID | None = None,
    ) -> VideoJobView:
        job = self._get_job_row(session, workspace_id=workspace_id, job_id=job_id)
        current = VideoJobStatus(job.status)
        if current == VideoJobStatus.CANCELLED:
            return _job_to_view(job)
        validate_transition(current, VideoJobStatus.CANCELLED)

        raw_cancel: dict[str, Any] = {}
        if job.provider_job_id:
            provider = self._registry.get_provider(job.provider)
            cancel_res = provider.cancel_job(job.provider_job_id)
            raw_cancel = cancel_res.raw_metadata

        now = datetime.now(UTC)
        job.status = VideoJobStatus.CANCELLED.value
        job.failure_category = VideoFailureCategory.CANCELLED.value
        job.cancelled_at = now
        job.completed_at = now
        job.next_poll_at = None

        self._record_event(
            session,
            job=job,
            event_type="cancel",
            from_status=current,
            to_status=VideoJobStatus.CANCELLED,
            payload=raw_cancel,
        )
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=job.workspace_id,
                client_id=job.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.VIDEO_JOB_CANCELLED.value,
                target_type="video_generation_job",
                target_id=str(job.id),
                request_id=request_id,
                metadata_json={"provider": job.provider, "provider_job_id": job.provider_job_id},
            )
        )
        session.flush()
        return _job_to_view(job)

    def handle_webhook(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        provider_id: str,
        raw_body: bytes,
        headers: dict[str, str],
        request_id: UUID | None = None,
    ) -> VideoJobView:
        provider = self._registry.get_provider(provider_id)
        verified = provider.verify_and_parse_webhook(raw_body=raw_body, headers=headers)

        job = session.execute(
            select(VideoGenerationJob).where(
                VideoGenerationJob.workspace_id == workspace_id,
                VideoGenerationJob.provider == provider.provider_id,
                VideoGenerationJob.provider_job_id == verified.provider_job_id,
            )
        ).scalar_one_or_none()
        if job is None:
            raise NotFoundError(
                "Video job referenced by webhook not found in workspace",
                details={
                    "provider": provider.provider_id,
                    "provider_job_id": verified.provider_job_id,
                },
            )

        # Deduplicate webhook events by (workspace_id, provider, provider_event_id)
        existing_event = session.execute(
            select(VideoProviderEvent).where(
                VideoProviderEvent.workspace_id == workspace_id,
                VideoProviderEvent.provider == provider.provider_id,
                VideoProviderEvent.provider_event_id == verified.provider_event_id,
            )
        ).scalar_one_or_none()
        if existing_event is not None:
            return _job_to_view(job)

        return self._apply_poll_result(
            session,
            job=job,
            poll_result=verified.poll_result,
            event_type="webhook",
            actor_user_id=job.actor_user_id,
            request_id=request_id,
            provider_event_id=verified.provider_event_id,
        )
