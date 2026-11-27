"""Video provider capability registry and deterministic routing engine (Section 6.6)."""

from __future__ import annotations

from collections.abc import Sequence

from packages.shared.config import BaseAppSettings
from packages.shared.enums import ErrorCode
from packages.shared.errors import (
    CapabilityNotSupportedError,
    MetuseError,
    NotFoundError,
    ProviderDisabledError,
)
from packages.video_gateway.contracts import (
    NormalizedVideoRequest,
    VideoModelCapability,
    VideoProvider,
    VideoProviderDescriptor,
)
from packages.video_gateway.providers.mock import MockVideoProvider
from packages.video_gateway.providers.runway import RunwayDevVideoProvider


class VideoGatewayRegistry:
    """In-memory registry of video generation providers and their advertised capabilities."""

    def __init__(self, providers: Sequence[VideoProvider] = ()) -> None:
        self._providers: dict[str, VideoProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: VideoProvider) -> None:
        """Register or replace a video provider adapter by its provider_id."""
        self._providers[provider.provider_id] = provider

    def get_provider(self, provider_id: str) -> VideoProvider:
        """Look up a registered provider by ID or raise NotFoundError."""
        provider = self._providers.get(provider_id)
        if provider is None:
            raise NotFoundError(
                f"Video provider '{provider_id}' is not registered",
                details={"provider": provider_id},
            )
        return provider

    def list_providers(self) -> list[VideoProviderDescriptor]:
        """Return descriptors for all registered video providers."""
        return [
            VideoProviderDescriptor(
                provider=provider.provider_id,
                display_name=provider.display_name,
                enabled=provider.is_enabled,
                webhook_support=provider.supports_webhooks,
                models=provider.capabilities(),
            )
            for provider in self._providers.values()
        ]

    def list_models(self, *, enabled_only: bool = False) -> list[VideoModelCapability]:
        """Return all advertised model capabilities across registered providers."""
        models: list[VideoModelCapability] = []
        for provider in self._providers.values():
            for cap in provider.capabilities():
                if enabled_only and (not provider.is_enabled or not cap.enabled):
                    continue
                models.append(cap)
        return models

    def select_provider_and_model(
        self,
        request: NormalizedVideoRequest,
    ) -> tuple[VideoProvider, VideoModelCapability]:
        """Select the optimal eligible provider & model for a normalized video request.

        Enforces Section 6.6 rules:
        - Explicit provider_preference pinning (rejects unknown or disabled providers).
        - Strict capability matching (mode, aspect_ratio, duration, resolution, reference count,
          audio generation, optional model_preference).
        - Cost-first routing by default, or high-quality tier preference when requested.
        """
        candidate_providers: list[VideoProvider]
        if request.provider_preference != "auto":
            pinned = self._providers.get(request.provider_preference)
            if pinned is None:
                raise CapabilityNotSupportedError(
                    f"Requested video provider '{request.provider_preference}' is not supported",
                    details={"provider_preference": request.provider_preference},
                )
            if not pinned.is_enabled:
                raise ProviderDisabledError(
                    f"Video provider '{pinned.provider_id}' is currently disabled "
                    "(credentials not configured or provider turned off)",
                    details={"provider": pinned.provider_id},
                )
            candidate_providers = [pinned]
        else:
            candidate_providers = [p for p in self._providers.values() if p.is_enabled]
            if not candidate_providers:
                raise ProviderDisabledError(
                    "No video generation providers are currently enabled",
                    details={"provider_preference": "auto"},
                )

        eligible: list[tuple[VideoProvider, VideoModelCapability]] = []
        rejection_reasons: list[dict[str, str]] = []
        ref_count = len(request.reference_asset_ids)

        for provider in candidate_providers:
            for cap in provider.capabilities():
                if not cap.enabled:
                    rejection_reasons.append(
                        {"model": f"{cap.provider}/{cap.model}", "reason": "model_disabled"}
                    )
                    continue
                if request.model_preference and cap.model != request.model_preference:
                    rejection_reasons.append(
                        {"model": f"{cap.provider}/{cap.model}", "reason": "model_mismatch"}
                    )
                    continue
                if request.mode not in cap.modes:
                    rejection_reasons.append(
                        {
                            "model": f"{cap.provider}/{cap.model}",
                            "reason": f"mode '{request.mode.value}' unsupported",
                        }
                    )
                    continue
                if request.aspect_ratio not in cap.aspect_ratios:
                    rejection_reasons.append(
                        {
                            "model": f"{cap.provider}/{cap.model}",
                            "reason": f"aspect_ratio '{request.aspect_ratio}' unsupported",
                        }
                    )
                    continue
                if request.duration_seconds not in cap.durations_seconds:
                    rejection_reasons.append(
                        {
                            "model": f"{cap.provider}/{cap.model}",
                            "reason": f"duration_seconds {request.duration_seconds} unsupported",
                        }
                    )
                    continue
                if request.resolution not in cap.resolutions:
                    rejection_reasons.append(
                        {
                            "model": f"{cap.provider}/{cap.model}",
                            "reason": f"resolution '{request.resolution}' unsupported",
                        }
                    )
                    continue
                if ref_count > cap.max_reference_images:
                    rejection_reasons.append(
                        {
                            "model": f"{cap.provider}/{cap.model}",
                            "reason": (
                                f"reference_asset_ids count {ref_count} exceeds "
                                f"max_reference_images {cap.max_reference_images}"
                            ),
                        }
                    )
                    continue
                if request.audio.generate and not cap.audio_generation:
                    rejection_reasons.append(
                        {
                            "model": f"{cap.provider}/{cap.model}",
                            "reason": "audio_generation unsupported",
                        }
                    )
                    continue
                eligible.append((provider, cap))

        if not eligible:
            raise CapabilityNotSupportedError(
                "No enabled video provider model supports the requested generation parameters",
                details={
                    "mode": request.mode.value,
                    "aspect_ratio": request.aspect_ratio,
                    "duration_seconds": request.duration_seconds,
                    "resolution": request.resolution,
                    "reference_count": ref_count,
                    "audio_generate": request.audio.generate,
                    "provider_preference": request.provider_preference,
                    "model_preference": request.model_preference,
                    "rejections": rejection_reasons[:10],
                },
            )

        if request.quality_preference == "high_quality":
            eligible.sort(
                key=lambda item: (
                    0 if item[1].quality_tier == "high" else 1,
                    item[1].cost_per_second_usd,
                    item[1].provider,
                    item[1].model,
                )
            )
        else:
            eligible.sort(
                key=lambda item: (
                    item[1].cost_per_second_usd,
                    item[1].provider,
                    item[1].model,
                )
            )

        selected = eligible[0]
        if not selected[0].is_enabled:
            raise MetuseError(
                "Selected provider is disabled",
                code=ErrorCode.NO_ELIGIBLE_PROVIDER,
                status_code=422,
            )
        return selected


def build_default_video_registry(settings: BaseAppSettings) -> VideoGatewayRegistry:
    """Build the default video gateway registry from BaseAppSettings.

    - MockVideoProvider is enabled unless VIDEO_GATEWAY_MODE=live.
    - RunwayDevVideoProvider is registered always but enabled ONLY when RUNWAY_API_KEY is set.
    """
    mock_enabled = settings.video_gateway_mode.strip().lower() != "live"
    mock_provider = MockVideoProvider(enabled=mock_enabled)
    runway_provider = RunwayDevVideoProvider(
        base_url=settings.runway_base_url,
        api_key=settings.runway_api_key.get_secret_value(),
        api_version=settings.runway_api_version,
        webhook_secret=settings.runway_webhook_secret.get_secret_value(),
    )
    return VideoGatewayRegistry([mock_provider, runway_provider])
