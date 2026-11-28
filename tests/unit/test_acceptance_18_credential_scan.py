"""Acceptance Test #18 — Automated Credential & Secret Leak Scanner.

Verifies across settings summaries, web static bundles (when built), frontend source,
and error envelopes that zero raw secrets, private tokens, or database passwords leak.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import SecretStr

from packages.shared.config import BaseAppSettings
from packages.shared.scrubbing import scrub_secrets

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sb_secret_[A-Za-z0-9_\-]{12,}"),
    re.compile(r"postgresql(?:\+psycopg)?://[^/\s:@]+:[^/\s@]+@"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"EAAG[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{24,}"),
)


def test_acceptance_18_settings_and_scrubber_never_expose_raw_secrets() -> None:
    sentinel_values = [
        "postgresql://metuse_app:SuperSecretPgPass999@db.example.supabase.co:6543/postgres",
        "sb_secret_live_service_role_9988776655",
        "meta_app_secret_live_9988776655",
        "muse_live_key_9988776655",
        "runway_live_key_9988776655",
        "fernet_meta_enc_key_9988776655",
        "jwt_signing_secret_9988776655",
    ]
    settings = BaseAppSettings(
        _env_file=None,
        database_url=SecretStr(sentinel_values[0]),
        app_database_url=SecretStr(sentinel_values[0]),
        supabase_secret_key=SecretStr(sentinel_values[1]),
        meta_app_secret=SecretStr(sentinel_values[2]),
        muse_api_key=SecretStr(sentinel_values[3]),
        runway_api_key=SecretStr(sentinel_values[4]),
        meta_secret_encryption_key=SecretStr(sentinel_values[5]),
        app_jwt_secret=SecretStr(sentinel_values[6]),
    )
    rendered = json.dumps(settings.safe_summary())
    for secret in sentinel_values:
        assert secret not in rendered
        assert "SuperSecretPgPass999" not in rendered

    scrubbed = json.dumps(
        scrub_secrets(
            {
                "access_token": "EAAGsecretMetaToken1234567890",
                "authorization": "Bearer super-secret-jwt",
                "nested": {"meta_app_secret": "top-secret-app-key"},
                "safe": "public-status",
            }
        )
    )
    assert "EAAGsecretMetaToken1234567890" not in scrubbed
    assert "super-secret-jwt" not in scrubbed
    assert "top-secret-app-key" not in scrubbed
    assert "public-status" in scrubbed


def test_acceptance_18_frontend_source_and_static_bundles_contain_no_secrets() -> None:
    scan_roots = [REPO_ROOT / "apps" / "web" / "src"]
    out_dir = REPO_ROOT / "apps" / "web" / "out"
    if out_dir.exists():
        scan_roots.append(out_dir)

    forbidden_env_refs = (
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_SECRET_KEY",
        "META_APP_SECRET",
        "MUSE_API_KEY",
        "RUNWAY_API_KEY",
        "APP_JWT_SECRET",
        "META_SECRET_ENCRYPTION_KEY",
        "APP_DATABASE_URL",
        "MIGRATION_DATABASE_URL",
    )

    scanned_files = 0
    for root in scan_roots:
        is_bundle = root == out_dir
        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix not in {".ts", ".tsx", ".js", ".mjs", ".html", ".json", ".txt"}:
                continue
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            scanned_files += 1
            for env_name in forbidden_env_refs:
                needle = env_name if is_bundle else f"process.env.{env_name}"
                assert needle not in text, f"Server-only env ref {needle} leaked in {file_path}"
            for pattern in FORBIDDEN_PATTERNS:
                assert not pattern.search(text), f"Secret pattern leaked in {file_path}"

    assert scanned_files > 0
