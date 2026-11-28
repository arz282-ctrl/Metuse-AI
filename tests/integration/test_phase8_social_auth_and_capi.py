"""Live PostgreSQL Phase 8 integration tests: social auth, asset picker, prompt ads, CAPI."""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from apps.api.routes.auth import _find_or_create_social_user
from database.models import User
from database.seeds.seed_dev import seed_canonical_roles
from packages.meta_integrations import (
    AdsOperationsService,
    CapiService,
    EncryptedSecretStore,
    MetaConnectionService,
    MockCapiClient,
    MockMetaAdsClient,
    MockMetaGraphClient,
    build_capi_user_data,
)
from packages.muse_adapter import MockMuseAdapter
from packages.shared.config import BaseAppSettings, normalize_database_url
from packages.shared.enums import MetaAssetType, MetaConnectionType, WorkspaceType
from packages.shared.errors import ConflictError, NotFoundError, ValidationDomainError
from packages.tenancy import (
    create_client,
    create_session_factory,
    create_workspace,
    rls_transaction,
    tenant_transaction,
)
from packages.tenancy.db import is_supabase_transaction_pooler_url
from workers.ads_ops import sync_workspace_ads

pytestmark = pytest.mark.integration


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


@pytest.fixture()
def phase8_settings() -> BaseAppSettings:
    return BaseAppSettings(
        _env_file=None,
        app_jwt_secret=SecretStr("p8-integration-jwt-secret-32-bytes-ok"),
        meta_connections_mode="mock",
        meta_redirect_uri="https://develop.metuse-ai-web.pages.dev/auth/meta/callback",
        ads_max_daily_budget_cents=500_000,
    )


def _insert_user(owner_engine: Engine, label: str) -> UUID:
    uid = uuid4()
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO public.users (id, email, full_name, is_active) "
                "VALUES (:id, :email, :name, true)"
            ),
            {"id": uid, "email": f"p8-{label}-{uid.hex[:8]}@metuse.test", "name": label},
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


def test_phase8_migration_allows_google_provider_and_pixel_column(
    owner_engine: Engine,
) -> None:
    """Migration 0010: 'google' identity provider accepted; pixel column present."""
    uid = _insert_user(owner_engine, "social-provision")
    try:
        with owner_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO public.user_identities (id, user_id, provider, provider_subject) "
                    "VALUES (:id, :user_id, 'google', :subject)"
                ),
                {"id": uuid4(), "user_id": uid, "subject": f"google-sub-{uid.hex[:8]}"},
            )
            col = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'ad_accounts' "
                    "AND column_name = 'pixel_dataset_id'"
                )
            ).scalar_one_or_none()
            assert col == "pixel_dataset_id"

            bad_identity_sql = (
                "INSERT INTO public.user_identities "
                "(id, user_id, provider, provider_subject) "
                "VALUES (:id, :user_id, 'yahoo', :subject)"
            )
            with pytest.raises(IntegrityError), conn.begin_nested():
                conn.execute(
                    text(bad_identity_sql),
                    {"id": uuid4(), "user_id": uid, "subject": "yahoo-sub-1"},
                )
    finally:
        _cleanup(owner_engine, workspace_ids=[], user_ids=[uid])


def test_phase8_social_provisioning_linking_and_creator_workspace(
    owner_engine: Engine,
    app_session_factory: sessionmaker[Session],
) -> None:
    """Social find-or-create provisions, links by verified email, and auto-workspaces."""
    created_workspaces: list[UUID] = []
    created_users: list[UUID] = []
    try:
        with rls_transaction(app_session_factory, user_id=None, workspace_id=None) as session:
            user, is_new = _find_or_create_social_user(
                session,
                provider="google",
                provider_subject="google-sub-p8-new",
                email="p8-new-creator@metuse.test",
                full_name="P8 New Creator",
                avatar_url=None,
                request_id=uuid4(),
            )
            assert is_new is True
            created_users.append(user.id)
            assert user.password_hash is None

            same_user, is_new_again = _find_or_create_social_user(
                session,
                provider="google",
                provider_subject="google-sub-p8-new",
                email="p8-new-creator@metuse.test",
                full_name="P8 New Creator",
                avatar_url=None,
                request_id=uuid4(),
            )
            assert is_new_again is False
            assert same_user.id == user.id

        ws = create_workspace(
            app_session_factory,
            user_id=user.id,
            name="P8 New Creator's Studio",
            workspace_type=WorkspaceType.CREATOR,
        )
        created_workspaces.append(ws.id)
        assert ws.workspace_type == WorkspaceType.CREATOR

        # Verified-email linking attaches a second provider to the same user.
        email_user_id = _insert_user(owner_engine, "link-target")
        created_users.append(email_user_id)
        with rls_transaction(app_session_factory, user_id=None, workspace_id=None) as session:
            email_user = session.execute(select(User).where(User.id == email_user_id)).scalar_one()
            linked, linked_is_new = _find_or_create_social_user(
                session,
                provider="meta",
                provider_subject="meta-sub-p8-link",
                email=email_user.email,
                full_name="Link Target",
                avatar_url=None,
                request_id=uuid4(),
            )
            assert linked_is_new is False
            assert linked.id == email_user_id

            with pytest.raises(ConflictError, match="different meta identity"):
                _find_or_create_social_user(
                    session,
                    provider="meta",
                    provider_subject="meta-sub-p8-other",
                    email=email_user.email,
                    full_name="Link Target",
                    avatar_url=None,
                    request_id=uuid4(),
                )
    finally:
        _cleanup(owner_engine, workspace_ids=created_workspaces, user_ids=created_users)


