"""Unified Video Generation Gateway contracts, state machine, registry, providers, and service."""

from packages.video_gateway.contracts import (
    AssetView,
    NormalizedVideoRequest,
    ProjectView,
    ProviderCancelResult,
    ProviderPollResult,
    ProviderReferenceImage,
    ProviderSubmissionRequest,
    ProviderSubmissionResult,
    VideoAudioConfig,
    VideoJobView,
    VideoModelCapability,
    VideoProvider,
    VideoProviderDescriptor,
    WebhookVerificationResult,
)
from packages.video_gateway.providers import MockVideoProvider, RunwayDevVideoProvider
from packages.video_gateway.registry import VideoGatewayRegistry, build_default_video_registry
from packages.video_gateway.service import VideoGatewayService
from packages.video_gateway.state_machine import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    compute_next_poll_delay_seconds,
    validate_transition,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "TERMINAL_STATUSES",
    "AssetView",
    "MockVideoProvider",
    "NormalizedVideoRequest",
    "ProjectView",
    "ProviderCancelResult",
    "ProviderPollResult",
    "ProviderReferenceImage",
    "ProviderSubmissionRequest",
    "ProviderSubmissionResult",
    "RunwayDevVideoProvider",
    "VideoAudioConfig",
    "VideoGatewayRegistry",
    "VideoGatewayService",
    "VideoJobView",
    "VideoModelCapability",
    "VideoProvider",
    "VideoProviderDescriptor",
    "WebhookVerificationResult",
    "build_default_video_registry",
    "compute_next_poll_delay_seconds",
    "validate_transition",
]
