"""Deterministic mock video provider for free-tier local development and CI tests."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from packages.shared.enums import (
    ErrorCode,
    VideoFailureCategory,
    VideoGenerationMode,
    VideoJobStatus,
)
from packages.shared.errors import (
    BadRequestError,
    CapabilityNotSupportedError,
    MetuseError,
    ProviderDisabledError,
)
from packages.video_gateway.contracts import (
    ProviderCancelResult,
    ProviderPollResult,
    ProviderSubmissionRequest,
    ProviderSubmissionResult,
    VideoModelCapability,
    WebhookVerificationResult,
)

_MP4_PREFIX = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom\x00\x00\x00\x08free"

_DIMENSIONS_MAP: dict[tuple[str, str], tuple[int, int]] = {
    ("9:16", "720p"): (720, 1280),
    ("9:16", "1080p"): (1080, 1920),
    ("16:9", "720p"): (1280, 720),
    ("16:9", "1080p"): (1920, 1080),
    ("1:1", "720p"): (720, 720),
    ("1:1", "1080p"): (1080, 1080),
}


@dataclass
class _MockJobState:
    provider_job_id: str
    model: str
    aspect_ratio: str
    duration_seconds: int
    resolution: str
    estimated_cost_usd: Decimal
    simulate_fail: bool = False
    poll_count: int = 0
    status: VideoJobStatus = VideoJobStatus.SUBMITTED


def _build_deterministic_mp4_bytes(provider_job_id: str) -> bytes:
    """Return deterministic minimal valid MP4 container bytes for a given provider job ID."""
    digest = hashlib.sha256(provider_job_id.encode("utf-8")).digest()
    moov_payload = b"metuse-mock-video:" + digest
    moov_box = (8 + len(moov_payload)).to_bytes(4, "big") + b"moov" + moov_payload
    return _MP4_PREFIX + moov_box


def _dimensions_for(aspect_ratio: str, resolution: str) -> tuple[int, int]:
    return _DIMENSIONS_MAP.get((aspect_ratio, resolution), (720, 1280))


def _lookup_header_ci(headers: dict[str, str], target_name: str) -> str | None:
    needle = target_name.lower()
    for key, value in headers.items():
        if key.lower() == needle:
            return value
    return None


def _parse_optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float, str)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    return None


def _parse_optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


class MockVideoProvider:
    """Deterministic zero-cost VideoProvider adapter with restart-safe polling and webhooks."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        webhook_secret: str = "mock-video-webhook-secret",  # noqa: S107
        polls_until_complete: int = 2,
    ) -> None:
        self._enabled = enabled
        self._webhook_secret = webhook_secret
        self._polls_until_complete = max(1, int(polls_until_complete))
        self._jobs: dict[str, _MockJobState] = {}

    @property
    def provider_id(self) -> str:
        return "mock"

    @property
    def display_name(self) -> str:
        return "Metuse Mock Video Studio"

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def supports_webhooks(self) -> bool:
        return True

    def capabilities(self) -> list[VideoModelCapability]:
        return [
            VideoModelCapability(
                provider="mock",
                model="mock-fast-v1",
                modes=[VideoGenerationMode.TEXT_TO_VIDEO, VideoGenerationMode.IMAGE_TO_VIDEO],
                aspect_ratios=["9:16", "16:9", "1:1"],
                durations_seconds=[5, 8, 10],
                resolutions=["720p", "1080p"],
                max_reference_images=1,
                audio_generation=True,
                webhook_support=True,
                cost_per_second_usd=Decimal("0.040000"),
                quality_tier="standard",
                enabled=self._enabled,
            ),
            VideoModelCapability(
                provider="mock",
                model="mock-cinema-v1",
                modes=[VideoGenerationMode.TEXT_TO_VIDEO, VideoGenerationMode.IMAGE_TO_VIDEO],
                aspect_ratios=["9:16", "16:9", "1:1"],
                durations_seconds=[5, 8, 10, 15],
                resolutions=["720p", "1080p"],
                max_reference_images=2,
                audio_generation=True,
                webhook_support=True,
                cost_per_second_usd=Decimal("0.120000"),
                quality_tier="high",
                enabled=self._enabled,
            ),
        ]

    def create_text_to_video(self, request: ProviderSubmissionRequest) -> ProviderSubmissionResult:
        return self._submit(request, expected_mode=VideoGenerationMode.TEXT_TO_VIDEO)

    def create_image_to_video(self, request: ProviderSubmissionRequest) -> ProviderSubmissionResult:
        return self._submit(request, expected_mode=VideoGenerationMode.IMAGE_TO_VIDEO)

    def get_job(self, provider_job_id: str) -> ProviderPollResult:
        state = self._jobs.get(provider_job_id)
        if state is None:
            # Post-restart deterministic recovery when in-memory state is empty.
            output_bytes = _build_deterministic_mp4_bytes(provider_job_id)
            return ProviderPollResult(
                provider=self.provider_id,
                provider_job_id=provider_job_id,
                status=VideoJobStatus.SUCCEEDED,
                progress_pct=100,
                output_bytes=output_bytes,
                output_mime_type="video/mp4",
                output_filename=f"{provider_job_id}.mp4",
                duration_seconds=Decimal("5"),
                width=720,
                height=1280,
                actual_cost_usd=Decimal("0.200000"),
                raw_metadata={"recovered_after_restart": True},
            )

        if state.status == VideoJobStatus.CANCELLED:
            return ProviderPollResult(
                provider=self.provider_id,
                provider_job_id=provider_job_id,
                status=VideoJobStatus.CANCELLED,
                progress_pct=0,
                failure_category=VideoFailureCategory.CANCELLED,
                error_message="Mock video job was cancelled",
                raw_metadata={"poll_count": state.poll_count, "model": state.model},
            )

        state.poll_count += 1
        if state.simulate_fail:
            state.status = VideoJobStatus.FAILED
            return ProviderPollResult(
                provider=self.provider_id,
                provider_job_id=provider_job_id,
                status=VideoJobStatus.FAILED,
                progress_pct=100,
                failure_category=VideoFailureCategory.PROVIDER_ERROR,
                provider_error_code="MOCK_SYNTHETIC_FAILURE",
                error_message="Simulated mock video generation failure",
                raw_metadata={"poll_count": state.poll_count, "model": state.model},
            )

        if state.poll_count < self._polls_until_complete:
            state.status = VideoJobStatus.PROCESSING
            progress = min(95, max(10, int((state.poll_count / self._polls_until_complete) * 100)))
            return ProviderPollResult(
                provider=self.provider_id,
                provider_job_id=provider_job_id,
                status=VideoJobStatus.PROCESSING,
                progress_pct=progress,
                raw_metadata={"poll_count": state.poll_count, "model": state.model},
            )

        state.status = VideoJobStatus.SUCCEEDED
        width, height = _dimensions_for(state.aspect_ratio, state.resolution)
        output_bytes = _build_deterministic_mp4_bytes(provider_job_id)
        return ProviderPollResult(
            provider=self.provider_id,
            provider_job_id=provider_job_id,
            status=VideoJobStatus.SUCCEEDED,
            progress_pct=100,
            output_bytes=output_bytes,
            output_mime_type="video/mp4",
            output_filename=f"{provider_job_id}.mp4",
            duration_seconds=Decimal(state.duration_seconds),
            width=width,
            height=height,
            actual_cost_usd=state.estimated_cost_usd,
            raw_metadata={"poll_count": state.poll_count, "model": state.model},
        )

    def cancel_job(self, provider_job_id: str) -> ProviderCancelResult:
        state = self._jobs.get(provider_job_id)
        if state is not None:
            state.status = VideoJobStatus.CANCELLED
        else:
            self._jobs[provider_job_id] = _MockJobState(
                provider_job_id=provider_job_id,
                model="mock-fast-v1",
                aspect_ratio="9:16",
                duration_seconds=5,
                resolution="720p",
                estimated_cost_usd=Decimal("0.000000"),
                status=VideoJobStatus.CANCELLED,
            )
        return ProviderCancelResult(
            provider=self.provider_id,
            provider_job_id=provider_job_id,
            cancelled=True,
            status=VideoJobStatus.CANCELLED,
        )

    def verify_and_parse_webhook(
        self,
        *,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> WebhookVerificationResult:
        provided_raw = _lookup_header_ci(headers, "X-Metuse-Signature")
        if not self._webhook_secret or not provided_raw:
            raise MetuseError(
                "Invalid webhook signature",
                code=ErrorCode.WEBHOOK_SIGNATURE_INVALID,
                status_code=401,
            )

        provided = provided_raw.strip().lower()
        if provided.startswith("sha256="):
            provided = provided[len("sha256=") :]

        expected = hmac.new(
            self._webhook_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(provided, expected):
            raise MetuseError(
                "Invalid webhook signature",
                code=ErrorCode.WEBHOOK_SIGNATURE_INVALID,
                status_code=401,
            )

        try:
            payload_obj: object = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BadRequestError("Mock webhook payload must be valid JSON") from exc

        if not isinstance(payload_obj, dict):
            raise BadRequestError("Mock webhook payload must be a JSON object")

        raw_job_id = payload_obj.get("provider_job_id") or payload_obj.get("job_id")
        if not isinstance(raw_job_id, str) or not raw_job_id.strip():
            raise BadRequestError("Mock webhook payload missing provider_job_id")
        provider_job_id = raw_job_id.strip()

        raw_event_id = (
            payload_obj.get("provider_event_id")
            or payload_obj.get("event_id")
            or payload_obj.get("id")
        )
        if isinstance(raw_event_id, str) and raw_event_id.strip():
            provider_event_id = raw_event_id.strip()
        else:
            provider_event_id = f"mock_evt_{hashlib.sha256(raw_body).hexdigest()[:16]}"

        raw_status = payload_obj.get("status")
        if not isinstance(raw_status, str) or not raw_status.strip():
            raise BadRequestError("Mock webhook payload missing status")
        try:
            status = VideoJobStatus(raw_status.strip().lower())
        except ValueError as exc:
            raise BadRequestError(f"Unsupported mock webhook status: {raw_status}") from exc

        progress_pct = _parse_optional_int(payload_obj.get("progress_pct"))
        output_bytes: bytes | None = None
        output_filename: str | None = None
        duration_seconds: Decimal | None = _parse_optional_decimal(
            payload_obj.get("duration_seconds")
        )
        width: int | None = _parse_optional_int(payload_obj.get("width"))
        height: int | None = _parse_optional_int(payload_obj.get("height"))
        actual_cost_usd: Decimal | None = _parse_optional_decimal(
            payload_obj.get("actual_cost_usd")
        )
        failure_category: VideoFailureCategory | None = None
        provider_error_code: str | None = None
        error_message: str | None = None

        if status == VideoJobStatus.SUCCEEDED:
            output_bytes = _build_deterministic_mp4_bytes(provider_job_id)
            raw_filename = payload_obj.get("output_filename")
            output_filename = (
                raw_filename.strip()
                if isinstance(raw_filename, str) and raw_filename.strip()
                else f"{provider_job_id}.mp4"
            )
            progress_pct = 100
            if duration_seconds is None:
                duration_seconds = Decimal("5")
            if width is None:
                width = 720
            if height is None:
                height = 1280
        elif status == VideoJobStatus.FAILED:
            raw_cat = payload_obj.get("failure_category")
            if isinstance(raw_cat, str):
                try:
                    failure_category = VideoFailureCategory(raw_cat.strip().lower())
                except ValueError:
                    failure_category = VideoFailureCategory.PROVIDER_ERROR
            else:
                failure_category = VideoFailureCategory.PROVIDER_ERROR
            raw_code = payload_obj.get("provider_error_code")
            provider_error_code = (
                raw_code.strip()
                if isinstance(raw_code, str) and raw_code.strip()
                else "MOCK_WEBHOOK_FAILURE"
            )
            raw_msg = payload_obj.get("error_message")
            error_message = (
                raw_msg.strip()
                if isinstance(raw_msg, str) and raw_msg.strip()
                else "Mock provider reported failure via webhook"
            )
            progress_pct = 100 if progress_pct is None else max(0, min(100, progress_pct))
        elif status == VideoJobStatus.CANCELLED:
            failure_category = VideoFailureCategory.CANCELLED
            progress_pct = 0 if progress_pct is None else max(0, min(100, progress_pct))
        else:
            progress_pct = 50 if progress_pct is None else max(0, min(100, progress_pct))

        raw_output_url = payload_obj.get("output_url")
        output_url = (
            raw_output_url.strip()
            if isinstance(raw_output_url, str) and raw_output_url.strip()
            else None
        )

        poll_result = ProviderPollResult(
            provider=self.provider_id,
            provider_job_id=provider_job_id,
            status=status,
            progress_pct=max(0, min(100, progress_pct)),
            output_bytes=output_bytes,
            output_url=output_url,
            output_mime_type="video/mp4",
            output_filename=output_filename,
            duration_seconds=duration_seconds,
            width=width,
            height=height,
            actual_cost_usd=actual_cost_usd,
            failure_category=failure_category,
            provider_error_code=provider_error_code,
            error_message=error_message,
            raw_metadata={"event_id": provider_event_id, "source": "webhook"},
        )
        return WebhookVerificationResult(
            provider=self.provider_id,
            provider_event_id=provider_event_id,
            provider_job_id=provider_job_id,
            poll_result=poll_result,
        )

    def _submit(
        self,
        request: ProviderSubmissionRequest,
        *,
        expected_mode: VideoGenerationMode,
    ) -> ProviderSubmissionResult:
        if not self._enabled:
            raise ProviderDisabledError(
                "Mock video provider is currently disabled",
                details={"provider": self.provider_id},
            )

        cap = self._validate_request(request, expected_mode=expected_mode)
        provider_job_id = f"mock_vid_{request.internal_job_id.hex[:16]}"
        estimated_cost = (cap.cost_per_second_usd * Decimal(request.duration_seconds)).quantize(
            Decimal("0.000001")
        )
        self._jobs[provider_job_id] = _MockJobState(
            provider_job_id=provider_job_id,
            model=cap.model,
            aspect_ratio=request.aspect_ratio,
            duration_seconds=request.duration_seconds,
            resolution=request.resolution,
            estimated_cost_usd=estimated_cost,
            simulate_fail="[simulate_fail]" in request.prompt.lower(),
            poll_count=0,
            status=VideoJobStatus.SUBMITTED,
        )
        return ProviderSubmissionResult(
            provider=self.provider_id,
            model=cap.model,
            provider_job_id=provider_job_id,
            status=VideoJobStatus.SUBMITTED,
            progress_pct=0,
            estimated_cost_usd=estimated_cost,
            raw_metadata={
                "mode": request.mode.value,
                "aspect_ratio": request.aspect_ratio,
                "duration_seconds": request.duration_seconds,
                "resolution": request.resolution,
            },
        )

    def _validate_request(
        self,
        request: ProviderSubmissionRequest,
        *,
        expected_mode: VideoGenerationMode,
    ) -> VideoModelCapability:
        if request.mode != expected_mode:
            raise CapabilityNotSupportedError(
                f"Mock provider endpoint expects mode '{expected_mode.value}', "
                f"got '{request.mode.value}'",
                details={
                    "provider": self.provider_id,
                    "expected_mode": expected_mode.value,
                    "actual_mode": request.mode.value,
                },
            )

        cap_map = {cap.model: cap for cap in self.capabilities()}
        cap = cap_map.get(request.model)
        if cap is None:
            raise CapabilityNotSupportedError(
                f"Model '{request.model}' is not supported by provider '{self.provider_id}'",
                details={
                    "provider": self.provider_id,
                    "model": request.model,
                    "supported_models": sorted(cap_map.keys()),
                },
            )

        if request.mode not in cap.modes:
            raise CapabilityNotSupportedError(
                f"Model '{cap.model}' does not support mode '{request.mode.value}'",
                details={"provider": self.provider_id, "model": cap.model, "mode": request.mode},
            )

        if request.aspect_ratio not in cap.aspect_ratios:
            raise CapabilityNotSupportedError(
                f"Model '{cap.model}' does not support aspect ratio '{request.aspect_ratio}'",
                details={
                    "provider": self.provider_id,
                    "model": cap.model,
                    "aspect_ratio": request.aspect_ratio,
                    "supported_aspect_ratios": cap.aspect_ratios,
                },
            )

        if request.duration_seconds not in cap.durations_seconds:
            raise CapabilityNotSupportedError(
                f"Model '{cap.model}' does not support duration {request.duration_seconds}s",
                details={
                    "provider": self.provider_id,
                    "model": cap.model,
                    "duration_seconds": request.duration_seconds,
                    "supported_durations_seconds": cap.durations_seconds,
                },
            )

        if request.resolution not in cap.resolutions:
            raise CapabilityNotSupportedError(
                f"Model '{cap.model}' does not support resolution '{request.resolution}'",
                details={
                    "provider": self.provider_id,
                    "model": cap.model,
                    "resolution": request.resolution,
                    "supported_resolutions": cap.resolutions,
                },
            )

        if expected_mode == VideoGenerationMode.IMAGE_TO_VIDEO and not request.reference_images:
            raise CapabilityNotSupportedError(
                "image_to_video requires at least one reference image",
                details={"provider": self.provider_id, "model": cap.model},
            )

        if len(request.reference_images) > cap.max_reference_images:
            raise CapabilityNotSupportedError(
                f"Model '{cap.model}' supports at most {cap.max_reference_images} "
                f"reference image(s), got {len(request.reference_images)}",
                details={
                    "provider": self.provider_id,
                    "model": cap.model,
                    "max_reference_images": cap.max_reference_images,
                    "provided_reference_images": len(request.reference_images),
                },
            )

        if request.audio_generate and not cap.audio_generation:
            raise CapabilityNotSupportedError(
                f"Model '{cap.model}' does not support audio generation",
                details={"provider": self.provider_id, "model": cap.model},
            )

        return cap
