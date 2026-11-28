"""Tenant-scoped Meta Account Connection, Unified Login, Capabilities & Webhook routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from apps.api.deps import (
    AppContainer,
    AuthenticatedActor,
    get_container,
    get_current_actor,
    get_request_id,
    get_session_factory,
)
from packages.auth import require_permission
from packages.meta_integrations.contracts import (
    MetaCapabilitiesView,
    MetaConnectedAssetView,
    MetaConnectionView,
    MetaWebhookReceipt,
    PermissionMatrixEntry,
    UnifiedLoginStartResponse,
)
from packages.shared.context import TenantContext
from packages.shared.enums import MetaConnectionType
from packages.shared.errors import WorkspaceAccessDeniedError
from packages.tenancy import rls_transaction, tenant_transaction

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/meta", tags=["meta"])


class UnifiedLoginStartPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_type: MetaConnectionType
    client_id: UUID | None = None
    redirect_uri: str | None = Field(default=None, max_length=512)
    include_ads: bool = False
    include_comments: bool = False
    include_messages: bool = False


class UnifiedLoginCallbackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=1, max_length=4096)
    redirect_uri: str | None = Field(default=None, max_length=512)


class ReconnectPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    redirect_uri: str | None = Field(default=None, max_length=512)


class AssetSelectionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    is_selected: bool


class UpdateAssetSelectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selections: list[AssetSelectionItem] = Field(min_length=1, max_length=200)


def _require_ws_perm(ctx: TenantContext, permission: str) -> None:
    if ctx.role_name is None:
        raise WorkspaceAccessDeniedError("Access to the requested workspace is denied")
    require_permission(ctx.role_name, permission)


@router.get("/permission-matrix", response_model=list[PermissionMatrixEntry])
def get_meta_permission_matrix(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> list[PermissionMatrixEntry]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (_, ctx):
        _require_ws_perm(ctx, "meta:read")
        return container.meta_service.get_permission_matrix()


@router.get("/connections", response_model=list[MetaConnectionView])
def get_meta_connections(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    client_id: Annotated[UUID | None, Query()] = None,
) -> list[MetaConnectionView]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "meta:read")
        return container.meta_service.list_connections(
            session, workspace_id=workspace_id, client_id=client_id
        )


@router.post("/connections/unified-login/start", response_model=UnifiedLoginStartResponse)
def post_unified_login_start(
    workspace_id: UUID,
    body: UnifiedLoginStartPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> UnifiedLoginStartResponse:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "meta:write")
        return container.meta_service.start_unified_login(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            connection_type=body.connection_type,
            client_id=body.client_id,
            redirect_uri=body.redirect_uri,
            include_ads=body.include_ads,
            include_comments=body.include_comments,
            include_messages=body.include_messages,
            request_id=request_id,
        )


@router.get("/connections/unified-login/callback", response_model=MetaConnectionView)
def get_unified_login_callback(
    workspace_id: UUID,
    code: Annotated[str, Query(min_length=1, max_length=2048)],
    state: Annotated[str, Query(min_length=1, max_length=4096)],
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    redirect_uri: Annotated[str | None, Query()] = None,
) -> MetaConnectionView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "meta:write")
        return container.meta_service.complete_unified_login(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            code=code,
            state=state,
            redirect_uri=redirect_uri,
            request_id=request_id,
        )


@router.post(
    "/connections/unified-login/callback",
    response_model=MetaConnectionView,
    status_code=status.HTTP_201_CREATED,
)
def post_unified_login_callback(
    workspace_id: UUID,
    body: UnifiedLoginCallbackPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> MetaConnectionView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "meta:write")
        return container.meta_service.complete_unified_login(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            code=body.code,
            state=body.state,
            redirect_uri=body.redirect_uri,
            request_id=request_id,
        )


@router.post("/connections/{connection_id}/reconnect", response_model=UnifiedLoginStartResponse)
def post_reconnect_meta_connection(
    workspace_id: UUID,
    connection_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    body: ReconnectPayload | None = None,
) -> UnifiedLoginStartResponse:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "meta:write")
        return container.meta_service.reconnect_connection(
            session,
            workspace_id=workspace_id,
            connection_id=connection_id,
            actor_user_id=actor.user_id,
            redirect_uri=body.redirect_uri if body else None,
            request_id=request_id,
        )


@router.delete("/connections/{connection_id}", response_model=MetaConnectionView)
def delete_meta_connection(
    workspace_id: UUID,
    connection_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> MetaConnectionView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "meta:write")
        return container.meta_service.disconnect_connection(
            session,
            workspace_id=workspace_id,
            connection_id=connection_id,
            actor_user_id=actor.user_id,
            request_id=request_id,
        )


@router.get("/connections/{connection_id}/assets", response_model=list[MetaConnectedAssetView])
def get_meta_connection_assets(
    workspace_id: UUID,
    connection_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    include_deselected: Annotated[bool, Query()] = False,
) -> list[MetaConnectedAssetView]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "meta:read")
        return container.meta_service.list_connection_assets(
            session,
            workspace_id=workspace_id,
            connection_id=connection_id,
            include_deselected=include_deselected,
        )


@router.patch("/connections/{connection_id}/assets", response_model=MetaConnectionView)
def patch_meta_connection_assets(
    workspace_id: UUID,
    connection_id: UUID,
    body: UpdateAssetSelectionPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> MetaConnectionView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "meta:write")
        return container.meta_service.update_asset_selection(
            session,
            workspace_id=workspace_id,
            connection_id=connection_id,
            actor_user_id=actor.user_id,
            selections={item.asset_id: item.is_selected for item in body.selections},
            request_id=request_id,
        )


@router.get("/connections/{connection_id}/capabilities", response_model=MetaCapabilitiesView)
def get_meta_connection_capabilities(
    workspace_id: UUID,
    connection_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> MetaCapabilitiesView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_ws_perm(ctx, "meta:read")
        return container.meta_service.get_connection_capabilities(
            session, workspace_id=workspace_id, connection_id=connection_id
        )


@router.get("/webhooks", response_class=PlainTextResponse)
def get_meta_webhook_challenge(
    workspace_id: UUID,
    hub_mode: Annotated[str, Query(alias="hub.mode")],
    hub_verify_token: Annotated[str, Query(alias="hub.verify_token")],
    hub_challenge: Annotated[str, Query(alias="hub.challenge")],
    container: Annotated[AppContainer, Depends(get_container)],
) -> str:
    _ = workspace_id
    return container.meta_service.verify_webhook_challenge(
        hub_mode=hub_mode,
        hub_verify_token=hub_verify_token,
        hub_challenge=hub_challenge,
    )


@router.post("/webhooks", response_model=MetaWebhookReceipt)
async def post_meta_webhook(
    workspace_id: UUID,
    request: Request,
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> MetaWebhookReceipt:
    raw_body = await request.body()
    headers = dict(request.headers)
    with rls_transaction(session_factory, user_id=None, workspace_id=workspace_id) as session:
        return container.meta_service.ingest_webhook(
            session,
            workspace_id=workspace_id,
            raw_body=raw_body,
            headers=headers,
            request_id=request_id,
        )
