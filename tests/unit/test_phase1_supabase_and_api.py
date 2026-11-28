"""Phase 1 unit tests: HS256 & JWKS Supabase JWT, key rotation, FastAPI API, RBAC, migrations."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import jwt
import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from pydantic import SecretStr

from apps.api.main import create_app
from packages.auth import (
    InMemorySessionStore,
    SupabaseJwksCache,
    encode_hs256_token,
    verify_supabase_jwt,
    verify_supabase_jwt_from_settings,
)
from packages.shared.config import BaseAppSettings
from packages.shared.context import TenantContext
from packages.shared.enums import ClientStatus, MemberStatus, RoleName, WorkspaceType
from packages.shared.errors import (
    AuthenticationError,
    PermissionDeniedError,
    WorkspaceAccessDeniedError,
)
from packages.shared.schemas import AuditLogOut, ClientOut, MemberOut, WorkspaceOut
from packages.tenancy.db import is_supabase_transaction_pooler_url

pytestmark = pytest.mark.unit


def _generate_ec_keypair(kid: str) -> tuple[ec.EllipticCurvePrivateKey, dict[str, Any]]:
    priv = ec.generate_private_key(ec.SECP256R1())
    jwk_dict = json.loads(ECAlgorithm.to_jwk(priv.public_key()))
    jwk_dict["kid"] = kid
    jwk_dict["use"] = "sig"
    jwk_dict["alg"] = "ES256"
    return priv, jwk_dict


def _generate_rsa_keypair(kid: str) -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk_dict = json.loads(RSAAlgorithm.to_jwk(priv.public_key()))
    jwk_dict["kid"] = kid
    jwk_dict["use"] = "sig"
    jwk_dict["alg"] = "RS256"
    return priv, jwk_dict


def test_supabase_hs256_jwt_valid_and_rejection_cases() -> None:
    secret = "super-secret-supabase-jwt-signing-key-2026"
    sub = uuid4()
    now = int(time.time())
    valid_token = jwt.encode(
        {
            "sub": str(sub),
            "email": "Founder@Agency.io",
            "aud": "authenticated",
            "role": "authenticated",
            "iss": "https://demo.supabase.co/auth/v1",
            "iat": now,
            "exp": now + 300,
            "user_metadata": {
                "full_name": "Agency Founder",
                "avatar_url": "https://cdn.example.com/a.png",
            },
        },
        secret,
        algorithm="HS256",
    )

    claims = verify_supabase_jwt(
        valid_token,
        jwt_secret=secret,
        expected_issuer="https://demo.supabase.co/auth/v1",
    )
    assert claims.subject == sub
    assert claims.email == "founder@agency.io"
    assert claims.full_name == "Agency Founder"
    assert claims.avatar_url == "https://cdn.example.com/a.png"
    assert claims.algorithm == "HS256"

    settings = BaseAppSettings(
        _env_file=None,
        next_public_supabase_url="https://demo.supabase.co",
        supabase_jwt_secret=SecretStr(secret),
    )
    assert verify_supabase_jwt_from_settings(valid_token, settings).subject == sub

    # Invalid signature
    with pytest.raises(AuthenticationError, match="signature"):
        verify_supabase_jwt(valid_token, jwt_secret="wrong-secret")

    # Expired token
    expired = jwt.encode(
        {"sub": str(sub), "aud": "authenticated", "exp": now - 60},
        secret,
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationError, match="expired"):
        verify_supabase_jwt(expired, jwt_secret=secret)

    # Not yet valid (nbf in future)
    immature = jwt.encode(
        {"sub": str(sub), "aud": "authenticated", "nbf": now + 120, "exp": now + 300},
        secret,
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationError, match="not yet valid"):
        verify_supabase_jwt(immature, jwt_secret=secret)

    # Incorrect issuer
    with pytest.raises(AuthenticationError, match="issuer"):
        verify_supabase_jwt(
            valid_token,
            jwt_secret=secret,
            expected_issuer="https://other.supabase.co/auth/v1",
        )

    # Incorrect audience
    bad_aud = jwt.encode(
        {"sub": str(sub), "aud": "anon", "exp": now + 300},
        secret,
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationError, match="audience"):
        verify_supabase_jwt(bad_aud, jwt_secret=secret)

    # Malformed non-UUID subject
    bad_sub = jwt.encode(
        {"sub": "not-a-uuid", "aud": "authenticated", "exp": now + 300},
        secret,
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationError, match="UUID"):
        verify_supabase_jwt(bad_sub, jwt_secret=secret)

    # alg=none token rejected
    unsigned = jwt.encode(
        {"sub": str(sub), "aud": "authenticated", "exp": now + 300},
        key="",
        algorithm="none",
    )
    with pytest.raises(AuthenticationError, match="Unsupported"):
        verify_supabase_jwt(unsigned, jwt_secret=secret)

    # Unconfigured secret fails closed
    with pytest.raises(AuthenticationError, match="not configured"):
        verify_supabase_jwt(valid_token, jwt_secret="")


def test_supabase_asymmetric_jwks_verification_and_key_rotation() -> None:
    ec_priv_1, ec_jwk_1 = _generate_ec_keypair("kid-ec-1")
    ec_priv_2, ec_jwk_2 = _generate_ec_keypair("kid-ec-2")
    rsa_priv, rsa_jwk = _generate_rsa_keypair("kid-rsa-1")

    active_keys: list[dict[str, Any]] = [ec_jwk_1, rsa_jwk]
    fetch_count = 0

    def mock_fetcher(_url: str) -> dict[str, Any]:
        nonlocal fetch_count
        fetch_count += 1
        return {"keys": list(active_keys)}

    cache = SupabaseJwksCache(
        "https://demo.supabase.co/auth/v1/.well-known/jwks.json",
        ttl_seconds=300,
        refresh_cooldown_seconds=0.0,
        fetcher=mock_fetcher,
    )

    sub = uuid4()
    now = int(time.time())
    iss = "https://demo.supabase.co/auth/v1"

    # 1. Valid ES256 token
    es256_token = jwt.encode(
        {
            "sub": str(sub),
            "email": "ec@agency.io",
            "aud": "authenticated",
            "iss": iss,
            "exp": now + 300,
        },
        ec_priv_1,
        algorithm="ES256",
        headers={"kid": "kid-ec-1"},
    )
    claims = verify_supabase_jwt(es256_token, jwks_cache=cache, expected_issuer=iss)
    assert claims.subject == sub
    assert claims.algorithm == "ES256"
    assert fetch_count == 1

    # 2. Valid RS256 token served from cache (no extra fetch)
    rs256_token = jwt.encode(
        {"sub": str(sub), "aud": "authenticated", "iss": iss, "exp": now + 300},
        rsa_priv,
        algorithm="RS256",
        headers={"kid": "kid-rsa-1"},
    )
    rsa_claims = verify_supabase_jwt(rs256_token, jwks_cache=cache, expected_issuer=iss)
    assert rsa_claims.algorithm == "RS256"
    assert fetch_count == 1

    # 3. Invalid signature (signed by ec_priv_2 but claiming kid-ec-1)
    forged = jwt.encode(
        {"sub": str(sub), "aud": "authenticated", "iss": iss, "exp": now + 300},
        ec_priv_2,
        algorithm="ES256",
        headers={"kid": "kid-ec-1"},
    )
    with pytest.raises(AuthenticationError, match="signature"):
        verify_supabase_jwt(forged, jwks_cache=cache, expected_issuer=iss)

    # 4. Unknown kid rejected
    unknown_kid = jwt.encode(
        {"sub": str(sub), "aud": "authenticated", "iss": iss, "exp": now + 300},
        ec_priv_2,
        algorithm="ES256",
        headers={"kid": "kid-does-not-exist"},
    )
    with pytest.raises(AuthenticationError, match="Unknown Supabase signing key"):
        verify_supabase_jwt(unknown_kid, jwks_cache=cache, expected_issuer=iss)

    # 5. Key rotation: rotate JWKS from kid-ec-1 to kid-ec-2
    active_keys = [ec_jwk_2, rsa_jwk]
    rotated_token = jwt.encode(
        {"sub": str(sub), "aud": "authenticated", "iss": iss, "exp": now + 300},
        ec_priv_2,
        algorithm="ES256",
        headers={"kid": "kid-ec-2"},
    )
    rotated_claims = verify_supabase_jwt(rotated_token, jwks_cache=cache, expected_issuer=iss)
    assert rotated_claims.subject == sub
    assert rotated_claims.algorithm == "ES256"

    # Old rotated-out key kid-ec-1 is now rejected
    with pytest.raises(AuthenticationError, match="Unknown Supabase signing key"):
        verify_supabase_jwt(es256_token, jwks_cache=cache, expected_issuer=iss)


def test_supabase_pooler_detection_and_secret_scrubbing() -> None:
    assert is_supabase_transaction_pooler_url(
        "postgresql+psycopg://metuse_app:pw@aws-0-us-east-1.pooler.supabase.com:6543/postgres"
    )
    assert is_supabase_transaction_pooler_url(
        "postgresql+psycopg://metuse_app:pw@db.example.com:6543/postgres"
    )
    assert not is_supabase_transaction_pooler_url(
        "postgresql+psycopg://metuse_app:pw@localhost:5433/metuse"
    )

    service_key = "sb_secret_service_role_do_not_expose_12345"
    cf_token = "cf_secret_token_do_not_expose_67890"
    settings = BaseAppSettings(
        _env_file=None,
        next_public_supabase_url="https://demo.supabase.co",
        supabase_service_role_key=SecretStr(service_key),
        supabase_jwt_secret=SecretStr("jwt-secret-xyz"),
        cloudflare_account_id="acct-123",
        cloudflare_api_token=SecretStr(cf_token),
    )
    summary_text = json.dumps(settings.safe_summary())
    assert service_key not in summary_text
    assert cf_token not in summary_text
    assert "jwt-secret-xyz" not in summary_text
    assert settings.safe_summary()["supabase_service_role_configured"] is True


def test_alembic_migration_graph_has_single_linear_head() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    cfg = AlembicConfig(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "database" / "migrations"))
    script = ScriptDirectory.from_config(cfg)

    heads = script.get_heads()
    assert heads == ["0008_phase6_publishing_and_insights"]
    revisions = [rev.revision for rev in script.walk_revisions()]
    assert revisions == [
        "0008_phase6_publishing_and_insights",
        "0007_phase5_meta_connections",
        "0006_phase4_cloud_editing_and_approvals",
        "0005_phase3_video_generation_gateway",
        "0004_phase2_ai_workers_and_usage",
        "0003_supabase_compat",
        "0002_harden_functions_search_path",
        "0001_initial_schema",
    ]


def test_fastapi_endpoints_security_cors_rbac_and_supabase_jwks_auth() -> None:
    ec_priv, ec_jwk = _generate_ec_keypair("kid-api-1")
    jwks_cache = SupabaseJwksCache(
        "https://demo.supabase.co/auth/v1/.well-known/jwks.json",
        fetcher=lambda _u: {"keys": [ec_jwk]},
    )
    settings = BaseAppSettings(
        _env_file=None,
        app_env="test",
        app_jwt_secret=SecretStr("unit-test-jwt-secret-123456"),
        app_cors_origins="http://localhost:3000",
        next_public_supabase_url="https://demo.supabase.co",
    )
    store = InMemorySessionStore()
    fake_session_factory = MagicMock()
    app = create_app(
        settings=settings,
        session_factory=fake_session_factory,
        session_store=store,
        supabase_jwks_cache=jwks_cache,
        enable_db=False,
    )
    client = TestClient(app)

    # 1. Health, security headers, request ID, and CORS
    health = client.get("/healthz", headers={"Origin": "http://localhost:3000"})
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "metuse-api"}
    assert health.headers["X-Content-Type-Options"] == "nosniff"
    assert health.headers["X-Frame-Options"] == "DENY"
    assert health.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert health.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert UUID(health.headers["X-Request-Id"])

    # Disallowed CORS origin is not echoed
    bad_cors = client.get("/healthz", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in bad_cors.headers

    # 2. Readiness probe with in-memory session fallback
    ready_app = create_app(
        settings=settings, session_factory=None, session_store=store, enable_db=False
    )
    ready_resp = TestClient(ready_app).get("/readyz")
    assert ready_resp.status_code == 200
    assert ready_resp.json()["checks"] == {"database": "unconfigured", "session_store": "ok"}

    # 3. Unauthenticated request returns canonical scrubbed 401 envelope
    unauth = client.get("/api/v1/workspaces")
    assert unauth.status_code == 401
    assert unauth.json()["error"]["code"] == "UNAUTHORIZED"
    assert unauth.json()["error"]["request_id"]

    # 4. Logout revokes Metuse session jti in InMemorySessionStore
    user_id = uuid4()
    ws_id = uuid4()
    client_id = uuid4()
    metuse_token = encode_hs256_token(
        {"sub": str(user_id)},
        secret="unit-test-jwt-secret-123456",
        ttl_seconds=300,
    )
    assert (
        client.post(
            "/api/v1/auth/logout", headers={"Authorization": f"Bearer {metuse_token}"}
        ).status_code
        == 204
    )
    assert (
        client.get(
            "/api/v1/workspaces", headers={"Authorization": f"Bearer {metuse_token}"}
        ).status_code
        == 401
    )

    # 5. Authenticated workspace, member, client, and audit endpoints via Supabase ES256 JWT
    now = int(time.time())
    sb_token = jwt.encode(
        {
            "sub": str(user_id),
            "email": "owner@metuse.ai",
            "aud": "authenticated",
            "iss": "https://demo.supabase.co/auth/v1",
            "exp": now + 300,
        },
        ec_priv,
        algorithm="ES256",
        headers={"kid": "kid-api-1"},
    )
    auth_headers = {"Authorization": f"Bearer {sb_token}"}
    ts = datetime.now(UTC)
    ws_out = WorkspaceOut(
        id=ws_id,
        name="Metuse Studio",
        slug="metuse-studio",
        workspace_type=WorkspaceType.AGENCY,
        max_members=5,
        created_by_user_id=user_id,
        created_at=ts,
        updated_at=ts,
    )
    client_out = ClientOut(
        id=client_id,
        workspace_id=ws_id,
        name="Acme Brand",
        slug="acme-brand",
        status=ClientStatus.ACTIVE,
        metadata={},
        created_at=ts,
        updated_at=ts,
    )
    member_out = MemberOut(
        id=uuid4(),
        workspace_id=ws_id,
        user_id=uuid4(),
        email="member@metuse.ai",
        full_name="Member One",
        role_name=RoleName.AGENCY_MEMBER,
        status=MemberStatus.ACTIVE,
        created_at=ts,
        updated_at=ts,
    )
    audit_out = AuditLogOut(
        id=uuid4(),
        workspace_id=ws_id,
        client_id=client_id,
        actor_user_id=user_id,
        action="client.created",
        target_type="client",
        target_id=str(client_id),
        request_id=uuid4(),
        metadata={"status": "active"},
        created_at=ts,
    )

    @contextmanager
    def _mock_tenant_tx(
        _sf: Any,
        *,
        user_id: UUID,
        workspace_id: UUID,
        request_id: UUID | None = None,
    ) -> Iterator[tuple[Any, TenantContext]]:
        ctx = TenantContext(
            workspace_id=workspace_id,
            user_id=user_id,
            role_name=RoleName.AGENCY_OWNER,
            request_id=request_id or uuid4(),
        )
        yield MagicMock(), ctx

    with (
        patch("apps.api.deps._resolve_or_provision_supabase_user", return_value=user_id),
        patch("apps.api.routes.workspaces.create_workspace", return_value=ws_out),
        patch("apps.api.routes.workspaces.tenant_transaction", side_effect=_mock_tenant_tx),
        patch("apps.api.routes.workspaces.add_workspace_member", return_value=member_out),
        patch("apps.api.routes.clients.tenant_transaction", side_effect=_mock_tenant_tx),
        patch("apps.api.routes.clients.create_client", return_value=client_out),
        patch("apps.api.routes.audit.tenant_transaction", side_effect=_mock_tenant_tx),
        patch("apps.api.routes.audit.list_audit_logs", return_value=([audit_out], 1)),
    ):
        ws_resp = client.post(
            "/api/v1/workspaces",
            json={"name": "Metuse Studio", "workspace_type": "agency"},
            headers=auth_headers,
        )
        assert ws_resp.status_code == 201
        assert ws_resp.json()["id"] == str(ws_id)

        mem_resp = client.post(
            f"/api/v1/workspaces/{ws_id}/members",
            json={"email": "member@metuse.ai", "role_name": "agency_member"},
            headers=auth_headers,
        )
        assert mem_resp.status_code == 201
        assert mem_resp.json()["role_name"] == "agency_member"

        cli_resp = client.post(
            f"/api/v1/workspaces/{ws_id}/clients",
            json={"name": "Acme Brand"},
            headers=auth_headers,
        )
        assert cli_resp.status_code == 201
        assert cli_resp.json()["id"] == str(client_id)

        aud_resp = client.get(f"/api/v1/workspaces/{ws_id}/audit-logs", headers=auth_headers)
        assert aud_resp.status_code == 200
        assert aud_resp.json()["total"] == 1
        assert aud_resp.json()["items"][0]["action"] == "client.created"

    # 6. Authorization (RBAC 403) & cross-tenant workspace isolation (403)
    with (
        patch("apps.api.deps._resolve_or_provision_supabase_user", return_value=user_id),
        patch(
            "apps.api.routes.workspaces.tenant_transaction",
            side_effect=WorkspaceAccessDeniedError("Access to the requested workspace is denied"),
        ),
    ):
        denied_ws = client.get(f"/api/v1/workspaces/{uuid4()}", headers=auth_headers)
        assert denied_ws.status_code == 403
        assert denied_ws.json()["error"]["code"] == "WORKSPACE_ACCESS_DENIED"

    with (
        patch("apps.api.deps._resolve_or_provision_supabase_user", return_value=user_id),
        patch("apps.api.routes.workspaces.tenant_transaction", side_effect=_mock_tenant_tx),
        patch(
            "apps.api.routes.workspaces.add_workspace_member",
            side_effect=PermissionDeniedError("Role 'agency_member' lacks required permission"),
        ),
    ):
        denied_rbac = client.post(
            f"/api/v1/workspaces/{ws_id}/members",
            json={"email": "x@metuse.ai", "role_name": "agency_owner"},
            headers=auth_headers,
        )
        assert denied_rbac.status_code == 403
        assert denied_rbac.json()["error"]["code"] == "PERMISSION_DENIED"


def test_supabase_modern_env_keys_and_explicit_jwks_url() -> None:
    """Verify SUPABASE_URL, SUPABASE_SECRET_KEY, and explicit SUPABASE_JWKS_URL precedence."""
    from apps.api.deps import AppContainer

    es_priv, es_jwk = _generate_ec_keypair("sb-modern-kid")
    user_id = uuid4()
    now = int(time.time())

    settings = BaseAppSettings(
        _env_file=None,
        supabase_url="https://modern.supabase.co",
        supabase_publishable_key="sb_publishable_dummy_key",
        supabase_secret_key=SecretStr("sb_secret_super_sensitive_value"),
        supabase_jwks_url="https://modern.supabase.co/auth/v1/.well-known/jwks.json",
    )

    # Secret scrubbing: never leak supabase_secret_key in safe_summary()
    summary = settings.safe_summary()
    assert summary["supabase_url_configured"] is True
    assert summary["supabase_jwks_url_configured"] is True
    assert summary["supabase_secret_key_configured"] is True
    assert "sb_secret_super_sensitive_value" not in str(summary)

    # AppContainer resolves explicit JWKS URL cleanly
    container = AppContainer.from_settings(
        settings,
        session_factory=None,
        session_store=InMemorySessionStore(),
        enable_db=False,
    )
    assert container.supabase_jwks_cache is not None
    assert (
        container.supabase_jwks_cache.jwks_url
        == "https://modern.supabase.co/auth/v1/.well-known/jwks.json"
    )

    # Token verification succeeds against explicit JWKS URL with issuer derived from SUPABASE_URL
    cache = SupabaseJwksCache(
        "https://modern.supabase.co/auth/v1/.well-known/jwks.json",
        fetcher=lambda _url: {"keys": [es_jwk]},
    )
    token = jwt.encode(
        {
            "sub": str(user_id),
            "email": "modern@metuse.ai",
            "aud": "authenticated",
            "iss": "https://modern.supabase.co/auth/v1",
            "exp": now + 300,
        },
        es_priv,
        algorithm="ES256",
        headers={"kid": "sb-modern-kid"},
    )
    claims = verify_supabase_jwt_from_settings(token, settings, jwks_cache=cache)
    assert claims.subject == user_id
    assert claims.algorithm == "ES256"


def test_sentry_observability_noop_and_secret_scrubbing() -> None:
    """Verify Sentry init skips empty/non-HTTP DSNs and scrubs sensitive keys before send."""
    from apps.api.observability import _scrub_sentry_event, init_sentry

    assert init_sentry(BaseAppSettings(_env_file=None, sentry_dsn="")) is False
    assert init_sentry(BaseAppSettings(_env_file=None, sentry_dsn="disabled")) is False

    raw_event: dict[str, Any] = {
        "message": "Unhandled error",
        "extra": {
            "supabase_service_role_key": "sb_secret_live_value_123456",
            "authorization": "Bearer top-secret-token",
            "safe_field": "visible",
        },
    }
    scrubbed = _scrub_sentry_event(raw_event, {})  # type: ignore[arg-type]
    assert scrubbed is not None
    assert "sb_secret_live_value_123456" not in str(scrubbed)
    assert "top-secret-token" not in str(scrubbed)
    assert scrubbed["extra"]["safe_field"] == "visible"
