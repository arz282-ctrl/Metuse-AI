"""Phase 3 unit tests: Video Gateway state machine, capability routing, Mock & Runway adapters."""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from packages.shared.config import BaseAppSettings
from packages.shared.enums import (
    ErrorCode,
    VideoFailureCategory,
    VideoGenerationMode,
    VideoJobStatus,
)
from packages.shared.errors import (
    CapabilityNotSupportedError,
    ExternalServiceError,
    MetuseError,
    ProviderDisabledError,
)
from packages.video_gateway import (
    MockVideoProvider,
    NormalizedVideoRequest,
    ProviderReferenceImage,
    ProviderSubmissionRequest,
    RunwayDevVideoProvider,
    VideoAudioConfig,
    VideoGatewayRegistry,
    build_default_video_registry,
    compute_next_poll_delay_seconds,
    validate_transition,
)

pytestmark = pytest.mark.unit


def test_video_job_state_machine_and_backoff() -> None:
    """Valid transitions succeed; terminal/skip transitions raise INVALID_STATE_TRANSITION (409)."""
    assert validate_transition(VideoJobStatus.QUEUED, VideoJobStatus.VALIDATING) == (
        VideoJobStatus.VALIDATING
    )
    assert validate_transition(VideoJobStatus.VALIDATING, VideoJobStatus.SUBMITTED) == (
        VideoJobStatus.SUBMITTED
    )
    assert validate_transition(VideoJobStatus.SUBMITTED, VideoJobStatus.PROCESSING) == (
        VideoJobStatus.PROCESSING
    )
    assert validate_transition(VideoJobStatus.PROCESSING, VideoJobStatus.SUCCEEDED) == (
        VideoJobStatus.SUCCEEDED
    )

    with pytest.raises(MetuseError) as exc_info:
        validate_transition(VideoJobStatus.SUCCEEDED, VideoJobStatus.PROCESSING)
    assert exc_info.value.code == ErrorCode.INVALID_STATE_TRANSITION
    assert exc_info.value.status_code == 409

    with pytest.raises(MetuseError) as skip_exc:
        validate_transition(VideoJobStatus.QUEUED, VideoJobStatus.SUCCEEDED)
    assert skip_exc.value.code == ErrorCode.INVALID_STATE_TRANSITION

    assert compute_next_poll_delay_seconds(1, base_delay=2.0, max_delay=10.0) == 2.0
    assert compute_next_poll_delay_seconds(2, base_delay=2.0, max_delay=10.0) == 4.0
    assert compute_next_poll_delay_seconds(5, base_delay=2.0, max_delay=10.0) == 10.0


def test_capability_registry_routing_cost_quality_pinning_and_rejections() -> None:
    """Registry picks lowest-cost by default, high-quality on request, and fails clearly on gaps."""
    settings = BaseAppSettings(_env_file=None, video_gateway_mode="mock")
    registry = build_default_video_registry(settings)

    providers = registry.list_providers()
    by_id = {p.provider: p for p in providers}
    assert by_id["mock"].enabled is True
    assert by_id["runway"].enabled is False

    base_req = NormalizedVideoRequest(
        workspace_id=uuid4(),
        mode=VideoGenerationMode.TEXT_TO_VIDEO,
        prompt="Slow drone shot over coastal cliffs at golden hour",
        aspect_ratio="9:16",
        duration_seconds=5,
        resolution="720p",
    )
    prov, model = registry.select_provider_and_model(base_req)
    assert prov.provider_id == "mock"
    assert model.model == "mock-fast-v1"
    assert model.cost_per_second_usd == Decimal("0.040000")

    hq_req = base_req.model_copy(update={"quality_preference": "high_quality"})
    _, hq_model = registry.select_provider_and_model(hq_req)
    assert hq_model.model == "mock-cinema-v1"

    # Requesting 15s routes cleanly to mock-cinema-v1 (mock-fast-v1 only supports 5/8/10s)
    long_req = base_req.model_copy(update={"duration_seconds": 15})
    _, long_model = registry.select_provider_and_model(long_req)
    assert long_model.model == "mock-cinema-v1"

    # Unsupported aspect ratio fails clearly with 422 CapabilityNotSupportedError
    bad_aspect = base_req.model_copy(update={"aspect_ratio": "21:9"})
    with pytest.raises(CapabilityNotSupportedError) as cap_exc:
        registry.select_provider_and_model(bad_aspect)
    assert cap_exc.value.status_code == 422

    # Pinning disabled runway provider raises 503 ProviderDisabledError
    pinned_disabled = base_req.model_copy(update={"provider_preference": "runway"})
    with pytest.raises(ProviderDisabledError) as dis_exc:
        registry.select_provider_and_model(pinned_disabled)
    assert dis_exc.value.status_code == 503

    # image_to_video without reference_asset_ids rejected at schema level
    with pytest.raises(ValidationError):
        NormalizedVideoRequest(
            workspace_id=uuid4(),
            mode=VideoGenerationMode.IMAGE_TO_VIDEO,
            prompt="Animate product bottle with light sweep",
            reference_asset_ids=[],
        )


