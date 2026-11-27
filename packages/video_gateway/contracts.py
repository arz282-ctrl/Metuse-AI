"""Frozen Phase 3 contracts for the Unified Video Generation Gateway.

Matches IMPLEMENTATION.md Sections 6.2, 6.3, 6.4, 6.6, and 11.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.shared.enums import (
    AssetKind,
    AssetSourceType,
    VideoFailureCategory,
    VideoGenerationMode,
    VideoJobStatus,
)


class VideoAudioConfig(BaseModel):
    """Normalized audio options for video generation requests."""

    model_config = ConfigDict(extra="forbid")

    generate: bool = False
    voiceover_asset_id: UUID | None = None


class VideoModelCapability(BaseModel):
    """Capability descriptor advertised by a video provider model (Section 6.6)."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    modes: list[VideoGenerationMode]
    aspect_ratios: list[str]
    durations_seconds: list[int]
    resolutions: list[str] = Field(default_factory=lambda: ["720p", "1080p"])
    max_reference_images: int = Field(default=0, ge=0)
    audio_generation: bool = False
    webhook_support: bool = False
    regions: list[str] = Field(default_factory=lambda: ["global"])
    cost_per_second_usd: Decimal = Field(default=Decimal("0.000000"), ge=Decimal("0"))
    quality_tier: Literal["standard", "high"] = "standard"
    enabled: bool = False


class VideoProviderDescriptor(BaseModel):
    """Summary of a registered video provider and its availability."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    display_name: str
    enabled: bool
    webhook_support: bool
    models: list[VideoModelCapability] = Field(default_factory=list)


class NormalizedVideoRequest(BaseModel):
    """Provider-independent video generation request contract (Section 6.3)."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    client_id: UUID | None = None
    project_id: UUID | None = None
    mode: VideoGenerationMode
    prompt: str = Field(min_length=3, max_length=4000)
    reference_asset_ids: list[UUID] = Field(default_factory=list)
    aspect_ratio: str = Field(default="9:16", min_length=3, max_length=16)
    duration_seconds: int = Field(default=5, ge=1, le=60)
    resolution: str = Field(default="720p", min_length=2, max_length=32)
    audio: VideoAudioConfig = Field(default_factory=VideoAudioConfig)
    brand_constraints: dict[str, Any] = Field(default_factory=dict)
    provider_preference: str = Field(default="auto", min_length=1, max_length=64)
    model_preference: str | None = Field(default=None, max_length=128)
    quality_preference: Literal["lowest_cost", "high_quality"] = "lowest_cost"
    idempotency_key: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_mode_references(self) -> NormalizedVideoRequest:
        if self.mode == VideoGenerationMode.IMAGE_TO_VIDEO and not self.reference_asset_ids:
            raise ValueError("image_to_video requires at least one reference_asset_id")
        return self


class ProviderReferenceImage(BaseModel):
    """Short-lived signed reference image passed by the gateway to a video provider."""

    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    signed_url: str
    mime_type: str
    filename: str


class ProviderSubmissionRequest(BaseModel):
    """Normalized provider-bound submission payload (never contains raw credentials)."""

    model_config = ConfigDict(extra="forbid")

    internal_job_id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    mode: VideoGenerationMode
    model: str
    prompt: str
    aspect_ratio: str
    duration_seconds: int
    resolution: str
    audio_generate: bool = False
    reference_images: list[ProviderReferenceImage] = Field(default_factory=list)
    brand_constraints: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class ProviderSubmissionResult(BaseModel):
    """Normalized response after submitting a job to a video provider."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    provider_job_id: str
    status: VideoJobStatus = VideoJobStatus.SUBMITTED
    progress_pct: int = Field(default=0, ge=0, le=100)
    estimated_cost_usd: Decimal | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderPollResult(BaseModel):
    """Normalized status snapshot returned from provider polling or webhook parsing."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    provider_job_id: str
    status: VideoJobStatus
    progress_pct: int = Field(default=0, ge=0, le=100)
    output_bytes: bytes | None = None
    output_url: str | None = None
    output_mime_type: str = "video/mp4"
    output_filename: str | None = None
    duration_seconds: Decimal | None = None
    width: int | None = None
    height: int | None = None
    actual_cost_usd: Decimal | None = None
    failure_category: VideoFailureCategory | None = None
    provider_error_code: str | None = None
    error_message: str | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderCancelResult(BaseModel):
    """Normalized cancellation result from a video provider."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    provider_job_id: str
    cancelled: bool
    status: VideoJobStatus = VideoJobStatus.CANCELLED
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class WebhookVerificationResult(BaseModel):
    """Verified and parsed inbound provider webhook event."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    provider_event_id: str
    provider_job_id: str
    poll_result: ProviderPollResult


@runtime_checkable
class VideoProvider(Protocol):
    """Unified provider adapter interface (IMPLEMENTATION.md Section 6.2)."""

    @property
    def provider_id(self) -> str: ...

    @property
    def display_name(self) -> str: ...

    @property
    def is_enabled(self) -> bool: ...

    @property
    def supports_webhooks(self) -> bool: ...

    def capabilities(self) -> list[VideoModelCapability]: ...

    def create_text_to_video(
        self, request: ProviderSubmissionRequest
    ) -> ProviderSubmissionResult: ...

    def create_image_to_video(
        self, request: ProviderSubmissionRequest
    ) -> ProviderSubmissionResult: ...

    def get_job(self, provider_job_id: str) -> ProviderPollResult: ...

    def cancel_job(self, provider_job_id: str) -> ProviderCancelResult: ...

    def verify_and_parse_webhook(
        self,
        *,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> WebhookVerificationResult: ...


class VideoJobView(BaseModel):
    """Serialized API response view for a video generation job."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    project_id: UUID | None = None
    actor_user_id: UUID
    mode: VideoGenerationMode
    provider: str
    model: str
    provider_job_id: str | None = None
    status: VideoJobStatus
    progress_pct: int
    idempotency_key: str | None = None
    prompt: str
    aspect_ratio: str
    duration_seconds: int
    resolution: str
    provider_preference: str
    input_asset_ids: list[UUID] = Field(default_factory=list)
    output_asset_ids: list[UUID] = Field(default_factory=list)
    failure_category: VideoFailureCategory | None = None
    provider_error_code: str | None = None
    error_message: str | None = None
    poll_attempts: int = 0
    next_poll_at: datetime | None = None
    estimated_cost_usd: Decimal | None = None
    actual_cost_usd: Decimal | None = None
    submitted_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProjectView(BaseModel):
    """Serialized API response view for a tenant-owned project."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    name: str
    slug: str
    status: str
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime


class AssetView(BaseModel):
    """Serialized API response view for a tenant-owned private object storage asset."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    project_id: UUID | None = None
    asset_kind: AssetKind
    source_type: AssetSourceType
    filename: str
    mime_type: str
    size_bytes: int
    storage_bucket: str
    storage_key: str
    duration_seconds: Decimal | None = None
    width: int | None = None
    height: int | None = None
    sha256: str | None = None
    status: str
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime
