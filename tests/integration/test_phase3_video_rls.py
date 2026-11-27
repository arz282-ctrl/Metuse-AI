"""Live PostgreSQL Phase 3 integration tests: FORCE RLS, T2V, I2V, polling & webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Iterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select, table, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from database.seeds.seed_dev import seed_canonical_roles
from packages.ai_core import get_usage_summary, upsert_workspace_budget
from packages.shared.config import BaseAppSettings, normalize_database_url
from packages.shared.enums import AssetKind, VideoGenerationMode, VideoJobStatus, WorkspaceType
from packages.shared.errors import (
    BudgetExceededError,
    ConflictError,
    NotFoundError,
    ProviderDisabledError,
)
from packages.storage import InMemoryStorageBackend
from packages.tenancy import (
    create_client,
    create_session_factory,
    create_workspace,
    tenant_transaction,
)
from packages.tenancy.db import is_supabase_transaction_pooler_url
from packages.video_gateway import (
    MockVideoProvider,
    NormalizedVideoRequest,
    VideoGatewayRegistry,
    VideoGatewayService,
)
from workers.video_generation import poll_workspace_video_jobs

pytestmark = pytest.mark.integration

_PHASE3_RLS_TABLES = (
    "projects",
    "assets",
    "video_generation_jobs",
    "video_provider_events",
)

# Valid minimal 1x1 PNG header + bytes
_MINIMAL_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc`\x00\x00"
    b"\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
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
            {"id": uid, "email": f"p3-{label}-{uid.hex[:8]}@metuse.test", "name": label},
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


def test_phase3_tables_have_force_rls_enabled(owner_engine: Engine) -> None:
    """Every tenant-owned Phase 3 table must have ENABLE + FORCE ROW LEVEL SECURITY."""
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
            {"tables": list(_PHASE3_RLS_TABLES)},
        ).fetchall()

    found = {r[0]: (bool(r[1]), bool(r[2])) for r in rows}
    assert set(found.keys()) == set(_PHASE3_RLS_TABLES)
    for tbl, (rls_enabled, rls_forced) in found.items():
        assert rls_enabled is True, f"{tbl} missing ENABLE ROW LEVEL SECURITY"
        assert rls_forced is True, f"{tbl} missing FORCE ROW LEVEL SECURITY"


def test_phase3_t2v_i2v_restart_safe_poll_webhook_cancel_budget_and_rls(
    owner_engine: Engine,
    app_session_factory: sessionmaker[Session],
) -> None:
    """End-to-end Acceptance #9, #10, #17, restart-safe polling, webhooks, budgets, and RLS."""
    user_a = _insert_user(owner_engine, "agency-a-video")
    user_b = _insert_user(owner_engine, "agency-b-video")
    created_workspaces: list[UUID] = []

    settings = BaseAppSettings(_env_file=None, video_gateway_mode="mock")
    webhook_secret = "integration-webhook-secret"
    storage = InMemoryStorageBackend(bucket="metuse-p3-test")
    provider = MockVideoProvider(
        enabled=True, webhook_secret=webhook_secret, polls_until_complete=2
    )
    service = VideoGatewayService(
        settings=settings,
        registry=VideoGatewayRegistry([provider]),
        storage=storage,
    )

    try:
        ws_a = create_workspace(
            app_session_factory,
            user_id=user_a,
            name="Video Agency A",
            workspace_type=WorkspaceType.AGENCY,
        )
        created_workspaces.append(ws_a.id)
        ws_b = create_workspace(
            app_session_factory,
            user_id=user_b,
            name="Video Agency B",
            workspace_type=WorkspaceType.AGENCY,
        )
        created_workspaces.append(ws_b.id)

        with tenant_transaction(app_session_factory, user_id=user_a, workspace_id=ws_a.id) as (
            session,
            ctx,
        ):
            client_a = create_client(session, ctx, name="Client Video A")
            project_a = service.create_project(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                client_id=client_a.id,
                name="Summer Launch Reels",
            )
            ref_image = service.create_asset(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                client_id=client_a.id,
                project_id=project_a.id,
                filename="hero-frame.png",
                mime_type="image/png",
                asset_kind=AssetKind.IMAGE,
                data=_MINIMAL_PNG_BYTES,
            )

            # 1. Acceptance #9: Text-to-Video + Idempotency dedupe & conflict
            t2v_req = NormalizedVideoRequest(
                workspace_id=ws_a.id,
                client_id=client_a.id,
                project_id=project_a.id,
                mode=VideoGenerationMode.TEXT_TO_VIDEO,
                prompt="Slow tracking shot of minimalist cold brew can on travertine",
                aspect_ratio="9:16",
                duration_seconds=5,
                resolution="720p",
                idempotency_key="p3-t2v-idem-1",
            )
            t2v_job = service.create_job(session, actor_user_id=user_a, request=t2v_req)
            assert t2v_job.status == VideoJobStatus.SUBMITTED
            assert t2v_job.provider == "mock"
            assert t2v_job.model == "mock-fast-v1"
            assert t2v_job.estimated_cost_usd == Decimal("0.200000")

            # Same idempotency key + same payload returns exact same job
            t2v_dedup = service.create_job(session, actor_user_id=user_a, request=t2v_req)
            assert t2v_dedup.id == t2v_job.id

            # Same idempotency key + different prompt -> 409 IDEMPOTENCY_CONFLICT
            with pytest.raises(ConflictError):
                service.create_job(
                    session,
                    actor_user_id=user_a,
                    request=t2v_req.model_copy(update={"prompt": "Completely different prompt"}),
                )

        # 2. Simulate process restart: brand-new MockVideoProvider + VideoGatewayService!
        restarted_service = VideoGatewayService(
            settings=settings,
            registry=VideoGatewayRegistry(
                [MockVideoProvider(enabled=True, webhook_secret=webhook_secret)]
            ),
            storage=storage,
        )
        polled = poll_workspace_video_jobs(
            app_session_factory,
            restarted_service,
            workspace_id=ws_a.id,
        )
        assert len(polled) == 1
        assert polled[0].id == t2v_job.id
        assert polled[0].status == VideoJobStatus.SUCCEEDED
        assert len(polled[0].output_asset_ids) == 1

        # Verify saved output asset is owned & readable via signed download URL
        with tenant_transaction(app_session_factory, user_id=user_a, workspace_id=ws_a.id) as (
            session,
            _,
        ):
            out_asset = service.get_asset(
                session, workspace_id=ws_a.id, asset_id=polled[0].output_asset_ids[0]
            )
            assert out_asset.asset_kind == AssetKind.VIDEO
            assert "generated/video-scenes" in out_asset.storage_key
            dl = service.create_asset_download_url(
                session, workspace_id=ws_a.id, asset_id=out_asset.id
            )
            assert dl.bucket == "metuse-p3-test"

        # 3. Acceptance #10: Image-to-Video + signed webhook completion + duplicate deduplication
        with tenant_transaction(app_session_factory, user_id=user_a, workspace_id=ws_a.id) as (
            session,
            ctx_a,
        ):
            i2v_job = service.create_job(
                session,
                actor_user_id=user_a,
                request=NormalizedVideoRequest(
                    workspace_id=ws_a.id,
                    client_id=client_a.id,
                    project_id=project_a.id,
                    mode=VideoGenerationMode.IMAGE_TO_VIDEO,
                    prompt="Subtle camera orbit around hero product bottle",
                    reference_asset_ids=[ref_image.id],
                    aspect_ratio="9:16",
                    duration_seconds=8,
                    resolution="1080p",
                    idempotency_key="p3-i2v-idem-1",
                ),
            )
            assert i2v_job.status == VideoJobStatus.SUBMITTED
            assert i2v_job.input_asset_ids == [ref_image.id]

            wh_body = json.dumps(
                {
                    "event_id": "wh_evt_i2v_001",
                    "provider_job_id": i2v_job.provider_job_id,
                    "status": "succeeded",
                    "progress_pct": 100,
                }
            ).encode("utf-8")
            wh_sig = hmac.new(webhook_secret.encode("utf-8"), wh_body, hashlib.sha256).hexdigest()

            completed_i2v = service.handle_webhook(
                session,
                workspace_id=ws_a.id,
                provider_id="mock",
                raw_body=wh_body,
                headers={"X-Metuse-Signature": wh_sig},
            )
            assert completed_i2v.status == VideoJobStatus.SUCCEEDED
            assert len(completed_i2v.output_asset_ids) == 1

            # Duplicate webhook delivery must be idempotent (still exactly 1 output asset)
            dup_i2v = service.handle_webhook(
                session,
                workspace_id=ws_a.id,
                provider_id="mock",
                raw_body=wh_body,
                headers={"X-Metuse-Signature": wh_sig},
            )
            assert len(dup_i2v.output_asset_ids) == 1

            # 4. Acceptance #17: Usage & cost attribution across T2V + I2V
            usage = get_usage_summary(session, ctx_a, client_id=client_a.id)
            assert usage.total_input_units >= 13
            assert usage.total_output_units >= 13
            assert usage.total_estimated_cost_usd >= Decimal("0.520000")

            # 5. Cancellation flow
            cancel_target = service.create_job(
                session,
                actor_user_id=user_a,
                request=NormalizedVideoRequest(
                    workspace_id=ws_a.id,
                    client_id=client_a.id,
                    mode=VideoGenerationMode.TEXT_TO_VIDEO,
                    prompt="Job to cancel immediately",
                    aspect_ratio="16:9",
                    duration_seconds=5,
                    resolution="720p",
                ),
            )
            cancelled = service.cancel_job(
                session,
                workspace_id=ws_a.id,
                job_id=cancel_target.id,
                actor_user_id=user_a,
            )
            assert cancelled.status == VideoJobStatus.CANCELLED

            # 6. Pre-flight budget cap (429) and kill switch (503)
            upsert_workspace_budget(
                session,
                ctx_a,
                client_id=client_a.id,
                monthly_cost_limit_usd=Decimal("0.100000"),
                provider_kill_switch=False,
            )
            with pytest.raises(BudgetExceededError):
                service.create_job(
                    session,
                    actor_user_id=user_a,
                    request=NormalizedVideoRequest(
                        workspace_id=ws_a.id,
                        client_id=client_a.id,
                        mode=VideoGenerationMode.TEXT_TO_VIDEO,
                        prompt="Should fail budget cap",
                        aspect_ratio="9:16",
                        duration_seconds=5,
                        resolution="720p",
                    ),
                )

            upsert_workspace_budget(
                session,
                ctx_a,
                client_id=client_a.id,
                monthly_cost_limit_usd=Decimal("100.000000"),
                provider_kill_switch=True,
            )
            with pytest.raises(ProviderDisabledError):
                service.create_job(
                    session,
                    actor_user_id=user_a,
                    request=NormalizedVideoRequest(
                        workspace_id=ws_a.id,
                        client_id=client_a.id,
                        mode=VideoGenerationMode.TEXT_TO_VIDEO,
                        prompt="Should fail kill switch",
                        aspect_ratio="9:16",
                        duration_seconds=5,
                        resolution="720p",
                    ),
                )

        # 7. Cross-tenant FORCE RLS isolation: Workspace B sees strictly 0 rows from Workspace A
        with tenant_transaction(app_session_factory, user_id=user_b, workspace_id=ws_b.id) as (
            session_b,
            _,
        ):
            for tbl_name in _PHASE3_RLS_TABLES:
                count_b = session_b.execute(
                    select(func.count()).select_from(table(tbl_name))
                ).scalar_one()
                assert count_b == 0, f"FORCE RLS leak on {tbl_name} for Workspace B"

            with pytest.raises(NotFoundError):
                service.get_job(session_b, workspace_id=ws_b.id, job_id=t2v_job.id)
            with pytest.raises(NotFoundError):
                service.get_asset(session_b, workspace_id=ws_b.id, asset_id=ref_image.id)
            with pytest.raises(NotFoundError):
                service.create_job(
                    session_b,
                    actor_user_id=user_b,
                    request=NormalizedVideoRequest(
                        workspace_id=ws_b.id,
                        mode=VideoGenerationMode.IMAGE_TO_VIDEO,
                        prompt="Cross-tenant asset theft attempt",
                        reference_asset_ids=[ref_image.id],
                        aspect_ratio="9:16",
                        duration_seconds=5,
                        resolution="720p",
                    ),
                )
    finally:
        _cleanup(owner_engine, workspace_ids=created_workspaces, user_ids=[user_a, user_b])
