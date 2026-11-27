"""Primary Metuse AI FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from apps.api.deps import AppContainer
from apps.api.observability import init_sentry
from apps.api.routes import (
    approvals,
    audit,
    auth,
    brand,
    clients,
    editing,
    health,
    jobs,
    meta,
    projects,
    video,
    workspaces,
)
from packages.auth import SessionStore, SupabaseJwksCache
from packages.media_engine import RenderBackend
from packages.muse_adapter import MuseAdapter
from packages.shared.config import BaseAppSettings
from packages.shared.errors import MetuseError
from packages.skill_runtime import SkillRegistry
from packages.storage import StorageBackend
from packages.video_gateway import VideoGatewayRegistry


def create_app(
    *,
    settings: BaseAppSettings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    session_store: SessionStore | None = None,
    supabase_jwks_cache: SupabaseJwksCache | None = None,
    skill_registry: SkillRegistry | None = None,
    muse_adapter: MuseAdapter | None = None,
    storage: StorageBackend | None = None,
    video_registry: VideoGatewayRegistry | None = None,
    render_backend: RenderBackend | None = None,
    enable_db: bool = True,
) -> FastAPI:
    """Create and configure the Metuse AI FastAPI application."""
    resolved_settings = settings or BaseAppSettings()
    init_sentry(resolved_settings)
    container = AppContainer.from_settings(
        resolved_settings,
        session_factory=session_factory,
        session_store=session_store,
        supabase_jwks_cache=supabase_jwks_cache,
        skill_registry=skill_registry,
        muse_adapter=muse_adapter,
        storage=storage,
        video_registry=video_registry,
        render_backend=render_backend,
        enable_db=enable_db,
    )

    app = FastAPI(
        title="Metuse AI API",
        version="0.1.0",
        docs_url="/docs" if resolved_settings.is_dev_env() else None,
        redoc_url=None,
    )
    app.state.container = container

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.get_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-Id",
            "X-CSRF-Token",
            "Idempotency-Key",
        ],
    )

    @app.middleware("http")
    async def security_and_request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        req_id = uuid4()
        request.state.request_id = req_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = str(req_id)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    @app.exception_handler(MetuseError)
    async def metuse_error_handler(request: Request, exc: MetuseError) -> JSONResponse:
        req_id = getattr(request.state, "request_id", None)
        envelope = exc.to_envelope(request_id=req_id)
        return JSONResponse(status_code=exc.status_code, content=envelope.model_dump())

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(clients.router)
    app.include_router(brand.router)
    app.include_router(jobs.router)
    app.include_router(projects.router)
    app.include_router(video.router)
    app.include_router(editing.router)
    app.include_router(approvals.router)
    app.include_router(meta.router)
    app.include_router(audit.router)
    return app


app = create_app()
