"""Brand Profile CRUD and private Brand Asset presigned upload/verification service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import BrandAsset, BrandProfile, Client
from packages.auth.rbac import require_permission
from packages.shared.context import TenantContext
from packages.shared.enums import AuditAction, BrandAssetType
from packages.shared.errors import (
    ConflictError,
    NotFoundError,
    ValidationDomainError,
    WorkspaceContextRequiredError,
)
from packages.shared.events import AuditEventPayload
from packages.shared.schemas import BrandAssetOut, BrandProfileOut
from packages.storage.keys import build_brand_asset_key, sanitize_filename, validate_storage_key
from packages.storage.protocol import PresignedDownloadUrl, StorageBackend
from packages.storage.validation import (
    MAX_BRAND_ASSET_BYTES,
    validate_magic_bytes,
    validate_upload_metadata,
)
from packages.tenancy.audit import record_audit_log
from packages.tenancy.clients import assert_client_readable


@dataclass(frozen=True)
class BrandAssetUploadInit:
    """Short-lived presigned upload details returned before direct object upload."""

    asset_id: UUID
    brand_profile_id: UUID
    storage_bucket: str
    storage_key: str
    upload_url: str
    upload_method: str
    upload_headers: dict[str, str]
    expires_in: int


def _brand_profile_to_out(row: BrandProfile) -> BrandProfileOut:
    raw_palette = row.color_palette
    palette: list[str] | dict[str, Any] = (
        [str(item) for item in raw_palette] if isinstance(raw_palette, list) else raw_palette
    )
    return BrandProfileOut(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        brand_name=row.brand_name,
        tone_of_voice=row.tone_of_voice,
        target_audience=row.target_audience,
        color_palette=palette,
        guidelines=row.guidelines,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _brand_asset_to_out(row: BrandAsset) -> BrandAssetOut:
    return BrandAssetOut(
        id=row.id,
        workspace_id=row.workspace_id,
        client_id=row.client_id,
        brand_profile_id=row.brand_profile_id,
        asset_type=BrandAssetType(row.asset_type),
        filename=row.filename,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        storage_bucket=row.storage_bucket,
        storage_key=row.storage_key,
        created_at=row.created_at,
    )


def _ensure_brand_profile(session: Session, client: Client) -> BrandProfile:
    existing = session.execute(
        select(BrandProfile).where(
            BrandProfile.workspace_id == client.workspace_id,
            BrandProfile.client_id == client.id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    profile = BrandProfile(
        id=uuid4(),
        workspace_id=client.workspace_id,
        client_id=client.id,
        brand_name=client.name,
        color_palette=[],
    )
    session.add(profile)
    session.flush()
    return profile


def get_brand_profile(session: Session, ctx: TenantContext, *, client_id: UUID) -> BrandProfileOut:
    """Return a client's brand profile after enforcing RLS, RBAC, and client scoping."""
    if ctx.workspace_id is None or ctx.role_name is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")
    require_permission(ctx.role_name, "brand:read")
    assert_client_readable(session, ctx, client_id)

    profile = session.execute(
        select(BrandProfile).where(
            BrandProfile.workspace_id == ctx.workspace_id,
            BrandProfile.client_id == client_id,
        )
    ).scalar_one_or_none()
    if profile is None:
        raise NotFoundError("Brand profile not found for this client")
    return _brand_profile_to_out(profile)