def test_mock_provider_t2v_i2v_restart_safe_poll_fail_cancel_and_webhook() -> None:
    """MockVideoProvider supports T2V, I2V, restart-safe polling, fail/cancel, and HMAC webhooks."""
    provider = MockVideoProvider(enabled=True, webhook_secret="unit-secret", polls_until_complete=2)
    job_id = uuid4()
    ws_id = uuid4()

    sub = provider.create_text_to_video(
        ProviderSubmissionRequest(
            internal_job_id=job_id,
            workspace_id=ws_id,
            mode=VideoGenerationMode.TEXT_TO_VIDEO,
            model="mock-fast-v1",
            prompt="Minimal product reveal on warm stone plinth",
            aspect_ratio="9:16",
            duration_seconds=5,
            resolution="720p",
        )
    )
    assert sub.provider == "mock"
    assert sub.provider_job_id == f"mock_vid_{job_id.hex[:16]}"

    first_poll = provider.get_job(sub.provider_job_id)
    assert first_poll.status == VideoJobStatus.PROCESSING

    second_poll = provider.get_job(sub.provider_job_id)
    assert second_poll.status == VideoJobStatus.SUCCEEDED
    assert second_poll.output_bytes is not None
    assert b"ftypmp42" in second_poll.output_bytes

    # Simulated process restart: fresh MockVideoProvider with empty memory still resolves job!
    fresh_provider = MockVideoProvider(enabled=True, webhook_secret="unit-secret")
    recovered = fresh_provider.get_job(sub.provider_job_id)
    assert recovered.status == VideoJobStatus.SUCCEEDED
    assert recovered.output_bytes is not None

    # Image-to-video submission with reference image
    i2v_sub = provider.create_image_to_video(
        ProviderSubmissionRequest(
            internal_job_id=uuid4(),
            workspace_id=ws_id,
            mode=VideoGenerationMode.IMAGE_TO_VIDEO,
            model="mock-fast-v1",
            prompt="Animate bottle rotation",
            aspect_ratio="9:16",
            duration_seconds=5,
            resolution="720p",
            reference_images=[
                ProviderReferenceImage(
                    asset_id=uuid4(),
                    signed_url="https://storage.local/signed/ref.png",
                    mime_type="image/png",
                    filename="ref.png",
                )
            ],
        )
    )
    assert i2v_sub.status == VideoJobStatus.SUBMITTED
    cancel_res = provider.cancel_job(i2v_sub.provider_job_id)
    assert cancel_res.cancelled is True
    assert cancel_res.status == VideoJobStatus.CANCELLED

    # Synthetic failure trigger
    fail_sub = provider.create_text_to_video(
        ProviderSubmissionRequest(
            internal_job_id=uuid4(),
            workspace_id=ws_id,
            mode=VideoGenerationMode.TEXT_TO_VIDEO,
            model="mock-fast-v1",
            prompt="Test prompt [simulate_fail]",
            aspect_ratio="9:16",
            duration_seconds=5,
            resolution="720p",
        )
    )
    fail_poll = provider.get_job(fail_sub.provider_job_id)
    assert fail_poll.status == VideoJobStatus.FAILED
    assert fail_poll.failure_category == VideoFailureCategory.PROVIDER_ERROR

    # Webhook signature verification
    body = json.dumps(
        {
            "event_id": "evt_123",
            "provider_job_id": sub.provider_job_id,
            "status": "succeeded",
            "progress_pct": 100,
        }
    ).encode("utf-8")
    good_sig = hmac.new(b"unit-secret", body, hashlib.sha256).hexdigest()
    parsed = provider.verify_and_parse_webhook(
        raw_body=body, headers={"X-Metuse-Signature": good_sig}
    )
    assert parsed.provider_event_id == "evt_123"
    assert parsed.poll_result.status == VideoJobStatus.SUCCEEDED
    assert parsed.poll_result.output_bytes is not None

    with pytest.raises(MetuseError) as bad_sig_exc:
        provider.verify_and_parse_webhook(raw_body=body, headers={"X-Metuse-Signature": "bad"})
    assert bad_sig_exc.value.code == ErrorCode.WEBHOOK_SIGNATURE_INVALID
    assert bad_sig_exc.value.status_code == 401