def test_phase8_asset_selection_recomputes_capabilities(
    owner_engine: Engine,
    app_session_factory: sessionmaker[Session],
    phase8_settings: BaseAppSettings,
) -> None:
    """Deselecting the ad account disables ads capabilities; reselecting restores them."""
    user_a = _insert_user(owner_engine, "agency-a-picker")
    created_workspaces: list[UUID] = []
    secret_store = EncryptedSecretStore.from_settings(phase8_settings)
    meta_svc = MetaConnectionService(
        settings=phase8_settings,
        graph_client=MockMetaGraphClient(),
        secret_store=secret_store,
    )
    try:
        ws = create_workspace(
            app_session_factory,
            user_id=user_a,
            name="Picker Agency",
            workspace_type=WorkspaceType.AGENCY,
        )
        created_workspaces.append(ws.id)

        with tenant_transaction(app_session_factory, user_id=user_a, workspace_id=ws.id) as (
            session,
            _ctx,
        ):
            start = meta_svc.start_unified_login(
                session,
                workspace_id=ws.id,
                actor_user_id=user_a,
                connection_type=MetaConnectionType.AGENCY_BUSINESS,
                include_ads=True,
            )
            conn = meta_svc.complete_unified_login(
                session,
                workspace_id=ws.id,
                actor_user_id=user_a,
                code="mock_agency_biz_manage",
                state=start.state,
            )
            assert conn.capabilities["meta_ads_manage"] is True

            assets = meta_svc.list_connection_assets(
                session,
                workspace_id=ws.id,
                connection_id=conn.id,
                include_deselected=True,
            )
            ad_assets = [a for a in assets if a.asset_type == MetaAssetType.AD_ACCOUNT]
            assert len(ad_assets) == 1

            deselected = meta_svc.update_asset_selection(
                session,
                workspace_id=ws.id,
                connection_id=conn.id,
                actor_user_id=user_a,
                selections={ad_assets[0].id: False},
            )
            assert deselected.ad_account_ids == []
            assert deselected.capabilities["meta_ads_manage"] is False
            assert deselected.capabilities["instagram_publish"] is True

            reselected = meta_svc.update_asset_selection(
                session,
                workspace_id=ws.id,
                connection_id=conn.id,
                actor_user_id=user_a,
                selections={ad_assets[0].id: True},
            )
            assert reselected.capabilities["meta_ads_manage"] is True
    finally:
        _cleanup(owner_engine, workspace_ids=created_workspaces, user_ids=[user_a])


