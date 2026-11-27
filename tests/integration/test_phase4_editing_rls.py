"""Live PostgreSQL Phase 4 integration tests: FORCE RLS, Cloud Editing, Media QA & Approvals."""

from __future__ import annotations

import os
from collections.abc import Iterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select, table, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from database.seeds.seed_dev import seed_canonical_roles
from packages.ai_core import get_usage_summary
from packages.media_engine import (
    CanvasSpec,
    CaptionItem,
    CloudEditingService,
    MockRenderBackend,
    TimelinePlan,
    TimelineTrack,
    VideoClip,
    build_synthetic_mp4_bytes,
)
from packages.shared.config import BaseAppSettings, normalize_database_url
from packages.shared.enums import (
    ApprovalStatus,
    AssetKind,
    ErrorCode,
    RenderJobStatus,
    RenderKind,
    WorkspaceType,
)
from packages.shared.errors import MetuseError, NotFoundError
from packages.storage import InMemoryStorageBackend
from packages.tenancy import (
    create_client,
    create_session_factory,
    create_workspace,
    tenant_transaction,
)
from packages.tenancy.db import is_supabase_transaction_pooler_url
from packages.video_gateway import MockVideoProvider, VideoGatewayRegistry, VideoGatewayService

pytestmark = pytest.mark.integration

_PHASE4_RLS_TABLES = (
    "media_metadata",
    "editing_plans",
    "render_jobs",
    "creative_versions",
    "approvals",
    "revision_requests",
)


def _get_owner_dsn() -> str:
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return normalize_database_url(env_url)
    return BaseAppSettings().get_sqlalchemy_database_url(use_app_role=False)


def _get_app_dsn() -> str:
    env_url = os.environ.get("APP_DATABASE_URL")
    if env_url:
        return normalize_database_url(env_url)
    settings = BaseAppSettings()
    if (
        settings.app_database_url is not None
        and settings.app_database_url.get_secret_value().strip()
    ):
        return settings.get_sqlalchemy_database_url(use_app_role=True)
    return (
        f"postgresql+psycopg://metuse_app:change-me-app@localhost:"
        f"{settings.postgres_host_port}/metuse"
    )


@pytest.fixture(scope="module")
def owner_engine() -> Iterator[Engine]:
    engine = create_engine(_get_owner_dsn(), future=True)
    with engine.begin() as conn:
        seed_canonical_roles(conn)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def app_session_factory() -> Iterator[sessionmaker[Session]]:
    dsn = _get_app_dsn()
    connect_args = {"prepare_threshold": None} if is_supabase_transaction_pooler_url(dsn) else {}
    engine = create_engine(dsn, connect_args=connect_args, future=True)
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


def _insert_user(owner_engine: Engine, label: str) -> UUID:
    uid = uuid4()
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO public.users (id, email, full_name, is_active) "
                "VALUES (:id, :email, :name, true)"
            ),
            {"id": uid, "email": f"p4-{label}-{uid.hex[:8]}@metuse.test", "name": label},
        )
    return uid


def _cleanup(owner_engine: Engine, *, workspace_ids: list[UUID], user_ids: list[UUID]) -> None:
    with owner_engine.begin() as conn:
        if workspace_ids:
            conn.execute(
                text("DELETE FROM public.workspaces WHERE id = ANY(:ws_ids)"),
                {"ws_ids": workspace_ids},
            )
        if user_ids:
            conn.execute(
                text("DELETE FROM public.users WHERE id = ANY(:u_ids)"),
                {"u_ids": user_ids},
            )


