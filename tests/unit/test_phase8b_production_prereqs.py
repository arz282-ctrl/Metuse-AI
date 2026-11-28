"""Phase 8b unit tests: vault helpers, boot fail-fasts, deploy wiring (no network/DB)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr

from apps.api.deps import AppContainer
from packages.auth import InMemorySessionStore
from packages.meta_integrations import (
    EncryptedSecretStore,
    default_scopes_for_connection,
    parse_vault_secret_ref,
    resolve_vault_key_material,
)
from packages.meta_integrations.capabilities import PERMISSION_MATRIX
from packages.shared.config import BaseAppSettings
from packages.shared.errors import ExternalServiceError, NotFoundError

pytestmark = pytest.mark.unit


def _settings(**overrides: Any) -> BaseAppSettings:
    kwargs: dict[str, Any] = {
        "_env_file": None,
        "app_env": "test",
        "app_jwt_secret": SecretStr("phase8b-unit-test-secret-32-bytes-ok"),
    }
    kwargs.update(overrides)
    return BaseAppSettings(**kwargs)


def test_parse_vault_secret_ref_vectors() -> None:
    ws = uuid4()
    parsed = parse_vault_secret_ref(f"secret://meta/{ws}/abc123")
    assert parsed == ("meta", ws, "abc123")
    assert parse_vault_secret_ref("env://meta-oauth/x/y") is None
    assert parse_vault_secret_ref("secret://meta/only-two") is None
    assert parse_vault_secret_ref("secret://meta/not-a-uuid/token") is None
    assert parse_vault_secret_ref("") is None


def test_resolve_vault_key_material_precedence_and_prod_fail_closed() -> None:
    meta_first = _settings(
        meta_secret_encryption_key=SecretStr("meta-vault-key"),
        app_jwt_secret=SecretStr("jwt-secret"),
    )
    assert resolve_vault_key_material(meta_first) == "meta-vault-key"

    jwt_fallback = _settings(
        meta_secret_encryption_key=SecretStr(""),
        app_jwt_secret=SecretStr("jwt-secret"),
    )
    assert resolve_vault_key_material(jwt_fallback) == "jwt-secret"

    dev_fallback = _settings(
        app_env="development",
        app_jwt_secret=SecretStr(""),
        meta_secret_encryption_key=SecretStr(""),
    )
    assert resolve_vault_key_material(dev_fallback) != ""

    prod_bare = _settings(
        app_env="production",
        app_jwt_secret=SecretStr(""),
        meta_secret_encryption_key=SecretStr(""),
        redis_url=SecretStr("redis://localhost:6379/0"),
    )
    with pytest.raises(RuntimeError, match="META_SECRET_ENCRYPTION_KEY"):
        resolve_vault_key_material(prod_bare)


def test_memory_vault_roundtrip_and_wrong_key_decrypt_failure() -> None:
    ws = uuid4()
    store = EncryptedSecretStore(master_key_material="correct-key-material")
    ref = store.put_secret(workspace_id=ws, namespace="meta", secret_value="EAA_SECRET")
    assert store.get_secret(secret_ref=ref) == "EAA_SECRET"
    assert store.has_secret(secret_ref=ref) is True

    with pytest.raises(NotFoundError):
        store.get_secret(secret_ref=f"secret://meta/{ws}/missing")

    tampered = EncryptedSecretStore(master_key_material="wrong-key-material")
    tampered._vault[ref] = store._vault[ref]
    with pytest.raises(ExternalServiceError, match="decryption failed"):
        tampered.get_secret(secret_ref=ref)


def _staging_live_settings(**overrides: Any) -> BaseAppSettings:
    return _settings(
        app_env="staging",
        meta_auth_mode="live",
        meta_app_id="meta-app-id",
        meta_app_secret=SecretStr("meta-app-secret-value"),
        google_auth_mode="live",
        google_client_id="google-client-id",
        google_client_secret=SecretStr("google-client-secret"),
        meta_connections_mode="mock",
        **overrides,
    )


def test_boot_fails_fast_without_redis_outside_dev() -> None:
    staging = _staging_live_settings(redis_url=SecretStr(""))
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        AppContainer.from_settings(staging, session_factory=None, enable_db=False)


def test_boot_fails_fast_on_memory_vault_outside_dev() -> None:
    staging = _staging_live_settings(
        redis_url=SecretStr("redis://localhost:6379/0"),
        secret_store_backend="memory",
    )
    with pytest.raises(RuntimeError, match="SECRET_STORE_BACKEND=db"):
        AppContainer.from_settings(
            staging,
            session_factory=None,
            session_store=InMemorySessionStore(),
            enable_db=False,
        )


def test_boot_fails_fast_on_db_vault_without_database() -> None:
    staging = _staging_live_settings(
        redis_url=SecretStr("redis://localhost:6379/0"),
        secret_store_backend="db",
    )
    with pytest.raises(RuntimeError, match="APP_DATABASE_URL"):
        AppContainer.from_settings(
            staging,
            session_factory=None,
            session_store=InMemorySessionStore(),
            enable_db=False,
        )


def test_boot_fails_fast_on_unknown_vault_backend() -> None:
    settings = _settings(secret_store_backend="vault3000")
    with pytest.raises(RuntimeError, match="Unsupported SECRET_STORE_BACKEND"):
        AppContainer.from_settings(settings, session_factory=None, enable_db=False)


def test_boot_validates_live_modes_and_credentials() -> None:
    live_meta_bare = _settings(
        meta_auth_mode="live",
        meta_app_id="",
        meta_app_secret=SecretStr(""),
    )
    with pytest.raises(RuntimeError, match="META_AUTH_MODE=live"):
        AppContainer.from_settings(live_meta_bare, session_factory=None, enable_db=False)

    live_google_bare = _settings(
        google_auth_mode="live",
        google_client_id="",
        google_client_secret=SecretStr(""),
    )
    with pytest.raises(RuntimeError, match="GOOGLE_AUTH_MODE=live"):
        AppContainer.from_settings(live_google_bare, session_factory=None, enable_db=False)

    bad_connections_mode = _settings(meta_connections_mode="sometimes")
    with pytest.raises(RuntimeError, match="META_CONNECTIONS_MODE"):
        AppContainer.from_settings(bad_connections_mode, session_factory=None, enable_db=False)

    # Fully credentialed live config constructs (stores injected, no network touched).
    live_ok = _settings(
        app_env="staging",
        redis_url=SecretStr("redis://localhost:6379/0"),
        secret_store_backend="memory",
        meta_auth_mode="live",
        meta_app_id="meta-app-id",
        meta_app_secret=SecretStr("meta-app-secret-value"),
        google_auth_mode="live",
        google_client_id="google-client-id",
        google_client_secret=SecretStr("google-client-secret"),
        meta_connections_mode="live",
    )
    container = AppContainer.from_settings(
        live_ok,
        session_factory=None,
        session_store=InMemorySessionStore(),
        secret_store=EncryptedSecretStore(master_key_material="unit-key"),
        enable_db=False,
    )
    assert container.settings.meta_connections_mode == "live"


def test_pages_show_list_requested_for_live_discovery() -> None:
    assert "pages_show_list" in default_scopes_for_connection()
    assert "pages_show_list" in default_scopes_for_connection(is_agency=True, include_ads=True)
    matrix_perms = {entry.permission for entry in PERMISSION_MATRIX}
    assert "pages_show_list" in matrix_perms


def test_render_blueprint_and_entrypoint_cover_prod_prereqs() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    render_yaml = (repo_root / "render.yaml").read_text(encoding="utf-8")
    for key in (
        "DATABASE_URL",
        "APP_DATABASE_URL",
        "REDIS_URL",
        "SECRET_STORE_BACKEND",
        "META_SECRET_ENCRYPTION_KEY",
        "META_APP_ID",
        "META_APP_SECRET",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
    ):
        assert key in render_yaml
    assert "value: db" in render_yaml
    assert 'value: ""' not in render_yaml

    entrypoint = (repo_root / "infrastructure" / "docker" / "api-entrypoint.sh").read_text(
        encoding="utf-8"
    )
    assert "alembic upgrade head" in entrypoint


def test_vault_envelopes_are_portable_across_store_instances() -> None:
    # Same key material must decrypt envelopes produced by another instance
    # (this is what lets every API replica read the shared DB vault).
    ws = uuid4()
    writer = EncryptedSecretStore(master_key_material="shared-key-material")
    ref = writer.put_secret(workspace_id=ws, namespace="meta", secret_value="EAA_SHARED")

    reader = EncryptedSecretStore(master_key_material="shared-key-material")
    reader._vault[ref] = writer._vault[ref]
    assert reader.get_secret(secret_ref=ref) == "EAA_SHARED"
