"""Phase 2 unit tests: skill registry/router, Meta Muse adapter, workers, schema & secret safety."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from packages.ai_core import (
    WorkerJobEnvelope,
    compile_worker_prompt,
)
from packages.muse_adapter import (
    LiveMuseAdapter,
    MockMuseAdapter,
    MuseCapabilityRegistry,
    MuseStructuredRequest,
    MuseStructuredResponse,
)
from packages.shared.config import BaseAppSettings
from packages.shared.enums import ErrorCode, MuseCapability, WorkerTaskType
from packages.shared.errors import (
    CapabilityNotSupportedError,
    ProviderDisabledError,
    ValidationDomainError,
)
from packages.skill_runtime import SkillRegistry, route_task
from workers.media_pack import run_media_pack_worker
from workers.research import run_research_worker
from workers.writing_seo_aeo import run_writing_seo_aeo_worker

pytestmark = pytest.mark.unit


def test_skill_registry_loads_only_three_allowlisted_packs_as_untrusted_data() -> None:
    registry = SkillRegistry()
    skills = registry.list_skills()
    assert [s.skill_id for s in skills] == [
        "marketing-seo-pack",
        "media-pack",
        "research-pack",
    ]
    for doc in skills:
        assert len(doc.content_sha256) == 64
        framed = doc.as_untrusted_reference_block()
        assert framed.startswith("<untrusted_skill_reference ")
        assert framed.endswith("</untrusted_skill_reference>")

    # Unapproved packs must be rejected
    for forbidden in ("starter-pack", "devops-pack", "webdev-pack", "../etc/passwd"):
        with pytest.raises(ValidationDomainError, match="allowlist"):
            registry.get_skill(forbidden)


def test_deterministic_router_maps_all_four_tasks_and_rejects_unknown() -> None:
    assert route_task(WorkerTaskType.RESEARCH).skill_id == "research-pack"
    assert route_task(WorkerTaskType.WRITING).skill_id == "marketing-seo-pack"
    assert route_task(WorkerTaskType.SEO_AEO).skill_id == "marketing-seo-pack"
    assert route_task(WorkerTaskType.MEDIA_PLAN).skill_id == "media-pack"

    with pytest.raises(ValidationDomainError) as exc:
        route_task("publish_to_instagram")
    assert exc.value.code == ErrorCode.INVALID_TASK_TYPE


def test_muse_capability_registry_rejects_video_generation() -> None:
    registry = MuseCapabilityRegistry()
    spec = registry.resolve_capability(MuseCapability.REASONING_STRUCTURED_OUTPUT)
    assert spec.provider == "meta_muse"
    assert spec.privacy_preserving_default is True

    # Muse is explicitly NOT a video generator
    with pytest.raises(CapabilityNotSupportedError, match="video gateway"):
        registry.resolve_capability("video_generation")


def test_live_muse_adapter_retries_429_honors_privacy_and_never_leaks_key() -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert req.headers.get("X-Meta-Privacy-Mode") == "standard_no_training"
        assert req.headers.get("Authorization") == "Bearer muse-super-secret-key-999"
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"}, json={"error": "rate"})
        return httpx.Response(
            200,
            json={
                "model": "muse-spark-reasoning",
                "choices": [{"message": {"content": json.dumps({"ok": True})}}],
                "usage": {"prompt_tokens": 200, "completion_tokens": 100},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = LiveMuseAdapter(
        base_url="https://api.meta.ai/v1",
        api_key="muse-super-secret-key-999",
        max_retries=2,
        http_client=client,
    )
    resp = adapter.invoke_structured(
        MuseStructuredRequest(
            capability=MuseCapability.REASONING_TOOL_USE,
            system_policy="policy",
            user_prompt="prompt",
            output_schema_id="test.v1",
        )
    )
    assert calls == 2
    assert resp.provider == "meta_muse"
    assert resp.input_units == 200
    assert resp.output_units == 100
    assert resp.estimated_cost_usd > Decimal("0")

    # Kill switch blocks calls immediately
    disabled = MockMuseAdapter(kill_switch=True)
    with pytest.raises(ProviderDisabledError):
        disabled.invoke_structured(
            MuseStructuredRequest(
                capability=MuseCapability.REASONING_TOOL_USE,
                system_policy="p",
                user_prompt="u",
                output_schema_id="test.v1",
            )
        )

    # Credential safety (Acceptance #18): MUSE_API_KEY must never appear in safe_summary
    settings = BaseAppSettings(
        _env_file=None,
        muse_api_key=SecretStr("muse-super-secret-key-999"),
    )
    summary = settings.safe_summary()
    assert summary["muse_api_key_configured"] is True
    assert "muse-super-secret-key-999" not in str(summary)


def test_all_three_specialist_workers_produce_schema_valid_outputs_and_reject_bad_json() -> None:
    registry = SkillRegistry()
    adapter = MockMuseAdapter()
    ws_id, client_id, user_id = uuid4(), uuid4(), uuid4()
    brand_ctx = {"brand_name": "Rareware Coffee", "target_audience": "specialty coffee buyers"}

    # Acceptance #7: Research worker returns evidence-backed report with URLs & confidence
    res_route = route_task(WorkerTaskType.RESEARCH)
    res_env = WorkerJobEnvelope(
        job_id=uuid4(),
        workspace_id=ws_id,
        client_id=client_id,
        actor_user_id=user_id,
        task_type=WorkerTaskType.RESEARCH,
        skill_id=res_route.skill_id,
        skill_version=res_route.skill_version,
        requested_output_schema=res_route.requested_output_schema,
    )
    res_result, report, _, _ = run_research_worker(
        envelope=res_env,
        topic="Cold brew reels growth",
        brand_context=brand_ctx,
        skill_doc=registry.get_skill(res_route.skill_id),
        muse_adapter=adapter,
    )
    assert res_result.status == "succeeded"
    assert len(res_result.citations) >= 1
    assert res_result.citations[0].url.startswith("https://")
    assert 0.0 <= report.confidence_overall <= 1.0
    assert res_result.usage.input_units > 0

    # Acceptance #8: Writing & SEO/AEO worker returns structured copy, metadata, and AEO passages
    for task_type in (WorkerTaskType.WRITING, WorkerTaskType.SEO_AEO):
        route = route_task(task_type)
        env = WorkerJobEnvelope(
            job_id=uuid4(),
            workspace_id=ws_id,
            client_id=client_id,
            actor_user_id=user_id,
            task_type=task_type,
            skill_id=route.skill_id,
            skill_version=route.skill_version,
            requested_output_schema=route.requested_output_schema,
        )
        out, _, _ = run_writing_seo_aeo_worker(
            envelope=env,
            topic="Specialty cold brew",
            brand_context=brand_ctx,
            skill_doc=registry.get_skill(route.skill_id),
            muse_adapter=adapter,
        )
        assert out.status == "succeeded"
        assert len(out.artifacts) == 1
        if task_type == WorkerTaskType.SEO_AEO:
            assert "seo_metadata" in out.artifacts[0].data
            assert len(out.artifacts[0].data["aeo_answer_passages"]) >= 1

    # Media-pack worker returns playbook only (storyboard + video prompts + QA checklist)
    mp_route = route_task(WorkerTaskType.MEDIA_PLAN)
    mp_env = WorkerJobEnvelope(
        job_id=uuid4(),
        workspace_id=ws_id,
        client_id=client_id,
        actor_user_id=user_id,
        task_type=WorkerTaskType.MEDIA_PLAN,
        skill_id=mp_route.skill_id,
        skill_version=mp_route.skill_version,
        requested_output_schema=mp_route.requested_output_schema,
    )
    mp_out, _, _ = run_media_pack_worker(
        envelope=mp_env,
        topic="Espresso launch reel",
        brand_context=brand_ctx,
        skill_doc=registry.get_skill(mp_route.skill_id),
        muse_adapter=adapter,
    )
    assert mp_out.status == "succeeded"
    assert len(mp_out.artifacts[0].data["storyboard"]) >= 1
    assert len(mp_out.artifacts[0].data["video_prompts"]) >= 1
    assert any("Playbook output only" in w for w in mp_out.warnings)

    # Prompt compiler scrubs secrets from constraints and frames untrusted data
    compiled = compile_worker_prompt(
        skill_doc=registry.get_skill("research-pack"),
        task_type=WorkerTaskType.RESEARCH,
        topic="Test",
        requested_output_schema="metuse.research_report.v1",
        constraints={"api_key": "sk_live_should_never_appear_123456"},
    )
    assert "sk_live_should_never_appear_123456" not in compiled.user_prompt
    assert "<untrusted_skill_reference" in compiled.user_prompt
    assert "<untrusted_user_input>" in compiled.user_prompt

    # Malformed provider JSON is rejected with SCHEMA_VALIDATION_FAILED
    class _BadMuse:
        def list_capabilities(self) -> list[Any]:
            return []

        def invoke_structured(self, request: MuseStructuredRequest) -> MuseStructuredResponse:
            return MuseStructuredResponse(
                provider="bad",
                model="bad",
                capability=request.capability,
                content_json={"unexpected": "shape"},
                input_units=10,
                output_units=10,
                estimated_cost_usd=Decimal("0.0001"),
                latency_ms=1,
                prompt_version="v1",
                schema_version=request.output_schema_id,
            )

    with pytest.raises(ValidationDomainError) as bad_exc:
        run_research_worker(
            envelope=res_env,
            topic="Broken output test",
            brand_context=brand_ctx,
            skill_doc=registry.get_skill("research-pack"),
            muse_adapter=_BadMuse(),
        )
    assert bad_exc.value.code == ErrorCode.SCHEMA_VALIDATION_FAILED
