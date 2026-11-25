"""Live PostgreSQL integration tests for M1 schema, roles, FORCE RLS, triggers, and seeds."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import errors as pg_errors
from sqlalchemy import create_engine, func, select, table, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError

from database.seeds.seed_dev import seed_canonical_roles
from packages.shared.config import BaseAppSettings, normalize_database_url

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RLS_TABLES = (
    "workspaces",
    "workspace_memberships",
    "clients",
    "client_memberships",
    "brand_profiles",
    "brand_assets",
    "audit_logs",
)
_ALL_TABLES = (
    "users",
    "user_identities",
    "roles",
    *_RLS_TABLES,
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
    port = settings.postgres_host_port
    return f"postgresql+psycopg://metuse_app:change-me-app@localhost:{port}/metuse"


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
def app_engine() -> Iterator[Engine]:
    engine = create_engine(_get_app_dsn(), future=True)
    try:
        yield engine
    finally:
        engine.dispose()


def _create_test_user(conn: Any, tag: str) -> UUID:
    uid = uuid4()
    email = f"rls-{tag}-{uid.hex[:10]}@metuse.test"
    conn.execute(
        text(
            """
            INSERT INTO public.users (id, email, password_hash, full_name, is_active)
            VALUES (:id, :email, 'scrypt$test$hash', :full_name, true)
            """
        ),
        {"id": uid, "email": email, "full_name": f"Test {tag}"},
    )
    return uid


def _cleanup_test_entities(
    owner_engine: Engine,
    *,
    workspace_ids: list[UUID],
    user_ids: list[UUID],
) -> None:
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


def test_metuse_app_role_and_force_rls_metadata(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        role_row = conn.execute(
            text(
                """
                SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolcanlogin
                FROM pg_roles
                WHERE rolname = 'metuse_app'
                """
            )
        ).mappings().one()
        assert role_row["rolsuper"] is False
        assert role_row["rolbypassrls"] is False
        assert role_row["rolcreatedb"] is False
        assert role_row["rolcreaterole"] is False
        assert role_row["rolcanlogin"] is True

        owned_tables = conn.execute(
            text(
                """
                SELECT count(*)
                FROM pg_tables
                WHERE schemaname = 'public' AND tableowner = 'metuse_app'
                """
            )
        ).scalar_one()
        assert owned_tables == 0

        existing_tables = {
            row[0]
            for row in conn.execute(
                text(
                    """
                    SELECT tablename
                    FROM pg_tables
                    WHERE schemaname = 'public'
                    """
                )
            ).all()
        }
        assert set(_ALL_TABLES).issubset(existing_tables)

        rls_rows = conn.execute(
            text(
                """
                SELECT relname, relrowsecurity, relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relname = ANY(:tables)
                """
            ),
            {"tables": list(_RLS_TABLES)},
        ).mappings().all()

        rls_by_name = {str(r["relname"]): r for r in rls_rows}
        assert set(rls_by_name.keys()) == set(_RLS_TABLES)
        for tbl in _RLS_TABLES:
            assert rls_by_name[tbl]["relrowsecurity"] is True, tbl
            assert rls_by_name[tbl]["relforcerowsecurity"] is True, tbl


def test_fail_closed_when_gucs_unset(owner_engine: Engine, app_engine: Engine) -> None:
    ws_id = uuid4()
    created_users: list[UUID] = []
    created_workspaces: list[UUID] = []
    try:
        with owner_engine.begin() as conn:
            u_id = _create_test_user(conn, "failclosed")
            created_users.append(u_id)
            role_id = conn.execute(
                text("SELECT id FROM public.roles WHERE name = 'agency_owner'")
            ).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO public.workspaces (
                        id, name, slug, workspace_type, max_members, created_by_user_id
                    )
                    VALUES (:id, 'FailClosed WS', :slug, 'agency', 5, :uid)
                    """
                ),
                {"id": ws_id, "slug": f"failclosed-{ws_id.hex[:8]}", "uid": u_id},
            )
            created_workspaces.append(ws_id)
            client_id = uuid4()
            conn.execute(
                text(
                    """
                    INSERT INTO public.clients (id, workspace_id, name, slug, status)
                    VALUES (:cid, :ws, 'FailClosed Client', :cslug, 'active')
                    """
                ),
                {"cid": client_id, "ws": ws_id, "cslug": f"fc-client-{client_id.hex[:8]}"},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO public.audit_logs (
                        id, workspace_id, actor_user_id, action, target_type, target_id
                    )
                    VALUES (:id, NULL, :uid, 'auth.login', 'user', :tid)
                    """
                ),
                {"id": uuid4(), "uid": u_id, "tid": str(u_id)},
            )

        with app_engine.connect() as conn:
            # No GUCs set -> every RLS table must return 0 rows
            for tbl in _RLS_TABLES:
                count_stmt = select(func.count()).select_from(table(tbl, schema="public"))
                count = conn.execute(count_stmt).scalar_one()
                assert count == 0, f"Expected 0 rows for {tbl} with unset GUCs"

        # Writes without GUCs must be rejected on tenant-scoped tables
        with pytest.raises(DBAPIError) as ws_exc:
            with app_engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO public.workspaces (
                            id, name, slug, workspace_type, max_members, created_by_user_id
                        )
                        VALUES (:id, 'Denied WS', :slug, 'agency', 5, :uid)
                        """
                    ),
                    {"id": uuid4(), "slug": f"denied-{uuid4().hex[:8]}", "uid": u_id},
                )
        assert isinstance(ws_exc.value.orig, pg_errors.InsufficientPrivilege)

        with pytest.raises(DBAPIError) as client_exc:
            with app_engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO public.clients (id, workspace_id, name, slug, status)
                        VALUES (:id, :ws, 'Denied Client', :slug, 'active')
                        """
                    ),
                    {"id": uuid4(), "ws": ws_id, "slug": f"denied-c-{uuid4().hex[:8]}"},
                )
        assert isinstance(client_exc.value.orig, pg_errors.InsufficientPrivilege)

        with pytest.raises(DBAPIError) as wm_exc:
            with app_engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO public.workspace_memberships (
                            id, workspace_id, user_id, role_id, status
                        )
                        VALUES (:id, :ws, :uid, :rid, 'active')
                        """
                    ),
                    {"id": uuid4(), "ws": ws_id, "uid": u_id, "rid": role_id},
                )
        assert isinstance(wm_exc.value.orig, pg_errors.InsufficientPrivilege)

        with pytest.raises(DBAPIError) as audit_exc:
            with app_engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO public.audit_logs (
                            id, workspace_id, actor_user_id, action, target_type
                        )
                        VALUES (:id, :ws, :uid, 'workspace.created', 'workspace')
                        """
                    ),
                    {"id": uuid4(), "ws": ws_id, "uid": u_id},
                )
        assert isinstance(audit_exc.value.orig, pg_errors.InsufficientPrivilege)
    finally:
        _cleanup_test_entities(
            owner_engine,
            workspace_ids=created_workspaces,
            user_ids=created_users,
        )


def test_workspace_insert_returning_and_non_recursive_membership_listing(
    owner_engine: Engine,
    app_engine: Engine,
) -> None:
    created_users: list[UUID] = []
    created_workspaces: list[UUID] = []
    try:
        with owner_engine.begin() as conn:
            u_id = _create_test_user(conn, "wsreturn")
            created_users.append(u_id)
            owner_role_id = conn.execute(
                text("SELECT id FROM public.roles WHERE name = 'agency_owner'")
            ).scalar_one()

        ws_id = uuid4()
        ws_slug = f"ws-ret-{ws_id.hex[:8]}"
        with app_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(u_id)},
            )
            inserted = conn.execute(
                text(
                    """
                    INSERT INTO public.workspaces (
                        id, name, slug, workspace_type, max_members, created_by_user_id
                    )
                    VALUES (:id, 'Returning WS', :slug, 'agency', 5, :uid)
                    RETURNING id, name, slug, workspace_type, max_members, created_by_user_id
                    """
                ),
                {"id": ws_id, "slug": ws_slug, "uid": u_id},
            ).mappings().one()
            created_workspaces.append(ws_id)
            assert UUID(str(inserted["id"])) == ws_id
            assert inserted["slug"] == ws_slug
            assert UUID(str(inserted["created_by_user_id"])) == u_id

            # Set workspace context to create initial owner membership
            conn.execute(
                text("SELECT set_config('app.current_workspace_id', :ws, true)"),
                {"ws": str(ws_id)},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO public.workspace_memberships (
                        id, workspace_id, user_id, role_id, status
                    )
                    VALUES (:id, :ws, :uid, :rid, 'active')
                    """
                ),
                {"id": uuid4(), "ws": ws_id, "uid": u_id, "rid": owner_role_id},
            )

        # Listing workspaces & workspace_memberships with ONLY current_user_id must not recurse
        with app_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(u_id)},
            )
            ws_rows = conn.execute(
                text("SELECT id FROM public.workspaces")
            ).scalars().all()
            assert ws_id in [UUID(str(r)) for r in ws_rows]

            wm_rows = conn.execute(
                text("SELECT workspace_id, user_id FROM public.workspace_memberships")
            ).mappings().all()
            assert len(wm_rows) == 1
            assert UUID(str(wm_rows[0]["workspace_id"])) == ws_id
            assert UUID(str(wm_rows[0]["user_id"])) == u_id
    finally:
        _cleanup_test_entities(
            owner_engine,
            workspace_ids=created_workspaces,
            user_ids=created_users,
        )


