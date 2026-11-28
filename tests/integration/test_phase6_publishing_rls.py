"""Live PostgreSQL Phase 6 integration tests: Publishing, Duplicate Prevention, Insights & RLS."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select, table, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from database.seeds.seed_dev import seed_canonical_roles
from packages.media_engine import (
    CanvasSpec,
    CloudEditingService,
    MockRenderBackend,
    TimelinePlan,
    TimelineTrack,
    VideoClip,
    build_synthetic_mp4_bytes,
)
from packages.meta_integrations import (
    EncryptedSecretStore,
    MetaConnectionService,
    MockMetaGraphClient,
    MockMetaPublisherClient,
    PublishingAndInsightsService,
)
from packages.shared.config import BaseAppSettings, normalize_database_url
from packages.shared.enums import (
    AssetKind,
    ContentItemStatus,
    ErrorCode,
    MetaConnectionType,
    PublishingJobStatus,
    PublishMediaType,
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
from workers.meta_publishing import execute_due_scheduled_publishes

pytestmark = pytest.mark.integration

_PHASE6_RLS_TABLES = (
    "content_calendars",
    "content_items",
    "publishing_jobs",
    "publishing_results",
    "insights_snapshots",
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
            {"id": uid, "email": f"p6-{label}-{uid.hex[:8]}@metuse.test", "name": label},
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


def test_phase6_tables_have_force_rls_enabled(owner_engine: Engine) -> None:
    """Every tenant-owned Phase 6 table must have ENABLE + FORCE ROW LEVEL SECURITY."""
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
            {"tables": list(_PHASE6_RLS_TABLES)},
        ).fetchall()

    found = {r[0]: (bool(r[1]), bool(r[2])) for r in rows}
    assert set(found.keys()) == set(_PHASE6_RLS_TABLES)
    for tbl, (rls_enabled, rls_forced) in found.items():
        assert rls_enabled is True, f"{tbl} missing ENABLE ROW LEVEL SECURITY"
        assert rls_forced is True, f"{tbl} missing FORCE ROW LEVEL SECURITY"


def test_phase6_approved_publishing_duplicate_prevention_insights_and_rls(
    owner_engine: Engine,
    app_session_factory: sessionmaker[Session],
) -> None:
    """End-to-end Acceptance #14, #15, approval gate, insights attribution & 5-table FORCE RLS."""
    user_a = _insert_user(owner_engine, "agency-a-pub")
    user_b = _insert_user(owner_engine, "agency-b-pub")
    created_workspaces: list[UUID] = []

    settings = BaseAppSettings(
        _env_file=None,
        app_jwt_secret=SecretStr("p6-integration-jwt-secret-32-bytes-ok"),
        meta_connections_mode="mock",
        meta_redirect_uri="https://develop.metuse-ai-web.pages.dev/auth/meta/callback",
    )
    storage = InMemoryStorageBackend(bucket="metuse-p6-test")
    secret_store = EncryptedSecretStore.from_settings(settings)
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
    meta_svc = MetaConnectionService(
        settings=settings,
        graph_client=MockMetaGraphClient(),
        secret_store=secret_store,
    )
    timeout_publisher = MockMetaPublisherClient(simulate_ambiguous_timeout_once=True)
    pub_svc = PublishingAndInsightsService(
        settings=settings,
        meta_service=meta_svc,
        secret_store=secret_store,
        storage=storage,
        publisher=timeout_publisher,
    )

    try:
        ws_a = create_workspace(
            app_session_factory,
            user_id=user_a,
            name="Publish Agency A",
            workspace_type=WorkspaceType.AGENCY,
        )
        ws_b = create_workspace(
            app_session_factory,
            user_id=user_b,
            name="Publish Agency B",
            workspace_type=WorkspaceType.AGENCY,
        )
        created_workspaces.extend([ws_a.id, ws_b.id])

        with tenant_transaction(app_session_factory, user_id=user_a, workspace_id=ws_a.id) as (
            session,
            ctx_a,
        ):
            client_a = create_client(session, ctx_a, name="Brand Publish A")

            # Connect eligible Agency Business Meta account
            start = meta_svc.start_unified_login(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                client_id=client_a.id,
                connection_type=MetaConnectionType.AGENCY_BUSINESS,
            )
            meta_conn = meta_svc.complete_unified_login(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                code="mock_agency_biz",
                state=start.state,
            )
            ig_account_id = meta_conn.instagram_account_ids[0]

            # Build rendered creative version
            project_a = video_svc.create_project(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                client_id=client_a.id,
                name="Q4 Reel Campaign",
            )
            clip_asset = video_svc.create_asset(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                client_id=client_a.id,
                project_id=project_a.id,
                filename="source.mp4",
                mime_type="video/mp4",
                asset_kind=AssetKind.VIDEO,
                data=build_synthetic_mp4_bytes(duration_ms=5000),
            )
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
                            clips=[VideoClip(asset_id=clip_asset.id, source_out_ms=5000)],
                        )
                    ],
                ),
            )
            _, final_ver = editing_svc.render_project(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                project_id=project_a.id,
                render_kind=RenderKind.FINAL,
            )
            assert final_ver is not None

            calendar = pub_svc.create_calendar(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                client_id=client_a.id,
                name="December Reels",
            )
            item = pub_svc.create_content_item(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                client_id=client_a.id,
                calendar_id=calendar.id,
                meta_connection_id=meta_conn.id,
                external_account_id=ig_account_id,
                creative_version_id=final_ver.id,
                caption="Approved launch reel #metuse",
                media_type=PublishMediaType.REELS,
                is_ai_generated=True,
            )

            # 1. Unapproved creative version MUST be blocked from publishing!
            with pytest.raises(MetuseError) as unapproved_exc:
                pub_svc.publish_content_item(
                    session,
                    workspace_id=ws_a.id,
                    content_id=item.id,
                    actor_user_id=user_a,
                    idempotency_key="p6-unapproved-1",
                )
            assert unapproved_exc.value.code == ErrorCode.CONTENT_APPROVAL_REQUIRED
            assert unapproved_exc.value.status_code == 403

            # Approve the creative version explicitly
            appr = editing_svc.create_approval(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                creative_version_id=final_ver.id,
            )
            editing_svc.approve(
                session,
                workspace_id=ws_a.id,
                approval_id=appr.id,
                actor_user_id=user_a,
                notes="Approved for IG Reels",
            )

            # 2. Acceptance #15: First attempt hits ambiguous timeout AFTER remote publish succeeded
            first_attempt = pub_svc.publish_content_item(
                session,
                workspace_id=ws_a.id,
                content_id=item.id,
                actor_user_id=user_a,
                idempotency_key="p6-publish-idem-1",
            )
            assert first_attempt.status == PublishingJobStatus.VERIFYING_REMOTE
            assert timeout_publisher.publish_call_count == 1

            # Retry with SAME idempotency key verifies remote state -> ZERO duplicate publish!
            retry_attempt = pub_svc.publish_content_item(
                session,
                workspace_id=ws_a.id,
                content_id=item.id,
                actor_user_id=user_a,
                idempotency_key="p6-publish-idem-1",
            )
            assert retry_attempt.id == first_attempt.id
            assert retry_attempt.status == PublishingJobStatus.PUBLISHED
            assert retry_attempt.remote_media_id is not None
            assert retry_attempt.result is not None
            assert retry_attempt.result.verified_via_remote_check is True
            # Still strictly 1 publish call total!
            assert timeout_publisher.publish_call_count == 1

            # 3. Acceptance #14: Schedule another approved item & publish cleanly via worker
            item2 = pub_svc.create_content_item(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                client_id=client_a.id,
                calendar_id=calendar.id,
                meta_connection_id=meta_conn.id,
                external_account_id=ig_account_id,
                creative_version_id=final_ver.id,
                caption="Scheduled follow-up reel",
            )
            scheduled = pub_svc.schedule_content_item(
                session,
                workspace_id=ws_a.id,
                content_id=item2.id,
                actor_user_id=user_a,
                scheduled_for=datetime.now(UTC) - timedelta(minutes=1),
            )
            assert scheduled.status == ContentItemStatus.SCHEDULED

        # Execute scheduled worker cleanly (with normal publisher)
        clean_pub_svc = PublishingAndInsightsService(
            settings=settings,
            meta_service=meta_svc,
            secret_store=secret_store,
            storage=storage,
            publisher=MockMetaPublisherClient(simulate_ambiguous_timeout_once=False),
        )
        worker_jobs = execute_due_scheduled_publishes(
            app_session_factory, clean_pub_svc, workspace_id=ws_a.id
        )
        assert len(worker_jobs) == 1
        assert worker_jobs[0].status == PublishingJobStatus.PUBLISHED
        assert worker_jobs[0].remote_media_id is not None

        # 4. Sync insights & verify client attribution
        with tenant_transaction(app_session_factory, user_id=user_a, workspace_id=ws_a.id) as (
            session,
            _,
        ):
            synced = clean_pub_svc.sync_client_insights(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                client_id=client_a.id,
            )
            assert len(synced) >= 3  # 1 account snapshot + 2 published media snapshots
            summary = clean_pub_svc.get_client_analytics(
                session, workspace_id=ws_a.id, client_id=client_a.id
            )
            assert summary.total_reach > 0
            assert summary.total_impressions > 0
            assert summary.latest_follower_count == 8420

        # 5. Cross-tenant FORCE RLS isolation on all 5 Phase 6 tables
        with tenant_transaction(app_session_factory, user_id=user_b, workspace_id=ws_b.id) as (
            session_b,
            _,
        ):
            for tbl_name in _PHASE6_RLS_TABLES:
                count_b = session_b.execute(
                    select(func.count()).select_from(table(tbl_name))
                ).scalar_one()
                assert count_b == 0, f"FORCE RLS leak on {tbl_name} for Workspace B"

            with pytest.raises(NotFoundError):
                clean_pub_svc.get_publishing_job(
                    session_b, workspace_id=ws_b.id, job_id=retry_attempt.id
                )
    finally:
        _cleanup(owner_engine, workspace_ids=created_workspaces, user_ids=[user_a, user_b])