def upsert_brand_profile(
    session: Session,
    ctx: TenantContext,
    *,
    client_id: UUID,
    brand_name: str,
    tone_of_voice: str | None = None,
    target_audience: str | None = None,
    color_palette: list[str] | dict[str, Any] | None = None,
    guidelines: str | None = None,
) -> BrandProfileOut:
    """Create or update a client's brand profile and write an audit entry."""
    if ctx.workspace_id is None or ctx.role_name is None or ctx.user_id is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")
    require_permission(ctx.role_name, "brand:write")
    assert_client_readable(session, ctx, client_id)

    cleaned_name = brand_name.strip()
    if not cleaned_name:
        raise ValidationDomainError("Brand name must not be empty")

    profile = session.execute(
        select(BrandProfile).where(
            BrandProfile.workspace_id == ctx.workspace_id,
            BrandProfile.client_id == client_id,
        )
    ).scalar_one_or_none()
    if profile is None:
        profile = BrandProfile(
            id=uuid4(),
            workspace_id=ctx.workspace_id,
            client_id=client_id,
            brand_name=cleaned_name,
            tone_of_voice=tone_of_voice,
            target_audience=target_audience,
            color_palette=color_palette if color_palette is not None else [],
            guidelines=guidelines,
        )
        session.add(profile)
    else:
        profile.brand_name = cleaned_name
        profile.tone_of_voice = tone_of_voice
        profile.target_audience = target_audience
        if color_palette is not None:
            profile.color_palette = color_palette
        profile.guidelines = guidelines

    session.flush()
    record_audit_log(
        session,
        AuditEventPayload(
            action=AuditAction.BRAND_PROFILE_UPSERTED,
            workspace_id=ctx.workspace_id,
            client_id=client_id,
            actor_user_id=ctx.user_id,
            target_type="brand_profile",
            target_id=str(profile.id),
            request_id=ctx.request_id,
            metadata={"brand_name": profile.brand_name},
        ),
    )
    return _brand_profile_to_out(profile)