def test_phase4_tables_have_force_rls_enabled(owner_engine: Engine) -> None:
    """Every tenant-owned Phase 4 table must have ENABLE + FORCE ROW LEVEL SECURITY."""
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname = ANY(:tables)
                """
            ),
            {"tables": list(_PHASE4_RLS_TABLES)},
        ).fetchall()

    found = {r[0]: (bool(r[1]), bool(r[2])) for r in rows}
    assert set(found.keys()) == set(_PHASE4_RLS_TABLES)
    for tbl, (rls_enabled, rls_forced) in found.items():
        assert rls_enabled is True, f"{tbl} missing ENABLE ROW LEVEL SECURITY"
        assert rls_forced is True, f"{tbl} missing FORCE ROW LEVEL SECURITY"


def test_phase4_cloud_editing_qa_approval_binding_cost_and_cross_tenant_rls(
    owner_engine: Engine,
    app_session_factory: sessionmaker[Session],
) -> None:
    """End-to-end Acceptance #11, #12, #13, #17, and 6-table cross-tenant FORCE RLS."""
    user_a = _insert_user(owner_engine, "agency-a-edit")
    user_b = _insert_user(owner_engine, "agency-b-edit")
    created_workspaces: list[UUID] = []

    settings = BaseAppSettings(_env_file=None, render_mode="mock")
    storage = InMemoryStorageBackend(bucket="metuse-p4-test")
    video_svc = VideoGatewayService(
        settings=settings,
        registry=VideoGatewayRegistry([MockVideoProvider(enabled=True)]),
        storage=storage,
    )
    editing_svc = CloudEditingService(
        settings=settings,
        storage=storage,
        renderer=MockRenderBackend(),
    )
    bad_aspect_svc = CloudEditingService(
        settings=settings,
        storage=storage,
        renderer=MockRenderBackend(override_width=1920, override_height=1080),
    )

    try:
        ws_a = create_workspace(
            app_session_factory,
            user_id=user_a,
            name="Studio Agency A",
            workspace_type=WorkspaceType.AGENCY,
        )
        ws_b = create_workspace(
            app_session_factory,
            user_id=user_b,
            name="Studio Agency B",
            workspace_type=WorkspaceType.AGENCY,
        )
        created_workspaces.extend([ws_a.id, ws_b.id])

        with tenant_transaction(app_session_factory, user_id=user_a, workspace_id=ws_a.id) as (
            session,
            ctx_a,
        ):
            client_a = create_client(session, ctx_a, name="Edit Client A")
            project_a = video_svc.create_project(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                client_id=client_a.id,
                name="Product Launch Cut",
            )

            # 1. Acceptance #11: Upload -> validate -> proxy -> preview -> revision -> final
            source_mp4 = build_synthetic_mp4_bytes(
                width=1080, height=1920, duration_ms=7000, fps=30, has_audio=True, tag="raw-clip"
            )
            upload_ticket = editing_svc.create_upload_url(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                filename="source-clip.mp4",
                mime_type="video/mp4",
                size_bytes=len(source_mp4),
                asset_kind=AssetKind.VIDEO,
                client_id=client_a.id,
                project_id=project_a.id,
            )
            storage.put_object_bytes(
                key=upload_ticket.key, data=source_mp4, content_type="video/mp4"
            )
            ready_asset, media_meta = editing_svc.complete_upload(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                asset_id=upload_ticket.asset_id,
            )
            assert ready_asset.status == "ready"
            assert media_meta.proxy_asset_id is not None
            assert media_meta.width == 1080
            assert media_meta.height == 1920

            plan_v1 = editing_svc.create_editing_plan(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                project_id=project_a.id,
                plan=TimelinePlan(
                    project_id=project_a.id,
                    canvas=CanvasSpec(width=1080, height=1920, fps=30),
                    tracks=[
                        TimelineTrack(
                            id="video-main",
                            type="video",
                            clips=[
                                VideoClip(
                                    asset_id=ready_asset.id,
                                    source_in_ms=0,
                                    source_out_ms=6000,
                                    timeline_start_ms=0,
                                )
                            ],
                        ),
                        TimelineTrack(
                            id="captions",
                            type="text",
                            items=[
                                CaptionItem(
                                    text="Opening Hook",
                                    start_ms=0,
                                    end_ms=2500,
                                    x_norm=0.5,
                                    y_norm=0.75,
                                )
                            ],
                        ),
                    ],
                ),
            )
            assert plan_v1.version_number == 1

            # Render preview + pass automated QA
            preview_job, preview_ver = editing_svc.render_project(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                project_id=project_a.id,
                render_kind=RenderKind.PREVIEW,
                idempotency_key="p4-preview-v1",
            )
            assert preview_job.status == RenderJobStatus.SUCCEEDED
            assert preview_job.qa_passed is True
            assert preview_ver is not None
            assert preview_ver.version_number == 1

            # 2. Acceptance #12: Wrong-aspect-ratio output rejected cleanly by Media QA
            qa_fail_job, qa_fail_ver = bad_aspect_svc.render_project(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                project_id=project_a.id,
                render_kind=RenderKind.PREVIEW,
                idempotency_key="p4-qa-fail",
            )
            assert qa_fail_job.status == RenderJobStatus.QA_FAILED
            assert qa_fail_job.qa_passed is False
            assert qa_fail_job.error_code == ErrorCode.MEDIA_QA_FAILED.value
            assert qa_fail_ver is None

            # 3. Request approval on preview v1 -> revision -> approve v2 -> edit invalidates
            appr_v1 = editing_svc.create_approval(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                creative_version_id=preview_ver.id,
            )
            rev_appr, rev_req = editing_svc.request_revision(
                session,
                workspace_id=ws_a.id,
                approval_id=appr_v1.id,
                actor_user_id=user_a,
                notes="Tighten cut to 5s and add CTA at end",
                timecode_ms=4000,
            )
            assert rev_appr.status == ApprovalStatus.REVISION_REQUESTED
            assert rev_req.timecode_ms == 4000

            # Submit revised plan v2 + render final MP4
            plan_v2 = editing_svc.create_editing_plan(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                project_id=project_a.id,
                plan=TimelinePlan(
                    project_id=project_a.id,
                    canvas=CanvasSpec(width=1080, height=1920, fps=30),
                    tracks=[
                        TimelineTrack(
                            id="video-main",
                            type="video",
                            clips=[
                                VideoClip(
                                    asset_id=ready_asset.id,
                                    source_in_ms=0,
                                    source_out_ms=5000,
                                    timeline_start_ms=0,
                                )
                            ],
                        ),
                        TimelineTrack(
                            id="captions",
                            type="text",
                            items=[
                                CaptionItem(
                                    text="Shop Now",
                                    start_ms=3000,
                                    end_ms=4900,
                                    x_norm=0.5,
                                    y_norm=0.80,
                                    is_cta=True,
                                )
                            ],
                        ),
                    ],
                ),
            )
            assert plan_v2.version_number == 2

            final_job, final_ver = editing_svc.render_project(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                project_id=project_a.id,
                render_kind=RenderKind.FINAL,
                idempotency_key="p4-final-v2",
            )
            assert final_job.status == RenderJobStatus.SUCCEEDED
            assert final_ver is not None
            assert final_ver.render_kind == RenderKind.FINAL

            # Approve final version
            appr_v2 = editing_svc.create_approval(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                creative_version_id=final_ver.id,
            )
            approved_v2 = editing_svc.approve(
                session,
                workspace_id=ws_a.id,
                approval_id=appr_v2.id,
                actor_user_id=user_a,
                notes="Ready for scheduling",
            )
            assert approved_v2.status == ApprovalStatus.APPROVED

            # 4. Acceptance #13: Editing the approved project MUST invalidate prior approval!
            editing_svc.create_editing_plan(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                project_id=project_a.id,
                plan=TimelinePlan(
                    project_id=project_a.id,
                    canvas=CanvasSpec(width=1080, height=1920, fps=30),
                    tracks=[
                        TimelineTrack(
                            id="video-main",
                            type="video",
                            clips=[
                                VideoClip(
                                    asset_id=ready_asset.id,
                                    source_in_ms=500,
                                    source_out_ms=5500,
                                    timeline_start_ms=0,
                                )
                            ],
                        )
                    ],
                ),
            )
            all_approvals = editing_svc.list_approvals(
                session, workspace_id=ws_a.id, project_id=project_a.id
            )
            refreshed_v2 = next(a for a in all_approvals if a.id == appr_v2.id)
            assert refreshed_v2.status == ApprovalStatus.INVALIDATED
            assert refreshed_v2.invalidated_reason is not None

            # Attempting to re-approve an invalidated approval raises 409 APPROVAL_INVALIDATED
            with pytest.raises(MetuseError) as inv_exc:
                editing_svc.approve(
                    session,
                    workspace_id=ws_a.id,
                    approval_id=appr_v2.id,
                    actor_user_id=user_a,
                )
            assert inv_exc.value.code == ErrorCode.APPROVAL_INVALIDATED

            versions = editing_svc.list_project_versions(
                session, workspace_id=ws_a.id, project_id=project_a.id
            )
            assert all(v.is_approved is False for v in versions)

            # 5. Acceptance #17: Render jobs attributed cost/units in usage_records
            usage = get_usage_summary(session, ctx_a, client_id=client_a.id)
            assert usage.total_input_units >= 15
            assert usage.total_estimated_cost_usd > Decimal("0")

        # 6. Cross-tenant FORCE RLS isolation on all 6 Phase 4 tables
        with tenant_transaction(app_session_factory, user_id=user_b, workspace_id=ws_b.id) as (
            session_b,
            _,
        ):
            for tbl_name in _PHASE4_RLS_TABLES:
                count_b = session_b.execute(
                    select(func.count()).select_from(table(tbl_name))
                ).scalar_one()
                assert count_b == 0, f"FORCE RLS leak on {tbl_name} for Workspace B"

            with pytest.raises(NotFoundError):
                editing_svc.get_render_job(session_b, workspace_id=ws_b.id, job_id=final_job.id)
            with pytest.raises(NotFoundError):
                editing_svc.list_project_versions(
                    session_b, workspace_id=ws_b.id, project_id=project_a.id
                )
    finally:
        _cleanup(owner_engine, workspace_ids=created_workspaces, user_ids=[user_a, user_b])
