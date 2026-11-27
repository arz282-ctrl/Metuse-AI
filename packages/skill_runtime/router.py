"""Deterministic task router mapping task_type to the narrowest approved worker and skill."""

from __future__ import annotations

from dataclasses import dataclass

from packages.shared.enums import ErrorCode, MuseCapability, WorkerTaskType
from packages.shared.errors import ValidationDomainError


@dataclass(frozen=True)
class RouteDecision:
    """Deterministic routing assignment for a single worker job."""

    task_type: WorkerTaskType
    worker_name: str
    skill_id: str
    skill_version: str
    requested_output_schema: str
    required_capability: MuseCapability


_ROUTING_TABLE: dict[WorkerTaskType, RouteDecision] = {
    WorkerTaskType.RESEARCH: RouteDecision(
        task_type=WorkerTaskType.RESEARCH,
        worker_name="workers.research",
        skill_id="research-pack",
        skill_version="1",
        requested_output_schema="metuse.research_report.v1",
        required_capability=MuseCapability.REASONING_TOOL_USE,
    ),
    WorkerTaskType.WRITING: RouteDecision(
        task_type=WorkerTaskType.WRITING,
        worker_name="workers.writing_seo_aeo",
        skill_id="marketing-seo-pack",
        skill_version="1",
        requested_output_schema="metuse.writing_package.v1",
        required_capability=MuseCapability.REASONING_STRUCTURED_OUTPUT,
    ),
    WorkerTaskType.SEO_AEO: RouteDecision(
        task_type=WorkerTaskType.SEO_AEO,
        worker_name="workers.writing_seo_aeo",
        skill_id="marketing-seo-pack",
        skill_version="1",
        requested_output_schema="metuse.seo_aeo_package.v1",
        required_capability=MuseCapability.REASONING_STRUCTURED_OUTPUT,
    ),
    WorkerTaskType.MEDIA_PLAN: RouteDecision(
        task_type=WorkerTaskType.MEDIA_PLAN,
        worker_name="workers.media_pack",
        skill_id="media-pack",
        skill_version="1",
        requested_output_schema="metuse.media_plan.v1",
        required_capability=MuseCapability.REASONING_STRUCTURED_OUTPUT,
    ),
}


def route_task(task_type: WorkerTaskType | str) -> RouteDecision:
    """Route a task to the narrowest specialist worker; reject unknown/ambiguous task types."""
    if isinstance(task_type, WorkerTaskType):
        return _ROUTING_TABLE[task_type]

    cleaned = str(task_type).strip().lower()
    try:
        resolved = WorkerTaskType(cleaned)
    except ValueError as exc:
        raise ValidationDomainError(
            f"Unsupported or ambiguous task_type '{task_type}'",
            code=ErrorCode.INVALID_TASK_TYPE,
            details={"supported_task_types": [t.value for t in WorkerTaskType]},
        ) from exc
    return _ROUTING_TABLE[resolved]