def test_phase8_prompt_draft_pixel_and_capi_end_to_end(
    owner_engine: Engine,
    app_session_factory: sessionmaker[Session],
    phase8_settings: BaseAppSettings,
) -> None:
    """Prompt draft -> pixel binding -> CAPI test event, all tenant-isolated."""
    user_a = _insert_user(owner_engine, "agency-a-p8")
    user_b = _insert_user(owner_engine, "agency-b-p8")
    created_workspaces: list[UUID] = []
    secret_store = EncryptedSecretStore.from_settings(phase8_settings)
    meta_svc = MetaConnectionService(
        settings=phase8_settings,
        graph_client=MockMetaGraphClient(),
        secret_store=secret_store,
    )
    ads_svc = AdsOperationsService(
        settings=phase8_settings,
        meta_service=meta_svc,
        secret_store=secret_store,
        ads_client=MockMetaAdsClient(),
    )
    capi_svc = CapiService(
        settings=phase8_settings,
        secret_store=secret_store,
        capi_client=MockCapiClient(),
    )
    muse_adapter = MockMuseAdapter()
    try:
        ws_a = create_workspace(
            app_session_factory,
            user_id=user_a,
            name="Prompt Agency A",
            workspace_type=WorkspaceType.AGENCY,
        )
        ws_b = create_workspace(
            app_session_factory,
            user_id=user_b,
            name="Prompt Agency B",
            workspace_type=WorkspaceType.AGENCY,
        )
        created_workspaces.extend([ws_a.id, ws_b.id])

        with tenant_transaction(app_session_factory, user_id=user_a, workspace_id=ws_a.id) as (
            session,
            ctx_a,
        ):
            client_a = create_client(session, ctx_a, name="Brand P8 A")
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
            page_assets = [
                a
                for a in meta_svc.list_connection_assets(
                    session, workspace_id=ws_a.id, connection_id=conn_a.id
                )
                if a.asset_type == MetaAssetType.FACEBOOK_PAGE
            ]
            assert len(page_assets) == 1

        sync_res = sync_workspace_ads(
            app_session_factory,
            service=ads_svc,
            workspace_id=ws_a.id,
            actor_user_id=user_a,
            client_id=client_a.id,
        )
        assert sync_res.accounts_synced == 1
        ad_account_id = sync_res.accounts[0].id

        with tenant_transaction(app_session_factory, user_id=user_a, workspace_id=ws_a.id) as (
            session,
            _ctx,
        ):
            draft = ads_svc.create_prompt_draft(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                ad_account_id=ad_account_id,
                prompt="Launch a traffic campaign for our reels service in the US",
                page_asset_id=page_assets[0].id,
                creative_refs=None,
                muse_adapter=muse_adapter,
            )
            assert draft.targeting["countries"] == ["US"]
            assert draft.targeting["creative_brief"]["headline"] != ""
            assert draft.targeting["page_id"] == page_assets[0].external_asset_id
            # No image assets and no refs: draft exists but validation flags creative.
            assert draft.validation_passed is False

            ads_svc.update_draft(
                session,
                workspace_id=ws_a.id,
                draft_id=draft.id,
                actor_user_id=user_a,
                creative_refs=["approved-reel-v1"],
            )
            validated = ads_svc.validate_draft(
                session,
                workspace_id=ws_a.id,
                draft_id=draft.id,
                actor_user_id=user_a,
            )
            assert validated.validation_passed is True

            with pytest.raises(ValidationDomainError, match="numeric"):
                ads_svc.update_ad_account_pixel(
                    session,
                    workspace_id=ws_a.id,
                    ad_account_id=ad_account_id,
                    actor_user_id=user_a,
                    pixel_dataset_id="not-a-pixel",
                )
            pixel_bound = ads_svc.update_ad_account_pixel(
                session,
                workspace_id=ws_a.id,
                ad_account_id=ad_account_id,
                actor_user_id=user_a,
                pixel_dataset_id="123456789012345",
            )
            assert pixel_bound.pixel_dataset_id == "123456789012345"

            receipt = capi_svc.send_event(
                session,
                workspace_id=ws_a.id,
                actor_user_id=user_a,
                ad_account_id=ad_account_id,
                event_name="Lead",
                user_data=build_capi_user_data(email="buyer@shop.test"),
                test_event_code="TEST_P8",
            )
            assert receipt.test_mode is True
            assert receipt.events_received == 1
            assert receipt.pixel_dataset_id == "123456789012345"

        # Cross-tenant isolation: workspace B cannot touch workspace A's ads objects.
        with tenant_transaction(app_session_factory, user_id=user_b, workspace_id=ws_b.id) as (
            session,
            _ctx,
        ):
            with pytest.raises(NotFoundError):
                capi_svc.send_event(
                    session,
                    workspace_id=ws_b.id,
                    actor_user_id=user_b,
                    ad_account_id=ad_account_id,
                    event_name="Lead",
                    user_data=build_capi_user_data(email="intruder@evil.test"),
                    test_event_code="TEST_P8",
                )
    finally:
        _cleanup(owner_engine, workspace_ids=created_workspaces, user_ids=[user_a, user_b])
