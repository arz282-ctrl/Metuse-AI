"""Frozen Phase 4 contracts for Cloud Editing, Media QA, and Version-Bound Approvals.

Matches IMPLEMENTATION.md Sections 7.3, 7.5, 9, 10, and 11.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.shared.enums import ApprovalStatus, ApprovalType, RenderJobStatus, RenderKind


class CanvasSpec(BaseModel):
    """Output canvas dimensions and frame rate (Section 7.3)."""

    model_config = ConfigDict(extra="forbid")

    width: int = Field(default=1080, ge=240, le=3840)
    height: int = Field(default=1920, ge=240, le=3840)
    fps: int = Field(default=30, ge=15, le=60)


class VideoClip(BaseModel):
    """Single video segment placed on a timeline video track."""

    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    source_in_ms: int = Field(default=0, ge=0)
    source_out_ms: int = Field(gt=0)
    timeline_start_ms: int = Field(default=0, ge=0)
    fit: Literal["cover", "contain", "fill"] = "cover"
    crop: dict[str, int] = Field(default_factory=dict)
    transition_in: Literal["fade", "cut"] | None = None
    transition_out: Literal["fade", "cut"] | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> VideoClip:
        if self.source_out_ms <= self.source_in_ms:
            raise ValueError("source_out_ms must be strictly greater than source_in_ms")
        return self

    @property
    def duration_ms(self) -> int:
        return self.source_out_ms - self.source_in_ms


class CaptionItem(BaseModel):
    """Timed caption or CTA overlay with normalized safe-margin coordinates (0..1)."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=240)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    x_norm: float = Field(default=0.5, ge=0.0, le=1.0)
    y_norm: float = Field(default=0.75, ge=0.0, le=1.0)
    is_cta: bool = False

    @model_validator(mode="after")
    def validate_timing(self) -> CaptionItem:
        if self.end_ms <= self.start_ms:
            raise ValueError("Caption end_ms must be strictly greater than start_ms")
        return self


class AudioClip(BaseModel):
    """Single audio clip placed on an audio track."""

    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    source_in_ms: int = Field(default=0, ge=0)
    source_out_ms: int = Field(gt=0)
    timeline_start_ms: int = Field(default=0, ge=0)
    gain_db: float = Field(default=0.0, ge=-30.0, le=12.0)

    @model_validator(mode="after")
    def validate_bounds(self) -> AudioClip:
        if self.source_out_ms <= self.source_in_ms:
            raise ValueError("Audio clip source_out_ms must exceed source_in_ms")
        return self


class TimelineTrack(BaseModel):
    """Unified Section 7.3 track representation (video, text, or audio)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    type: Literal["video", "text", "audio"]
    clips: list[VideoClip | AudioClip] = Field(default_factory=list)
    items: list[CaptionItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_track_contents(self) -> TimelineTrack:
        if self.type == "video":
            for clip in self.clips:
                if not isinstance(clip, VideoClip):
                    raise ValueError("Video track clips must be VideoClip objects")
        elif self.type == "audio":
            for clip in self.clips:
                if not isinstance(clip, AudioClip):
                    raise ValueError("Audio track clips must be AudioClip objects")
        elif self.type == "text" and self.clips:
            raise ValueError("Text track must use 'items' rather than 'clips'")
        return self


class ExportSpec(BaseModel):
    """Allowlisted output container and codec specification (Section 7.3)."""

    model_config = ConfigDict(extra="forbid")

    container: Literal["mp4"] = "mp4"
    video_codec: Literal["h264"] = "h264"
    audio_codec: Literal["aac"] = "aac"
    target_bitrate: str | None = Field(default=None, pattern=r"^[0-9]+[kKmM]$")


class TimelinePlan(BaseModel):
    """Provider-independent editing timeline plan (Section 7.3)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    project_id: UUID
    canvas: CanvasSpec = Field(default_factory=CanvasSpec)
    tracks: list[TimelineTrack] = Field(min_length=1)
    export: ExportSpec = Field(default_factory=ExportSpec)

    @model_validator(mode="after")
    def validate_has_video_clip(self) -> TimelinePlan:
        video_clips = self.video_clips()
        if not video_clips:
            raise ValueError("TimelinePlan must contain at least one video clip")
        return self

    def video_clips(self) -> list[VideoClip]:
        clips: list[VideoClip] = []
        for track in self.tracks:
            if track.type == "video":
                for c in track.clips:
                    if isinstance(c, VideoClip):
                        clips.append(c)
        return clips

    def audio_clips(self) -> list[AudioClip]:
        clips: list[AudioClip] = []
        for track in self.tracks:
            if track.type == "audio":
                for c in track.clips:
                    if isinstance(c, AudioClip):
                        clips.append(c)
        return clips

    def caption_items(self) -> list[CaptionItem]:
        items: list[CaptionItem] = []
        for track in self.tracks:
            if track.type == "text":
                items.extend(track.items)
        return items

    def total_duration_ms(self) -> int:
        end_ms = 0
        for clip in self.video_clips():
            end_ms = max(end_ms, clip.timeline_start_ms + clip.duration_ms)
        return end_ms

    def referenced_asset_ids(self) -> list[UUID]:
        seen: list[UUID] = []
        for clip in self.video_clips():
            if clip.asset_id not in seen:
                seen.append(clip.asset_id)
        for audio_clip in self.audio_clips():
            if audio_clip.asset_id not in seen:
                seen.append(audio_clip.asset_id)
        return seen


