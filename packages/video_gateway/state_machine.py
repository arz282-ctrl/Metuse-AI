"""Strict provider-independent video generation job state machine and backoff helpers."""

from __future__ import annotations

from packages.shared.enums import ErrorCode, VideoJobStatus
from packages.shared.errors import MetuseError

TERMINAL_STATUSES: frozenset[VideoJobStatus] = frozenset(
    {
        VideoJobStatus.SUCCEEDED,
        VideoJobStatus.FAILED,
        VideoJobStatus.CANCELLED,
    }
)

ALLOWED_TRANSITIONS: dict[VideoJobStatus, frozenset[VideoJobStatus]] = {
    VideoJobStatus.QUEUED: frozenset(
        {
            VideoJobStatus.VALIDATING,
            VideoJobStatus.FAILED,
            VideoJobStatus.CANCELLED,
        }
    ),
    VideoJobStatus.VALIDATING: frozenset(
        {
            VideoJobStatus.SUBMITTED,
            VideoJobStatus.FAILED,
            VideoJobStatus.CANCELLED,
        }
    ),
    VideoJobStatus.SUBMITTED: frozenset(
        {
            VideoJobStatus.PROCESSING,
            VideoJobStatus.SUCCEEDED,
            VideoJobStatus.FAILED,
            VideoJobStatus.CANCELLED,
        }
    ),
    VideoJobStatus.PROCESSING: frozenset(
        {
            VideoJobStatus.PROCESSING,
            VideoJobStatus.SUCCEEDED,
            VideoJobStatus.FAILED,
            VideoJobStatus.CANCELLED,
        }
    ),
    VideoJobStatus.SUCCEEDED: frozenset(),
    VideoJobStatus.FAILED: frozenset(),
    VideoJobStatus.CANCELLED: frozenset(),
}


def validate_transition(current: VideoJobStatus, target: VideoJobStatus) -> VideoJobStatus:
    """Validate and return target status or raise HTTP 409 INVALID_STATE_TRANSITION."""
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise MetuseError(
            f"Illegal video job state transition: {current.value} -> {target.value}",
            code=ErrorCode.INVALID_STATE_TRANSITION,
            status_code=409,
            details={
                "current_status": current.value,
                "target_status": target.value,
            },
        )
    return target


def compute_next_poll_delay_seconds(
    attempt: int,
    *,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
) -> float:
    """Compute bounded exponential backoff delay in seconds for provider status polling."""
    safe_base = max(0.0, float(base_delay))
    safe_max = max(0.0, float(max_delay))
    exponent = min(max(0, attempt - 1), 60)
    return min(safe_max, safe_base * float(2**exponent))
