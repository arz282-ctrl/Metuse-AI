"""Phase 4 Cloud Editing, Media QA, and Version-Bound Approval service.

Implements IMPLEMENTATION.md Sections 7, 9, 10, 11, 14, and Acceptance Tests #11, #12, #13, #17.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import (
    Approval,
    Asset,
    AuditLog,
    Client,
    CreativeVersion,
    EditingPlan,
    MediaMetadata,
    Project,
    RenderJob,
    RevisionRequest,
    UsageRecord,
    WorkspaceBudget,
)
from packages.media_engine.contracts import (
    ApprovalView,
    CreativeVersionView,
    EditingPlanView,
    MediaMetadataView,
    RenderJobView,
    RevisionRequestView,
    TimelinePlan,
    UploadUrlResponse,
)
from packages.media_engine.probe import build_synthetic_mp4_bytes, probe_media_bytes
from packages.media_engine.qa import run_media_qa
from packages.media_engine.renderer import RenderBackend
from packages.shared.config import BaseAppSettings
from packages.shared.enums import (
    ApprovalStatus,
    ApprovalType,
    AssetKind,
    AssetSourceType,
    AuditAction,
    ErrorCode,
    RenderJobStatus,
    RenderKind,
)
from packages.shared.errors import (
    BudgetExceededError,
    ConflictError,
    MetuseError,
    NotFoundError,
    ProviderDisabledError,
    ValidationDomainError,
)
from packages.storage.keys import build_tenant_object_key, sanitize_filename, validate_storage_key
from packages.storage.protocol import StorageBackend
from packages.storage.validation import validate_upload_metadata
from packages.video_gateway.contracts import AssetView, ProjectView


def _billing_period(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y-%m")


def _sha256_json(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _project_view(row: Project) -> ProjectView:
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


def _asset_view(row: Asset) -> AssetView:
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


def _metadata_view(row: MediaMetadata) -> MediaMetadataView:
    return MediaMetadataView(
        id=row.id,
        workspace_id=row.workspace_id,
        asset_id=row.asset_id,
        proxy_asset_id=row.proxy_asset_id,
        container=row.container,
        video_codec=row.video_codec,
        audio_codec=row.audio_codec,
        duration_ms=row.duration_ms,
        fps=row.fps,
        width=row.width,
        height=row.height,
        bitrate_bps=row.bitrate_bps,
        has_audio=row.has_audio,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _plan_view(row: EditingPlan) -> EditingPlanView:
    return EditingPlanView(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        project_id=row.project_id,
        version_number=row.version_number,
        schema_version=row.schema_version,
        plan=TimelinePlan.model_validate(row.plan_json),
        plan_sha256=row.plan_sha256,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
    )


def _render_job_view(row: RenderJob) -> RenderJobView:
    return RenderJobView(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        project_id=row.project_id,
        editing_plan_id=row.editing_plan_id,
        actor_user_id=row.actor_user_id,
        render_kind=RenderKind(row.render_kind),
        status=RenderJobStatus(row.status),
        idempotency_key=row.idempotency_key,
        renderer=row.renderer,
        command_manifest=list(row.command_manifest),
        output_asset_id=row.output_asset_id,
        qa_passed=row.qa_passed,
        qa_report=dict(row.qa_report),
        duration_ms=row.duration_ms,
        cpu_time_ms=row.cpu_time_ms,
        estimated_cost_usd=row.estimated_cost_usd,
        error_code=row.error_code,
        error_message=row.error_message,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _creative_version_view(row: CreativeVersion) -> CreativeVersionView:
    return CreativeVersionView(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        project_id=row.project_id,
        editing_plan_id=row.editing_plan_id,
        render_job_id=row.render_job_id,
        output_asset_id=row.output_asset_id,
        version_number=row.version_number,
        render_kind=RenderKind(row.render_kind),
        content_sha256=row.content_sha256,
        qa_passed=row.qa_passed,
        qa_report=dict(row.qa_report),
        is_approved=row.is_approved,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
    )


def _approval_view(row: Approval) -> ApprovalView:
    return ApprovalView(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        project_id=row.project_id,
        creative_version_id=row.creative_version_id,
        approval_type=ApprovalType(row.approval_type),
        target_type=row.target_type,
        target_id=row.target_id,
        target_version_sha256=row.target_version_sha256,
        status=ApprovalStatus(row.status),
        requested_by_user_id=row.requested_by_user_id,
        decided_by_user_id=row.decided_by_user_id,
        decision_notes=row.decision_notes,
        invalidated_reason=row.invalidated_reason,
        decided_at=row.decided_at,
        invalidated_at=row.invalidated_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _revision_view(row: RevisionRequest) -> RevisionRequestView:
    return RevisionRequestView(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        project_id=row.project_id,
        creative_version_id=row.creative_version_id,
        approval_id=row.approval_id,
        requested_by_user_id=row.requested_by_user_id,
        notes=row.notes,
        timecode_ms=row.timecode_ms,
        resolved=row.resolved,
        created_at=row.created_at,
    )


class CloudEditingService:
    """Orchestrates uploads, probing, timeline plans, isolated rendering, QA, and approvals."""

    def __init__(
        self,
        *,
        settings: BaseAppSettings,
        storage: StorageBackend,
        renderer: RenderBackend,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._renderer = renderer

    def _get_project_row(
        self, session: Session, *, workspace_id: UUID, project_id: UUID
    ) -> Project:
        row = session.execute(
            select(Project).where(Project.workspace_id == workspace_id, Project.id == project_id)
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Project not found", details={"project_id": str(project_id)})
        return row

    def get_project(self, session: Session, *, workspace_id: UUID, project_id: UUID) -> ProjectView:
        return _project_view(
            self._get_project_row(session, workspace_id=workspace_id, project_id=project_id)
        )

    def _build_storage_key(
        self,
        *,
        workspace_id: UUID,
        client_id: UUID | None,
        project_id: UUID | None,
        category: str,
        asset_id: UUID,
        filename: str,
    ) -> str:
        if client_id is not None:
            return build_tenant_object_key(
                workspace_id=workspace_id,
                client_id=client_id,
                category=category,
                asset_id=asset_id,
                filename=filename,
                project_id=project_id
                if category in {"editing-plans", "previews", "exports"}
                else None,
            )
        safe_name = sanitize_filename(filename)
        if project_id is not None and category in {"editing-plans", "previews", "exports"}:
            key = (
                f"workspaces/{str(workspace_id).lower()}/projects/{str(project_id).lower()}/"
                f"{category}/{str(asset_id).lower()}/{safe_name}"
            )
        else:
            key = (
                f"workspaces/{str(workspace_id).lower()}/{category}/"
                f"{str(asset_id).lower()}/{safe_name}"
            )
        return validate_storage_key(key, expected_workspace_id=workspace_id)

    def create_upload_url(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        filename: str,
        mime_type: str,
        size_bytes: int,
        asset_kind: AssetKind,
        client_id: UUID | None = None,
        project_id: UUID | None = None,
        request_id: UUID | None = None,
    ) -> UploadUrlResponse:
        if client_id is not None:
            client = session.execute(
                select(Client).where(Client.workspace_id == workspace_id, Client.id == client_id)
            ).scalar_one_or_none()
            if client is None:
                raise NotFoundError("Client not found", details={"client_id": str(client_id)})
        if project_id is not None:
            self._get_project_row(session, workspace_id=workspace_id, project_id=project_id)

        verified_mime = validate_upload_metadata(
            filename=filename,
            declared_mime=mime_type,
            size_bytes=size_bytes,
            max_size_bytes=self._settings.render_max_output_bytes,
        )
        asset_id = uuid4()
        storage_key = self._build_storage_key(
            workspace_id=workspace_id,
            client_id=client_id,
            project_id=project_id,
            category="uploads/originals",
            asset_id=asset_id,
            filename=filename,
        )
        presigned = self._storage.create_presigned_put_url(
            key=storage_key,
            content_type=verified_mime,
            content_length=size_bytes,
        )

        asset = Asset(
            id=asset_id,
            workspace_id=workspace_id,
            client_id=client_id,
            project_id=project_id,
            asset_kind=asset_kind.value,
            source_type=AssetSourceType.UPLOAD.value,
            filename=sanitize_filename(filename),
            mime_type=verified_mime,
            size_bytes=size_bytes,
            storage_bucket=presigned.bucket,
            storage_key=presigned.key,
            status="pending",
            metadata_json={"upload_state": "awaiting_complete"},
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
                action=AuditAction.ASSET_UPLOAD_URL_CREATED.value,
                target_type="asset",
                target_id=str(asset.id),
                request_id=request_id,
                metadata_json={"mime_type": verified_mime, "size_bytes": size_bytes},
            )
        )
        session.flush()
        return UploadUrlResponse(
            asset_id=asset.id,
            url=presigned.url,
            method=presigned.method,
            headers=presigned.headers,
            bucket=presigned.bucket,
            key=presigned.key,
            expires_in=presigned.expires_in,
        )

    def complete_upload(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        asset_id: UUID,
        request_id: UUID | None = None,
    ) -> tuple[AssetView, MediaMetadataView]:
        """Validate uploaded magic bytes, extract metadata, and generate a lightweight proxy."""
        asset = session.execute(
            select(Asset).where(Asset.workspace_id == workspace_id, Asset.id == asset_id)
        ).scalar_one_or_none()
        if asset is None:
            raise NotFoundError("Asset not found", details={"asset_id": str(asset_id)})

        head = self._storage.head_object(key=asset.storage_key)
        sample_bytes = self._storage.read_object_prefix(
            key=asset.storage_key, num_bytes=min(head.size_bytes, 65536)
        )
        probe = probe_media_bytes(sample_bytes, declared_mime=asset.mime_type)
        if not probe.decodable:
            raise ValidationDomainError(
                f"Uploaded media failed validation: {probe.error or 'corrupt container'}",
                details={"asset_id": str(asset.id)},
            )

        asset.size_bytes = head.size_bytes
        asset.sha256 = hashlib.sha256(sample_bytes).hexdigest()
        asset.width = probe.width
        asset.height = probe.height
        asset.duration_seconds = (
            (Decimal(probe.duration_ms) / Decimal(1000)).quantize(Decimal("0.001"))
            if probe.duration_ms is not None
            else None
        )
        asset.status = "ready"
        asset.metadata_json = {**asset.metadata_json, "upload_state": "verified"}

        # Generate separate lightweight proxy asset for video uploads (preserving original)
        proxy_asset_id: UUID | None = None
        if asset.asset_kind == AssetKind.VIDEO.value:
            proxy_id = uuid4()
            proxy_bytes = build_synthetic_mp4_bytes(
                width=min(720, probe.width or 720),
                height=min(1280, probe.height or 1280),
                duration_ms=probe.duration_ms or 5000,
                fps=int(probe.fps or 30),
                has_audio=probe.has_audio,
                tag=f"proxy-{asset.id.hex[:8]}",
            )
            proxy_key = self._build_storage_key(
                workspace_id=workspace_id,
                client_id=asset.client_id,
                project_id=asset.project_id,
                category="uploads/proxies",
                asset_id=proxy_id,
                filename=f"proxy-{asset.filename}",
            )
            proxy_meta = self._storage.put_object_bytes(
                key=proxy_key,
                data=proxy_bytes,
                content_type="video/mp4",
            )
            proxy_row = Asset(
                id=proxy_id,
                workspace_id=workspace_id,
                client_id=asset.client_id,
                project_id=asset.project_id,
                asset_kind=AssetKind.VIDEO.value,
                source_type=AssetSourceType.RENDER.value,
                filename=sanitize_filename(f"proxy-{asset.filename}"),
                mime_type="video/mp4",
                size_bytes=proxy_meta.size_bytes,
                storage_bucket=proxy_meta.bucket,
                storage_key=proxy_meta.key,
                duration_seconds=asset.duration_seconds,
                width=min(720, probe.width or 720),
                height=min(1280, probe.height or 1280),
                sha256=hashlib.sha256(proxy_bytes).hexdigest(),
                status="ready",
                metadata_json={"proxy_for_asset_id": str(asset.id)},
                created_by_user_id=actor_user_id,
            )
            session.add(proxy_row)
            session.flush()
            proxy_asset_id = proxy_row.id

        meta_row = session.execute(
            select(MediaMetadata).where(
                MediaMetadata.workspace_id == workspace_id,
                MediaMetadata.asset_id == asset.id,
            )
        ).scalar_one_or_none()
        if meta_row is None:
            meta_row = MediaMetadata(
                id=uuid4(),
                workspace_id=workspace_id,
                asset_id=asset.id,
            )
            session.add(meta_row)

        meta_row.proxy_asset_id = proxy_asset_id
        meta_row.container = probe.container
        meta_row.video_codec = probe.video_codec
        meta_row.audio_codec = probe.audio_codec
        meta_row.duration_ms = probe.duration_ms
        meta_row.fps = probe.fps
        meta_row.width = probe.width
        meta_row.height = probe.height
        meta_row.bitrate_bps = probe.bitrate_bps
        meta_row.has_audio = probe.has_audio
        meta_row.probe_payload = probe.model_dump(mode="json")

        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=asset.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.ASSET_UPLOAD_COMPLETED.value,
                target_type="asset",
                target_id=str(asset.id),
                request_id=request_id,
                metadata_json={"proxy_asset_id": str(proxy_asset_id) if proxy_asset_id else None},
            )
        )
        session.flush()
        return _asset_view(asset), _metadata_view(meta_row)

    def analyze_project(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        project_id: UUID,
        request_id: UUID | None = None,
    ) -> list[MediaMetadataView]:
        """Probe all ready assets linked to a project and ensure MediaMetadata rows exist."""
        self._get_project_row(session, workspace_id=workspace_id, project_id=project_id)
        assets = (
            session.execute(
                select(Asset).where(
                    Asset.workspace_id == workspace_id,
                    Asset.project_id == project_id,
                    Asset.status == "ready",
                )
            )
            .scalars()
            .all()
        )
        results: list[MediaMetadataView] = []
        for asset in assets:
            existing = session.execute(
                select(MediaMetadata).where(
                    MediaMetadata.workspace_id == workspace_id,
                    MediaMetadata.asset_id == asset.id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                results.append(_metadata_view(existing))
                continue
            _, meta_view = self.complete_upload(
                session,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                asset_id=asset.id,
                request_id=request_id,
            )
            results.append(meta_view)
        return results

    def _invalidate_project_approvals(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        project_id: UUID,
        actor_user_id: UUID,
        reason: str,
        request_id: UUID | None = None,
    ) -> int:
        """Invalidate any active/approved approvals for a project when edited (Acceptance #13)."""
        active_approvals = (
            session.execute(
                select(Approval).where(
                    Approval.workspace_id == workspace_id,
                    Approval.project_id == project_id,
                    Approval.status.in_(
                        [ApprovalStatus.PENDING.value, ApprovalStatus.APPROVED.value]
                    ),
                )
            )
            .scalars()
            .all()
        )
        now = datetime.now(UTC)
        count = 0
        for appr in active_approvals:
            appr.status = ApprovalStatus.INVALIDATED.value
            appr.invalidated_reason = reason
            appr.invalidated_at = now
            if appr.creative_version_id is not None:
                ver = session.execute(
                    select(CreativeVersion).where(
                        CreativeVersion.workspace_id == workspace_id,
                        CreativeVersion.id == appr.creative_version_id,
                    )
                ).scalar_one_or_none()
                if ver is not None:
                    ver.is_approved = False
            session.add(
                AuditLog(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    client_id=appr.client_id,
                    actor_user_id=actor_user_id,
                    action=AuditAction.APPROVAL_INVALIDATED.value,
                    target_type="approval",
                    target_id=str(appr.id),
                    request_id=request_id,
                    metadata_json={"reason": reason, "project_id": str(project_id)},
                )
            )
            count += 1
        session.flush()
        return count

    def create_editing_plan(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        project_id: UUID,
        plan: TimelinePlan,
        request_id: UUID | None = None,
    ) -> EditingPlanView:
        """Validate asset ownership, persist a new EditingPlan, and invalidate prior approvals."""
        project = self._get_project_row(session, workspace_id=workspace_id, project_id=project_id)
        if plan.project_id != project_id:
            raise ValidationDomainError("TimelinePlan project_id must match the target project_id")

        # Validate every referenced asset exists and belongs to the active workspace
        ref_ids = plan.referenced_asset_ids()
        found_assets = (
            session.execute(
                select(Asset.id).where(
                    Asset.workspace_id == workspace_id,
                    Asset.id.in_(ref_ids),
                    Asset.status == "ready",
                )
            )
            .scalars()
            .all()
        )
        missing = sorted(str(aid) for aid in (set(ref_ids) - set(found_assets)))
        if missing:
            raise NotFoundError(
                "One or more timeline assets were not found in the active workspace",
                details={"missing_asset_ids": missing},
            )

        max_ver = session.execute(
            select(func.coalesce(func.max(EditingPlan.version_number), 0)).where(
                EditingPlan.workspace_id == workspace_id,
                EditingPlan.project_id == project_id,
            )
        ).scalar_one()
        next_version = int(max_ver) + 1
        plan_dict = plan.model_dump(mode="json")
        plan_sha = _sha256_json(plan_dict)

        row = EditingPlan(
            id=uuid4(),
            workspace_id=workspace_id,
            client_id=project.client_id,
            project_id=project_id,
            version_number=next_version,
            schema_version=plan.schema_version,
            plan_json=plan_dict,
            plan_sha256=plan_sha,
            created_by_user_id=actor_user_id,
        )
        session.add(row)
        session.flush()

        # Acceptance #13: modifying an approved project invalidates prior approvals!
        self._invalidate_project_approvals(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=actor_user_id,
            reason=f"Editing plan updated to v{next_version} ({plan_sha[:12]})",
            request_id=request_id,
        )

        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=project.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.EDITING_PLAN_CREATED.value,
                target_type="editing_plan",
                target_id=str(row.id),
                request_id=request_id,
                metadata_json={"version_number": next_version, "plan_sha256": plan_sha},
            )
        )
        session.flush()
        return _plan_view(row)

    def list_editing_plans(
        self, session: Session, *, workspace_id: UUID, project_id: UUID
    ) -> list[EditingPlanView]:
        self._get_project_row(session, workspace_id=workspace_id, project_id=project_id)
        rows = (
            session.execute(
                select(EditingPlan)
                .where(
                    EditingPlan.workspace_id == workspace_id,
                    EditingPlan.project_id == project_id,
                )
                .order_by(EditingPlan.version_number.desc())
            )
            .scalars()
            .all()
        )
        return [_plan_view(r) for r in rows]

    def _enforce_render_budget_and_kill_switch(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        client_id: UUID | None,
        estimated_cost_usd: Decimal,
        duration_units: int,
    ) -> None:
        if self._settings.render_kill_switch:
            raise ProviderDisabledError(
                "Cloud render workers are disabled via global RENDER_KILL_SWITCH",
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
        period = _billing_period()
        for label, budget, scope_client_id in (
            ("workspace", ws_budget, None),
            ("client", client_budget, client_id),
        ):
            if budget is None:
                continue
            if budget.provider_kill_switch:
                raise ProviderDisabledError(
                    f"Rendering disabled by {label} kill switch",
                    details={"scope": label},
                )
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
            if (
                budget.monthly_unit_limit is not None
                and int(used_units_raw) + duration_units > budget.monthly_unit_limit
            ):
                raise BudgetExceededError(f"Monthly {label} usage limit would be exceeded")
            if (
                budget.monthly_cost_limit_usd is not None
                and Decimal(str(used_cost_raw)) + estimated_cost_usd > budget.monthly_cost_limit_usd
            ):
                raise BudgetExceededError(f"Monthly {label} cost budget would be exceeded")

    def render_project(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        project_id: UUID,
        render_kind: RenderKind,
        editing_plan_id: UUID | None = None,
        idempotency_key: str | None = None,
        expect_audio: bool = True,
        request_id: UUID | None = None,
    ) -> tuple[RenderJobView, CreativeVersionView | None]:
        """Execute isolated preview or final render, run Section 7.5 Media QA, and save version."""
        project = self._get_project_row(session, workspace_id=workspace_id, project_id=project_id)

        if idempotency_key:
            existing_job = session.execute(
                select(RenderJob).where(
                    RenderJob.workspace_id == workspace_id,
                    RenderJob.idempotency_key == idempotency_key,
                )
            ).scalar_one_or_none()
            if existing_job is not None:
                if (
                    existing_job.project_id != project_id
                    or existing_job.render_kind != render_kind.value
                    or (
                        editing_plan_id is not None
                        and existing_job.editing_plan_id != editing_plan_id
                    )
                ):
                    raise ConflictError(
                        "Idempotency-Key already used with different render parameters",
                        code=ErrorCode.IDEMPOTENCY_CONFLICT,
                        details={"idempotency_key": idempotency_key},
                    )
                existing_ver = session.execute(
                    select(CreativeVersion).where(
                        CreativeVersion.workspace_id == workspace_id,
                        CreativeVersion.render_job_id == existing_job.id,
                    )
                ).scalar_one_or_none()
                return (
                    _render_job_view(existing_job),
                    _creative_version_view(existing_ver) if existing_ver else None,
                )

        if editing_plan_id is not None:
            plan_row = session.execute(
                select(EditingPlan).where(
                    EditingPlan.workspace_id == workspace_id,
                    EditingPlan.project_id == project_id,
                    EditingPlan.id == editing_plan_id,
                )
            ).scalar_one_or_none()
        else:
            plan_row = session.execute(
                select(EditingPlan)
                .where(
                    EditingPlan.workspace_id == workspace_id,
                    EditingPlan.project_id == project_id,
                )
                .order_by(EditingPlan.version_number.desc())
                .limit(1)
            ).scalar_one_or_none()

        if plan_row is None:
            raise NotFoundError(
                "No editing plan found for project; create an editing plan first",
                details={"project_id": str(project_id)},
            )

        plan = TimelinePlan.model_validate(plan_row.plan_json)
        duration_sec = max(1, (plan.total_duration_ms() + 999) // 1000)
        estimated_cost = (
            Decimal(str(self._settings.render_cost_per_second_usd)) * Decimal(duration_sec)
        ).quantize(Decimal("0.000001"))
        self._enforce_render_budget_and_kill_switch(
            session,
            workspace_id=workspace_id,
            client_id=project.client_id,
            estimated_cost_usd=estimated_cost,
            duration_units=duration_sec,
        )

        # Load referenced asset bytes from private storage
        ref_ids = plan.referenced_asset_ids()
        asset_rows = (
            session.execute(
                select(Asset).where(
                    Asset.workspace_id == workspace_id,
                    Asset.id.in_(ref_ids),
                    Asset.status == "ready",
                )
            )
            .scalars()
            .all()
        )
        available_ids = {row.id for row in asset_rows}
        asset_bytes: dict[UUID, bytes] = {}
        for row in asset_rows:
            head = self._storage.head_object(key=row.storage_key)
            asset_bytes[row.id] = self._storage.read_object_prefix(
                key=row.storage_key, num_bytes=max(64, head.size_bytes)
            )

        now = datetime.now(UTC)
        job = RenderJob(
            id=uuid4(),
            workspace_id=workspace_id,
            client_id=project.client_id,
            project_id=project_id,
            editing_plan_id=plan_row.id,
            actor_user_id=actor_user_id,
            render_kind=render_kind.value,
            status=RenderJobStatus.RENDERING.value,
            idempotency_key=idempotency_key,
            renderer=self._renderer.renderer_name,
            estimated_cost_usd=estimated_cost,
            started_at=now,
        )
        session.add(job)
        session.flush()

        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=project.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.RENDER_JOB_CREATED.value,
                target_type="render_job",
                target_id=str(job.id),
                request_id=request_id,
                metadata_json={"render_kind": render_kind.value, "plan_id": str(plan_row.id)},
            )
        )

        render_output = self._renderer.render(
            plan=plan,
            asset_bytes_by_id=asset_bytes,
            render_kind=render_kind,
        )
        job.command_manifest = list(render_output.command_manifest)
        job.cpu_time_ms = render_output.cpu_time_ms
        job.status = RenderJobStatus.QA_CHECKING.value

        # Automated Section 7.5 Media QA Gate (Acceptance #12)
        probe = probe_media_bytes(render_output.output_bytes, declared_mime=render_output.mime_type)
        qa_report = run_media_qa(
            plan=plan,
            probe=probe,
            available_asset_ids=available_ids,
            expect_audio=expect_audio,
        )
        job.qa_passed = qa_report.passed
        job.qa_report = qa_report.model_dump(mode="json")
        job.duration_ms = probe.duration_ms
        completed_at = datetime.now(UTC)
        job.completed_at = completed_at

        # Record Acceptance #17 render cost attribution regardless of QA pass/fail
        session.add(
            UsageRecord(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=project.client_id,
                worker_job_id=None,
                video_job_id=None,
                render_job_id=job.id,
                model_run_id=None,
                provider=render_output.renderer_name,
                model=f"render-{render_kind.value}",
                capability=f"cloud_render_{render_kind.value}",
                input_units=duration_sec,
                output_units=duration_sec,
                estimated_cost_usd=estimated_cost,
                billing_period=_billing_period(completed_at),
            )
        )

        if not qa_report.passed:
            job.status = RenderJobStatus.QA_FAILED.value
            job.error_code = ErrorCode.MEDIA_QA_FAILED.value
            job.error_message = (
                f"Automated Media QA rejected output: {', '.join(qa_report.failed_checks)}"
            )
            session.add(
                AuditLog(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    client_id=project.client_id,
                    actor_user_id=actor_user_id,
                    action=AuditAction.RENDER_JOB_FAILED.value,
                    target_type="render_job",
                    target_id=str(job.id),
                    request_id=request_id,
                    metadata_json={"failed_checks": qa_report.failed_checks},
                )
            )
            session.flush()
            return _render_job_view(job), None

        # QA passed -> store output MP4 as private tenant-owned Asset + create CreativeVersion
        out_asset_id = uuid4()
        category = "previews" if render_kind == RenderKind.PREVIEW else "exports"
        filename = f"{project.slug}-{render_kind.value}-{job.id.hex[:8]}.mp4"
        out_key = self._build_storage_key(
            workspace_id=workspace_id,
            client_id=project.client_id,
            project_id=project_id,
            category=category,
            asset_id=out_asset_id,
            filename=filename,
        )
        stored = self._storage.put_object_bytes(
            key=out_key,
            data=render_output.output_bytes,
            content_type="video/mp4",
        )
        content_sha = hashlib.sha256(render_output.output_bytes).hexdigest()
        out_asset = Asset(
            id=out_asset_id,
            workspace_id=workspace_id,
            client_id=project.client_id,
            project_id=project_id,
            asset_kind=AssetKind.VIDEO.value,
            source_type=AssetSourceType.RENDER.value,
            filename=sanitize_filename(filename),
            mime_type="video/mp4",
            size_bytes=stored.size_bytes,
            storage_bucket=stored.bucket,
            storage_key=stored.key,
            duration_seconds=(
                (Decimal(probe.duration_ms) / Decimal(1000)).quantize(Decimal("0.001"))
                if probe.duration_ms is not None
                else None
            ),
            width=probe.width,
            height=probe.height,
            sha256=content_sha,
            status="ready",
            metadata_json={"render_job_id": str(job.id), "render_kind": render_kind.value},
            created_by_user_id=actor_user_id,
        )
        session.add(out_asset)
        session.flush()

        job.output_asset_id = out_asset.id
        job.status = RenderJobStatus.SUCCEEDED.value

        max_cv = session.execute(
            select(func.coalesce(func.max(CreativeVersion.version_number), 0)).where(
                CreativeVersion.workspace_id == workspace_id,
                CreativeVersion.project_id == project_id,
            )
        ).scalar_one()
        cv = CreativeVersion(
            id=uuid4(),
            workspace_id=workspace_id,
            client_id=project.client_id,
            project_id=project_id,
            editing_plan_id=plan_row.id,
            render_job_id=job.id,
            output_asset_id=out_asset.id,
            version_number=int(max_cv) + 1,
            render_kind=render_kind.value,
            content_sha256=content_sha,
            qa_passed=True,
            qa_report=qa_report.model_dump(mode="json"),
            is_approved=False,
            created_by_user_id=actor_user_id,
        )
        session.add(cv)
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=project.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.RENDER_JOB_COMPLETED.value,
                target_type="render_job",
                target_id=str(job.id),
                request_id=request_id,
                metadata_json={
                    "output_asset_id": str(out_asset.id),
                    "creative_version_id": str(cv.id),
                    "version_number": cv.version_number,
                },
            )
        )
        session.flush()
        return _render_job_view(job), _creative_version_view(cv)

    def get_render_job(
        self, session: Session, *, workspace_id: UUID, job_id: UUID
    ) -> RenderJobView:
        row = session.execute(
            select(RenderJob).where(RenderJob.workspace_id == workspace_id, RenderJob.id == job_id)
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Render job not found", details={"job_id": str(job_id)})
        return _render_job_view(row)

    def list_project_versions(
        self, session: Session, *, workspace_id: UUID, project_id: UUID
    ) -> list[CreativeVersionView]:
        self._get_project_row(session, workspace_id=workspace_id, project_id=project_id)
        rows = (
            session.execute(
                select(CreativeVersion)
                .where(
                    CreativeVersion.workspace_id == workspace_id,
                    CreativeVersion.project_id == project_id,
                )
                .order_by(CreativeVersion.version_number.desc())
            )
            .scalars()
            .all()
        )
        return [_creative_version_view(r) for r in rows]

    def create_approval(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        creative_version_id: UUID,
        approval_type: ApprovalType = ApprovalType.CREATIVE,
        request_id: UUID | None = None,
    ) -> ApprovalView:
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
        if not cv.qa_passed:
            raise MetuseError(
                "Cannot request approval for a creative version that failed Media QA",
                code=ErrorCode.MEDIA_QA_FAILED,
                status_code=422,
            )

        appr = Approval(
            id=uuid4(),
            workspace_id=workspace_id,
            client_id=cv.client_id,
            project_id=cv.project_id,
            creative_version_id=cv.id,
            approval_type=approval_type.value,
            target_type="creative_version",
            target_id=cv.id,
            target_version_sha256=cv.content_sha256,
            status=ApprovalStatus.PENDING.value,
            requested_by_user_id=actor_user_id,
        )
        session.add(appr)
        session.flush()
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=cv.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.APPROVAL_CREATED.value,
                target_type="approval",
                target_id=str(appr.id),
                request_id=request_id,
                metadata_json={
                    "creative_version_id": str(cv.id),
                    "target_version_sha256": cv.content_sha256,
                },
            )
        )
        session.flush()
        return _approval_view(appr)

    def list_approvals(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        project_id: UUID | None = None,
        status: ApprovalStatus | None = None,
    ) -> list[ApprovalView]:
        stmt = select(Approval).where(Approval.workspace_id == workspace_id)
        if project_id is not None:
            stmt = stmt.where(Approval.project_id == project_id)
        if status is not None:
            stmt = stmt.where(Approval.status == status.value)
        stmt = stmt.order_by(Approval.created_at.desc())
        rows = session.execute(stmt).scalars().all()
        return [_approval_view(r) for r in rows]

    def _get_approval_row(
        self, session: Session, *, workspace_id: UUID, approval_id: UUID
    ) -> Approval:
        row = session.execute(
            select(Approval).where(
                Approval.workspace_id == workspace_id,
                Approval.id == approval_id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Approval not found", details={"approval_id": str(approval_id)})
        return row

    def approve(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        approval_id: UUID,
        actor_user_id: UUID,
        notes: str | None = None,
        request_id: UUID | None = None,
    ) -> ApprovalView:
        appr = self._get_approval_row(session, workspace_id=workspace_id, approval_id=approval_id)
        if appr.status == ApprovalStatus.INVALIDATED.value:
            raise MetuseError(
                "Approval has been invalidated because the underlying creative changed",
                code=ErrorCode.APPROVAL_INVALIDATED,
                status_code=409,
            )
        if appr.status != ApprovalStatus.PENDING.value:
            raise ConflictError(
                f"Approval is already in status '{appr.status}'",
                code=ErrorCode.INVALID_STATE_TRANSITION,
            )

        if appr.creative_version_id is not None:
            cv = session.execute(
                select(CreativeVersion).where(
                    CreativeVersion.workspace_id == workspace_id,
                    CreativeVersion.id == appr.creative_version_id,
                )
            ).scalar_one_or_none()
            if cv is None or cv.content_sha256 != appr.target_version_sha256:
                appr.status = ApprovalStatus.INVALIDATED.value
                appr.invalidated_reason = "Target creative version hash mismatch"
                appr.invalidated_at = datetime.now(UTC)
                session.flush()
                raise MetuseError(
                    "Approval invalidated due to creative version hash mismatch",
                    code=ErrorCode.APPROVAL_INVALIDATED,
                    status_code=409,
                )
            cv.is_approved = True

        appr.status = ApprovalStatus.APPROVED.value
        appr.decided_by_user_id = actor_user_id
        appr.decision_notes = notes
        appr.decided_at = datetime.now(UTC)
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=appr.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.APPROVAL_APPROVED.value,
                target_type="approval",
                target_id=str(appr.id),
                request_id=request_id,
                metadata_json={"notes": notes},
            )
        )
        session.flush()
        return _approval_view(appr)

    def reject(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        approval_id: UUID,
        actor_user_id: UUID,
        notes: str,
        request_id: UUID | None = None,
    ) -> ApprovalView:
        appr = self._get_approval_row(session, workspace_id=workspace_id, approval_id=approval_id)
        if appr.status != ApprovalStatus.PENDING.value:
            raise ConflictError(
                f"Cannot reject approval in status '{appr.status}'",
                code=ErrorCode.INVALID_STATE_TRANSITION,
            )
        appr.status = ApprovalStatus.REJECTED.value
        appr.decided_by_user_id = actor_user_id
        appr.decision_notes = notes
        appr.decided_at = datetime.now(UTC)
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=appr.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.APPROVAL_REJECTED.value,
                target_type="approval",
                target_id=str(appr.id),
                request_id=request_id,
                metadata_json={"notes": notes},
            )
        )
        session.flush()
        return _approval_view(appr)

    def request_revision(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        approval_id: UUID,
        actor_user_id: UUID,
        notes: str,
        timecode_ms: int | None = None,
        request_id: UUID | None = None,
    ) -> tuple[ApprovalView, RevisionRequestView]:
        appr = self._get_approval_row(session, workspace_id=workspace_id, approval_id=approval_id)
        if appr.status not in {ApprovalStatus.PENDING.value, ApprovalStatus.APPROVED.value}:
            raise ConflictError(
                f"Cannot request revision for approval in status '{appr.status}'",
                code=ErrorCode.INVALID_STATE_TRANSITION,
            )
        if appr.project_id is None or appr.creative_version_id is None:
            raise ValidationDomainError("Revision request requires a creative version approval")

        appr.status = ApprovalStatus.REVISION_REQUESTED.value
        appr.decided_by_user_id = actor_user_id
        appr.decision_notes = notes
        appr.decided_at = datetime.now(UTC)

        cv = session.execute(
            select(CreativeVersion).where(
                CreativeVersion.workspace_id == workspace_id,
                CreativeVersion.id == appr.creative_version_id,
            )
        ).scalar_one_or_none()
        if cv is not None:
            cv.is_approved = False

        rev = RevisionRequest(
            id=uuid4(),
            workspace_id=workspace_id,
            client_id=appr.client_id,
            project_id=appr.project_id,
            creative_version_id=appr.creative_version_id,
            approval_id=appr.id,
            requested_by_user_id=actor_user_id,
            notes=notes,
            timecode_ms=timecode_ms,
            resolved=False,
        )
        session.add(rev)
        session.flush()
        session.add(
            AuditLog(
                id=uuid4(),
                workspace_id=workspace_id,
                client_id=appr.client_id,
                actor_user_id=actor_user_id,
                action=AuditAction.APPROVAL_REVISION_REQUESTED.value,
                target_type="approval",
                target_id=str(appr.id),
                request_id=request_id,
                metadata_json={"revision_id": str(rev.id), "timecode_ms": timecode_ms},
            )
        )
        session.flush()
        return _approval_view(appr), _revision_view(rev)
