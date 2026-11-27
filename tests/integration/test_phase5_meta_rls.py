"""Live PostgreSQL Phase 5 integration tests: Meta Unified Login, Eligibility, Secret Refs & RLS."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select, table, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from database.models import MetaConnection
from database.seeds.seed_dev import seed_canonical_roles
from packages.meta_integrations import (
    EncryptedSecretStore,
    MetaConnectionService,
    MockMetaGraphClient,
)
from packages.shared.config import BaseAppSettings, normalize_database_url
from packages.shared.enums import (
    ErrorCode,
    MetaConnectionStatus,
    MetaConnectionType,
    WorkspaceType,
)
from packages.shared.errors import MetuseError, NotFoundError
from packages.tenancy import (
    create_client,
    create_session_factory,
    create_workspace,
    tenant_transaction,
)
from packages.tenancy.db import is_supabase_transaction_pooler_url

pytestmark = pytest.mark.integration

_PHASE5_RLS_TABLES = (
    "meta_connections",
    "meta_connected_assets",
    "meta_webhook_events",
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
            {"id": uid, "email": f"p5-{label}-{uid.hex[:8]}@metuse.test", "name": label},
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


def test_phase5_tables_have_force_rls_enabled(owner_engine: Engine) -> None:
    """Every tenant-owned Phase 5 Meta table must have ENABLE + FORCE ROW LEVEL SECURITY."""
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
            {"tables": list(_PHASE5_RLS_TABLES)},
        ).fetchall()

    found = {r[0]: (bool(r[1]), bool(r[2])) for r in rows}
    assert set(found.keys()) == set(_PHASE5_RLS_TABLES)
    for tbl, (rls_enabled, rls_forced) in found.items():
        assert rls_enabled is True, f"{tbl} missing ENABLE ROW LEVEL SECURITY"
        assert rls_forced is True, f"{tbl} missing FORCE ROW LEVEL SECURITY"


def test_phase5_meta_onboarding_eligibility_capabilities_webhooks_secrets_and_rls(
    owner_engine: Engine,
    app_session_factory: sessionmaker[Session],
) -> None:
    """End-to-end Acceptance #3, #4, #5, #6, #18, webhook revocation, and cross-tenant FORCE RLS."""
    user_creator = _insert_user(owner_engine, "creator-p5")
    user_agency = _insert_user(owner_engine, "agency-p5")
    created_workspaces: list[UUID] = []

    app_secret = "p5_integration_meta_app_secret"
    settings = BaseAppSettings(
        _env_file=None,
        app_jwt_secret=SecretStr("p5-integration-jwt-secret-32-bytes-ok"),
        meta_connections_mode="mock",
        meta_app_secret=SecretStr(app_secret),
        meta_redirect_uri="https://develop.metuse-ai-web.pages.dev/auth/meta/callback",
    )
    secret_store = EncryptedSecretStore.from_settings(settings)
    svc = MetaConnectionService(
        settings=settings,
        graph_client=MockMetaGraphClient(),
        secret_store=secret_store,
    )

    try:
        ws_creator = create_workspace(
            app_session_factory,
            user_id=user_creator,
            name="Solo Creator Studio",
            workspace_type=WorkspaceType.CREATOR,
        )
        ws_agency = create_workspace(
            app_session_factory,
            user_id=user_agency,
            name="Growth Agency Meta",
            workspace_type=WorkspaceType.AGENCY,
        )
        created_workspaces.extend([ws_creator.id, ws_agency.id])

        with tenant_transaction(
            app_session_factory, user_id=user_creator, workspace_id=ws_creator.id
        ) as (session, _):
            # 1. Acceptance #3: Personal IG account is blocked cleanly; ZERO connection stored!
            personal_start = svc.start_unified_login(
                session,
                workspace_id=ws_creator.id,
                actor_user_id=user_creator,
                connection_type=MetaConnectionType.CREATOR_PROFESSIONAL,
                include_ads=False,
            )
            with pytest.raises(MetuseError) as personal_exc:
                svc.complete_unified_login(
                    session,
                    workspace_id=ws_creator.id,
                    actor_user_id=user_creator,
                    code="mock_personal_ig",
                    state=personal_start.state,
                )
            assert personal_exc.value.code == ErrorCode.META_PERSONAL_ACCOUNT_INELIGIBLE
            assert personal_exc.value.status_code == 422
            assert len(personal_exc.value.details["conversion_steps"]) >= 3
            assert svc.list_connections(session, workspace_id=ws_creator.id) == []

            # 2. Acceptance #4: Creator Professional connects cleanly WITHOUT ad account
            creator_start = svc.start_unified_login(
                session,
                workspace_id=ws_creator.id,
                actor_user_id=user_creator,
                connection_type=MetaConnectionType.CREATOR_PROFESSIONAL,
                include_ads=False,
            )
            creator_conn = svc.complete_unified_login(
                session,
                workspace_id=ws_creator.id,
                actor_user_id=user_creator,
                code="mock_creator_pro",
                state=creator_start.state,
            )
            assert creator_conn.status == MetaConnectionStatus.ACTIVE
            assert creator_conn.ad_account_ids == []
            assert creator_conn.capabilities["instagram_publish"] is True
            assert creator_conn.capabilities["instagram_insights"] is True
            assert creator_conn.capabilities["meta_ads_read"] is False
            # Acceptance #18: raw Meta token NEVER in API view or DB column!
            assert "EAA_MOCK_" not in creator_conn.model_dump_json()

            raw_row = session.execute(
                select(MetaConnection).where(MetaConnection.id == creator_conn.id)
            ).scalar_one()
            assert raw_row.token_secret_ref.startswith("secret://meta/")
            assert "EAA_MOCK_" not in raw_row.token_secret_ref

            # 3. Acceptance #6: Missing permission disables feature and explains reconnect
            limited_start = svc.start_unified_login(
                session,
                workspace_id=ws_creator.id,
                actor_user_id=user_creator,
                connection_type=MetaConnectionType.CREATOR_PROFESSIONAL,
                include_ads=False,
            )
            limited_conn = svc.complete_unified_login(
                session,
                workspace_id=ws_creator.id,
                actor_user_id=user_creator,
                code="mock_missing_publish_scope",
                state=limited_start.state,
            )
            assert limited_conn.capabilities["instagram_publish"] is False
            with pytest.raises(MetuseError) as perm_exc:
                svc.assert_capability(
                    session,
                    workspace_id=ws_creator.id,
                    connection_id=limited_conn.id,
                    required_capability="instagram_publish",
                )
            assert perm_exc.value.code == ErrorCode.META_PERMISSION_MISSING
            assert perm_exc.value.status_code == 403

        # 4. Acceptance #5: Agency Business portfolio connects ONLY selected assets + optional ads
        with tenant_transaction(
            app_session_factory, user_id=user_agency, workspace_id=ws_agency.id
        ) as (session_ag, ctx_ag):
            client_ag = create_client(session_ag, ctx_ag, name="Agency Brand Client")
            ag_start = svc.start_unified_login(
                session_ag,
                workspace_id=ws_agency.id,
                actor_user_id=user_agency,
                client_id=client_ag.id,
                connection_type=MetaConnectionType.AGENCY_BUSINESS,
                include_ads=True,
            )
            ag_conn = svc.complete_unified_login(
                session_ag,
                workspace_id=ws_agency.id,
                actor_user_id=user_agency,
                code="mock_agency_biz",
                state=ag_start.state,
            )
            assert ag_conn.meta_business_id == "biz_portfolio_7701"
            assert ag_conn.capabilities["instagram_publish"] is True
            assert ag_conn.capabilities["meta_ads_read"] is True
            ag_assets = svc.list_connection_assets(
                session_ag, workspace_id=ws_agency.id, connection_id=ag_conn.id
            )
            ext_ids = {a.external_asset_id for a in ag_assets}
            assert "ig_biz_5001" in ext_ids
            assert "act_5501" in ext_ids
            # Unselected sister brand asset must NEVER be exposed!
            assert "ig_unshared_9999" not in ext_ids

            # 5. Signed Meta webhook revocation -> reconnect_required + deduplication
            wh_payload = json.dumps(
                {
                    "event_id": "meta_wh_revoke_001",
                    "object": "permissions",
                    "field": "deauthorize",
                    "external_asset_id": "ig_biz_5001",
                }
            ).encode("utf-8")
            sig = (
                "sha256="
                + hmac.new(app_secret.encode("utf-8"), wh_payload, hashlib.sha256).hexdigest()
            )
            receipt = svc.ingest_webhook(
                session_ag,
                workspace_id=ws_agency.id,
                raw_body=wh_payload,
                headers={"X-Hub-Signature-256": sig},
            )
            assert receipt.accepted is True
            assert receipt.deduplicated is False
            assert ag_conn.id in receipt.affected_connection_ids

            # Duplicate webhook delivery is cleanly deduplicated
            dup_receipt = svc.ingest_webhook(
                session_ag,
                workspace_id=ws_agency.id,
                raw_body=wh_payload,
                headers={"X-Hub-Signature-256": sig},
            )
            assert dup_receipt.deduplicated is True

            # Revoked connection blocks capability calls with META_RECONNECT_REQUIRED
            with pytest.raises(MetuseError) as reconn_exc:
                svc.assert_capability(
                    session_ag,
                    workspace_id=ws_agency.id,
                    connection_id=ag_conn.id,
                    required_capability="instagram_publish",
                )
            assert reconn_exc.value.code == ErrorCode.META_RECONNECT_REQUIRED

            # Cross-tenant FORCE RLS: Agency cannot see Creator's connections/assets!
            with pytest.raises(NotFoundError):
                svc.get_connection_capabilities(
                    session_ag, workspace_id=ws_agency.id, connection_id=creator_conn.id
                )

        # 6. Disconnect deletes secret & verify Creator cannot read Agency's rows via FORCE RLS
        with tenant_transaction(
            app_session_factory, user_id=user_creator, workspace_id=ws_creator.id
        ) as (session_c, _):
            disconnected = svc.disconnect_connection(
                session_c,
                workspace_id=ws_creator.id,
                connection_id=creator_conn.id,
                actor_user_id=user_creator,
            )
            assert disconnected.status == MetaConnectionStatus.DISCONNECTED
            assert disconnected.token_configured is False

            # Creator workspace sees strictly 0 webhook events from Agency workspace
            wh_count = session_c.execute(
                select(func.count()).select_from(table("meta_webhook_events"))
            ).scalar_one()
            assert wh_count == 0
            with pytest.raises(NotFoundError):
                svc.list_connection_assets(
                    session_c, workspace_id=ws_creator.id, connection_id=ag_conn.id
                )
    finally:
        _cleanup(
            owner_engine, workspace_ids=created_workspaces, user_ids=[user_creator, user_agency]
        )
