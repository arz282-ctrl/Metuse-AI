"""Phase 4 unit tests: Ucuts pin & CLI translator, FFmpeg argv, Media Probe, and Section 7.5 QA."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.media_engine import (
    CanvasSpec,
    CaptionItem,
    MockRenderBackend,
    TimelinePlan,
    TimelineTrack,
    VideoClip,
    build_synthetic_mp4_bytes,
    probe_media_bytes,
    run_media_qa,
)
from packages.shared.enums import ErrorCode, RenderKind
from packages.shared.errors import MetuseError
from packages.ucuts_adapter import (
    EXPECTED_UCUTS_COMMIT,
    UcutsPipelineRequest,
    UcutsSectionSpec,
    build_ffmpeg_render_argv,
    build_ucuts_cli_argv,
    load_ucuts_pin,
)

pytestmark = pytest.mark.unit


def test_ucuts_pin_and_allowlisted_cli_translation_rejects_urls_and_shell_injection() -> None:
    """Verify UCUTS_PIN matches Phase 0 audit, uses --sections (never --reels), blocks injection."""
    pin = load_ucuts_pin()
    assert pin.pinned_commit == EXPECTED_UCUTS_COMMIT
    assert pin.entrypoint == "scripts/ucuts_pipeline.py"

    req = UcutsPipelineRequest(
        local_input_path="/sandbox/inputs/clip.mp4",
        output_dir="/sandbox/out/reels",
        sections=[
            UcutsSectionSpec(start_ms=0, end_ms=7000),
            UcutsSectionSpec(start_ms=10000, end_ms=25000),
        ],
        whisper_model="small",
        language="en",
        font="Noto Sans",
    )
    argv = build_ucuts_cli_argv(req, ucuts_repo_root="/opt/ucuts")
    assert "--sections" in argv
    assert "00:00:00-00:00:07,00:00:10-00:00:25" in argv
    assert "--reels" not in argv
    assert "--cookies-from-browser" not in argv

    # Remote URLs must be rejected
    with pytest.raises((MetuseError, ValidationError)):
        UcutsPipelineRequest(
            local_input_path="https://youtube.com/watch?v=abc",
            output_dir="/sandbox/out",
            sections=[UcutsSectionSpec(start_ms=0, end_ms=5000)],
        )

    # Shell metacharacters / traversal must be rejected
    with pytest.raises((MetuseError, ValidationError)):
        UcutsPipelineRequest(
            local_input_path="/sandbox/inputs/clip.mp4; rm -rf /",
            output_dir="/sandbox/out",
            sections=[UcutsSectionSpec(start_ms=0, end_ms=5000)],
        )


def test_ffmpeg_timeline_compiler_and_mock_renderer() -> None:
    """TimelinePlan compiles into safe allowlisted FFmpeg argv with H.264/AAC/loudnorm/faststart."""
    project_id = uuid4()
    clip_asset_id = uuid4()
    plan = TimelinePlan(
        project_id=project_id,
        canvas=CanvasSpec(width=1080, height=1920, fps=30),
        tracks=[
            TimelineTrack(
                id="video-main",
                type="video",
                clips=[
                    VideoClip(
                        asset_id=clip_asset_id,
                        source_in_ms=0,
                        source_out_ms=7000,
                        timeline_start_ms=0,
                        fit="cover",
                    )
                ],
            )
        ],
    )
    argv = build_ffmpeg_render_argv(
        plan,
        asset_local_paths={clip_asset_id: "/sandbox/inputs/clip.mp4"},
        output_path="/sandbox/out/final.mp4",
        render_kind=RenderKind.FINAL,
    )
    assert argv[0] == "ffmpeg"
    assert "libx264" in argv
    assert "yuv420p" in argv
    assert "aac" in argv
    assert "+faststart" in argv
    assert "loudnorm=I=-14:TP=-1.5:LRA=11" in argv

    # Path traversal in output_path rejected
    with pytest.raises(MetuseError) as exc_info:
        build_ffmpeg_render_argv(
            plan,
            asset_local_paths={clip_asset_id: "/sandbox/inputs/clip.mp4"},
            output_path="/sandbox/out/../../etc/passwd",
            render_kind=RenderKind.PREVIEW,
        )
    assert exc_info.value.code == ErrorCode.UNSUPPORTED_TIMELINE_OPERATION


def test_media_qa_passes_valid_output_and_rejects_corrupt_wrong_aspect_and_bad_captions() -> None:
    """Acceptance #12: Section 7.5 Media QA passes valid MP4 and rejects corrupt/mismatched ones."""
    project_id = uuid4()
    asset_id = uuid4()
    valid_plan = TimelinePlan(
        project_id=project_id,
        canvas=CanvasSpec(width=1080, height=1920, fps=30),
        tracks=[
            TimelineTrack(
                id="video-main",
                type="video",
                clips=[VideoClip(asset_id=asset_id, source_in_ms=0, source_out_ms=6000)],
            ),
            TimelineTrack(
                id="captions",
                type="text",
                items=[
                    CaptionItem(
                        text="Opening hook",
                        start_ms=0,
                        end_ms=2500,
                        x_norm=0.5,
                        y_norm=0.75,
                    ),
                    CaptionItem(
                        text="Follow for more",
                        start_ms=4000,
                        end_ms=5900,
                        x_norm=0.5,
                        y_norm=0.80,
                        is_cta=True,
                    ),
                ],
            ),
        ],
    )

    good_out = MockRenderBackend().render(
        plan=valid_plan,
        asset_bytes_by_id={asset_id: build_synthetic_mp4_bytes(duration_ms=6000)},
        render_kind=RenderKind.PREVIEW,
    )
    good_probe = probe_media_bytes(good_out.output_bytes, declared_mime="video/mp4")
    good_qa = run_media_qa(plan=valid_plan, probe=good_probe, available_asset_ids={asset_id})
    assert good_qa.passed is True
    assert good_qa.failed_checks == []

    # 1. Corrupt bytes rejected
    corrupt_out = MockRenderBackend(corrupt_output=True).render(
        plan=valid_plan,
        asset_bytes_by_id={asset_id: build_synthetic_mp4_bytes(duration_ms=6000)},
        render_kind=RenderKind.PREVIEW,
    )
    corrupt_probe = probe_media_bytes(corrupt_out.output_bytes, declared_mime="video/mp4")
    corrupt_qa = run_media_qa(plan=valid_plan, probe=corrupt_probe, available_asset_ids={asset_id})
    assert corrupt_qa.passed is False
    assert "decodable_and_playable" in corrupt_qa.failed_checks

    # 2. Wrong aspect ratio / dimensions (1920x1080 instead of 1080x1920) rejected
    wrong_aspect_out = MockRenderBackend(override_width=1920, override_height=1080).render(
        plan=valid_plan,
        asset_bytes_by_id={asset_id: build_synthetic_mp4_bytes(duration_ms=6000)},
        render_kind=RenderKind.FINAL,
    )
    wrong_aspect_probe = probe_media_bytes(wrong_aspect_out.output_bytes, declared_mime="video/mp4")
    wrong_aspect_qa = run_media_qa(
        plan=valid_plan, probe=wrong_aspect_probe, available_asset_ids={asset_id}
    )
    assert wrong_aspect_qa.passed is False
    assert "dimensions_and_aspect_ratio" in wrong_aspect_qa.failed_checks

    # 3. Caption outside safe margin rejected
    unsafe_plan = TimelinePlan(
        project_id=project_id,
        canvas=CanvasSpec(width=1080, height=1920, fps=30),
        tracks=[
            TimelineTrack(
                id="video-main",
                type="video",
                clips=[VideoClip(asset_id=asset_id, source_in_ms=0, source_out_ms=6000)],
            ),
            TimelineTrack(
                id="captions",
                type="text",
                items=[
                    CaptionItem(
                        text="Clipped off bottom edge",
                        start_ms=0,
                        end_ms=2000,
                        x_norm=0.5,
                        y_norm=0.98,
                    )
                ],
            ),
        ],
    )
    unsafe_qa = run_media_qa(plan=unsafe_plan, probe=good_probe, available_asset_ids={asset_id})
    assert unsafe_qa.passed is False
    assert "captions_and_cta_safe_margins" in unsafe_qa.failed_checks
