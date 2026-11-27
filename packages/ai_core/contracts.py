"""Typed Pydantic v2 contracts for Phase 2 worker envelopes, structured outputs, and usage."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from packages.shared.enums import WorkerJobStatus, WorkerTaskType
from packages.shared.scrubbing import scrub_secrets


class WorkerJobEnvelope(BaseModel):
    """Canonical worker input envelope (Section 4.5)."""

    job_id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    actor_user_id: UUID
    task_type: WorkerTaskType
    input_asset_ids: list[UUID] = Field(default_factory=list)
    brand_profile_id: UUID | None = None
    skill_id: str
    skill_version: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    requested_output_schema: str


class WorkerUsageOut(BaseModel):
    """Model usage and estimated cost record attached to every worker result."""

    provider: str
    model: str
    input_units: int = Field(ge=0)
    output_units: int = Field(ge=0)
    estimated_cost: Decimal | None = None


class WorkerCitation(BaseModel):
    """Source citation for evidence-backed research findings."""

    url: str = Field(min_length=8)
    title: str = Field(min_length=1)
    published_date: str | None = None
    claim: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    is_verified: bool = True


class WorkerArtifact(BaseModel):
    """Schema-tagged structured artifact produced by a specialist worker."""

    artifact_type: str
    schema_id: str
    data: dict[str, Any]


class WorkerResult(BaseModel):
    """Canonical worker output contract (Section 4.5)."""

    job_id: UUID
    status: Literal["succeeded", "failed", "needs_review"]
    artifacts: list[WorkerArtifact] = Field(default_factory=list)
    citations: list[WorkerCitation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    usage: WorkerUsageOut


# --- Versioned Specialist Output Schemas ---


class ResearchFinding(BaseModel):
    claim: str = Field(min_length=1)
    source_url: str = Field(min_length=8)
    source_title: str = Field(min_length=1)
    published_date: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class ResearchInference(BaseModel):
    hypothesis: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class ResearchReportArtifact(BaseModel):
    """Validated output schema: metuse.research_report.v1."""

    topic: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    verified_findings: list[ResearchFinding] = Field(min_length=1)
    inferences: list[ResearchInference] = Field(default_factory=list)
    content_gaps: list[str] = Field(default_factory=list)
    confidence_overall: float = Field(ge=0.0, le=1.0)


class WritingPackageArtifact(BaseModel):
    """Validated output schema: metuse.writing_package.v1."""

    topic: str = Field(min_length=1)
    brand_name: str = Field(min_length=1)
    content_brief: str = Field(min_length=1)
    hooks: list[str] = Field(min_length=1)
    script: str = Field(min_length=1)
    captions: list[str] = Field(min_length=1)
    ctas: list[str] = Field(min_length=1)


class SeoMetadata(BaseModel):
    title_tag: str = Field(min_length=1, max_length=70)
    meta_description: str = Field(min_length=1, max_length=170)
    slug: str = Field(min_length=1, max_length=80)


class AeoAnswerPassage(BaseModel):
    question: str = Field(min_length=1)
    direct_answer: str = Field(min_length=1)
    entities: list[str] = Field(default_factory=list)


class SeoAeoPackageArtifact(BaseModel):
    """Validated output schema: metuse.seo_aeo_package.v1."""

    topic: str = Field(min_length=1)
    primary_keyword: str = Field(min_length=1)
    secondary_keywords: list[str] = Field(default_factory=list)
    search_intent: str = Field(min_length=1)
    seo_metadata: SeoMetadata
    aeo_answer_passages: list[AeoAnswerPassage] = Field(min_length=1)
    internal_link_suggestions: list[str] = Field(default_factory=list)


class StoryboardScene(BaseModel):
    scene_number: int = Field(ge=1)
    start_sec: int = Field(ge=0)
    end_sec: int = Field(gt=0)
    visual_direction: str = Field(min_length=1)
    on_screen_text: str = Field(min_length=1)
    voiceover: str = Field(min_length=1)


class VideoPromptSpec(BaseModel):
    scene_number: int = Field(ge=1)
    mode: Literal["text_to_video", "image_to_video"]
    prompt: str = Field(min_length=1)
    duration_seconds: int = Field(ge=3, le=15)


class MediaPlanArtifact(BaseModel):
    """Validated output schema: metuse.media_plan.v1 (playbook only; never renders)."""

    topic: str = Field(min_length=1)
    creative_brief: str = Field(min_length=1)
    aspect_ratio: Literal["9:16", "16:9", "1:1", "4:5"] = "9:16"
    target_duration_seconds: int = Field(ge=5, le=180)
    storyboard: list[StoryboardScene] = Field(min_length=1)
    video_prompts: list[VideoPromptSpec] = Field(min_length=1)
    continuity_constraints: list[str] = Field(default_factory=list)
    cta_placement: str = Field(min_length=1)
    qa_checklist: list[str] = Field(min_length=1)


# --- API Output DTOs ---


class WorkerJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    actor_user_id: UUID
    brand_profile_id: UUID | None = None
    task_type: WorkerTaskType
    skill_id: str
    skill_version: str
    status: WorkerJobStatus
    idempotency_key: str | None = None
    requested_output_schema: str
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    @field_validator("input_payload", "output_payload", mode="before")
    @classmethod
    def _scrub_payloads(cls, value: Any) -> Any:
        if value is None:
            return None
        return scrub_secrets(value)


class UsageBreakdownItem(BaseModel):
    provider: str
    model: str
    capability: str
    total_input_units: int
    total_output_units: int
    total_estimated_cost_usd: Decimal
    run_count: int


class UsageSummaryOut(BaseModel):
    workspace_id: UUID
    client_id: UUID | None = None
    billing_period: str
    total_input_units: int
    total_output_units: int
    total_estimated_cost_usd: Decimal
    cost_disclaimer: str = "Estimated provider usage cost only; not a provider invoice."
    breakdown: list[UsageBreakdownItem] = Field(default_factory=list)


class WorkspaceBudgetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    client_id: UUID | None = None
    monthly_unit_limit: int | None = None
    monthly_cost_limit_usd: Decimal | None = None
    provider_kill_switch: bool = False
    created_at: datetime
    updated_at: datetime