def test_runway_provider_disabled_without_key_and_scrubs_secrets_on_http_error() -> None:
    """RunwayDevVideoProvider enforces disabled state, capabilities, and secret scrubbing."""
    disabled = RunwayDevVideoProvider(base_url="https://api.dev.runwayml.com/v1", api_key="")
    assert disabled.is_enabled is False
    with pytest.raises(ProviderDisabledError):
        disabled.create_text_to_video(
            ProviderSubmissionRequest(
                internal_job_id=uuid4(),
                workspace_id=uuid4(),
                mode=VideoGenerationMode.TEXT_TO_VIDEO,
                model="gen3a_turbo",
                prompt="Test",
                aspect_ratio="16:9",
                duration_seconds=5,
                resolution="720p",
            )
        )

    secret_key = "rw_live_super_secret_token_do_not_leak"

    def _transport_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {secret_key}"
        assert request.headers["X-Runway-Version"] == "2024-11-06"
        if request.url.path.endswith("/text_to_video"):
            return httpx.Response(429, json={"error": f"Rate limited for {secret_key}"})
        if request.url.path.endswith("/image_to_video"):
            return httpx.Response(200, json={"id": "rw_task_999"})
        if request.url.path.endswith("/tasks/rw_task_999"):
            return httpx.Response(
                200,
                json={
                    "id": "rw_task_999",
                    "status": "SUCCEEDED",
                    "output": ["https://cdn.runwayml.com/out.mp4"],
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(_transport_handler))
    runway = RunwayDevVideoProvider(
        base_url="https://api.dev.runwayml.com/v1",
        api_key=secret_key,
        webhook_secret="rw-wh-secret",
        http_client=client,
    )
    assert runway.is_enabled is True

    # gen4_turbo does not support text_to_video -> CapabilityNotSupportedError
    with pytest.raises(CapabilityNotSupportedError):
        runway.create_text_to_video(
            ProviderSubmissionRequest(
                internal_job_id=uuid4(),
                workspace_id=uuid4(),
                mode=VideoGenerationMode.TEXT_TO_VIDEO,
                model="gen4_turbo",
                prompt="Unsupported T2V on gen4_turbo",
                aspect_ratio="9:16",
                duration_seconds=5,
                resolution="720p",
            )
        )

    # HTTP 429 must scrub API key
    with pytest.raises(ExternalServiceError) as err_429:
        runway.create_text_to_video(
            ProviderSubmissionRequest(
                internal_job_id=uuid4(),
                workspace_id=uuid4(),
                mode=VideoGenerationMode.TEXT_TO_VIDEO,
                model="gen3a_turbo",
                prompt="Valid T2V hitting 429",
                aspect_ratio="16:9",
                duration_seconds=5,
                resolution="720p",
            )
        )
    assert secret_key not in err_429.value.message

    # Valid I2V submission + poll
    i2v_res = runway.create_image_to_video(
        ProviderSubmissionRequest(
            internal_job_id=uuid4(),
            workspace_id=uuid4(),
            mode=VideoGenerationMode.IMAGE_TO_VIDEO,
            model="gen4_turbo",
            prompt="Subtle camera push-in",
            aspect_ratio="9:16",
            duration_seconds=5,
            resolution="720p",
            reference_images=[
                ProviderReferenceImage(
                    asset_id=uuid4(),
                    signed_url="https://storage.local/signed/frame.png",
                    mime_type="image/png",
                    filename="frame.png",
                )
            ],
        )
    )
    assert i2v_res.provider_job_id == "rw_task_999"
    polled = runway.get_job("rw_task_999")
    assert polled.status == VideoJobStatus.SUCCEEDED
    assert polled.output_url == "https://cdn.runwayml.com/out.mp4"

    # Config safe_summary never leaks runway_api_key or runway_webhook_secret
    cfg = BaseAppSettings(
        _env_file=None,
        runway_api_key=SecretStr(secret_key),
        runway_webhook_secret=SecretStr("rw_wh_secret_987654"),
    )
    summary = cfg.safe_summary()
    assert summary["runway_api_key_configured"] is True
    assert summary["runway_webhook_secret_configured"] is True
    assert secret_key not in str(summary)
    assert "rw_wh_secret_987654" not in str(summary)

    # Custom registry rejects audio when unsupported
    custom_reg = VideoGatewayRegistry([runway])
    with pytest.raises(CapabilityNotSupportedError):
        custom_reg.select_provider_and_model(
            NormalizedVideoRequest(
                workspace_id=uuid4(),
                mode=VideoGenerationMode.IMAGE_TO_VIDEO,
                prompt="Needs audio",
                reference_asset_ids=[uuid4()],
                aspect_ratio="9:16",
                duration_seconds=5,
                resolution="720p",
                audio=VideoAudioConfig(generate=True),
                provider_preference="runway",
            )
        )
