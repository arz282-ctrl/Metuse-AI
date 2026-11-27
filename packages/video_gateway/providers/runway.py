"""Official Runway Dev API provider adapter for the Unified Video Generation Gateway."""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal

import httpx

from packages.shared.enums import (
    ErrorCode,
    VideoFailureCategory,
    VideoGenerationMode,
    VideoJobStatus,
)
from packages.shared.errors import (
    BadRequestError,
    CapabilityNotSupportedError,
    ExternalServiceError,
    MetuseError,
    ProviderDisabledError,
)
from packages.shared.scrubbing import scrub_secrets, scrub_text
from packages.video_gateway.contracts import (
    ProviderCancelResult,
    ProviderPollResult,
    ProviderSubmissionRequest,
    ProviderSubmissionResult,
    VideoModelCapability,
    WebhookVerificationResult,
)

_RUNWAY_RATIO_MAP: dict[str, str] = {
    "9:16": "720:1280",
    "16:9": "1280:720",
    "1:1": "960:960",
}

_RUNWAY_STATUS_MAP: dict[str, VideoJobStatus] = {
    "PENDING": VideoJobStatus.SUBMITTED,
    "THROTTLED": VideoJobStatus.SUBMITTED,
    "RUNNING": VideoJobStatus.PROCESSING,
    "PROCESSING": VideoJobStatus.PROCESSING,
    "SUCCEEDED": VideoJobStatus.SUCCEEDED,
    "FAILED": VideoJobStatus.FAILED,
    "CANCELLED": VideoJobStatus.CANCELLED,
    "CANCELED": VideoJobStatus.CANCELLED,
}


def _lookup_header_ci(headers: dict[str, str], target_name: str) -> str | None:
    needle = target_name.lower()
    for key, value in headers.items():
        if key.lower() == needle:
            return value
    return None


def _scrub_dict(data: object) -> dict[str, object]:
    if not isinstance(data, dict):
        return {}
    scrubbed = scrub_secrets(data)
    if isinstance(scrubbed, dict):
        return {str(k): v for k, v in scrubbed.items()}
    return {}


def _normalize_progress(raw_progress: object, status: VideoJobStatus) -> int:
    if status == VideoJobStatus.SUCCEEDED:
        return 100
    if status == VideoJobStatus.CANCELLED:
        return 0
    if isinstance(raw_progress, bool):
        return 0
    if isinstance(raw_progress, (int, float)):
        numeric = float(raw_progress)
        if 0.0 <= numeric <= 1.0:
            return max(0, min(100, round(numeric * 100)))
        return max(0, min(100, int(numeric)))
    if status == VideoJobStatus.PROCESSING:
        return 50
    if status == VideoJobStatus.FAILED:
        return 100
    return 0


def _classify_failure_category(failure_code: str | None) -> VideoFailureCategory:
    if not failure_code:
        return VideoFailureCategory.PROVIDER_ERROR
    normalized = failure_code.strip().upper()
    if "RATE" in normalized or "429" in normalized or "THROTTL" in normalized:
        return VideoFailureCategory.RATE_LIMITED
    if "TIMEOUT" in normalized or "TIMED_OUT" in normalized:
        return VideoFailureCategory.TIMEOUT
    if "CANCEL" in normalized:
        return VideoFailureCategory.CANCELLED
    if "INVALID" in normalized or "VALIDATION" in normalized or "SAFETY" in normalized:
        return VideoFailureCategory.VALIDATION
    return VideoFailureCategory.PROVIDER_ERROR


