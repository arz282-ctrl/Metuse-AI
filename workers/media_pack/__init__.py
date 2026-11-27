"""Media-pack creative production playbook worker (skills/media-pack; never renders)."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from packages.ai_core.contracts import (
    MediaPlanArtifact,
    WorkerArtifact,
    WorkerJobEnvelope,
    WorkerResult,
    WorkerUsageOut,
)
from packages.ai_core.prompts import compile_worker_prompt
from packages.muse_adapter import MuseAdapter, MuseStructuredRequest
from packages.shared.enums import ErrorCode, MuseCapability
from packages.shared.errors import ValidationDomainError
from packages.skill_runtime import SkillDocument


def run_media_pack_worker(
    *,
    envelope: WorkerJobEnvelope,
    topic: str,
    brand_context: dict[str, Any],
    skill_doc: SkillDocument,
    muse_adapter: MuseAdapter,
) -> tuple[WorkerResult, int, str]:
    """Produce a validated storyboard, video-prompt plan, and QA checklist (no rendering)."""
    compiled = compile_worker_prompt(
        skill_doc=skill_doc,
        task_type=envelope.task_type,
        topic=topic,
        requested_output_schema=envelope.requested_output_schema,
        brand_context=brand_context,
        constraints=envelope.constraints,
    )
    response = muse_adapter.invoke_structured(
        MuseStructuredRequest(
            capability=MuseCapability.REASONING_STRUCTURED_OUTPUT,
            system_policy=compiled.system_policy,
            user_prompt=compiled.user_prompt,
            output_schema_id=envelope.requested_output_schema,
            prompt_version=compiled.prompt_version,
            metadata={
                "topic": topic,
                "brand_name": brand_context.get("brand_name"),
                "target_audience": brand_context.get("target_audience"),
            },
        )
    )
    try:
        media_plan = MediaPlanArtifact.model_validate(response.content_json)
    except ValidationError as exc:
        raise ValidationDomainError(
            "Media-pack worker output failed schema validation",
            code=ErrorCode.SCHEMA_VALIDATION_FAILED,
            details={"schema": envelope.requested_output_schema, "errors": exc.errors()},
        ) from exc

    result = WorkerResult(
        job_id=envelope.job_id,
        status="succeeded",
        artifacts=[
            WorkerArtifact(
                artifact_type="media_plan",
                schema_id=envelope.requested_output_schema,
                data=media_plan.model_dump(mode="json"),
            )
        ],
        citations=[],
        warnings=[
            "Playbook output only: video generation runs via video_gateway (Phase 3) "
            "and rendering via media_engine (Phase 4)."
        ],
        usage=WorkerUsageOut(
            provider=response.provider,
            model=response.model,
            input_units=response.input_units,
            output_units=response.output_units,
            estimated_cost=response.estimated_cost_usd,
        ),
    )
    return result, response.latency_ms, response.prompt_version