class MediaProbeResult(BaseModel):
    """Verified structural & codec probe output for a media file."""

    model_config = ConfigDict(extra="forbid")

    decodable: bool
    playable: bool
    container: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    duration_ms: int | None = None
    fps: Decimal | None = None
    width: int | None = None
    height: int | None = None
    bitrate_bps: int | None = None
    has_audio: bool = False
    audio_peak_db: float | None = None
    black_frame_ratio: float = 0.0
    frozen_frame_ratio: float = 0.0
    size_bytes: int = 0
    error: str | None = None


class MediaQaCheck(BaseModel):
    """Single deterministic automated Media QA check result (Section 7.5)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    detail: str


class MediaQaReport(BaseModel):
    """Full Section 7.5 automated Media QA verdict."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    checks: list[MediaQaCheck]
    failed_checks: list[str] = Field(default_factory=list)


class UploadUrlResponse(BaseModel):
    """Presigned upload ticket for private tenant media ingestion."""

    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    url: str
    method: str
    headers: dict[str, str]
    bucket: str
    key: str
    expires_in: int


class MediaMetadataView(BaseModel):
    """Serialized view of verified media probe metadata."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    asset_id: UUID
    proxy_asset_id: UUID | None = None
    container: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    duration_ms: int | None = None
    fps: Decimal | None = None
    width: int | None = None
    height: int | None = None
    bitrate_bps: int | None = None
    has_audio: bool = False
    created_at: datetime
    updated_at: datetime


class EditingPlanView(BaseModel):
    """Serialized view of a versioned editing timeline plan."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    project_id: UUID
    version_number: int
    schema_version: str
    plan: TimelinePlan
    plan_sha256: str
    created_by_user_id: UUID
    created_at: datetime


class RenderJobView(BaseModel):
    """Serialized view of an isolated cloud render job."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    project_id: UUID
    editing_plan_id: UUID
    actor_user_id: UUID
    render_kind: RenderKind
    status: RenderJobStatus
    idempotency_key: str | None = None
    renderer: str
    command_manifest: list[str] = Field(default_factory=list)
    output_asset_id: UUID | None = None
    qa_passed: bool | None = None
    qa_report: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int | None = None
    cpu_time_ms: int = 0
    estimated_cost_usd: Decimal | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CreativeVersionView(BaseModel):
    """Serialized view of an immutable versioned render artifact."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    project_id: UUID
    editing_plan_id: UUID
    render_job_id: UUID
    output_asset_id: UUID
    version_number: int
    render_kind: RenderKind
    content_sha256: str
    qa_passed: bool
    qa_report: dict[str, Any] = Field(default_factory=dict)
    is_approved: bool
    created_by_user_id: UUID
    created_at: datetime


class ApprovalView(BaseModel):
    """Serialized view of a version-bound human approval record (Section 9)."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    project_id: UUID | None = None
    creative_version_id: UUID | None = None
    approval_type: ApprovalType
    target_type: str
    target_id: UUID
    target_version_sha256: str
    status: ApprovalStatus
    requested_by_user_id: UUID
    decided_by_user_id: UUID | None = None
    decision_notes: str | None = None
    invalidated_reason: str | None = None
    decided_at: datetime | None = None
    invalidated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class RevisionRequestView(BaseModel):
    """Serialized view of a creative revision request."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    project_id: UUID
    creative_version_id: UUID
    approval_id: UUID | None = None
    requested_by_user_id: UUID
    notes: str
    timecode_ms: int | None = None
    resolved: bool
    created_at: datetime
