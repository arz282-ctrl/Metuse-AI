"""Automated Section 7.5 Media QA gate (Acceptance #12)."""

from __future__ import annotations

from uuid import UUID

from packages.media_engine.contracts import (
    MediaProbeResult,
    MediaQaCheck,
    MediaQaReport,
    TimelinePlan,
)

_DURATION_TOLERANCE_MS = 500
_MAX_BLACK_FRAME_RATIO = 0.15
_MAX_FROZEN_FRAME_RATIO = 0.20
_SAFE_X_MIN = 0.05
_SAFE_X_MAX = 0.95
_SAFE_Y_MIN = 0.10
_SAFE_Y_MAX = 0.85


def run_media_qa(
    *,
    plan: TimelinePlan,
    probe: MediaProbeResult,
    available_asset_ids: set[UUID],
    expect_audio: bool = True,
) -> MediaQaReport:
    """Run all Section 7.5 quality gates and reject corrupt/mismatched outputs."""
    checks: list[MediaQaCheck] = []

    # 1. File decodes and is playable
    checks.append(
        MediaQaCheck(
            name="decodable_and_playable",
            passed=probe.decodable and probe.playable,
            detail=(
                "Output container decoded and playable"
                if (probe.decodable and probe.playable)
                else f"Output failed decode/playability: {probe.error or 'corrupt stream'}"
            ),
        )
    )

    # 2. Duration within requested bounds
    expected_duration_ms = plan.total_duration_ms()
    if probe.duration_ms is None or probe.duration_ms <= 0:
        duration_ok = False
        duration_detail = "Missing or zero output duration"
    else:
        delta = abs(probe.duration_ms - expected_duration_ms)
        duration_ok = delta <= _DURATION_TOLERANCE_MS
        duration_detail = (
            f"Duration {probe.duration_ms}ms vs expected {expected_duration_ms}ms "
            f"(delta {delta}ms, tolerance {_DURATION_TOLERANCE_MS}ms)"
        )
    checks.append(MediaQaCheck(name="duration_bounds", passed=duration_ok, detail=duration_detail))

    # 3. Aspect ratio and canvas dimensions
    dims_ok = (
        probe.width == plan.canvas.width
        and probe.height == plan.canvas.height
        and probe.width is not None
        and probe.height is not None
    )
    checks.append(
        MediaQaCheck(
            name="dimensions_and_aspect_ratio",
            passed=dims_ok,
            detail=(
                f"Output {probe.width}x{probe.height} vs canvas "
                f"{plan.canvas.width}x{plan.canvas.height}"
            ),
        )
    )

    # 4. Audio presence & non-clipping check
    if expect_audio:
        audio_present = probe.has_audio
        not_clipped = probe.audio_peak_db is None or probe.audio_peak_db <= -0.1
        audio_ok = audio_present and not_clipped
        audio_detail = (
            f"Audio present ({probe.audio_codec}), peak {probe.audio_peak_db} dBFS"
            if audio_ok
            else (
                "Expected audio stream missing"
                if not audio_present
                else f"Audio peak clipped at {probe.audio_peak_db} dBFS"
            )
        )
    else:
        audio_ok = True
        audio_detail = "Audio check skipped (silent render requested)"
    checks.append(
        MediaQaCheck(name="audio_presence_and_levels", passed=audio_ok, detail=audio_detail)
    )

    # 5. Black / frozen frames check
    frames_ok = (
        probe.black_frame_ratio <= _MAX_BLACK_FRAME_RATIO
        and probe.frozen_frame_ratio <= _MAX_FROZEN_FRAME_RATIO
    )
    checks.append(
        MediaQaCheck(
            name="no_black_or_frozen_sections",
            passed=frames_ok,
            detail=(
                f"black_frame_ratio={probe.black_frame_ratio:.2f}, "
                f"frozen_frame_ratio={probe.frozen_frame_ratio:.2f}"
            ),
        )
    )

    # 6. Captions timing, safe margins, and CTA end placement
    captions = plan.caption_items()
    bad_captions: list[str] = []
    for idx, cap in enumerate(captions):
        if cap.end_ms > expected_duration_ms + _DURATION_TOLERANCE_MS:
            bad_captions.append(f"caption[{idx}] exceeds timeline duration")
        if not (
            _SAFE_X_MIN <= cap.x_norm <= _SAFE_X_MAX and _SAFE_Y_MIN <= cap.y_norm <= _SAFE_Y_MAX
        ):
            bad_captions.append(f"caption[{idx}] outside safe margins ({cap.x_norm},{cap.y_norm})")
        if cap.is_cta and cap.start_ms < int(expected_duration_ms * 0.5):
            bad_captions.append(f"cta caption[{idx}] appears before final half of clip")
    checks.append(
        MediaQaCheck(
            name="captions_and_cta_safe_margins",
            passed=len(bad_captions) == 0,
            detail="All captions timed inside safe margins"
            if not bad_captions
            else "; ".join(bad_captions),
        )
    )

    # 7. No missing media references
    referenced = set(plan.referenced_asset_ids())
    missing = sorted(str(aid) for aid in (referenced - available_asset_ids))
    checks.append(
        MediaQaCheck(
            name="no_missing_media_references",
            passed=len(missing) == 0,
            detail="All referenced media assets verified"
            if not missing
            else f"Missing asset IDs: {', '.join(missing)}",
        )
    )

    failed = [c.name for c in checks if not c.passed]
    return MediaQaReport(passed=len(failed) == 0, checks=checks, failed_checks=failed)
