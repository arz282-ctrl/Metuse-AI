"""Unit tests for packages.shared contracts, scrubbing, config, and seed env guards."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import SecretStr

from database.seeds.seed_dev import assert_dev_environment
from packages.shared import (
    REDACTED,
    AuditAction,
    AuditEventPayload,
    AuditLogOut,
    BaseAppSettings,
    BrandAssetType,
    ClientStatus,
    DomainEvent,
    ErrorCode,
    ErrorEnvelope,
    IdentityProvider,
    MemberStatus,
    MetuseError,
    RoleName,
    TenantContext,
    UserOut,
    WorkspaceMemberLimitExceededError,
    WorkspaceType,
    is_secret_ref_key,
    is_sensitive_key,
    normalize_database_url,
    scrub_secrets,
    scrub_text,
)

pytestmark = pytest.mark.unit


def test_role_names_and_core_enums_match_m1_spec() -> None:
    assert {r.value for r in RoleName} == {
        "agency_owner",
        "agency_admin",
        "agency_member",
        "creator_owner",
        "client_reviewer",
        "client_viewer",
    }
    assert {w.value for w in WorkspaceType} == {"agency", "creator"}
    assert {m.value for m in MemberStatus} == {"active", "invited", "suspended", "removed"}
    assert {c.value for c in ClientStatus} == {"active", "paused", "archived"}
    assert {p.value for p in IdentityProvider} == {"email", "meta"}
    assert BrandAssetType.LOGO.value == "logo"
    assert AuditAction.WORKSPACE_CREATED.value == "workspace.created"
    assert ErrorCode.WORKSPACE_MEMBER_LIMIT_EXCEEDED.value == "WORKSPACE_MEMBER_LIMIT_EXCEEDED"


def test_error_envelope_shape_and_secret_scrubbing() -> None:
    req_id = uuid4()
    err = WorkspaceMemberLimitExceededError(
        details={
            "workspace_id": str(uuid4()),
            "password": "super-secret-value",
            "token_secret_ref": "env://META_TOKEN_REF_1",
        }
    )
    assert err.status_code == 409
    assert err.code == ErrorCode.WORKSPACE_MEMBER_LIMIT_EXCEEDED

    envelope = err.to_envelope(request_id=req_id)
    assert isinstance(envelope, ErrorEnvelope)
    dumped = envelope.model_dump()
    assert set(dumped.keys()) == {"error"}
    assert set(dumped["error"].keys()) == {"code", "message", "details", "request_id"}
    assert dumped["error"]["code"] == "WORKSPACE_MEMBER_LIMIT_EXCEEDED"
    assert dumped["error"]["request_id"] == str(req_id)
    assert dumped["error"]["details"]["password"] == REDACTED
    assert dumped["error"]["details"]["token_secret_ref"] == "env://META_TOKEN_REF_1"

    base_err = MetuseError(
        "Failed connecting to postgresql://metuse:s3cr3tPass@localhost:5433/metuse "
        "with Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload12345.sig12345678",
        code=ErrorCode.SERVICE_UNAVAILABLE,
        status_code=503,
    )
    msg = base_err.to_envelope().error.message
    assert "s3cr3tPass" not in msg
    assert "eyJhbGci" not in msg
    assert REDACTED in msg


def test_secret_scrubber_preserves_secret_refs_and_handles_cycles() -> None:
    assert is_secret_ref_key("token_secret_ref") is True
    assert is_secret_ref_key("secret_ref") is True
    assert is_sensitive_key("token_secret_ref") is False
    assert is_sensitive_key("app_jwt_secret") is True
    assert is_sensitive_key("password_hash") is True

    cyclic: dict[str, object] = {
        "token_secret_ref": "vault://ws/1/meta",
        "api_key": "sk-1234567890abcdef1234",
    }
    cyclic["self"] = cyclic

    payload = {
        "nested": [
            {
                "access_token": "EAABsbCS1234567890abcdef",
                "authorization": "Bearer abcdefghijklmnop",
                "database_url": "postgresql://metuse:topsecret@db:5432/metuse",
                "meta_secret_ref": "env://META_DEV_REF",
            }
        ],
        "secret_obj": SecretStr("hidden-value"),
        "cyclic": cyclic,
    }
    scrubbed = scrub_secrets(payload)
    assert scrubbed["secret_obj"] == REDACTED
    nested_item = scrubbed["nested"][0]
    assert nested_item["access_token"] == REDACTED
    assert nested_item["authorization"] == REDACTED
    assert nested_item["database_url"] == f"postgresql://metuse:{REDACTED}@db:5432/metuse"
    assert nested_item["meta_secret_ref"] == "env://META_DEV_REF"
    assert scrubbed["cyclic"]["api_key"] == REDACTED
    assert scrubbed["cyclic"]["token_secret_ref"] == "vault://ws/1/meta"
    assert scrubbed["cyclic"]["self"] == "[CIRCULAR_REFERENCE]"


def test_scrub_text_redacts_tokens_and_dsns() -> None:
    raw = (
        "auth=Bearer mytoken123 meta=EAABsbCS1234567890XYZ "
        "dsn=postgres://admin:pw123@localhost:5433/metuse"
    )
    cleaned = scrub_text(raw)
    assert "mytoken123" not in cleaned
    assert "EAABsbCS1234567890XYZ" not in cleaned
    assert "pw123" not in cleaned
    assert cleaned.count(REDACTED) == 3


def test_scrub_redacts_empty_username_at_passwords_and_query_params() -> None:
    assert (
        scrub_text("redis://:secretpw@localhost:6379/0") == f"redis://:{REDACTED}@localhost:6379/0"
    )
    assert (
        scrub_text("postgresql://metuse:p@ssw0rd!#@localhost:5433/metuse")
        == f"postgresql://metuse:{REDACTED}@localhost:5433/metuse"
    )
    assert (
        scrub_text("postgresql://metuse:p%40ssw0rd!@db.internal:5433/metuse?sslmode=require")
        == f"postgresql://metuse:{REDACTED}@db.internal:5433/metuse?sslmode=require"
    )

    # Non-credential URLs (including :port + '@' in path/query/fragment & username-only)
    # must remain completely untouched and idempotent
    for safe_url in (
        "https://example.com/path?a=1",
        "http://localhost:8000/healthz",
        "https://example.com:8443/v1/items?page=2&limit=10",
        "https://example.com:8080/?email=alice@example.com",
        "https://example.com:8443/v1/@me/profile",
        "https://cdn.example.com:443/pkg@1.2.3/index.js",
        "http://localhost:3001/invite?to=bob@agency.io",
        "https://example.com:8080#email=alice@example.com",
        "https://user@example.com:8080/path",
        "postgresql://localhost:5433/metuse?email=alice@example.com",
    ):
        assert scrub_text(safe_url) == safe_url
        assert scrub_text(scrub_text(safe_url)) == safe_url

    # Credential URLs with '/', '@', '#' in password, HTTP user:secret with path '@',
    # and multiple DSNs in one line must redact only the password cleanly & idempotently
    credential_cases = [
        (
            "redis://:mysecretpw@localhost:6379/0",
            f"redis://:{REDACTED}@localhost:6379/0",
        ),
        (
            "postgresql://metuse:p@ssw0rd!#@localhost:5433/metuse",
            f"postgresql://metuse:{REDACTED}@localhost:5433/metuse",
        ),
        (
            "postgresql://metuse:p/w@localhost:5433/metuse",
            f"postgresql://metuse:{REDACTED}@localhost:5433/metuse",
        ),
        (
            "https://user:secret@example.com:8443/v1/@me/profile",
            f"https://user:{REDACTED}@example.com:8443/v1/@me/profile",
        ),
        (
            "primary=postgresql://u1:pw1@db1:5432/app replica=redis://:pw2@cache1:6379/0",
            f"primary=postgresql://u1:{REDACTED}@db1:5432/app "
            f"replica=redis://:{REDACTED}@cache1:6379/0",
        ),
    ]
    for raw_url, expected_url in credential_cases:
        cleaned = scrub_text(raw_url)
        assert cleaned == expected_url
        assert scrub_text(cleaned) == expected_url

    # DSN keys and arbitrary URLs must redact sensitive query params while keeping safe params
    scrubbed_dsn_map = scrub_secrets(
        {
            "database_url": (
                "postgresql://metuse:pw@localhost:5433/metuse?password=leaked_qp_pw&sslmode=require"
            ),
            "redis_url": "redis://:redis_pw@localhost:6379/0?token=qptok123&db=0",
            "callback_url": (
                "https://app.example.com/cb?code=ok_code&access_token=tok_leak"
                "&secret=sec_leak&api_key=key_leak"
            ),
        }
    )
    assert scrubbed_dsn_map["database_url"] == (
        f"postgresql://metuse:{REDACTED}@localhost:5433/metuse?password={REDACTED}&sslmode=require"
    )
    assert (
        scrubbed_dsn_map["redis_url"]
        == f"redis://:{REDACTED}@localhost:6379/0?token={REDACTED}&db=0"
    )
    assert scrubbed_dsn_map["callback_url"] == (
        f"https://app.example.com/cb?code=ok_code&access_token={REDACTED}"
        f"&secret={REDACTED}&api_key={REDACTED}"
    )


def test_normalize_database_url_and_settings_summary() -> None:
    assert (
        normalize_database_url("postgresql://metuse:pw@localhost:5433/metuse")
        == "postgresql+psycopg://metuse:pw@localhost:5433/metuse"
    )
    assert (
        normalize_database_url("postgres://metuse:pw@localhost:5433/metuse")
        == "postgresql+psycopg://metuse:pw@localhost:5433/metuse"
    )
    assert (
        normalize_database_url("postgresql+psycopg://metuse:pw@localhost:5433/metuse")
        == "postgresql+psycopg://metuse:pw@localhost:5433/metuse"
    )

    settings = BaseAppSettings(
        _env_file=None,
        app_env="development",
        database_url=SecretStr("postgresql://metuse:secretpw@localhost:5433/metuse"),
        app_database_url=SecretStr("postgresql://metuse_app:apppw@localhost:5433/metuse"),
        app_jwt_secret=SecretStr("jwt-secret-should-not-appear"),
    )
    assert settings.is_dev_env() is True
    assert settings.get_sqlalchemy_database_url(use_app_role=False).startswith(
        "postgresql+psycopg://metuse:secretpw@"
    )
    assert settings.get_sqlalchemy_database_url(use_app_role=True).startswith(
        "postgresql+psycopg://metuse_app:apppw@"
    )
    summary = settings.safe_summary()
    assert "secretpw" not in str(summary)
    assert "apppw" not in str(summary)
    assert "jwt-secret-should-not-appear" not in str(summary)


def test_get_sqlalchemy_database_url_app_role_fails_closed_when_unset() -> None:
    unset_settings = BaseAppSettings(
        _env_file=None,
        database_url=SecretStr("postgresql://metuse:secretpw@localhost:5433/metuse"),
        app_database_url=None,
    )
    with pytest.raises(
        ValueError, match=r"APP_DATABASE_URL must be configured when use_app_role=True"
    ):
        unset_settings.get_sqlalchemy_database_url(use_app_role=True)

    blank_settings = BaseAppSettings(
        _env_file=None,
        database_url=SecretStr("postgresql://metuse:secretpw@localhost:5433/metuse"),
        app_database_url=SecretStr("   "),
    )
    with pytest.raises(
        ValueError, match=r"APP_DATABASE_URL must be configured when use_app_role=True"
    ):
        blank_settings.get_sqlalchemy_database_url(use_app_role=True)

    # Owner/migration URL still works without app_database_url
    assert (
        unset_settings.get_sqlalchemy_database_url(use_app_role=False)
        == "postgresql+psycopg://metuse:secretpw@localhost:5433/metuse"
    )

    configured = BaseAppSettings(
        _env_file=None,
        database_url=SecretStr("postgresql://metuse:secretpw@localhost:5433/metuse"),
        app_database_url=SecretStr("postgres://metuse_app:apppw@localhost:5433/metuse"),
    )
    assert (
        configured.get_sqlalchemy_database_url(use_app_role=True)
        == "postgresql+psycopg://metuse_app:apppw@localhost:5433/metuse"
    )


def test_domain_events_and_dtos_scrub_and_exclude_secrets() -> None:
    event = DomainEvent(
        event_type="workspace.created",
        workspace_id=uuid4(),
        payload={"api_key": "secret-val", "token_secret_ref": "env://SAFE_REF"},
    )
    assert event.payload["api_key"] == REDACTED
    assert event.payload["token_secret_ref"] == "env://SAFE_REF"

    audit_payload = AuditEventPayload(
        action=AuditAction.AUTH_LOGIN,
        target_type="user",
        metadata={"password": "123", "secret_ref": "env://REF"},
    )
    assert audit_payload.metadata["password"] == REDACTED
    assert audit_payload.metadata["secret_ref"] == "env://REF"

    audit_out = AuditLogOut(
        id=uuid4(),
        action=AuditAction.AUTH_LOGIN.value,
        target_type="user",
        metadata={"refresh_token": "tok", "token_secret_ref": "env://SAFE"},
        created_at=datetime.now(UTC),
    )
    assert audit_out.metadata["refresh_token"] == REDACTED
    assert audit_out.metadata["token_secret_ref"] == "env://SAFE"

    user_out = UserOut(
        id=uuid4(),
        email="owner@example.com",
        created_at=datetime.now(UTC),
    )
    assert "password_hash" not in UserOut.model_fields
    assert user_out.email == "owner@example.com"

    ctx = TenantContext(workspace_id=uuid4(), user_id=uuid4(), role_name=RoleName.AGENCY_OWNER)
    assert ctx.role_name == RoleName.AGENCY_OWNER


@pytest.mark.parametrize("bad_env", ["production", "prod", "staging", "stage", "qa", ""])
def test_seed_guard_refuses_non_dev_environments(bad_env: str) -> None:
    with pytest.raises(RuntimeError):
        assert_dev_environment(bad_env)


@pytest.mark.parametrize("good_env", ["development", "dev", "local", "test"])
def test_seed_guard_allows_dev_environments(good_env: str) -> None:
    assert assert_dev_environment(good_env) == good_env
