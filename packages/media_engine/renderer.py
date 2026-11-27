"""Isolated FFmpeg and deterministic Mock render backends (Section 7.4)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from packages.media_engine.contracts import TimelinePlan
from packages.media_engine.probe import build_synthetic_mp4_bytes
from packages.shared.config import BaseAppSettings
from packages.shared.enums import RenderKind
from packages.shared.errors import ExternalServiceError
from packages.ucuts_adapter.translator import build_ffmpeg_render_argv


@dataclass(frozen=True)
class RenderOutput:
    """Raw output bytes and telemetry returned by a RenderBackend."""

    renderer_name: str
    output_bytes: bytes
    command_manifest: list[str]
    cpu_time_ms: int
    mime_type: str = "video/mp4"


class RenderBackend(Protocol):
    """Protocol for cloud render backends (Mock or Isolated FFmpeg)."""

    @property
    def renderer_name(self) -> str: ...

    def render(
        self,
        *,
        plan: TimelinePlan,
        asset_bytes_by_id: dict[UUID, bytes],
        render_kind: RenderKind,
    ) -> RenderOutput: ...


class MockRenderBackend:
    """$0 deterministic offline renderer producing valid MP4 bytes with probe metadata."""

    def __init__(
        self,
        *,
        corrupt_output: bool = False,
        override_width: int | None = None,
        override_height: int | None = None,
        override_duration_ms: int | None = None,
        override_has_audio: bool = True,
    ) -> None:
        self._corrupt_output = corrupt_output
        self._override_width = override_width
        self._override_height = override_height
        self._override_duration_ms = override_duration_ms
        self._override_has_audio = override_has_audio

    @property
    def renderer_name(self) -> str:
        return "mock"

    def render(
        self,
        *,
        plan: TimelinePlan,
        asset_bytes_by_id: dict[UUID, bytes],
        render_kind: RenderKind,
    ) -> RenderOutput:
        start = time.monotonic()
        staged_paths = {aid: f"/sandbox/inputs/{aid}.mp4" for aid in asset_bytes_by_id}
        argv = build_ffmpeg_render_argv(
            plan,
            asset_local_paths=staged_paths,
            output_path=f"/sandbox/out/{render_kind.value}.mp4",
            render_kind=render_kind,
        )
        if self._corrupt_output:
            output_bytes = b"CORRUPT_NOT_AN_MP4_STREAM"
        else:
            output_bytes = build_synthetic_mp4_bytes(
                width=self._override_width or plan.canvas.width,
                height=self._override_height or plan.canvas.height,
                duration_ms=self._override_duration_ms or plan.total_duration_ms(),
                fps=plan.canvas.fps,
                has_audio=self._override_has_audio,
                tag=f"render-{render_kind.value}",
            )
        elapsed_ms = max(1, int((time.monotonic() - start) * 1000))
        return RenderOutput(
            renderer_name=self.renderer_name,
            output_bytes=output_bytes,
            command_manifest=argv,
            cpu_time_ms=elapsed_ms,
        )


class IsolatedFfmpegRenderBackend:
    """Isolated local subprocess FFmpeg renderer (shell=False, bounded timeout/size, no secrets)."""

    def __init__(
        self,
        *,
        timeout_seconds: int = 120,
        max_output_bytes: int = 250 * 1024 * 1024,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes

    @property
    def renderer_name(self) -> str:
        return "ffmpeg_ucuts"

    def render(
        self,
        *,
        plan: TimelinePlan,
        asset_bytes_by_id: dict[UUID, bytes],
        render_kind: RenderKind,
    ) -> RenderOutput:
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            raise ExternalServiceError("ffmpeg binary is not installed in render worker runtime")

        start = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="metuse-render-") as tmp_dir:
            tmp_root = Path(tmp_dir)
            staged_paths: dict[UUID, str] = {}
            for aid, raw in asset_bytes_by_id.items():
                target = tmp_root / f"in_{aid.hex}.mp4"
                target.write_bytes(raw)
                staged_paths[aid] = str(target)

            out_file = tmp_root / f"out_{render_kind.value}.mp4"
            argv = build_ffmpeg_render_argv(
                plan,
                asset_local_paths=staged_paths,
                output_path=str(out_file),
                render_kind=render_kind,
            )
            argv[0] = ffmpeg_bin

            minimal_env = {"PATH": str(Path(ffmpeg_bin).parent)}
            try:
                proc = subprocess.run(  # noqa: S603
                    argv,
                    shell=False,
                    env=minimal_env,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ExternalServiceError(
                    f"Render timed out after {self._timeout_seconds}s"
                ) from exc

            if proc.returncode != 0 or not out_file.exists():
                raise ExternalServiceError("Isolated FFmpeg render process failed")

            out_bytes = out_file.read_bytes()
            if len(out_bytes) > self._max_output_bytes:
                raise ExternalServiceError("Rendered output exceeded configured file size limit")

        elapsed_ms = max(1, int((time.monotonic() - start) * 1000))
        return RenderOutput(
            renderer_name=self.renderer_name,
            output_bytes=out_bytes,
            command_manifest=argv,
            cpu_time_ms=elapsed_ms,
        )


def create_render_backend(settings: BaseAppSettings) -> RenderBackend:
    """Select MockRenderBackend (default $0 preview) or IsolatedFfmpegRenderBackend."""
    if settings.render_mode.strip().lower() == "ffmpeg":
        return IsolatedFfmpegRenderBackend(
            timeout_seconds=settings.render_timeout_seconds,
            max_output_bytes=settings.render_max_output_bytes,
        )
    return MockRenderBackend()
