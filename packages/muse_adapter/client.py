"""Meta Muse OpenAI-compatible server adapter with mock implementation and 429 retry/backoff."""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

import httpx

from packages.muse_adapter.capabilities import MuseCapabilityRegistry, MuseModelSpec
from packages.shared.config import BaseAppSettings
from packages.shared.enums import MuseCapability
from packages.shared.errors import ExternalServiceError, ProviderDisabledError


@dataclass(frozen=True)
class MuseStructuredRequest:
    """Server-side structured generation request routed by capability."""

    capability: MuseCapability
    system_policy: str
    user_prompt: str
    output_schema_id: str
    prompt_version: str = "v1"
    temperature: float = 0.2
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MuseStructuredResponse:
    """Normalized model execution response with usage and estimated cost."""

    provider: str
    model: str
    capability: MuseCapability
    content_json: dict[str, Any]
    input_units: int
    output_units: int
    estimated_cost_usd: Decimal
    latency_ms: int
    prompt_version: str
    schema_version: str


class MuseAdapter(Protocol):
    """Protocol implemented by MockMuseAdapter and LiveMuseAdapter."""

    def invoke_structured(self, request: MuseStructuredRequest) -> MuseStructuredResponse: ...

    def list_capabilities(self) -> list[MuseModelSpec]: ...


