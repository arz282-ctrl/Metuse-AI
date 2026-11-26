"""Shared Pydantic v2 DTO schemas aligned with Metuse AI API contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from packages.shared.enums import (
    BrandAssetType,
    ClientStatus,
    MemberStatus,
    RoleName,
    WorkspaceType,
)
from packages.shared.scrubbing import scrub_secrets


class UserOut(BaseModel):
    """Public user representation (never exposes password_hash or secret refs)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str | None = None
    is_active: bool = True
    created_at: datetime


class MembershipSummaryOut(BaseModel):
    """Compact membership item returned by /api/v1/auth/me."""

    model_config = ConfigDict(from_attributes=True)

    workspace_id: UUID
    workspace_name: str
    workspace_type: WorkspaceType
    role_name: RoleName
    status: MemberStatus


class AuthTokenOut(BaseModel):
    """Session/token response emitted on register, login, and Meta callback."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_in: int
    user: UserOut


class WorkspaceOut(BaseModel):
    """Workspace DTO returned by workspace endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    workspace_type: WorkspaceType
    max_members: int = Field(ge=1, le=5)
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


class MemberOut(BaseModel):
    """Workspace member DTO."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    user_id: UUID
    email: str
    full_name: str | None = None
    role_name: RoleName
    status: MemberStatus
    created_at: datetime
    updated_at: datetime


class ClientOut(BaseModel):
    """Tenant-scoped client DTO."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    name: str
    slug: str
    status: ClientStatus
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class BrandProfileOut(BaseModel):
    """Client brand profile DTO."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    client_id: UUID
    brand_name: str
    tone_of_voice: str | None = None
    target_audience: str | None = None
    color_palette: list[str] | dict[str, Any] = Field(default_factory=list)
    guidelines: str | None = None
    created_at: datetime
    updated_at: datetime


class BrandAssetOut(BaseModel):
    """Private brand asset metadata DTO."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    client_id: UUID
    brand_profile_id: UUID
    asset_type: BrandAssetType
    filename: str
    mime_type: str
    size_bytes: int = Field(ge=0)
    storage_bucket: str
    storage_key: str
    created_at: datetime


class AuditLogOut(BaseModel):
    """Read-only representation of an append-only audit log row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID | None = None
    client_id: UUID | None = None
    actor_user_id: UUID | None = None
    action: str
    target_type: str
    target_id: str | None = None
    request_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("metadata", mode="before")
    @classmethod
    def _scrub_metadata(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        scrubbed = scrub_secrets(value)
        return scrubbed if isinstance(scrubbed, dict) else {"value": scrubbed}


class HealthCheckResult(BaseModel):
    """Health and readiness response payload."""

    status: str
    checks: dict[str, str] = Field(default_factory=dict)
    version: str = "0.1.0"
