"""Tenant-scoped Project and private Asset routes."""

from __future__ import annotations

import base64
import binascii
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
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
from packages.shared.context import TenantContext
from packages.shared.enums import AssetKind, AssetSourceType
from packages.shared.errors import ValidationDomainError, WorkspaceAccessDeniedError
from packages.storage.validation import validate_magic_bytes
from packages.tenancy import tenant_transaction
from packages.video_gateway.contracts import AssetView, ProjectView

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}", tags=["projects", "assets"])

MAX_INLINE_ASSET_BYTES = 10 * 1024 * 1024  # 10 MiB
_MAX_BASE64_CHARS = ((MAX_INLINE_ASSET_BYTES + 2) // 3) * 4


class CreateProjectPayload(BaseModel):
    """Request payload for creating a tenant-scoped project."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    client_id: UUID | None = None
    slug: str | None = Field(default=None, max_length=64)


class CreateAssetPayload(BaseModel):
    """Request payload for uploading a private tenant asset via strict base64 JSON."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=3, max_length=128)
    asset_kind: AssetKind
    content_base64: str = Field(min_length=1)
    client_id: UUID | None = None
    project_id: UUID | None = None


class AssetDownloadUrlOut(BaseModel):
    """Short-lived signed download URL response for a private tenant asset."""

    model_config = ConfigDict(extra="forbid")

    url: str
    bucket: str
    key: str
    expires_in: int


def _require_workspace_permission(ctx: TenantContext, permission: str) -> None:
    if ctx.role_name is None:
        raise WorkspaceAccessDeniedError("Access to the requested workspace is denied")
    require_permission(ctx.role_name, permission)


def _decode_and_validate_asset_bytes(
    *,
    content_base64: str,
    asset_kind: AssetKind,
    mime_type: str,
) -> tuple[bytes, str]:
    """Strictly decode base64 asset bytes, enforce size cap, and validate image magic bytes."""
    encoded = content_base64.strip()
    if not encoded:
        raise ValidationDomainError("Uploaded asset content must not be empty")
    if len(encoded) > _MAX_BASE64_CHARS:
        raise ValidationDomainError(
            f"Uploaded asset exceeds maximum allowed size of {MAX_INLINE_ASSET_BYTES} bytes",
            details={"max_size_bytes": MAX_INLINE_ASSET_BYTES},
        )

    try:
        raw_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationDomainError("content_base64 must be valid base64-encoded data") from exc

    if not raw_bytes:
        raise ValidationDomainError("Uploaded asset content must not be empty")
    if len(raw_bytes) > MAX_INLINE_ASSET_BYTES:
        raise ValidationDomainError(
            f"Uploaded asset exceeds maximum allowed size of {MAX_INLINE_ASSET_BYTES} bytes",
            details={"size_bytes": len(raw_bytes), "max_size_bytes": MAX_INLINE_ASSET_BYTES},
        )

    normalized_mime = mime_type.split(";", 1)[0].strip().lower()
    if asset_kind == AssetKind.IMAGE:
        verified_mime = validate_magic_bytes(
            header=raw_bytes[:64],
            declared_mime=normalized_mime,
        )
        if not verified_mime.startswith("image/"):
            raise ValidationDomainError(
                "Asset kind 'image' requires an image MIME type",
                details={"asset_kind": asset_kind.value, "mime_type": verified_mime},
            )
        return raw_bytes, verified_mime

    return raw_bytes, normalized_mime


@router.post("/projects", response_model=ProjectView, status_code=status.HTTP_201_CREATED)
def post_workspace_project(
    workspace_id: UUID,
    body: CreateProjectPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> ProjectView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_workspace_permission(ctx, "projects:write")
        return container.video_service.create_project(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            name=body.name,
            client_id=body.client_id,
            slug=body.slug,
            request_id=request_id,
        )


@router.get("/projects", response_model=list[ProjectView])
def get_workspace_projects(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    client_id: Annotated[UUID | None, Query()] = None,
) -> list[ProjectView]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_workspace_permission(ctx, "projects:read")
        return container.video_service.list_projects(
            session,
            workspace_id=workspace_id,
            client_id=client_id,
        )


@router.post("/assets", response_model=AssetView, status_code=status.HTTP_201_CREATED)
def post_workspace_asset(
    workspace_id: UUID,
    body: CreateAssetPayload,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> AssetView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_workspace_permission(ctx, "assets:write")
        raw_bytes, verified_mime = _decode_and_validate_asset_bytes(
            content_base64=body.content_base64,
            asset_kind=body.asset_kind,
            mime_type=body.mime_type,
        )
        return container.video_service.create_asset(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            filename=body.filename,
            mime_type=verified_mime,
            asset_kind=body.asset_kind,
            data=raw_bytes,
            client_id=body.client_id,
            project_id=body.project_id,
            source_type=AssetSourceType.UPLOAD,
            request_id=request_id,
        )


@router.get("/assets", response_model=list[AssetView])
def get_workspace_assets(
    workspace_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    client_id: Annotated[UUID | None, Query()] = None,
    asset_kind: Annotated[AssetKind | None, Query()] = None,
) -> list[AssetView]:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_workspace_permission(ctx, "assets:read")
        return container.video_service.list_assets(
            session,
            workspace_id=workspace_id,
            client_id=client_id,
            asset_kind=asset_kind,
        )


@router.get("/assets/{asset_id}", response_model=AssetView)
def get_workspace_asset(
    workspace_id: UUID,
    asset_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
) -> AssetView:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_workspace_permission(ctx, "assets:read")
        return container.video_service.get_asset(
            session,
            workspace_id=workspace_id,
            asset_id=asset_id,
        )


@router.get("/assets/{asset_id}/download-url", response_model=AssetDownloadUrlOut)
def get_workspace_asset_download_url(
    workspace_id: UUID,
    asset_id: UUID,
    actor: Annotated[AuthenticatedActor, Depends(get_current_actor)],
    session_factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    container: Annotated[AppContainer, Depends(get_container)],
    request_id: Annotated[UUID, Depends(get_request_id)],
    expires_in: Annotated[int, Query(ge=1, le=3600)] = 300,
) -> AssetDownloadUrlOut:
    with tenant_transaction(
        session_factory,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        request_id=request_id,
    ) as (session, ctx):
        _require_workspace_permission(ctx, "assets:read")
        download = container.video_service.create_asset_download_url(
            session,
            workspace_id=workspace_id,
            asset_id=asset_id,
            expires_in=expires_in,
        )
        return AssetDownloadUrlOut(
            url=download.url,
            bucket=download.bucket,
            key=download.key,
            expires_in=download.expires_in,
        )
