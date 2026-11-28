"""Live PostgreSQL Phase 8b integration tests: persistent secret vault + RLS."""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from database.models import SecretVault
from database.seeds.seed_dev import seed_canonical_roles
from packages.meta_integrations import DbSecretStore
from packages.shared.config import BaseAppSettings, normalize_database_url
from packages.shared.enums import WorkspaceType
from packages.shared.errors import ExternalServiceError, NotFoundError
from packages.tenancy import create_session_factory, create_workspace, rls_transaction
from packages.tenancy.db import is_supabase_transaction_pooler_url

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


def _insert_user(owner_engine: Engine, label: str) -> UUID:
    uid = uuid4()
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO public.users (id, email, full_name, is_active) "
                "VALUES (:id, :email, :name, true)"
            ),
            {"id": uid, "email": f"p8b-{label}-{uid.hex[:8]}@metuse.test", "name": label},
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


def test_secret_vault_table_has_force_rls_enabled(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname = 'secret_vault'
                """
            )
        ).one()
    assert bool(row[0]) is True
    assert bool(row[1]) is True


def test_db_secret_store_roundtrip_across_instances_and_revocation(
    owner_engine: Engine,
    app_session_factory: sessionmaker[Session],
) -> None:
    """Vault envelopes persist in Postgres and are readable from any store instance."""
    user_a = _insert_user(owner_engine, "vault-owner")
    created_workspaces: list[UUID] = []
    try:
        ws = create_workspace(
            app_session_factory,
            user_id=user_a,
            name="Vault Agency",
            workspace_type=WorkspaceType.AGENCY,
        )
        created_workspaces.append(ws.id)

        writer = DbSecretStore(app_session_factory, master_key_material="p8b-vault-key-material")
        reader = DbSecretStore(app_session_factory, master_key_material="p8b-vault-key-material")
        ref = writer.put_secret(
            workspace_id=ws.id, namespace="meta", secret_value="EAA_LIVE_TOKEN_XYZ"
        )
        assert ref.startswith(f"secret://meta/{ws.id}/")
        assert "EAA_LIVE_TOKEN_XYZ" not in ref
        assert reader.get_secret(secret_ref=ref) == "EAA_LIVE_TOKEN_XYZ"
        assert reader.has_secret(secret_ref=ref) is True

        wrong_key = DbSecretStore(app_session_factory, master_key_material="wrong-key")
        with pytest.raises(ExternalServiceError, match="decryption failed"):
            wrong_key.get_secret(secret_ref=ref)

        with pytest.raises(NotFoundError):
            reader.get_secret(secret_ref=f"secret://meta/{ws.id}/missing")
        with pytest.raises(NotFoundError):
            reader.get_secret(secret_ref="not-a-vault-ref")

        reader.delete_secret(secret_ref=ref)
        assert reader.has_secret(secret_ref=ref) is False
        # Deletion is idempotent.
        reader.delete_secret(secret_ref=ref)
    finally:
        _cleanup(owner_engine, workspace_ids=created_workspaces, user_ids=[user_a])


def test_secret_vault_rows_are_workspace_isolated_under_rls(
    owner_engine: Engine,
    app_session_factory: sessionmaker[Session],
) -> None:
    """Workspace B's RLS context cannot read workspace A's vault envelopes."""
    user_a = _insert_user(owner_engine, "vault-tenant-a")
    user_b = _insert_user(owner_engine, "vault-tenant-b")
    created_workspaces: list[UUID] = []
    try:
        ws_a = create_workspace(
            app_session_factory,
            user_id=user_a,
            name="Vault Tenant A",
            workspace_type=WorkspaceType.AGENCY,
        )
        ws_b = create_workspace(
            app_session_factory,
            user_id=user_b,
            name="Vault Tenant B",
            workspace_type=WorkspaceType.AGENCY,
        )
        created_workspaces.extend([ws_a.id, ws_b.id])

        store = DbSecretStore(app_session_factory, master_key_material="p8b-rls-key")
        ref = store.put_secret(workspace_id=ws_a.id, namespace="meta", secret_value="EAA_TENANT_A")

        with rls_transaction(app_session_factory, user_id=None, workspace_id=ws_b.id) as session:
            rows_b = session.execute(select(SecretVault)).scalars().all()
            assert rows_b == []

        with rls_transaction(app_session_factory, user_id=None, workspace_id=ws_a.id) as session:
            rows_a = session.execute(select(SecretVault)).scalars().all()
            assert [r.secret_ref for r in rows_a] == [ref]
    finally:
        _cleanup(owner_engine, workspace_ids=created_workspaces, user_ids=[user_a, user_b])
