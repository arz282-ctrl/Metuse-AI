"""Frozen Phase 5 contracts for Meta Unified Login, Asset Discovery, Capabilities, and Webhooks."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from packages.shared.enums import (
    InstagramAccountType,
    MetaAssetType,
    MetaConnectionStatus,
    MetaConnectionType,
)


class PermissionMatrixEntry(BaseModel):
    """Visible permission matrix row explaining why each Meta scope is requested (Section 2.3)."""

    model_config = ConfigDict(extra="forbid")

    capability: str
    permission: str
    description: str
    required_for_creator_organic: bool = False
    required_for_agency_ads: bool = False
    app_review_tier: str = "Standard Access (dev/testers) -> Advanced Access (external clients)"


class DiscoveredMetaAsset(BaseModel):
    """Asset returned by Meta Unified Login consent/discovery."""

    model_config = ConfigDict(extra="forbid")

    asset_type: MetaAssetType
    external_asset_id: str
    display_name: str
    username: str | None = None
    instagram_account_type: InstagramAccountType | None = None
    meta_business_id: str | None = None
    is_selected: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class MetaTokenExchangeResult(BaseModel):
    """Server-side OAuth code exchange + discovered asset payload (never serialized to UI)."""

    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: str = "system_user"  # noqa: S105
    expires_at: datetime | None = None
    meta_business_id: str | None = None
    granted_scopes: list[str] = Field(default_factory=list)
    discovered_assets: list[DiscoveredMetaAsset] = Field(default_factory=list)


class UnifiedLoginStartResponse(BaseModel):
    """Response from POST /meta/connections/unified-login/start."""

    model_config = ConfigDict(extra="forbid")

    authorization_url: str
    state: str
    redirect_uri: str
    connection_type: MetaConnectionType
    requested_scopes: list[str]
    config_id: str
    mode: str


class MetaConnectedAssetView(BaseModel):
    """Serialized connected Meta asset view (no tokens)."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    connection_id: UUID
    asset_type: MetaAssetType
    external_asset_id: str
    display_name: str
    username: str | None = None
    instagram_account_type: str | None = None
    meta_business_id: str | None = None
    is_selected: bool
    is_eligible: bool
    status: str
    created_at: datetime
    updated_at: datetime


class MetaCapabilitiesView(BaseModel):
    """Computed capability matrix for a Meta connection (Section 8.4)."""

    model_config = ConfigDict(extra="forbid")

    connection_id: UUID
    status: MetaConnectionStatus
    granted_scopes: list[str]
    capabilities: dict[str, bool]
    missing_permissions_by_capability: dict[str, list[str]] = Field(default_factory=dict)
    reconnect_required: bool = False
    reconnect_reason: str | None = None


class MetaConnectionView(BaseModel):
    """Serialized MetaConnection response — NEVER exposes raw tokens."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    connection_type: MetaConnectionType
    meta_business_id: str | None = None
    config_id: str
    token_configured: bool
    token_type: str
    granted_scopes: list[str]
    instagram_account_ids: list[str]
    page_ids: list[str]
    ad_account_ids: list[str]
    capabilities: dict[str, bool]
    status: MetaConnectionStatus
    last_verified_at: datetime | None = None
    expires_at: datetime | None = None
    reconnect_required_at: datetime | None = None
    reconnect_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class MetaWebhookReceipt(BaseModel):
    """Result of verified inbound Meta webhook ingestion."""

    model_config = ConfigDict(extra="forbid")

    accepted: bool
    deduplicated: bool
    provider_event_id: str
    affected_connection_ids: list[UUID] = Field(default_factory=list)