def test_cross_tenant_rls_isolation_across_all_seven_tables_and_composite_fk(
    owner_engine: Engine,
    app_engine: Engine,
) -> None:
    created_users: list[UUID] = []
    created_workspaces: list[UUID] = []
    try:
        with owner_engine.begin() as conn:
            user_a = _create_test_user(conn, "tenant-a")
            user_b = _create_test_user(conn, "tenant-b")
            created_users.extend([user_a, user_b])

            roles = {
                str(row["name"]): UUID(str(row["id"]))
                for row in conn.execute(text("SELECT id, name FROM public.roles")).mappings().all()
            }

            ws_a, ws_b = uuid4(), uuid4()
            created_workspaces.extend([ws_a, ws_b])
            for ws_id, name, uid in (
                (ws_a, "Tenant A", user_a),
                (ws_b, "Tenant B", user_b),
            ):
                conn.execute(
                    text(
                        """
                        INSERT INTO public.workspaces (
                            id, name, slug, workspace_type, max_members, created_by_user_id
                        )
                        VALUES (:id, :name, :slug, 'agency', 5, :uid)
                        """
                    ),
                    {"id": ws_id, "name": name, "slug": f"iso-{ws_id.hex[:8]}", "uid": uid},
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO public.workspace_memberships (
                            id, workspace_id, user_id, role_id, status
                        )
                        VALUES (:id, :ws, :uid, :rid, 'active')
                        """
                    ),
                    {
                        "id": uuid4(),
                        "ws": ws_id,
                        "uid": uid,
                        "rid": roles["agency_owner"],
                    },
                )

            client_a, client_b = uuid4(), uuid4()
            brand_a, brand_b = uuid4(), uuid4()
            asset_a, asset_b = uuid4(), uuid4()
            audit_a, audit_b = uuid4(), uuid4()

            for ws_id, uid, cid, bid, aid, aud_id, label in (
                (ws_a, user_a, client_a, brand_a, asset_a, audit_a, "a"),
                (ws_b, user_b, client_b, brand_b, asset_b, audit_b, "b"),
            ):
                conn.execute(
                    text(
                        """
                        INSERT INTO public.clients (id, workspace_id, name, slug, status)
                        VALUES (:cid, :ws, :name, :slug, 'active')
                        """
                    ),
                    {
                        "cid": cid,
                        "ws": ws_id,
                        "name": f"Client {label}",
                        "slug": f"client-{label}-{cid.hex[:8]}",
                    },
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO public.client_memberships (
                            id, workspace_id, client_id, user_id, role_id
                        )
                        VALUES (:id, :ws, :cid, :uid, :rid)
                        """
                    ),
                    {
                        "id": uuid4(),
                        "ws": ws_id,
                        "cid": cid,
                        "uid": uid,
                        "rid": roles["client_reviewer"],
                    },
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO public.brand_profiles (
                            id, workspace_id, client_id, brand_name
                        )
                        VALUES (:bid, :ws, :cid, :bname)
                        """
                    ),
                    {"bid": bid, "ws": ws_id, "cid": cid, "bname": f"Brand {label}"},
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO public.brand_assets (
                            id, workspace_id, client_id, brand_profile_id,
                            asset_type, filename, mime_type, size_bytes,
                            storage_bucket, storage_key
                        )
                        VALUES (
                            :aid, :ws, :cid, :bid,
                            'logo', 'logo.png', 'image/png', 1024,
                            'metuse-dev', :skey
                        )
                        """
                    ),
                    {
                        "aid": aid,
                        "ws": ws_id,
                        "cid": cid,
                        "bid": bid,
                        "skey": f"workspaces/{ws_id}/clients/{cid}/logo-{aid.hex[:8]}.png",
                    },
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO public.audit_logs (
                            id, workspace_id, client_id, actor_user_id,
                            action, target_type, target_id
                        )
                        VALUES (:id, :ws, :cid, :uid, 'client.created', 'client', :tid)
                        """
                    ),
                    {"id": aud_id, "ws": ws_id, "cid": cid, "uid": uid, "tid": str(cid)},
                )

        # Verify metuse_app scoped to Tenant A sees ONLY Tenant A across all 7 tables
        with app_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.current_workspace_id', :ws, true)"),
                {"ws": str(ws_a)},
            )
            conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(user_a)},
            )

            assert conn.execute(
                text("SELECT id FROM public.workspaces")
            ).scalars().all() == [ws_a]
            assert conn.execute(
                text("SELECT user_id FROM public.workspace_memberships")
            ).scalars().all() == [user_a]
            assert conn.execute(
                text("SELECT id FROM public.clients")
            ).scalars().all() == [client_a]
            assert conn.execute(
                text("SELECT client_id FROM public.client_memberships")
            ).scalars().all() == [client_a]
            assert conn.execute(
                text("SELECT id FROM public.brand_profiles")
            ).scalars().all() == [brand_a]
            assert conn.execute(
                text("SELECT id FROM public.brand_assets")
            ).scalars().all() == [asset_a]
            assert conn.execute(
                text("SELECT id FROM public.audit_logs")
            ).scalars().all() == [audit_a]

            # Cross-tenant UPDATE/DELETE affects 0 rows
            res = conn.execute(
                text("UPDATE public.clients SET name = 'Hijacked' WHERE id = :cid"),
                {"cid": client_b},
            )
            assert res.rowcount == 0

            res_del = conn.execute(
                text("DELETE FROM public.clients WHERE id = :cid"),
                {"cid": client_b},
            )
            assert res_del.rowcount == 0

        # Cross-tenant INSERT into Tenant B while scoped to Tenant A fails RLS WITH CHECK
        with pytest.raises(DBAPIError) as insert_exc:
            with app_engine.begin() as conn:
                conn.execute(
                    text("SELECT set_config('app.current_workspace_id', :ws, true)"),
                    {"ws": str(ws_a)},
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO public.clients (id, workspace_id, name, slug, status)
                        VALUES (:id, :ws_b, 'Cross Tenant', :slug, 'active')
                        """
                    ),
                    {"id": uuid4(), "ws_b": ws_b, "slug": f"cross-{uuid4().hex[:8]}"},
                )
        assert isinstance(insert_exc.value.orig, pg_errors.InsufficientPrivilege)

        # Composite FK (workspace_id, client_id) rejects mixing ws_a with client_b
        with pytest.raises(IntegrityError) as fk_exc:
            with app_engine.begin() as conn:
                conn.execute(
                    text("SELECT set_config('app.current_workspace_id', :ws, true)"),
                    {"ws": str(ws_a)},
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO public.brand_profiles (
                            id, workspace_id, client_id, brand_name
                        )
                        VALUES (:id, :ws_a, :client_b, 'Mismatched Brand')
                        """
                    ),
                    {"id": uuid4(), "ws_a": ws_a, "client_b": client_b},
                )
        assert isinstance(fk_exc.value.orig, pg_errors.ForeignKeyViolation)
    finally:
        _cleanup_test_entities(
            owner_engine,
            workspace_ids=created_workspaces,
            user_ids=created_users,
        )


def test_audit_logs_split_rls_and_immutability_for_app_and_owner(
    owner_engine: Engine,
    app_engine: Engine,
) -> None:
    created_users: list[UUID] = []
    try:
        with owner_engine.begin() as conn:
            user_a = _create_test_user(conn, "audit-a")
            user_b = _create_test_user(conn, "audit-b")
            created_users.extend([user_a, user_b])

        pre_ws_audit_a = uuid4()
        pre_ws_audit_b = uuid4()

        # Pre-workspace audit log insert & select scoped to actor_user_id
        with app_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(user_a)},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO public.audit_logs (
                        id, workspace_id, actor_user_id, action, target_type, target_id
                    )
                    VALUES (:id, NULL, :uid, 'auth.login', 'user', :tid)
                    """
                ),
                {"id": pre_ws_audit_a, "uid": user_a, "tid": str(user_a)},
            )

        with app_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(user_b)},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO public.audit_logs (
                        id, workspace_id, actor_user_id, action, target_type, target_id
                    )
                    VALUES (:id, NULL, :uid, 'auth.login', 'user', :tid)
                    """
                ),
                {"id": pre_ws_audit_b, "uid": user_b, "tid": str(user_b)},
            )
            visible = conn.execute(
                text("SELECT id FROM public.audit_logs WHERE id = ANY(:ids)"),
                {"ids": [pre_ws_audit_a, pre_ws_audit_b]},
            ).scalars().all()
            assert visible == [pre_ws_audit_b]

        # UPDATE, DELETE, TRUNCATE must be rejected for BOTH metuse_app and metuse (owner)
        for engine_label, engine in (("metuse_app", app_engine), ("metuse", owner_engine)):
            with pytest.raises(DBAPIError) as upd_exc:
                with engine.begin() as conn:
                    conn.execute(
                        text("UPDATE public.audit_logs SET action = 'tampered' WHERE id = :id"),
                        {"id": pre_ws_audit_a},
                    )
            assert isinstance(upd_exc.value.orig, pg_errors.InsufficientPrivilege), engine_label

            with pytest.raises(DBAPIError) as del_exc:
                with engine.begin() as conn:
                    conn.execute(
                        text("DELETE FROM public.audit_logs WHERE id = :id"),
                        {"id": pre_ws_audit_a},
                    )
            assert isinstance(del_exc.value.orig, pg_errors.InsufficientPrivilege), engine_label

            with pytest.raises(DBAPIError) as trunc_exc:
                with engine.begin() as conn:
                    conn.execute(text("TRUNCATE TABLE public.audit_logs"))
            assert isinstance(trunc_exc.value.orig, pg_errors.InsufficientPrivilege), engine_label
    finally:
        _cleanup_test_entities(owner_engine, workspace_ids=[], user_ids=created_users)


def test_audit_logs_preworkspace_actor_spoofing_rejected(
    owner_engine: Engine,
    app_engine: Engine,
) -> None:
    created_users: list[UUID] = []
    try:
        with owner_engine.begin() as conn:
            user_a = _create_test_user(conn, "spoof-a")
            user_b = _create_test_user(conn, "spoof-b")
            created_users.extend([user_a, user_b])

        # 1. Unset-GUC metuse_app INSERT with workspace_id=NULL and actor_user_id=user_b -> REJECTED
        with pytest.raises(DBAPIError) as unset_spoof_exc:
            with app_engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        INSERT INTO public.audit_logs (
                            id, workspace_id, actor_user_id, action, target_type, target_id
                        )
                        VALUES (:id, NULL, :actor_uid, 'auth.login', 'user', :tid)
                        """
                    ),
                    {"id": uuid4(), "actor_uid": user_b, "tid": str(user_b)},
                )
        assert isinstance(unset_spoof_exc.value.orig, pg_errors.InsufficientPrivilege)

        # 2. app.current_user_id=user_a INSERT with workspace_id=NULL
        # and actor_user_id=user_b -> REJECTED
        with pytest.raises(DBAPIError) as cross_user_spoof_exc:
            with app_engine.begin() as conn:
                conn.execute(
                    text("SELECT set_config('app.current_user_id', :uid, true)"),
                    {"uid": str(user_a)},
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO public.audit_logs (
                            id, workspace_id, actor_user_id, action, target_type, target_id
                        )
                        VALUES (:id, NULL, :actor_uid, 'auth.login', 'user', :tid)
                        """
                    ),
                    {"id": uuid4(), "actor_uid": user_b, "tid": str(user_b)},
                )
        assert isinstance(cross_user_spoof_exc.value.orig, pg_errors.InsufficientPrivilege)

        # 3. app.current_user_id=user_a INSERT with workspace_id=NULL
        # and actor_user_id=user_a -> ALLOWED
        own_audit_id = uuid4()
        with app_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(user_a)},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO public.audit_logs (
                        id, workspace_id, actor_user_id, action, target_type, target_id
                    )
                    VALUES (:id, NULL, :actor_uid, 'auth.login', 'user', :tid)
                    """
                ),
                {"id": own_audit_id, "actor_uid": user_a, "tid": str(user_a)},
            )
            visible = conn.execute(
                text("SELECT id FROM public.audit_logs WHERE id = :id"),
                {"id": own_audit_id},
            ).scalars().all()
            assert visible == [own_audit_id]

        # 4. Unset-GUC INSERT with workspace_id=NULL and actor_user_id=NULL
        # -> ALLOWED (auth.login_failed)
        anon_audit_id = uuid4()
        with app_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO public.audit_logs (
                        id, workspace_id, actor_user_id, action, target_type
                    )
                    VALUES (:id, NULL, NULL, 'auth.login_failed', 'auth')
                    """
                ),
                {"id": anon_audit_id},
            )
    finally:
        _cleanup_test_entities(owner_engine, workspace_ids=[], user_ids=created_users)


def test_workspace_member_cap_five_limit_trigger(
    owner_engine: Engine,
    app_engine: Engine,
) -> None:
    created_users: list[UUID] = []
    created_workspaces: list[UUID] = []
    try:
        with owner_engine.begin() as conn:
            users = [_create_test_user(conn, f"cap-{idx}") for idx in range(6)]
            created_users.extend(users)
            roles = {
                str(row["name"]): UUID(str(row["id"]))
                for row in conn.execute(text("SELECT id, name FROM public.roles")).mappings().all()
            }

            ws_id = uuid4()
            created_workspaces.append(ws_id)
            conn.execute(
                text(
                    """
                    INSERT INTO public.workspaces (
                        id, name, slug, workspace_type, max_members, created_by_user_id
                    )
                    VALUES (:id, 'Cap Five WS', :slug, 'agency', 5, :uid)
                    """
                ),
                {"id": ws_id, "slug": f"cap-five-{ws_id.hex[:8]}", "uid": users[0]},
            )

        with app_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.current_workspace_id', :ws, true)"),
                {"ws": str(ws_id)},
            )
            conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(users[0])},
            )
            membership_ids: list[UUID] = []
            for idx in range(5):
                mid = uuid4()
                membership_ids.append(mid)
                role_id = roles["agency_owner"] if idx == 0 else roles["agency_member"]
                conn.execute(
                    text(
                        """
                        INSERT INTO public.workspace_memberships (
                            id, workspace_id, user_id, role_id, status
                        )
                        VALUES (:id, :ws, :uid, :rid, 'active')
                        """
                    ),
                    {"id": mid, "ws": ws_id, "uid": users[idx], "rid": role_id},
                )

            # Updating role of an existing member at 5 members must succeed
            conn.execute(
                text(
                    """
                    UPDATE public.workspace_memberships
                    SET role_id = :rid
                    WHERE id = :mid
                    """
                ),
                {"rid": roles["agency_admin"], "mid": membership_ids[1]},
            )

        # Adding 6th member must fail with WORKSPACE_MEMBER_LIMIT_EXCEEDED
        with pytest.raises(IntegrityError) as cap_exc:
            with app_engine.begin() as conn:
                conn.execute(
                    text("SELECT set_config('app.current_workspace_id', :ws, true)"),
                    {"ws": str(ws_id)},
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO public.workspace_memberships (
                            id, workspace_id, user_id, role_id, status
                        )
                        VALUES (:id, :ws, :uid, :rid, 'active')
                        """
                    ),
                    {
                        "id": uuid4(),
                        "ws": ws_id,
                        "uid": users[5],
                        "rid": roles["agency_member"],
                    },
                )
        assert isinstance(cap_exc.value.orig, pg_errors.CheckViolation)
        assert "WORKSPACE_MEMBER_LIMIT_EXCEEDED" in str(cap_exc.value)

        # Removing one member allows adding the 6th user as a replacement
        with app_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.current_workspace_id', :ws, true)"),
                {"ws": str(ws_id)},
            )
            conn.execute(
                text(
                    """
                    UPDATE public.workspace_memberships
                    SET status = 'removed'
                    WHERE id = :mid
                    """
                ),
                {"mid": membership_ids[4]},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO public.workspace_memberships (
                        id, workspace_id, user_id, role_id, status
                    )
                    VALUES (:id, :ws, :uid, :rid, 'active')
                    """
                ),
                {
                    "id": uuid4(),
                    "ws": ws_id,
                    "uid": users[5],
                    "rid": roles["agency_member"],
                },
            )
            active_count = conn.execute(
                text(
                    """
                    SELECT count(*)
                    FROM public.workspace_memberships
                    WHERE workspace_id = :ws AND status <> 'removed'
                    """
                ),
                {"ws": ws_id},
            ).scalar_one()
            assert active_count == 5
    finally:
        _cleanup_test_entities(
            owner_engine,
            workspace_ids=created_workspaces,
            user_ids=created_users,
        )


def test_dev_seed_refuses_non_dev_and_is_idempotent(owner_engine: Engine) -> None:
    def _snapshot_counts() -> dict[str, int]:
        with owner_engine.connect() as conn:
            return {
                tbl: int(
                    conn.execute(
                        select(func.count()).select_from(table(tbl, schema="public"))
                    ).scalar_one()
                )
                for tbl in _ALL_TABLES
            }

    before_non_dev = _snapshot_counts()
    for bad_env in ("production", "staging"):
        env = os.environ.copy()
        env["APP_ENV"] = bad_env
        env["DATABASE_URL"] = _get_owner_dsn()
        proc = subprocess.run(
            [sys.executable, "-m", "database.seeds.seed_dev"],
            cwd=_REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode != 0
        assert "Refusing to run dev seed script" in proc.stderr
        assert _snapshot_counts() == before_non_dev

    dev_env = os.environ.copy()
    dev_env["APP_ENV"] = "development"
    dev_env["DATABASE_URL"] = _get_owner_dsn()
    first = subprocess.run(
        [sys.executable, "-m", "database.seeds.seed_dev"],
        cwd=_REPO_ROOT,
        env=dev_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    assert "RuntimeWarning" not in first.stderr
    after_first = _snapshot_counts()

    second = subprocess.run(
        [sys.executable, "-m", "database.seeds.seed_dev"],
        cwd=_REPO_ROOT,
        env=dev_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert second.returncode == 0, second.stderr
    assert "RuntimeWarning" not in second.stderr
    assert _snapshot_counts() == after_first
    assert first.stdout.strip() == second.stdout.strip()


# Silence unused-import warning if psycopg module itself isn't referenced directly beyond errors
assert hasattr(psycopg, "__version__")
