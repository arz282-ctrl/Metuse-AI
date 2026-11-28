"""Live PostgreSQL Phase 7 integration tests: Ads Read, Approval-Gated Execution (#16) & RLS."""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select, table, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from database.models import AuditLog
from database.seeds.seed_dev import seed_canonical_roles
from packages.media_engine import CloudEditingService, MockRenderBackend
from packages.meta_integrations import (
    AdsOperationsService,
    EncryptedSecretStore,
    MetaConnectionService,
    MockMetaAdsClient,
    MockMetaGraphClient,
)
from packages.shared.config import BaseAppSettings, normalize_database_url
from packages.shared.enums import (
    AdDraftStatus,
    AdObjective,
    AuditAction,
    ErrorCode,
    MetaConnectionType,
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
from workers.ads_ops import sync_workspace_ads

pytestmark = pytest.mark.integration

_PHASE7_RLS_TABLES = (
    "ad_accounts",
    "ad_campaign_snapshots",
    "ad_set_snapshots",
    "ad_snapshots",
    "ad_campaign_drafts",
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
            {"id": uid, "email": f"p7-{label}-{uid.hex[:8]}@metuse.test", "name": label},
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


def test_phase7_tables_have_force_rls_enabled(owner_engine: Engine) -> None:
    """Verify ENABLE + FORCE ROW LEVEL SECURITY across all 5 Phase 7 Ads tables."""
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
            {"tables": list(_PHASE7_RLS_TABLES)},
        ).all()

    found = {r[0]: (bool(r[1]), bool(r[2])) for r in rows}
    assert set(found.keys()) == set(_PHASE7_RLS_TABLES)
    for tbl, (enabled, forced) in found.items():
        assert enabled is True, f"RLS not enabled on {tbl}"
        assert forced is True, f"FORCE RLS not enabled on {tbl}"


def test_phase7_ads_read_approval_gated_execution_budget_safety_and_rls(
    owner_engine: Engine,
    app_session_factory: sessionmaker[Session],
) -> None:
    """Acceptance #16 end-to-end: read-only Ads, draft validation, approval binding, audit & RLS."""
    user_a = _insert_user(owner_engine, "agency-a-ads")
    user_b = _insert_user(owner_engine, "agency-b-ads")
    created_workspaces: list[UUID] = []

    settings = BaseAppSettings(
        _env_file=None,
        app_jwt_secret=SecretStr("p7-integration-jwt-secret-32-bytes-ok"),
        meta_connections_mode="mock",
        meta_redirect_uri="https://develop.metuse-ai-web.pages.dev/auth/meta/callback",
        ads_kill_switch=False,
        ads_max_daily_budget_cents=500_000,
    )
    storage = InMemoryStorageBackend(bucket="metuse-p7-test")
    secret_store = EncryptedSecretStore.from_settings(settings)
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
    timeout_ads_client = MockMetaAdsClient(simulate_ambiguous_timeout_once=True)
    ads_svc = AdsOperationsService(
        settings=settings,
        meta_service=meta_svc,
        secret_store=secret_store,
        ads_client=timeout_ads_client,
    )

    try:
        ws_a = create_workspace(
            app_session_factory,
            user_id=user_a,
            name="Ads Agency A",
            workspace_type=WorkspaceType.AGENCY,
        )
        ws_b = create_workspace(
            app_session_factory,
            user_id=user_b,
            name="Ads Agency B",
            workspace_type=WorkspaceType.AGENCY,
        )
        created_workspaces.extend([ws_a.id, ws_b.id])

        # 1. Connect Agency A with ads_read + ads_management via mock_agency_biz_manage
        with tenant_transaction(app_session_factory, user_id=user_a, workspace_id=ws_a.id) as (
            session,
            ctx_a,
        ):
            client_a = create_client(session, ctx_a, name="Brand Ads A")
            start = meta_svc.start_unified_login(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                client_id=client_a.id,
                connection_type=MetaConnectionType.AGENCY_BUSINESS,
                include_ads=True,
            )
            conn_a = meta_svc.complete_unified_login(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                code="mock_agency_biz_manage",
                state=start.state,
            )
            assert conn_a.capabilities["meta_ads_read"] is True
            assert conn_a.capabilities["meta_ads_manage"] is True

        # 2. Worker read-only discovery & hierarchy sync
        sync_res = sync_workspace_ads(
            app_session_factory,
            service=ads_svc,
            workspace_id=ws_a.id,
            actor_user_id=user_a,
            client_id=client_a.id,
        )
        assert sync_res.accounts_synced == 1
        assert sync_res.campaigns_synced == 1
        assert sync_res.ad_sets_synced == 1
        assert sync_res.ads_synced == 1
        ad_account_id = sync_res.accounts[0].id

        with tenant_transaction(app_session_factory, user_id=user_a, workspace_id=ws_a.id) as (
            session,
            _ctx_a,
        ):
            hierarchy = ads_svc.list_campaign_hierarchy(
                session, workspace_id=ws_a.id, ad_account_id=ad_account_id
            )
            assert len(hierarchy) == 1
            existing_camp = hierarchy[0]
            assert existing_camp.daily_budget_cents == 5000
            assert len(existing_camp.ad_sets) == 1
            assert len(existing_camp.ad_sets[0].ads) == 1

            # 3. Create & validate campaign draft
            draft = ads_svc.create_draft(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                ad_account_id=ad_account_id,
                name="Spring Creator Growth",
                objective=AdObjective.OUTCOME_LEADS,
                daily_budget_cents=7500,
                targeting={"countries": ["US", "CA"], "age_min": 21, "age_max": 45},
                creative_refs=["cv_approved_101"],
            )
            assert draft.status == AdDraftStatus.DRAFT.value

            # Unvalidated & unapproved draft CANNOT execute (Acceptance #16)
            with pytest.raises(MetuseError) as unval_exc:
                ads_svc.execute_draft(
                    session,
                    workspace_id=ws_a.id,
                    draft_id=draft.id,
                    actor_user_id=user_a,
                )
            assert unval_exc.value.code == ErrorCode.ADS_DRAFT_VALIDATION_FAILED

            validated = ads_svc.validate_draft(
                session,
                workspace_id=ws_a.id,
                draft_id=draft.id,
                actor_user_id=user_a,
            )
            assert validated.validation_passed is True

            # Validated but unapproved draft STILL CANNOT execute (Acceptance #16)
            with pytest.raises(MetuseError) as unappr_exc:
                ads_svc.execute_draft(
                    session,
                    workspace_id=ws_a.id,
                    draft_id=draft.id,
                    actor_user_id=user_a,
                )
            assert unappr_exc.value.code == ErrorCode.ADS_APPROVAL_REQUIRED

            # Request approval -> approve -> then EDIT draft -> approval MUST invalidate!
            appr_req_1 = ads_svc.request_draft_approval(
                session,
                workspace_id=ws_a.id,
                draft_id=draft.id,
                actor_user_id=user_a,
            )
            editing_svc.approve(
                session,
                workspace_id=ws_a.id,
                approval_id=appr_req_1.approval_id,
                actor_user_id=user_a,
                notes="Approved v1",
            )
            edited = ads_svc.update_draft(
                session,
                workspace_id=ws_a.id,
                draft_id=draft.id,
                actor_user_id=user_a,
                daily_budget_cents=9000,
            )
            assert edited.version_number == 2
            assert edited.approval_id is None

            # Re-validate, obtain fresh v2 approval, and execute through ambiguous timeout!
            ads_svc.validate_draft(
                session, workspace_id=ws_a.id, draft_id=draft.id, actor_user_id=user_a
            )
            appr_req_2 = ads_svc.request_draft_approval(
                session, workspace_id=ws_a.id, draft_id=draft.id, actor_user_id=user_a
            )
            # Pending approval MUST NOT allow execution yet
            with pytest.raises(MetuseError) as pending_exc:
                ads_svc.execute_draft(
                    session,
                    workspace_id=ws_a.id,
                    draft_id=draft.id,
                    actor_user_id=user_a,
                )
            assert pending_exc.value.code == ErrorCode.ADS_APPROVAL_REQUIRED

            editing_svc.approve(
                session,
                workspace_id=ws_a.id,
                approval_id=appr_req_2.approval_id,
                actor_user_id=user_a,
                notes="Explicit campaign execution approval for v2",
            )
            exec_res = ads_svc.execute_draft(
                session,
                workspace_id=ws_a.id,
                draft_id=draft.id,
                actor_user_id=user_a,
                idempotency_key="ads-exec-v2-key-001",
            )
            assert exec_res.draft.status == AdDraftStatus.EXECUTED.value
            assert exec_res.draft.remote_campaign_id is not None
            assert exec_res.verified_via_remote_check is True
            assert timeout_ads_client.create_call_count == 1  # ZERO duplicate campaign creation

            # 4. Budget change requires explicit approval + explicit_human_confirmation=True
            budget_appr = ads_svc.request_budget_change_approval(
                session,
                workspace_id=ws_a.id,
                campaign_id=existing_camp.id,
                actor_user_id=user_a,
                new_daily_budget_cents=8000,
            )
            # Pending budget approval blocked
            with pytest.raises(MetuseError) as b_pend:
                ads_svc.apply_budget_change(
                    session,
                    workspace_id=ws_a.id,
                    campaign_id=existing_camp.id,
                    actor_user_id=user_a,
                    approval_id=budget_appr.approval_id,
                    new_daily_budget_cents=8000,
                    explicit_human_confirmation=True,
                )
            assert b_pend.value.code == ErrorCode.ADS_APPROVAL_REQUIRED

            editing_svc.approve(
                session,
                workspace_id=ws_a.id,
                approval_id=budget_appr.approval_id,
                actor_user_id=user_a,
                notes="Confirmed daily budget increase to $80",
            )
            # Missing explicit_human_confirmation blocked
            with pytest.raises(MetuseError) as b_unconf:
                ads_svc.apply_budget_change(
                    session,
                    workspace_id=ws_a.id,
                    campaign_id=existing_camp.id,
                    actor_user_id=user_a,
                    approval_id=budget_appr.approval_id,
                    new_daily_budget_cents=8000,
                    explicit_human_confirmation=False,
                )
            assert b_unconf.value.code == ErrorCode.ADS_BUDGET_CONFIRMATION_REQUIRED

            # Tampered amount (different from approved $80) blocked with ADS_APPROVAL_STALE
            with pytest.raises(MetuseError) as b_tamper:
                ads_svc.apply_budget_change(
                    session,
                    workspace_id=ws_a.id,
                    campaign_id=existing_camp.id,
                    actor_user_id=user_a,
                    approval_id=budget_appr.approval_id,
                    new_daily_budget_cents=12000,
                    explicit_human_confirmation=True,
                )
            assert b_tamper.value.code == ErrorCode.ADS_APPROVAL_STALE

            # Approved + confirmed budget mutation succeeds via remote-state verification & audit!
            budget_res = ads_svc.apply_budget_change(
                session,
                workspace_id=ws_a.id,
                campaign_id=existing_camp.id,
                actor_user_id=user_a,
                approval_id=budget_appr.approval_id,
                new_daily_budget_cents=8000,
                explicit_human_confirmation=True,
            )
            assert budget_res.previous_daily_budget_cents == 5000
            assert budget_res.new_daily_budget_cents == 8000
            assert budget_res.verified_via_remote_check is True
            assert timeout_ads_client.budget_update_call_count == 1

            # Verify append-only before/after audit log entries exist
            budget_audit = session.execute(
                select(AuditLog).where(
                    AuditLog.workspace_id == ws_a.id,
                    AuditLog.action == AuditAction.AD_BUDGET_CHANGED.value,
                )
            ).scalar_one()
            assert budget_audit.metadata_json["before"] == {"daily_budget_cents": 5000}
            assert budget_audit.metadata_json["after"] == {"daily_budget_cents": 8000}
            assert budget_audit.metadata_json["approval_id"] == str(budget_appr.approval_id)

        # 5. Cross-tenant FORCE RLS isolation: Workspace B sees 0 rows across all 5 Phase 7 tables
        with tenant_transaction(app_session_factory, user_id=user_b, workspace_id=ws_b.id) as (
            session_b,
            _ctx_b,
        ):
            for tbl_name in _PHASE7_RLS_TABLES:
                count_b = session_b.execute(
                    select(func.count()).select_from(table(tbl_name))
                ).scalar_one()
                assert count_b == 0, f"Tenant B leaked {count_b} row(s) from {tbl_name}"

            with pytest.raises(NotFoundError):
                ads_svc.validate_draft(
                    session_b,
                    workspace_id=ws_b.id,
                    draft_id=draft.id,
                    actor_user_id=user_b,
                )
    finally:
        _cleanup(owner_engine, workspace_ids=created_workspaces, user_ids=[user_a, user_b])
