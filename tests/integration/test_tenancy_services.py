"""Live PostgreSQL integration tests for Step 1 function hardening & Batch A tenancy/storage."""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from database.seeds.seed_dev import seed_canonical_roles
from packages.shared.config import BaseAppSettings, normalize_database_url
from packages.shared.enums import (
    BrandAssetType,
    ClientStatus,
    MemberStatus,
    RoleName,
    WorkspaceType,
)
from packages.shared.errors import (
    NotFoundError,
    ValidationDomainError,
    WorkspaceAccessDeniedError,
    WorkspaceMemberLimitExceededError,
)
from packages.storage import InMemoryStorageBackend
from packages.tenancy import (
    add_workspace_member,
    assign_client_member,
    complete_brand_asset_upload,
    create_client,
    create_session_factory,
    create_workspace,
    get_brand_asset_download_url,
    get_brand_profile,
    get_client,
    initiate_brand_asset_upload,
    list_audit_logs,
    list_brand_assets,
    list_clients,
    list_user_workspaces,
    list_workspace_members,
    tenant_transaction,
    update_client,
    update_workspace_member,
    upsert_brand_profile,
)

pytestmark = pytest.mark.integration

_HARDENED_FUNCTIONS = (
    "current_app_workspace_id",
    "current_app_user_id",
    "set_updated_at",
    "enforce_workspace_member_limit",
    "prevent_audit_log_mutation",
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
    return f"postgresql+psycopg://metuse_app:change-me-app@localhost:{settings.postgres_host_port}/metuse"


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
    engine = create_engine(_get_app_dsn(), future=True)
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


def _insert_user(owner_engine: Engine, label: str) -> tuple[UUID, str]:
    uid = uuid4()
    email = f"tenancy-{label}-{uid.hex[:10]}@metuse.test"
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO public.users (id, email, password_hash, full_name, is_active)
                VALUES (:id, :email, 'scrypt$test$hash', :full_name, true)
                """
            ),
            {"id": uid, "email": email, "full_name": f"User {label}"},
        )
    return uid, email


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


def test_postgres_functions_enforce_pg_catalog_public_search_path(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    """
                SELECT p.proname, p.proconfig
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public'
                  AND p.proname = ANY(:names)
                """
                ),
                {"names": list(_HARDENED_FUNCTIONS)},
            )
            .mappings()
            .all()
        )

    by_name = {str(r["proname"]): r["proconfig"] for r in rows}
    assert set(by_name.keys()) == set(_HARDENED_FUNCTIONS)
    for fn_name, cfg in by_name.items():
        assert cfg is not None, f"{fn_name} missing proconfig"
        assert "search_path=pg_catalog, public" in list(cfg), f"{fn_name} proconfig={cfg}"


def test_agency_5_member_cap_and_reactivation_maps_to_409(
    owner_engine: Engine,
    app_session_factory: sessionmaker[Session],
) -> None:
    ws_ids: list[UUID] = []
    user_ids: list[UUID] = []
    try:
        owner_id, _ = _insert_user(owner_engine, "cap-owner")
        user_ids.append(owner_id)
        extras = [_insert_user(owner_engine, f"cap-m{i}") for i in range(5)]
        user_ids.extend([uid for uid, _ in extras])

        ws = create_workspace(
            app_session_factory,
            user_id=owner_id,
            name="Cap Test Agency",
            workspace_type=WorkspaceType.AGENCY,
        )
        ws_ids.append(ws.id)
        assert ws.max_members == 5

        added_ids: list[UUID] = []
        with tenant_transaction(app_session_factory, user_id=owner_id, workspace_id=ws.id) as (
            session,
            ctx,
        ):
            for idx in range(4):
                member = add_workspace_member(
                    session,
                    ctx,
                    email=extras[idx][1],
                    role_name=RoleName.AGENCY_MEMBER,
                )
                added_ids.append(member.id)

            assert len(list_workspace_members(session, ctx)) == 5

            # 6th member must raise WorkspaceMemberLimitExceededError (HTTP 409)
            with pytest.raises(WorkspaceMemberLimitExceededError) as exc_info:
                add_workspace_member(
                    session,
                    ctx,
                    email=extras[4][1],
                    role_name=RoleName.AGENCY_MEMBER,
                )
            assert exc_info.value.status_code == 409

            # Remove 5th member, add replacement, then attempt reactivating removed member -> 409
            update_workspace_member(
                session,
                ctx,
                member_id=added_ids[3],
                status=MemberStatus.REMOVED,
            )
            add_workspace_member(
                session,
                ctx,
                email=extras[4][1],
                role_name=RoleName.AGENCY_MEMBER,
            )
            with pytest.raises(WorkspaceMemberLimitExceededError) as reactivate_exc:
                update_workspace_member(
                    session,
                    ctx,
                    member_id=added_ids[3],
                    status=MemberStatus.ACTIVE,
                )
            assert reactivate_exc.value.status_code == 409
    finally:
        _cleanup(owner_engine, workspace_ids=ws_ids, user_ids=user_ids)


def test_creator_onboarding_works_without_ad_account_and_caps_at_one(
    owner_engine: Engine,
    app_session_factory: sessionmaker[Session],
) -> None:
    ws_ids: list[UUID] = []
    user_ids: list[UUID] = []
    try:
        creator_id, _ = _insert_user(owner_engine, "solo-creator")
        other_id, other_email = _insert_user(owner_engine, "other-user")
        user_ids.extend([creator_id, other_id])

        ws = create_workspace(
            app_session_factory,
            user_id=creator_id,
            name="Maya Creator Studio",
            workspace_type=WorkspaceType.CREATOR,
        )
        ws_ids.append(ws.id)
        assert ws.workspace_type == WorkspaceType.CREATOR
        assert ws.max_members == 1

        summaries = list_user_workspaces(app_session_factory, user_id=creator_id)
        assert any(
            s.workspace_id == ws.id and s.role_name == RoleName.CREATOR_OWNER for s in summaries
        )

        with tenant_transaction(app_session_factory, user_id=creator_id, workspace_id=ws.id) as (
            session,
            ctx,
        ):
            clients = list_clients(session, ctx)
            assert len(clients) == 1
            assert clients[0].metadata.get("requires_ad_account") is False

            brand = get_brand_profile(session, ctx, client_id=clients[0].id)
            assert brand.brand_name == "Maya Creator Studio"

            updated = upsert_brand_profile(
                session,
                ctx,
                client_id=clients[0].id,
                brand_name="Maya Creator Studio",
                tone_of_voice="Warm, honest, fast-paced",
                color_palette=["#0F172A", "#22C55E"],
            )
            assert updated.tone_of_voice == "Warm, honest, fast-paced"

            with pytest.raises(WorkspaceMemberLimitExceededError):
                add_workspace_member(
                    session,
                    ctx,
                    email=other_email,
                    role_name=RoleName.AGENCY_MEMBER,
                )
    finally:
        _cleanup(owner_engine, workspace_ids=ws_ids, user_ids=user_ids)


def test_cross_tenant_isolation_client_memberships_and_brand_assets(
    owner_engine: Engine,
    app_session_factory: sessionmaker[Session],
) -> None:
    ws_ids: list[UUID] = []
    user_ids: list[UUID] = []
    storage = InMemoryStorageBackend(bucket="metuse-test")
    try:
        owner_a, _ = _insert_user(owner_engine, "agency-a")
        owner_b, _ = _insert_user(owner_engine, "agency-b")
        reviewer_a, reviewer_email = _insert_user(owner_engine, "reviewer-a")
        user_ids.extend([owner_a, owner_b, reviewer_a])

        ws_a = create_workspace(
            app_session_factory,
            user_id=owner_a,
            name="Agency Alpha",
            workspace_type=WorkspaceType.AGENCY,
        )
        ws_b = create_workspace(
            app_session_factory,
            user_id=owner_b,
            name="Agency Beta",
            workspace_type=WorkspaceType.AGENCY,
        )
        ws_ids.extend([ws_a.id, ws_b.id])

        with tenant_transaction(app_session_factory, user_id=owner_a, workspace_id=ws_a.id) as (
            session,
            ctx_a,
        ):
            c1 = create_client(session, ctx_a, name="Client One")
            c2 = create_client(session, ctx_a, name="Client Two")
            c1_upd = update_client(session, ctx_a, client_id=c1.id, status=ClientStatus.ACTIVE)
            assert c1_upd.id == c1.id

            add_workspace_member(
                session,
                ctx_a,
                email=reviewer_email,
                role_name=RoleName.CLIENT_REVIEWER,
            )
            assign_client_member(
                session,
                ctx_a,
                client_id=c1.id,
                user_id=reviewer_a,
                role_name=RoleName.CLIENT_REVIEWER,
            )

            upsert_brand_profile(session, ctx_a, client_id=c1.id, brand_name="Client One Brand")

            # Valid PNG presign -> upload -> complete -> download
            png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
            init = initiate_brand_asset_upload(
                session,
                ctx_a,
                storage,
                client_id=c1.id,
                asset_type=BrandAssetType.LOGO,
                filename="logo.png",
                mime_type="image/png",
                size_bytes=len(png_data),
            )
            storage.put_object_bytes(key=init.storage_key, data=png_data, content_type="image/png")
            asset = complete_brand_asset_upload(
                session,
                ctx_a,
                storage,
                client_id=c1.id,
                asset_id=init.asset_id,
                asset_type=BrandAssetType.LOGO,
                filename="logo.png",
                mime_type="image/png",
                storage_key=init.storage_key,
            )
            assert asset.mime_type == "image/png"
            assert len(list_brand_assets(session, ctx_a, client_id=c1.id)) == 1
            dl = get_brand_asset_download_url(
                session,
                ctx_a,
                storage,
                client_id=c1.id,
                asset_id=asset.id,
            )
            assert init.storage_key in dl.url

            # Spoofed magic bytes must fail and purge uploaded object
            bad_init = initiate_brand_asset_upload(
                session,
                ctx_a,
                storage,
                client_id=c1.id,
                asset_type=BrandAssetType.LOGO,
                filename="fake.png",
                mime_type="image/png",
                size_bytes=32,
            )
            storage.put_object_bytes(
                key=bad_init.storage_key,
                data=b"<svg><script>alert(1)</script></svg>",
                content_type="image/png",
            )
            with pytest.raises(ValidationDomainError):
                complete_brand_asset_upload(
                    session,
                    ctx_a,
                    storage,
                    client_id=c1.id,
                    asset_id=bad_init.asset_id,
                    asset_type=BrandAssetType.LOGO,
                    filename="fake.png",
                    mime_type="image/png",
                    storage_key=bad_init.storage_key,
                )
            with pytest.raises(NotFoundError):
                storage.head_object(key=bad_init.storage_key)

            logs_a, total_a = list_audit_logs(session, ctx_a)
            assert total_a >= 4
            assert all(entry.workspace_id == ws_a.id for entry in logs_a)

        # Client reviewer in Agency A sees ONLY Client One, NEVER Client Two
        with tenant_transaction(app_session_factory, user_id=reviewer_a, workspace_id=ws_a.id) as (
            session,
            ctx_rev,
        ):
            visible = list_clients(session, ctx_rev)
            assert [c.id for c in visible] == [c1.id]
            assert get_client(session, ctx_rev, client_id=c1.id).id == c1.id
            with pytest.raises(NotFoundError):
                get_client(session, ctx_rev, client_id=c2.id)

        # Agency B owner CANNOT enter Agency A workspace
        with pytest.raises(WorkspaceAccessDeniedError):
            with tenant_transaction(app_session_factory, user_id=owner_b, workspace_id=ws_a.id):
                pass

        # Agency B scoped to ws_b sees zero clients/brands/logs from Agency A
        with tenant_transaction(app_session_factory, user_id=owner_b, workspace_id=ws_b.id) as (
            session,
            ctx_b,
        ):
            assert list_clients(session, ctx_b) == []
            with pytest.raises(NotFoundError):
                get_client(session, ctx_b, client_id=c1.id)
            with pytest.raises(NotFoundError):
                get_brand_profile(session, ctx_b, client_id=c1.id)
            logs_b, _ = list_audit_logs(session, ctx_b)
            assert all(entry.workspace_id == ws_b.id for entry in logs_b)
    finally:
        _cleanup(owner_engine, workspace_ids=ws_ids, user_ids=user_ids)
