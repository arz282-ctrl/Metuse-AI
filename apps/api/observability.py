"""Sentry observability initialization with secret scrubbing for Metuse AI FastAPI."""

from __future__ import annotations

import os
import sys
from typing import Any

import sentry_sdk
from sentry_sdk.types import Event, Hint

from packages.shared.config import BaseAppSettings
from packages.shared.scrubbing import scrub_secrets

_DEFAULT_RELEASE = "metuse-ai-api@0.1.0"


def _scrub_sentry_event(event: Event, _hint: Hint) -> Event | None:
    """Scrub secrets (tokens, passwords, service-role keys) before transmitting to Sentry."""
    scrubbed: Any = scrub_secrets(dict(event))
    if isinstance(scrubbed, dict):
        return scrubbed  # type: ignore[return-value]
    return event


def resolve_sentry_release() -> str:
    """Resolve release identifier from CI/host environment or fallback version."""
    for env_key in ("SENTRY_RELEASE", "RENDER_GIT_COMMIT", "GITHUB_SHA"):
        val = os.environ.get(env_key, "").strip()
        if val:
            return f"metuse-ai-api@{val[:12]}" if env_key != "SENTRY_RELEASE" else val
    return _DEFAULT_RELEASE


def init_sentry(settings: BaseAppSettings, *, force: bool = False) -> bool:
    """Initialize Sentry SDK (errors, tracing, profiling, logs, metrics) when DSN is configured.

    Returns True when Sentry was initialized, False when skipped (empty/non-HTTP DSN or pytest).
    """
    if not force and (
        "pytest" in sys.modules
        or "PYTEST_CURRENT_TEST" in os.environ
        or settings.app_env.strip().lower() == "test"
    ):
        return False

    dsn = settings.sentry_dsn.strip()
    if not (dsn.startswith("https://") or dsn.startswith("http://")):
        return False

    sample_rate = 1.0 if settings.is_dev_env() else 0.1
    sentry_sdk.init(
        dsn=dsn,
        environment=settings.app_env.strip().lower() or "development",
        release=resolve_sentry_release(),
        traces_sample_rate=sample_rate,
        profile_session_sample_rate=sample_rate,
        profile_lifecycle="trace",
        data_collection={"user_info": False},
        before_send=_scrub_sentry_event,
    )
    return True