class RunwayDevVideoProvider:
    """Official Runway Dev VideoProvider adapter using synchronous httpx.Client."""

    def __init__(
        self,
        base_url: str = "https://api.dev.runwayml.com/v1",
        api_key: str = "",
        api_version: str = "2024-11-06",
        webhook_secret: str = "",
        timeout_seconds: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.strip().rstrip("/")
        self._api_key = api_key.strip()
        self._api_version = api_version.strip() or "2024-11-06"
        self._webhook_secret = webhook_secret.strip()
        self._timeout = max(1.0, float(timeout_seconds))
        self._http_client = http_client

    @property
    def provider_id(self) -> str:
        return "runway"

    @property
    def display_name(self) -> str:
        return "Runway Dev"

    @property
    def is_enabled(self) -> bool:
        return bool(self._api_key)

    @property
    def supports_webhooks(self) -> bool:
        return True

    def capabilities(self) -> list[VideoModelCapability]:
        return [
            VideoModelCapability(
                provider="runway",
                model="gen4_turbo",
                modes=[VideoGenerationMode.IMAGE_TO_VIDEO],
                aspect_ratios=["9:16", "16:9", "1:1"],
                durations_seconds=[5, 10],
                resolutions=["720p", "1080p"],
                max_reference_images=1,
                audio_generation=False,
                webhook_support=True,
                cost_per_second_usd=Decimal("0.050000"),
                quality_tier="high",
                enabled=self.is_enabled,
            ),
            VideoModelCapability(
                provider="runway",
                model="gen3a_turbo",
                modes=[VideoGenerationMode.IMAGE_TO_VIDEO, VideoGenerationMode.TEXT_TO_VIDEO],
                aspect_ratios=["9:16", "16:9", "1:1"],
                durations_seconds=[5, 10],
                resolutions=["720p", "1080p"],
                max_reference_images=1,
                audio_generation=False,
                webhook_support=True,
                cost_per_second_usd=Decimal("0.050000"),
                quality_tier="standard",
                enabled=self.is_enabled,
            ),
        ]

    def create_text_to_video(self, request: ProviderSubmissionRequest) -> ProviderSubmissionResult:
        self._require_enabled()
        cap = self._validate_request(request, expected_mode=VideoGenerationMode.TEXT_TO_VIDEO)
        payload: dict[str, object] = {
            "model": cap.model,
            "promptText": request.prompt,
            "ratio": _RUNWAY_RATIO_MAP[request.aspect_ratio],
            "duration": request.duration_seconds,
        }
        data = self._request_json("POST", "/text_to_video", json_body=payload)
        return self._build_submission_result(cap=cap, request=request, data=data)

    def create_image_to_video(self, request: ProviderSubmissionRequest) -> ProviderSubmissionResult:
        self._require_enabled()
        cap = self._validate_request(request, expected_mode=VideoGenerationMode.IMAGE_TO_VIDEO)
        payload: dict[str, object] = {
            "model": cap.model,
            "promptImage": request.reference_images[0].signed_url,
            "promptText": request.prompt,
            "ratio": _RUNWAY_RATIO_MAP[request.aspect_ratio],
            "duration": request.duration_seconds,
        }
        data = self._request_json("POST", "/image_to_video", json_body=payload)
        return self._build_submission_result(cap=cap, request=request, data=data)

    def get_job(self, provider_job_id: str) -> ProviderPollResult:
        self._require_enabled()
        data = self._request_json("GET", f"/tasks/{provider_job_id}")
        return self._parse_task_snapshot(provider_job_id=provider_job_id, data=data)

    def cancel_job(self, provider_job_id: str) -> ProviderCancelResult:
        self._require_enabled()
        data = self._request_json(
            "DELETE",
            f"/tasks/{provider_job_id}",
            allow_empty_body=True,
        )
        return ProviderCancelResult(
            provider=self.provider_id,
            provider_job_id=provider_job_id,
            cancelled=True,
            status=VideoJobStatus.CANCELLED,
            raw_metadata=_scrub_dict(data),
        )

    def verify_and_parse_webhook(
        self,
        *,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> WebhookVerificationResult:
        provided_raw = _lookup_header_ci(headers, "X-Runway-Signature")
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
            raise BadRequestError("Runway webhook payload must be valid JSON") from exc

        if not isinstance(payload_obj, dict):
            raise BadRequestError("Runway webhook payload must be a JSON object")

        task_obj = payload_obj.get("task")
        task_data: dict[str, object] = (
            {str(k): v for k, v in task_obj.items()}
            if isinstance(task_obj, dict)
            else {str(k): v for k, v in payload_obj.items()}
        )

        raw_job_id = (
            payload_obj.get("provider_job_id")
            or payload_obj.get("task_id")
            or payload_obj.get("job_id")
            or task_data.get("id")
        )
        if not isinstance(raw_job_id, str) or not raw_job_id.strip():
            raise BadRequestError("Runway webhook payload missing task/job id")
        provider_job_id = raw_job_id.strip()

        raw_event_id = payload_obj.get("event_id") or payload_obj.get("provider_event_id")
        if isinstance(raw_event_id, str) and raw_event_id.strip():
            provider_event_id = raw_event_id.strip()
        else:
            provider_event_id = f"runway_evt_{hashlib.sha256(raw_body).hexdigest()[:16]}"

        poll_result = self._parse_task_snapshot(
            provider_job_id=provider_job_id,
            data=task_data,
        )
        return WebhookVerificationResult(
            provider=self.provider_id,
            provider_event_id=provider_event_id,
            provider_job_id=provider_job_id,
            poll_result=poll_result,
        )

    def _require_enabled(self) -> None:
        if not self.is_enabled:
            raise ProviderDisabledError(
                "Runway Dev provider is disabled because RUNWAY_API_KEY is not configured",
                details={"provider": self.provider_id},
            )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "X-Runway-Version": self._api_version,
            "Content-Type": "application/json",
        }

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
        allow_empty_body: bool = False,
    ) -> dict[str, object]:
        url = f"{self._base_url}{path}"
        client = self._http_client or httpx.Client(timeout=self._timeout)
        should_close = self._http_client is None
        try:
            try:
                response = client.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json_body,
                )
            except httpx.HTTPError as exc:
                raise ExternalServiceError(
                    f"Runway Dev transport error: {type(exc).__name__}",
                    details={"provider": self.provider_id, "method": method, "path": path},
                ) from exc

            if response.status_code == 429:
                raise ExternalServiceError(
                    "Runway Dev rate limit exceeded (HTTP 429)",
                    details={
                        "provider": self.provider_id,
                        "http_status": 429,
                        "failure_category": VideoFailureCategory.RATE_LIMITED.value,
                    },
                )

            if response.status_code in (401, 403):
                raise ExternalServiceError(
                    f"Runway Dev authentication failed (HTTP {response.status_code})",
                    details={
                        "provider": self.provider_id,
                        "http_status": response.status_code,
                    },
                )

            if response.status_code >= 500:
                raise ExternalServiceError(
                    f"Runway Dev upstream error (HTTP {response.status_code})",
                    details={
                        "provider": self.provider_id,
                        "http_status": response.status_code,
                    },
                )

            if response.status_code not in (200, 201, 202, 204):
                raise ExternalServiceError(
                    f"Runway Dev rejected request (HTTP {response.status_code})",
                    details={
                        "provider": self.provider_id,
                        "http_status": response.status_code,
                    },
                )

            if allow_empty_body and (response.status_code == 204 or not response.content):
                return {}

            try:
                parsed: object = response.json()
            except ValueError as exc:
                raise ExternalServiceError(
                    "Runway Dev returned non-JSON response payload",
                    details={"provider": self.provider_id, "http_status": response.status_code},
                ) from exc

            if not isinstance(parsed, dict):
                raise ExternalServiceError(
                    "Runway Dev returned non-object JSON response payload",
                    details={"provider": self.provider_id, "http_status": response.status_code},
                )
            return {str(k): v for k, v in parsed.items()}
        finally:
            if should_close:
                client.close()

    def _build_submission_result(
        self,
        *,
        cap: VideoModelCapability,
        request: ProviderSubmissionRequest,
        data: dict[str, object],
    ) -> ProviderSubmissionResult:
        raw_id = data.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise ExternalServiceError(
                "Runway Dev submission response missing task id",
                details={"provider": self.provider_id, "model": cap.model},
            )
        provider_job_id = raw_id.strip()
        estimated_cost = (cap.cost_per_second_usd * Decimal(request.duration_seconds)).quantize(
            Decimal("0.000001")
        )
        return ProviderSubmissionResult(
            provider=self.provider_id,
            model=cap.model,
            provider_job_id=provider_job_id,
            status=VideoJobStatus.SUBMITTED,
            progress_pct=0,
            estimated_cost_usd=estimated_cost,
            raw_metadata=_scrub_dict(data),
        )

    def _parse_task_snapshot(
        self,
        *,
        provider_job_id: str,
        data: dict[str, object],
    ) -> ProviderPollResult:
        raw_status = str(data.get("status") or "PROCESSING").strip().upper()
        status = _RUNWAY_STATUS_MAP.get(raw_status, VideoJobStatus.PROCESSING)
        progress_pct = _normalize_progress(data.get("progress"), status)

        output_url: str | None = None
        raw_output = data.get("output")
        if isinstance(raw_output, list):
            for entry in raw_output:
                if isinstance(entry, str) and entry.strip():
                    output_url = entry.strip()
                    break
        elif isinstance(raw_output, str) and raw_output.strip():
            output_url = raw_output.strip()
        elif isinstance(data.get("output_url"), str):
            candidate = str(data["output_url"]).strip()
            output_url = candidate or None

        failure_category: VideoFailureCategory | None = None
        provider_error_code: str | None = None
        error_message: str | None = None

        if status == VideoJobStatus.FAILED:
            raw_code = data.get("failureCode") or data.get("error_code")
            if isinstance(raw_code, str) and raw_code.strip():
                provider_error_code = scrub_text(raw_code.strip())
            failure_category = _classify_failure_category(provider_error_code)
            raw_err = data.get("failure") or data.get("error_message") or "Runway Dev task failed"
            error_message = scrub_text(str(raw_err))
        elif status == VideoJobStatus.CANCELLED:
            failure_category = VideoFailureCategory.CANCELLED

        output_filename = f"{provider_job_id}.mp4" if status == VideoJobStatus.SUCCEEDED else None
        return ProviderPollResult(
            provider=self.provider_id,
            provider_job_id=provider_job_id,
            status=status,
            progress_pct=progress_pct,
            output_url=output_url,
            output_mime_type="video/mp4",
            output_filename=output_filename,
            failure_category=failure_category,
            provider_error_code=provider_error_code,
            error_message=error_message,
            raw_metadata=_scrub_dict(data),
        )

    def _validate_request(
        self,
        request: ProviderSubmissionRequest,
        *,
        expected_mode: VideoGenerationMode,
    ) -> VideoModelCapability:
        if request.mode != expected_mode:
            raise CapabilityNotSupportedError(
                f"Runway Dev endpoint expects mode '{expected_mode.value}', "
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
                f"Runway Dev model '{cap.model}' does not support mode '{request.mode.value}'",
                details={
                    "provider": self.provider_id,
                    "model": cap.model,
                    "mode": request.mode.value,
                    "supported_modes": [m.value for m in cap.modes],
                },
            )

        if request.aspect_ratio not in cap.aspect_ratios or (
            request.aspect_ratio not in _RUNWAY_RATIO_MAP
        ):
            raise CapabilityNotSupportedError(
                f"Runway Dev model '{cap.model}' does not support aspect ratio "
                f"'{request.aspect_ratio}'",
                details={
                    "provider": self.provider_id,
                    "model": cap.model,
                    "aspect_ratio": request.aspect_ratio,
                    "supported_aspect_ratios": cap.aspect_ratios,
                },
            )

        if request.duration_seconds not in cap.durations_seconds:
            raise CapabilityNotSupportedError(
                f"Runway Dev model '{cap.model}' does not support duration "
                f"{request.duration_seconds}s",
                details={
                    "provider": self.provider_id,
                    "model": cap.model,
                    "duration_seconds": request.duration_seconds,
                    "supported_durations_seconds": cap.durations_seconds,
                },
            )

        if request.resolution not in cap.resolutions:
            raise CapabilityNotSupportedError(
                f"Runway Dev model '{cap.model}' does not support resolution "
                f"'{request.resolution}'",
                details={
                    "provider": self.provider_id,
                    "model": cap.model,
                    "resolution": request.resolution,
                    "supported_resolutions": cap.resolutions,
                },
            )

        if expected_mode == VideoGenerationMode.IMAGE_TO_VIDEO and not request.reference_images:
            raise CapabilityNotSupportedError(
                "Runway Dev image_to_video requires at least one reference image",
                details={"provider": self.provider_id, "model": cap.model},
            )

        if len(request.reference_images) > cap.max_reference_images:
            raise CapabilityNotSupportedError(
                f"Runway Dev model '{cap.model}' supports at most {cap.max_reference_images} "
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
                f"Runway Dev model '{cap.model}' does not support audio generation",
                details={"provider": self.provider_id, "model": cap.model},
            )

        return cap
