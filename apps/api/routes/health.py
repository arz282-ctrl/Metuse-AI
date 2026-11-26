"""Health and readiness endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text

from apps.api.deps import AppContainer, get_container

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe — does not touch external dependencies."""
    return {"status": "ok", "service": "metuse-api"}


@router.get("/readyz")
def readyz(container: Annotated[AppContainer, Depends(get_container)]) -> dict[str, object]:
    """Readiness probe reporting database and session store status without leaking secrets."""
    db_status = "unconfigured"
    if container.session_factory is not None:
        try:
            with container.session_factory() as session:
                session.execute(text("SELECT 1"))
            db_status = "ok"
        except Exception:
            db_status = "error"

    store_ok = container.session_store.check_health()
    overall = "ok" if db_status in {"ok", "unconfigured"} and store_ok else "degraded"
    return {
        "status": overall,
        "checks": {
            "database": db_status,
            "session_store": "ok" if store_ok else "error",
        },
    }
