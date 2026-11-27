"""Allowlisted timeline-to-command compiler for pinned Ucuts & FFmpeg (Section 7.2).

Security invariants:
- NEVER executes arbitrary shell strings or LLM-assembled command lines.
- NEVER passes remote URLs or --cookies-from-browser to Ucuts in cloud workers.
- Always uses verified Ucuts CLI flag `--sections` (never the erroneous `--reels` doc flag).
- Returns strictly validated `list[str]` argv arrays for `subprocess.run(..., shell=False)`.
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.media_engine.contracts import TimelinePlan
from packages.shared.enums import ErrorCode, RenderKind
from packages.shared.errors import MetuseError, ValidationDomainError
from packages.ucuts_adapter.pin import load_ucuts_pin

_SAFE_FONT_RE = re.compile(r"^[A-Za-z0-9 _.-]{1,64}$")
_SAFE_LANG_RE = re.compile(r"^(auto|[a-z]{2,3}(-[A-Za-z]{2,4})?)$")
_SHELL_META_RE = re.compile(r"[;&|`$<>\\]")
_ALLOWED_WHISPER_MODELS = frozenset({"tiny", "base", "small", "medium", "large-v3"})


def _format_ms_as_hms(ms: int) -> str:
    if ms < 0:
        raise ValidationDomainError("Timestamp milliseconds must be non-negative")
    total_seconds = ms // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _assert_safe_local_path(path_value: str, *, label: str) -> str:
    cleaned = path_value.strip()
    if not cleaned:
        raise ValidationDomainError(f"{label} path must not be empty")
    lowered = cleaned.lower()
    if lowered.startswith(("http://", "https://", "ftp://", "file://")):
        raise MetuseError(
            f"{label} must be a local sandboxed asset path, never a remote URL",
            code=ErrorCode.UNSUPPORTED_TIMELINE_OPERATION,
            status_code=422,
        )
    if _SHELL_META_RE.search(cleaned) or "\x00" in cleaned or ".." in Path(cleaned).parts:
        raise MetuseError(
            f"{label} contains prohibited path or shell metacharacters",
            code=ErrorCode.UNSUPPORTED_TIMELINE_OPERATION,
            status_code=422,
        )
    return cleaned


class UcutsSectionSpec(BaseModel):
    """Single validated clip section in milliseconds for Ucuts `--sections`."""

    model_config = ConfigDict(extra="forbid")

    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_range(self) -> UcutsSectionSpec:
        if self.end_ms <= self.start_ms:
            raise ValueError("Ucuts section end_ms must be greater than start_ms")
        return self

    def to_ucuts_flag_segment(self) -> str:
        return f"{_format_ms_as_hms(self.start_ms)}-{_format_ms_as_hms(self.end_ms)}"


class UcutsPipelineRequest(BaseModel):
    """Typed allowlisted parameters for pinned `scripts/ucuts_pipeline.py`."""

    model_config = ConfigDict(extra="forbid")

    local_input_path: str
    output_dir: str
    sections: list[UcutsSectionSpec] = Field(min_length=1, max_length=20)
    whisper_model: str = "small"
    language: str = "auto"
    font: str = "Noto Sans"

    @model_validator(mode="after")
    def validate_allowlisted_flags(self) -> UcutsPipelineRequest:
        _assert_safe_local_path(self.local_input_path, label="local_input_path")
        _assert_safe_local_path(self.output_dir, label="output_dir")
        if self.whisper_model not in _ALLOWED_WHISPER_MODELS:
            raise ValueError(f"Unsupported whisper model '{self.whisper_model}'")
        if not _SAFE_LANG_RE.fullmatch(self.language):
            raise ValueError(f"Unsupported language code '{self.language}'")
        if not _SAFE_FONT_RE.fullmatch(self.font):
            raise ValueError(f"Unsupported caption font name '{self.font}'")
        return self


def build_ucuts_cli_argv(
    request: UcutsPipelineRequest,
    *,
    python_executable: str = "python3",
    ucuts_repo_root: str = "/opt/ucuts",
) -> list[str]:
    """Translate a typed UcutsPipelineRequest into verified `scripts/ucuts_pipeline.py` argv.

    Reconciles Phase 0 audit: strictly uses `--sections` (never `--reels`) and never passes
    browser cookies or remote URLs.
    """
    pin = load_ucuts_pin()
    safe_root = _assert_safe_local_path(ucuts_repo_root, label="ucuts_repo_root")
    script_path = str(Path(safe_root) / pin.entrypoint)
    sections_csv = ",".join(sec.to_ucuts_flag_segment() for sec in request.sections)
    return [
        python_executable,
        script_path,
        "--url",
        request.local_input_path,
        "--sections",
        sections_csv,
        "--model",
        request.whisper_model,
        "--language",
        request.language,
        "--font",
        request.font,
        "--output-dir",
        request.output_dir,
    ]


def build_ffmpeg_render_argv(
    plan: TimelinePlan,
    *,
    asset_local_paths: dict[UUID, str],
    output_path: str,
    render_kind: RenderKind,
) -> list[str]:
    """Compile a validated Section 7.3 TimelinePlan into a strict allowlisted FFmpeg argv list."""
    safe_output = _assert_safe_local_path(output_path, label="output_path")
    clips = plan.video_clips()
    if not clips:
        raise MetuseError(
            "TimelinePlan requires at least one video clip",
            code=ErrorCode.UNSUPPORTED_TIMELINE_OPERATION,
            status_code=422,
        )

    primary = clips[0]
    if primary.asset_id not in asset_local_paths:
        raise MetuseError(
            f"Missing local staged path for asset '{primary.asset_id}'",
            code=ErrorCode.UNSUPPORTED_TIMELINE_OPERATION,
            status_code=422,
        )
    primary_input = _assert_safe_local_path(
        asset_local_paths[primary.asset_id], label="primary_input"
    )

    width = plan.canvas.width
    height = plan.canvas.height
    fps = plan.canvas.fps
    if render_kind == RenderKind.PREVIEW:
        # Preview renders keep exact aspect ratio at lightweight CRF/preset for fast turnaround
        preset = "veryfast"
        crf = "28"
    else:
        preset = "medium"
        crf = "19"

    start_sec = f"{primary.source_in_ms / 1000:.3f}"
    duration_sec = f"{primary.duration_ms / 1000:.3f}"

    if primary.fit == "cover":
        scale_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={fps},format=yuv420p"
        )
    elif primary.fit == "contain":
        scale_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p"
        )
    else:
        scale_filter = f"scale={width}:{height},fps={fps},format=yuv420p"

    argv: list[str] = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        start_sec,
        "-t",
        duration_sec,
        "-i",
        primary_input,
        "-vf",
        scale_filter,
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        crf,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-af",
        "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-movflags",
        "+faststart",
    ]

    if plan.export.target_bitrate:
        argv.extend(["-b:v", plan.export.target_bitrate])

    argv.append(safe_output)
    return argv