class MockMuseAdapter:
    """Deterministic zero-cost Muse adapter for tests and local dev without paid keys."""

    def __init__(
        self,
        *,
        registry: MuseCapabilityRegistry | None = None,
        kill_switch: bool = False,
    ) -> None:
        self._registry = registry or MuseCapabilityRegistry()
        self._kill_switch = kill_switch

    def list_capabilities(self) -> list[MuseModelSpec]:
        return self._registry.list_models()

    def invoke_structured(self, request: MuseStructuredRequest) -> MuseStructuredResponse:
        if self._kill_switch:
            raise ProviderDisabledError("Meta Muse provider is currently disabled by kill switch")

        spec = self._registry.resolve_capability(request.capability)
        topic = str(request.metadata.get("topic") or "social growth campaign").strip()
        brand_name = str(request.metadata.get("brand_name") or "Client Brand").strip()
        audience = str(request.metadata.get("target_audience") or "growth-focused buyers").strip()

        payload = _build_mock_schema_payload(
            schema_id=request.output_schema_id,
            topic=topic,
            brand_name=brand_name,
            audience=audience,
        )
        input_units = max(120, len(request.system_policy + request.user_prompt) // 4)
        output_units = max(180, len(json.dumps(payload)) // 4)
        est_cost = self._registry.estimate_cost_usd(
            spec, input_units=input_units, output_units=output_units
        )
        return MuseStructuredResponse(
            provider="meta_muse_mock",
            model=spec.model_id,
            capability=request.capability,
            content_json=payload,
            input_units=input_units,
            output_units=output_units,
            estimated_cost_usd=est_cost,
            latency_ms=12,
            prompt_version=request.prompt_version,
            schema_version=request.output_schema_id,
        )


class LiveMuseAdapter:
    """OpenAI-compatible Meta Model API client with bounded 429 backoff and privacy defaults."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        kill_switch: bool = False,
        registry: MuseCapabilityRegistry | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.strip().rstrip("/")
        self._api_key = api_key.strip()
        self._timeout = max(1.0, float(timeout_seconds))
        self._max_retries = max(0, int(max_retries))
        self._kill_switch = kill_switch
        self._registry = registry or MuseCapabilityRegistry()
        self._http_client = http_client

    def list_capabilities(self) -> list[MuseModelSpec]:
        return self._registry.list_models()

    def invoke_structured(self, request: MuseStructuredRequest) -> MuseStructuredResponse:
        if self._kill_switch:
            raise ProviderDisabledError("Meta Muse provider is currently disabled by kill switch")
        if not self._api_key:
            raise ExternalServiceError("MUSE_API_KEY must be configured for live Meta Muse mode")

        spec = self._registry.resolve_capability(request.capability)
        endpoint = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Meta-Privacy-Mode": "standard_no_training",
        }
        body: dict[str, Any] = {
            "model": spec.model_id,
            "temperature": request.temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": request.system_policy},
                {"role": "user", "content": request.user_prompt},
            ],
        }

        client = self._http_client or httpx.Client(timeout=self._timeout)
        should_close = self._http_client is None
        started = time.monotonic()
        try:
            last_error: str = "Meta Muse request failed"
            for attempt in range(self._max_retries + 1):
                try:
                    resp = client.post(endpoint, headers=headers, json=body)
                except httpx.HTTPError as exc:
                    last_error = f"Meta Muse transport failure: {type(exc).__name__}"
                    if attempt < self._max_retries:
                        time.sleep(_backoff_seconds(attempt, retry_after=None))
                        continue
                    raise ExternalServiceError(last_error) from exc

                if resp.status_code == 429:
                    last_error = "Meta Muse rate limit exceeded (HTTP 429)"
                    if attempt < self._max_retries:
                        retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                        time.sleep(_backoff_seconds(attempt, retry_after=retry_after))
                        continue
                    raise ExternalServiceError(last_error)

                if resp.status_code >= 500:
                    last_error = f"Meta Muse upstream error (HTTP {resp.status_code})"
                    if attempt < self._max_retries:
                        time.sleep(_backoff_seconds(attempt, retry_after=None))
                        continue
                    raise ExternalServiceError(last_error)

                if resp.status_code != 200:
                    raise ExternalServiceError(
                        f"Meta Muse rejected request (HTTP {resp.status_code})"
                    )

                data = resp.json()
                raw_content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                try:
                    parsed = (
                        json.loads(raw_content) if isinstance(raw_content, str) else raw_content
                    )
                except json.JSONDecodeError as exc:
                    raise ExternalServiceError(
                        "Meta Muse returned non-JSON structured output"
                    ) from exc
                if not isinstance(parsed, dict):
                    raise ExternalServiceError("Meta Muse returned non-object JSON payload")

                usage = data.get("usage", {}) if isinstance(data, dict) else {}
                in_units = int(usage.get("prompt_tokens", 0) or 0)
                out_units = int(usage.get("completion_tokens", 0) or 0)
                latency_ms = max(1, int((time.monotonic() - started) * 1000))
                est_cost = self._registry.estimate_cost_usd(
                    spec, input_units=in_units, output_units=out_units
                )
                return MuseStructuredResponse(
                    provider="meta_muse",
                    model=str(data.get("model") or spec.model_id),
                    capability=request.capability,
                    content_json=parsed,
                    input_units=in_units,
                    output_units=out_units,
                    estimated_cost_usd=est_cost,
                    latency_ms=latency_ms,
                    prompt_version=request.prompt_version,
                    schema_version=request.output_schema_id,
                )
            raise ExternalServiceError(last_error)
        finally:
            if should_close:
                client.close()


def create_muse_adapter(
    settings: BaseAppSettings,
    *,
    http_client: httpx.Client | None = None,
) -> MuseAdapter:
    """Instantiate MockMuseAdapter or LiveMuseAdapter from BaseAppSettings."""
    mode = settings.muse_mode.strip().lower() or "mock"
    api_key = settings.muse_api_key.get_secret_value().strip()
    if mode == "live":
        return LiveMuseAdapter(
            base_url=settings.muse_base_url,
            api_key=api_key,
            timeout_seconds=settings.muse_timeout_seconds,
            max_retries=settings.muse_max_retries,
            kill_switch=settings.muse_kill_switch,
            http_client=http_client,
        )
    return MockMuseAdapter(kill_switch=settings.muse_kill_switch)


def _parse_retry_after(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        return max(0.0, min(float(raw.strip()), 10.0))
    except ValueError:
        return None


def _backoff_seconds(attempt: int, *, retry_after: float | None) -> float:
    if retry_after is not None:
        return retry_after
    base: float = float(min(0.25 * (2**attempt), 4.0))
    jitter: float = random.uniform(0.0, 0.15)  # noqa: S311
    return base + jitter


def _build_mock_schema_payload(
    *,
    schema_id: str,
    topic: str,
    brand_name: str,
    audience: str,
) -> dict[str, Any]:
    """Deterministic schema-valid mock outputs for offline/free-tier verification."""
    if schema_id == "metuse.research_report.v1":
        return {
            "topic": topic,
            "summary": (
                f"Evidence-backed niche research for {brand_name} targeting "
                f"{audience} on '{topic}'."
            ),
            "verified_findings": [
                {
                    "claim": (
                        f"Short-form vertical reels outperform static posts for {topic} discovery."
                    ),
                    "source_url": "https://developers.facebook.com/documentation/instagram-platform",
                    "source_title": "Meta Instagram Platform Documentation",
                    "published_date": "2026-11-01",
                    "confidence": 0.88,
                },
                {
                    "claim": (
                        f"Structured answer passages improve AEO visibility for {audience} queries."
                    ),
                    "source_url": "https://developers.google.com/search/docs/fundamentals/creating-helpful-content",
                    "source_title": "Search Helpful Content Guidelines",
                    "published_date": "2026-10-15",
                    "confidence": 0.84,
                },
            ],
            "inferences": [
                {
                    "hypothesis": (
                        f"Pairing weekly educational reels with AEO landing guides closes the "
                        f"content gap for {brand_name}."
                    ),
                    "rationale": (
                        "Synthesized from platform format trends and search intent signals."
                    ),
                    "confidence": 0.72,
                }
            ],
            "content_gaps": [
                f"Comparison breakdowns tailored to {audience}",
                f"Behind-the-scenes proof reels for {topic}",
            ],
            "confidence_overall": 0.82,
        }

    if schema_id == "metuse.writing_package.v1":
        return {
            "topic": topic,
            "brand_name": brand_name,
            "content_brief": (
                f"Conversion-focused social and landing copy for {brand_name} around '{topic}'."
            ),
            "hooks": [
                f"Most {audience} waste 80% of their budget on {topic} — here is the fix.",
                f"3 repeatable {topic} systems {brand_name} uses to double retention.",
            ],
            "script": (
                f"[0-3s Hook] Stop guessing on {topic}. "
                f"[3-22s Value] Here is the 3-step framework tailored for {audience}. "
                f"[22-30s CTA] Comment 'PLAYBOOK' to get the {brand_name} checklist."
            ),
            "captions": [
                f"{brand_name} breakdown: how {audience} turn {topic} into measurable pipeline.",
            ],
            "ctas": [
                "Save this post for your next campaign sprint.",
                "Reply with 'GUIDE' for the full breakdown.",
            ],
        }

    if schema_id == "metuse.seo_aeo_package.v1":
        return {
            "topic": topic,
            "primary_keyword": topic.lower(),
            "secondary_keywords": [
                f"{topic.lower()} strategy",
                f"{topic.lower()} for {audience.lower()}",
                f"{brand_name.lower()} {topic.lower()} guide",
            ],
            "search_intent": "commercial_investigation",
            "seo_metadata": {
                "title_tag": f"{topic} Guide for {audience} | {brand_name}"[:60],
                "meta_description": (
                    f"Actionable {topic} playbook for {audience} from {brand_name} "
                    "with verified workflows."
                )[:155],
                "slug": "-".join(topic.lower().split())[:64] or "guide",
            },
            "aeo_answer_passages": [
                {
                    "question": f"What is the most effective approach to {topic}?",
                    "direct_answer": (
                        f"For {audience}, the highest-leverage {topic} approach combines "
                        "evidence-backed research, structured briefs, and approved publishing."
                    ),
                    "entities": [brand_name, topic, audience],
                }
            ],
            "internal_link_suggestions": [
                "/guides/content-operations",
                "/case-studies/agency-growth",
            ],
        }

    # metuse.media_plan.v1
    return {
        "topic": topic,
        "creative_brief": (
            f"30-second vertical 9:16 reel for {brand_name} addressing {audience} on '{topic}'."
        ),
        "aspect_ratio": "9:16",
        "target_duration_seconds": 30,
        "storyboard": [
            {
                "scene_number": 1,
                "start_sec": 0,
                "end_sec": 5,
                "visual_direction": (
                    f"High-contrast opener illustrating the core {topic} pain point."
                ),
                "on_screen_text": f"Stop guessing on {topic}",
                "voiceover": f"Most {audience} lose momentum in the first five seconds.",
            },
            {
                "scene_number": 2,
                "start_sec": 5,
                "end_sec": 22,
                "visual_direction": (
                    f"Clean 3-step visual breakdown with {brand_name} brand palette."
                ),
                "on_screen_text": "1. Research -> 2. Script -> 3. Approve",
                "voiceover": "Anchor every clip in evidence, tight pacing, and brand consistency.",
            },
            {
                "scene_number": 3,
                "start_sec": 22,
                "end_sec": 30,
                "visual_direction": f"Product/brand end-card with crisp CTA for {brand_name}.",
                "on_screen_text": f"Get the {brand_name} checklist",
                "voiceover": "Save this reel and request the full campaign blueprint.",
            },
        ],
        "video_prompts": [
            {
                "scene_number": 1,
                "mode": "text_to_video",
                "prompt": (
                    f"Editorial studio close-up, dynamic lighting, modern workspace "
                    f"visualizing {topic}, 9:16 vertical."
                ),
                "duration_seconds": 5,
            },
            {
                "scene_number": 2,
                "mode": "image_to_video",
                "prompt": (
                    f"Subtle parallax push-in on {brand_name} hero reference visual "
                    "with crisp typography overlay, 9:16 vertical."
                ),
                "duration_seconds": 8,
            },
        ],
        "continuity_constraints": [
            f"Maintain {brand_name} color palette and typography across all scenes.",
            "Keep captions inside 9:16 safe margins (avoid bottom 20% UI overlay zone).",
        ],
        "cta_placement": "Final 6 seconds centered in safe zone with spoken + on-screen CTA.",
        "qa_checklist": [
            "Decodes cleanly as H.264/AAC MP4 at 1080x1920 30fps",
            "Duration within 28-32s target bounds",
            "No frozen/black frames or clipped audio",
            "Captions timed accurately within safe margins",
        ],
    }
