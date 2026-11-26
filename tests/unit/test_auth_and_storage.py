"""Unit tests for Step 1 auth hardening (sessions, meta_oauth) and Batch A storage security."""

from __future__ import annotations

import inspect
from uuid import uuid4

import pytest
from pydantic import SecretStr

from packages.auth import (
    InMemorySessionStore,
    build_session_cookie_kwargs,
    decode_hs256_token,
    encode_hs256_token,
    exchange_meta_code,
    hash_password,
    resolve_meta_auth_mode,
    start_meta_oauth,
    verify_password,
)
from packages.auth import meta_oauth as meta_oauth_module
from packages.shared.config import BaseAppSettings
from packages.shared.errors import BadRequestError, ExternalServiceError, ValidationDomainError
from packages.storage import (
    InMemoryStorageBackend,
    S3StorageBackend,
    build_brand_asset_key,
    build_tenant_object_key,
    detect_mime_from_magic_bytes,
    sanitize_filename,
    validate_magic_bytes,
    validate_storage_key,
    validate_upload_metadata,
)

pytestmark = pytest.mark.unit


def test_password_kdf_roundtrip() -> None:
    hashed = hash_password("StrongPass!2026")
    assert hashed.startswith("scrypt$")
    assert verify_password("StrongPass!2026", hashed) is True
    assert verify_password("WrongPass!2026", hashed) is False


def test_session_store_revocation_and_cookie_security() -> None:
    store = InMemorySessionStore()
    token = encode_hs256_token({"sub": str(uuid4())}, secret="unit-secret", ttl_seconds=300)
    claims = decode_hs256_token(token, secret="unit-secret")
    jti = str(claims["jti"])

    assert store.is_revoked(jti) is False
    store.revoke_jti(jti, ttl_seconds=300)
    assert store.is_revoked(jti) is True

    dev_settings = BaseAppSettings(_env_file=None, app_env="development")
    dev_cookie = build_session_cookie_kwargs(dev_settings)
    assert dev_cookie["httponly"] is True
    assert dev_cookie["secure"] is True
    assert dev_cookie["samesite"] == "lax"

    prod_settings = BaseAppSettings(
        _env_file=None,
        app_env="production",
        session_cookie_secure=False,
    )
    prod_cookie = build_session_cookie_kwargs(prod_settings)
    assert prod_cookie["httponly"] is True
    assert prod_cookie["secure"] is True
    assert prod_cookie["samesite"] == "lax"


@pytest.mark.parametrize("bad_env", ["production", "prod", "staging", "stage", "local", "dev", ""])
def test_meta_oauth_blocks_mock_mode_outside_development_and_test(bad_env: str) -> None:
    settings = BaseAppSettings(
        _env_file=None,
        app_env=bad_env,
        meta_auth_mode="mock_dev",
        app_jwt_secret=SecretStr("test-secret-value-12345"),
    )
    with pytest.raises(ExternalServiceError, match="development/test"):
        resolve_meta_auth_mode(settings)


@pytest.mark.parametrize("allowed_env", ["development", "test"])
def test_meta_oauth_allows_mock_mode_in_dev_and_test_with_single_use_nonce(
    allowed_env: str,
) -> None:
    settings = BaseAppSettings(
        _env_file=None,
        app_env=allowed_env,
        meta_auth_mode="mock_dev",
        meta_redirect_uri="http://localhost:3000/auth/meta/callback",
    )
    store = InMemorySessionStore()
    start = start_meta_oauth(settings, session_store=store)
    assert start.mode == "mock_dev"
    assert start.authorization_url.startswith("http://localhost:3000/auth/meta/callback?")

    # Unlisted redirect URI must be rejected
    with pytest.raises(BadRequestError, match="allowlist"):
        start_meta_oauth(
            settings,
            redirect_uri="https://evil.example.com/steal",
            session_store=store,
        )

    # First callback succeeds and consumes nonce
    identity = exchange_meta_code(
        settings,
        code="mock-code-123",
        state=start.state,
        mock_email="creator@example.com",
        session_store=store,
    )
    assert identity.email == "creator@example.com"
    assert identity.token_secret_ref.startswith("env://meta-oauth/")

    # Replay of the same state fails immediately
    with pytest.raises(BadRequestError, match="already consumed"):
        exchange_meta_code(
            settings,
            code="mock-code-123",
            state=start.state,
            session_store=store,
        )


def test_meta_oauth_has_zero_phase5_coupling() -> None:
    source = inspect.getsource(meta_oauth_module)
    assert "meta_integrations" not in source
    assert "instagram_content_publish" not in source
    assert "ads_management" not in source


