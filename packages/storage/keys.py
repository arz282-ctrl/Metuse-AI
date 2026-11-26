"""Strict UUID-bound tenant object key builder and path-traversal guards."""

from __future__ import annotations

import re
from urllib.parse import unquote
from uuid import UUID

from packages.shared.errors import BadRequestError, ValidationDomainError

_ALLOWED_CLIENT_CATEGORIES = frozenset(
    {
        "brand",
        "uploads/originals",
        "uploads/proxies",
        "generated/images",
        "generated/video-scenes",
        "reports",
    }
)
_ALLOWED_PROJECT_SUBDIRS = frozenset({"editing-plans", "previews", "exports"})
_SAFE_FILENAME_CHAR_RE = re.compile(r"[^A-Za-z0-9._-]")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_ENCODED_TRAVERSAL_RE = re.compile(r"%(?:2e|2f|5c|00)", re.IGNORECASE)
_UUID_SEGMENT_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_MAX_FILENAME_LEN = 120


def sanitize_filename(filename: str) -> str:
    """Sanitize a user-supplied filename while rejecting path traversal or control chars."""
    if not filename or not filename.strip():
        raise ValidationDomainError("Filename must not be empty")
    raw = filename.strip()
    if (
        _CONTROL_CHAR_RE.search(raw)
        or "/" in raw
        or "\\" in raw
        or ".." in raw
        or _ENCODED_TRAVERSAL_RE.search(raw)
    ):
        raise ValidationDomainError("Filename contains prohibited path or control characters")

    decoded = unquote(raw)
    if "/" in decoded or "\\" in decoded or ".." in decoded or _CONTROL_CHAR_RE.search(decoded):
        raise ValidationDomainError("Filename contains encoded path traversal characters")

    cleaned = _SAFE_FILENAME_CHAR_RE.sub("_", raw).lstrip(".")
    if not cleaned or cleaned in {".", ".."}:
        raise ValidationDomainError("Filename does not contain valid characters")

    if len(cleaned) > _MAX_FILENAME_LEN:
        if "." in cleaned:
            stem, ext = cleaned.rsplit(".", 1)
            ext_part = f".{ext[:16]}"
            cleaned = f"{stem[: _MAX_FILENAME_LEN - len(ext_part)]}{ext_part}"
        else:
            cleaned = cleaned[:_MAX_FILENAME_LEN]
    return cleaned


def validate_storage_key(
    key: str,
    *,
    expected_workspace_id: UUID,
    expected_client_id: UUID | None = None,
) -> str:
    """Validate an object key against path traversal and enforce strict UUID tenant prefix."""
    if not key or not key.strip():
        raise BadRequestError("Storage key must not be empty")
    if key != key.strip():
        raise BadRequestError("Storage key must not have leading or trailing whitespace")
    if (
        key.startswith("/")
        or key.endswith("/")
        or "//" in key
        or "\\" in key
        or _CONTROL_CHAR_RE.search(key)
        or _ENCODED_TRAVERSAL_RE.search(key)
    ):
        raise BadRequestError("Storage key contains prohibited path characters")

    decoded = unquote(key)
    if "\\" in decoded or "//" in decoded or _CONTROL_CHAR_RE.search(decoded):
        raise BadRequestError("Storage key contains prohibited encoded characters")

    segments = key.split("/")
    for seg in segments:
        if not seg or seg in {".", ".."} or ".." in seg:
            raise BadRequestError("Storage key contains path traversal segments")

    decoded_segments = decoded.split("/")
    for seg in decoded_segments:
        if not seg or seg in {".", ".."} or ".." in seg:
            raise BadRequestError("Storage key contains encoded path traversal segments")

    expected_ws = str(expected_workspace_id).lower()
    if len(segments) < 4 or segments[0] != "workspaces":
        raise BadRequestError("Storage key does not match tenant workspace layout")
    if not _UUID_SEGMENT_RE.fullmatch(segments[1]) or segments[1] != expected_ws:
        raise BadRequestError("Storage key does not belong to the active workspace")

    if expected_client_id is not None:
        expected_client = str(expected_client_id).lower()
        if len(segments) < 6 or segments[2] != "clients":
            raise BadRequestError("Storage key does not match tenant client layout")
        if not _UUID_SEGMENT_RE.fullmatch(segments[3]) or segments[3] != expected_client:
            raise BadRequestError("Storage key does not belong to the requested client")

    return key


def build_tenant_object_key(
    *,
    workspace_id: UUID,
    client_id: UUID,
    category: str,
    asset_id: UUID,
    filename: str,
    project_id: UUID | None = None,
) -> str:
    """Build and validate a canonical Section 7.4 tenant-scoped object storage key."""
    safe_name = sanitize_filename(filename)
    ws_part = str(workspace_id).lower()
    client_part = str(client_id).lower()
    asset_part = str(asset_id).lower()

    if project_id is not None:
        if category not in _ALLOWED_PROJECT_SUBDIRS:
            raise ValidationDomainError(f"Unsupported project storage category '{category}'")
        project_part = str(project_id).lower()
        key = (
            f"workspaces/{ws_part}/clients/{client_part}/projects/"
            f"{project_part}/{category}/{asset_part}/{safe_name}"
        )
    else:
        if category not in _ALLOWED_CLIENT_CATEGORIES:
            raise ValidationDomainError(f"Unsupported client storage category '{category}'")
        key = f"workspaces/{ws_part}/clients/{client_part}/{category}/{asset_part}/{safe_name}"

    return validate_storage_key(
        key,
        expected_workspace_id=workspace_id,
        expected_client_id=client_id,
    )


def build_brand_asset_key(
    *,
    workspace_id: UUID,
    client_id: UUID,
    asset_id: UUID,
    filename: str,
) -> str:
    """Build a canonical private storage key for a client brand asset."""
    return build_tenant_object_key(
        workspace_id=workspace_id,
        client_id=client_id,
        category="brand",
        asset_id=asset_id,
        filename=filename,
    )
