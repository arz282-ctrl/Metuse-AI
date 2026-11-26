from __future__ import annotations

import sys
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import httpx
import pytest

_E2E_DIR = str(Path(__file__).resolve().parent)
if _E2E_DIR not in sys.path:
    sys.path.insert(0, _E2E_DIR)

from helpers import (  # noqa: E402
    ApiSession,
    E2EConfig,
    open_pg_conn,
    unique_email,
    unique_id,
)


@pytest.fixture(scope="session")
def e2e_config() -> E2EConfig:
    return E2EConfig.from_env()


@pytest.fixture()
def session_factory(e2e_config: E2EConfig) -> Generator[Callable[..., ApiSession], None, None]:
    created: list[ApiSession] = []

    def _make(*, auth_mode: str = "bearer") -> ApiSession:
        sess = ApiSession(e2e_config, auth_mode=auth_mode)
        created.append(sess)
        return sess

    try:
        yield _make
    finally:
        for sess in created:
            sess.close()


@pytest.fixture()
def api_session(session_factory: Callable[..., ApiSession]) -> ApiSession:
    return session_factory(auth_mode="bearer")


@pytest.fixture()
def web_client(e2e_config: E2EConfig) -> Generator[httpx.Client, None, None]:
    with httpx.Client(
        base_url=e2e_config.web_url,
        timeout=15.0,
        follow_redirects=False,
    ) as client:
        yield client


@pytest.fixture()
def workspace_factory(
    session_factory: Callable[..., ApiSession],
) -> Callable[..., tuple[ApiSession, dict[str, Any], dict[str, Any], str]]:
    def _create(
        *,
        workspace_type: str = "agency",
        auth_mode: str = "bearer",
        password: str = "StrongPass!2026",
        full_name: str = "E2E Owner",
    ) -> tuple[ApiSession, dict[str, Any], dict[str, Any], str]:
        sess = session_factory(auth_mode=auth_mode)
        email = unique_email(f"{workspace_type}-owner")
        reg = sess.register(email=email, password=password, full_name=full_name)
        assert reg.status_code == 201, f"Failed to register {email}: {reg.status_code} {reg.text}"
        ws = sess.create_workspace(
            name=unique_id(f"{workspace_type}-ws"),
            workspace_type=workspace_type,
        )
        assert ws.status_code == 201, f"Failed to create workspace: {ws.status_code} {ws.text}"
        return sess, reg.json()["user"], ws.json(), password

    return _create


@pytest.fixture()
def pg_conn_factory(e2e_config: E2EConfig) -> Generator[Callable[[str | None], Any], None, None]:
    conns: list[Any] = []

    def _connect(dsn: str | None = None) -> Any:
        conn = open_pg_conn(dsn or e2e_config.database_url)
        conns.append(conn)
        return conn

    try:
        yield _connect
    finally:
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass


@pytest.fixture(scope="session")
def playwright_browser(e2e_config: E2EConfig) -> Generator[Any, None, None]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        if e2e_config.require_playwright:
            pytest.fail("E2E_REQUIRE_PLAYWRIGHT=1 but 'playwright' is not installed.")
        yield None
        return

    pw_cm = sync_playwright()
    pw = pw_cm.__enter__()
    browser = None
    try:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception:
            try:
                browser = pw.chromium.launch(headless=True, channel="chrome")
            except Exception as exc:
                if e2e_config.require_playwright:
                    pytest.fail(f"E2E_REQUIRE_PLAYWRIGHT=1 but browser launch failed: {exc}")
                browser = None
        yield browser
    finally:
        if browser is not None:
            browser.close()
        pw_cm.__exit__(None, None, None)
