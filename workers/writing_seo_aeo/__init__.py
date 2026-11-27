"""Writing, copywriting, SEO & AEO specialist worker (skills/marketing-seo-pack)."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from packages.ai_core.contracts import (
    SeoAeoPackageArtifact,
    WorkerArtifact,
    WorkerJobEnvelope,
    WorkerResult,
    WorkerUsageOut,
    WritingPackageArtifact,
)
from packages.ai_core.prompts import compile_worker_prompt
from packages.muse_adapter import MuseAdapter, MuseStructuredRequest
from packages.shared.enums import ErrorCode, MuseCapability, WorkerTaskType
from packages.shared.errors import ValidationDomainError
from packages.skill_runtime import SkillDocument


def run_writing_seo_aeo_worker(
    *,
    envelope: WorkerJobEnvelope,
    topic: str,
    brand_context: dict[str, Any],
    skill_doc: SkillDocument,
    muse_adapter: MuseAdapter,
) -> tuple[WorkerResult, int, str]:
    """Execute writing or SEO/AEO specialist worker and strictly validate versioned output."""
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
        if envelope.task_type == WorkerTaskType.SEO_AEO:
            validated_data = SeoAeoPackageArtifact.model_validate(response.content_json).model_dump(
                mode="json"
            )
            artifact_type = "seo_aeo_package"
        else:
            validated_data = WritingPackageArtifact.model_validate(
                response.content_json
            ).model_dump(mode="json")
            artifact_type = "writing_package"
    except ValidationError as exc:
        raise ValidationDomainError(
            "Writing/SEO/AEO worker output failed schema validation",
            code=ErrorCode.SCHEMA_VALIDATION_FAILED,
            details={"schema": envelope.requested_output_schema, "errors": exc.errors()},
        ) from exc

    result = WorkerResult(
        job_id=envelope.job_id,
        status="succeeded",
        artifacts=[
            WorkerArtifact(
                artifact_type=artifact_type,
                schema_id=envelope.requested_output_schema,
                data=validated_data,
            )
        ],
        citations=[],
        warnings=[],
        usage=WorkerUsageOut(
            provider=response.provider,
            model=response.model,
            input_units=response.input_units,
            output_units=response.output_units,
            estimated_cost=response.estimated_cost_usd,
        ),
    )
    return result, response.latency_ms, response.prompt_version
