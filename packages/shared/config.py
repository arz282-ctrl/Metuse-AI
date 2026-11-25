"""Environment configuration and PostgreSQL URL normalization for Metuse AI."""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from packages.shared.scrubbing import scrub_secrets

DEV_APP_ENVS = frozenset({"development", "dev", "local", "test"})
NON_DEV_APP_ENVS = frozenset({"production", "prod", "staging", "stage"})


def normalize_database_url(url: str) -> str:
    """Normalize postgresql:// or postgres:// DSNs to SQLAlchemy psycopg v3 driver URLs."""
    stripped = url.strip()
    lowered = stripped.lower()
    if lowered.startswith("postgresql+psycopg://"):
        return stripped
    if lowered.startswith("postgresql://"):
        return "postgresql+psycopg://" + stripped[len("postgresql://") :]
    if lowered.startswith("postgres://"):
        return "postgresql+psycopg://" + stripped[len("postgres://") :]
    return stripped


class BaseAppSettings(BaseSettings):
    """Shared application settings loaded from environment / .env without leaking secrets."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    postgres_host_port: int = 5433
    database_url: SecretStr = SecretStr("postgresql://metuse:change-me@localhost:5433/metuse")
    app_database_url: SecretStr | None = None
    migration_database_url: SecretStr | None = None
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    s3_endpoint: str = "http://localhost:9000"
    s3_public_endpoint: str | None = None
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    s3_bucket: str = "metuse-dev"
    s3_region: str = "us-east-1"
    s3_presign_ttl_seconds: int = 900
    temporal_address: str = "localhost:7233"
    secret_store_backend: str = "env_dev"  # noqa: S105
    app_jwt_secret: SecretStr = SecretStr("")
    jwt_expires_seconds: int = 3600
    app_cors_origins: str = "http://localhost:3000,http://localhost:3001"
    meta_auth_mode: str = "mock_dev"
    meta_app_id: str = ""
    meta_app_secret: SecretStr = SecretStr("")
    meta_config_id: str = ""
    meta_redirect_uri: str = "http://localhost:3000/auth/meta/callback"
    meta_webhook_verify_token: SecretStr = SecretStr("")

    def is_dev_env(self) -> bool:
        """Return True only when APP_ENV is an explicit development/local/test value."""
        normalized = self.app_env.strip().lower()
        return normalized in DEV_APP_ENVS and normalized not in NON_DEV_APP_ENVS

    def get_sqlalchemy_database_url(self, *, use_app_role: bool = False) -> str:
        """Return psycopg-normalized SQLAlchemy URL for runtime or owner connections."""
        if use_app_role:
            if (
                self.app_database_url is None
                or not self.app_database_url.get_secret_value().strip()
            ):
                raise ValueError("APP_DATABASE_URL must be configured when use_app_role=True")
            raw = self.app_database_url.get_secret_value()
        elif self.migration_database_url is not None and (
            self.migration_database_url.get_secret_value().strip()
        ):
            raw = self.migration_database_url.get_secret_value()
        else:
            raw = self.database_url.get_secret_value()
        return normalize_database_url(raw)

    def safe_summary(self) -> dict[str, Any]:
        """Return a secret-scrubbed summary suitable for structured startup logs."""
        raw = {
            "app_env": self.app_env,
            "postgres_host_port": self.postgres_host_port,
            "database_url": self.database_url.get_secret_value(),
            "app_database_url": (
                self.app_database_url.get_secret_value() if self.app_database_url else None
            ),
            "redis_url": self.redis_url.get_secret_value(),
            "s3_endpoint": self.s3_endpoint,
            "s3_bucket": self.s3_bucket,
            "temporal_address": self.temporal_address,
            "secret_store_backend": self.secret_store_backend,
            "app_jwt_secret_configured": bool(self.app_jwt_secret.get_secret_value()),
            "meta_app_id": self.meta_app_id,
            "meta_app_secret_configured": bool(self.meta_app_secret.get_secret_value()),
        }
        scrubbed = scrub_secrets(raw)
        return scrubbed if isinstance(scrubbed, dict) else {}