def test_storage_key_builder_and_path_traversal_guards() -> None:
    ws_a, ws_b = uuid4(), uuid4()
    client_a, client_b = uuid4(), uuid4()
    asset_id = uuid4()

    key = build_brand_asset_key(
        workspace_id=ws_a,
        client_id=client_a,
        asset_id=asset_id,
        filename="Primary Logo (2026).png",
    )
    assert key == f"workspaces/{ws_a}/clients/{client_a}/brand/{asset_id}/Primary_Logo__2026_.png"
    assert validate_storage_key(key, expected_workspace_id=ws_a, expected_client_id=client_a) == key

    # Cross-tenant & cross-client key validation must fail
    with pytest.raises(BadRequestError):
        validate_storage_key(key, expected_workspace_id=ws_b, expected_client_id=client_a)
    with pytest.raises(BadRequestError):
        validate_storage_key(key, expected_workspace_id=ws_a, expected_client_id=client_b)

    # Traversal and control characters must fail
    for bad_name in ("../secret.png", "dir/file.png", "a\\b.png", "%2e%2e/x.png", "bad\x00.png"):
        with pytest.raises(ValidationDomainError):
            sanitize_filename(bad_name)

    for bad_key in (
        f"/workspaces/{ws_a}/clients/{client_a}/brand/{asset_id}/logo.png",
        f"workspaces/{ws_a}/clients/{client_a}/brand/../../other/logo.png",
        f"workspaces/{ws_a}/clients/{client_a}/brand/%2e%2e/logo.png",
        f"workspaces/{ws_a}/clients/{client_a}/brand//{asset_id}/logo.png",
    ):
        with pytest.raises(BadRequestError):
            validate_storage_key(bad_key, expected_workspace_id=ws_a, expected_client_id=client_a)

    # Project-level Section 7.4 layout works cleanly
    project_id = uuid4()
    proj_key = build_tenant_object_key(
        workspace_id=ws_a,
        client_id=client_a,
        category="previews",
        asset_id=asset_id,
        filename="preview.mp4",
        project_id=project_id,
    )
    expected_proj_key = (
        f"workspaces/{ws_a}/clients/{client_a}/projects/"
        f"{project_id}/previews/{asset_id}/preview.mp4"
    )
    assert proj_key == expected_proj_key


def test_storage_magic_bytes_and_svg_xss_rejection() -> None:
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 32
    webp_bytes = b"RIFF\x24\x00\x00\x00WEBP" + b"\x00" * 20
    pdf_bytes = b"%PDF-1.7\n%..."

    assert detect_mime_from_magic_bytes(png_bytes) == "image/png"
    assert detect_mime_from_magic_bytes(jpeg_bytes) == "image/jpeg"
    assert detect_mime_from_magic_bytes(webp_bytes) == "image/webp"
    assert detect_mime_from_magic_bytes(pdf_bytes) == "application/pdf"

    assert validate_magic_bytes(header=png_bytes, declared_mime="image/png") == "image/png"

    # Spoofed MIME (HTML/SVG/EXE pretending to be PNG) must fail
    for malicious in (
        b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
        b"<!DOCTYPE html><html><script>alert(1)</script></html>",
        b"MZ\x90\x00\x03\x00\x00\x00",
        jpeg_bytes,
    ):
        with pytest.raises(ValidationDomainError):
            validate_magic_bytes(header=malicious, declared_mime="image/png")

    # Pre-upload metadata rejects SVG, zero size, oversized
    with pytest.raises(ValidationDomainError):
        validate_upload_metadata(filename="logo.svg", declared_mime="image/svg+xml", size_bytes=100)
    with pytest.raises(ValidationDomainError):
        validate_upload_metadata(filename="logo.svg", declared_mime="image/png", size_bytes=100)
    with pytest.raises(ValidationDomainError):
        validate_upload_metadata(filename="logo.png", declared_mime="image/png", size_bytes=0)


def test_s3_dual_endpoint_presign_uses_public_endpoint() -> None:
    settings = BaseAppSettings(
        _env_file=None,
        s3_internal_endpoint_url="http://minio:9000",
        s3_public_endpoint_url="http://localhost:9000",
        s3_access_key=SecretStr("minioadmin"),
        s3_secret_key=SecretStr("minioadmin"),
        s3_bucket="metuse-dev",
    )
    backend = S3StorageBackend.from_settings(settings)
    assert backend.internal_endpoint_url == "http://minio:9000"
    assert backend.public_endpoint_url == "http://localhost:9000"

    ws_id, client_id, asset_id = uuid4(), uuid4(), uuid4()
    key = build_brand_asset_key(
        workspace_id=ws_id,
        client_id=client_id,
        asset_id=asset_id,
        filename="logo.png",
    )
    put_res = backend.create_presigned_put_url(
        key=key,
        content_type="image/png",
        content_length=128,
        expires_in=300,
    )
    get_res = backend.create_presigned_get_url(
        key=key,
        expires_in=300,
        download_filename="logo.png",
    )
    assert put_res.url.startswith("http://localhost:9000/metuse-dev/")
    assert "minio:9000" not in put_res.url
    assert get_res.url.startswith("http://localhost:9000/metuse-dev/")
    assert "minio:9000" not in get_res.url

    mem = InMemoryStorageBackend()
    mem.put_object_bytes(key=key, data=b"\x89PNG\r\n\x1a\n1234", content_type="image/png")
    assert mem.head_object(key=key).size_bytes == 12
    assert mem.read_object_prefix(key=key, num_bytes=8) == b"\x89PNG\r\n\x1a\n"