def list_brand_assets(
    session: Session,
    ctx: TenantContext,
    *,
    client_id: UUID,
) -> list[BrandAssetOut]:
    """List verified private brand assets for a client."""
    if ctx.workspace_id is None or ctx.role_name is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")
    require_permission(ctx.role_name, "brand:read")
    assert_client_readable(session, ctx, client_id)

    rows = (
        session.execute(
            select(BrandAsset)
            .where(
                BrandAsset.workspace_id == ctx.workspace_id,
                BrandAsset.client_id == client_id,
            )
            .order_by(BrandAsset.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [_brand_asset_to_out(row) for row in rows]


def initiate_brand_asset_upload(
    session: Session,
    ctx: TenantContext,
    storage: StorageBackend,
    *,
    client_id: UUID,
    asset_type: BrandAssetType,
    filename: str,
    mime_type: str,
    size_bytes: int,
) -> BrandAssetUploadInit:
    """Validate upload metadata and mint a short-lived presigned PUT URL."""
    if ctx.workspace_id is None or ctx.role_name is None or ctx.user_id is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")
    require_permission(ctx.role_name, "brand:write")
    client = assert_client_readable(session, ctx, client_id)
    profile = _ensure_brand_profile(session, client)

    safe_name = sanitize_filename(filename)
    normalized_mime = validate_upload_metadata(
        filename=safe_name,
        declared_mime=mime_type,
        size_bytes=size_bytes,
    )

    asset_id = uuid4()
    storage_key = build_brand_asset_key(
        workspace_id=ctx.workspace_id,
        client_id=client_id,
        asset_id=asset_id,
        filename=safe_name,
    )
    presigned = storage.create_presigned_put_url(
        key=storage_key,
        content_type=normalized_mime,
        content_length=size_bytes,
    )
    return BrandAssetUploadInit(
        asset_id=asset_id,
        brand_profile_id=profile.id,
        storage_bucket=presigned.bucket,
        storage_key=presigned.key,
        upload_url=presigned.url,
        upload_method=presigned.method,
        upload_headers=presigned.headers,
        expires_in=presigned.expires_in,
    )


def complete_brand_asset_upload(
    session: Session,
    ctx: TenantContext,
    storage: StorageBackend,
    *,
    client_id: UUID,
    asset_id: UUID,
    asset_type: BrandAssetType,
    filename: str,
    mime_type: str,
    storage_key: str,
) -> BrandAssetOut:
    """Verify uploaded object existence, size, tenant key, and magic bytes, then persist."""
    if ctx.workspace_id is None or ctx.role_name is None or ctx.user_id is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")
    require_permission(ctx.role_name, "brand:write")
    client = assert_client_readable(session, ctx, client_id)
    profile = _ensure_brand_profile(session, client)

    safe_name = sanitize_filename(filename)
    validated_key = validate_storage_key(
        storage_key,
        expected_workspace_id=ctx.workspace_id,
        expected_client_id=client_id,
    )

    meta = storage.head_object(key=validated_key)
    if meta.size_bytes <= 0 or meta.size_bytes > MAX_BRAND_ASSET_BYTES:
        storage.delete_object(key=validated_key)
        raise ValidationDomainError(
            "Uploaded object size is outside allowed bounds",
            details={"size_bytes": meta.size_bytes, "max_size_bytes": MAX_BRAND_ASSET_BYTES},
        )

    header = storage.read_object_prefix(key=validated_key, num_bytes=64)
    try:
        verified_mime = validate_magic_bytes(header=header, declared_mime=mime_type)
    except ValidationDomainError:
        storage.delete_object(key=validated_key)
        raise

    asset = BrandAsset(
        id=asset_id,
        workspace_id=ctx.workspace_id,
        client_id=client_id,
        brand_profile_id=profile.id,
        asset_type=asset_type.value,
        filename=safe_name,
        mime_type=verified_mime,
        size_bytes=meta.size_bytes,
        storage_bucket=meta.bucket,
        storage_key=validated_key,
    )
    try:
        with session.begin_nested():
            session.add(asset)
            session.flush()
    except IntegrityError as exc:
        raise ConflictError("Brand asset with this storage key already exists") from exc

    record_audit_log(
        session,
        AuditEventPayload(
            action=AuditAction.BRAND_ASSET_CREATED,
            workspace_id=ctx.workspace_id,
            client_id=client_id,
            actor_user_id=ctx.user_id,
            target_type="brand_asset",
            target_id=str(asset.id),
            request_id=ctx.request_id,
            metadata={
                "asset_type": asset.asset_type,
                "mime_type": asset.mime_type,
                "size_bytes": asset.size_bytes,
                "storage_key": asset.storage_key,
            },
        ),
    )
    return _brand_asset_to_out(asset)


def get_brand_asset_download_url(
    session: Session,
    ctx: TenantContext,
    storage: StorageBackend,
    *,
    client_id: UUID,
    asset_id: UUID,
) -> PresignedDownloadUrl:
    """Validate ownership and mint a short-lived presigned GET URL for a brand asset."""
    if ctx.workspace_id is None or ctx.role_name is None or ctx.user_id is None:
        raise WorkspaceContextRequiredError("Active workspace context is required")
    require_permission(ctx.role_name, "brand:read")
    assert_client_readable(session, ctx, client_id)

    asset = session.execute(
        select(BrandAsset).where(
            BrandAsset.workspace_id == ctx.workspace_id,
            BrandAsset.client_id == client_id,
            BrandAsset.id == asset_id,
        )
    ).scalar_one_or_none()
    if asset is None:
        raise NotFoundError("Brand asset not found")

    validated_key = validate_storage_key(
        asset.storage_key,
        expected_workspace_id=ctx.workspace_id,
        expected_client_id=client_id,
    )
    download = storage.create_presigned_get_url(
        key=validated_key,
        download_filename=asset.filename,
    )
    record_audit_log(
        session,
        AuditEventPayload(
            action=AuditAction.BRAND_ASSET_ACCESSED,
            workspace_id=ctx.workspace_id,
            client_id=client_id,
            actor_user_id=ctx.user_id,
            target_type="brand_asset",
            target_id=str(asset.id),
            request_id=ctx.request_id,
            metadata={"storage_key": validated_key},
        ),
    )
    return download
