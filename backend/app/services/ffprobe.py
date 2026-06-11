from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.errors import FFprobeMissingError


@dataclass(frozen=True)
class ProbedMetadata:
    duration_seconds: float | None
    fps: float | None
    width: int | None
    height: int | None
    codec: str | None


def _parse_fps(rate: str | None) -> float | None:
    if not rate or rate == "0/0":
        return None
    if "/" in rate:
        num, den = rate.split("/", 1)
        try:
            n, d = float(num), float(den)
            return n / d if d else None
        except ValueError:
            return None
    try:
        return float(rate)
    except ValueError:
        return None


def parse_ffprobe_json(data: dict[str, Any]) -> ProbedMetadata:
    streams = data.get("streams") or []
    video_stream: dict[str, Any] | None = next(
        (s for s in streams if s.get("codec_type") == "video"),
        None,
    )

    duration: float | None = None
    fmt = data.get("format") or {}
    if "duration" in fmt:
        try:
            duration = float(fmt["duration"])
        except (TypeError, ValueError):
            duration = None
    if duration is None and video_stream and "duration" in video_stream:
        try:
            duration = float(video_stream["duration"])
        except (TypeError, ValueError):
            duration = None

    if video_stream is None:
        return ProbedMetadata(duration_seconds=duration, fps=None, width=None, height=None, codec=None)

    fps = _parse_fps(video_stream.get("avg_frame_rate")) or _parse_fps(video_stream.get("r_frame_rate"))
    width = video_stream.get("width")
    height = video_stream.get("height")
    codec = video_stream.get("codec_name")

    return ProbedMetadata(
        duration_seconds=duration,
        fps=fps,
        width=int(width) if isinstance(width, int) else None,
        height=int(height) if isinstance(height, int) else None,
        codec=codec,
    )


def probe_file(path: Path, *, which=shutil.which, runner=subprocess.run) -> ProbedMetadata:
    binary = which("ffprobe")
    if not binary:
        raise FFprobeMissingError()
    proc = runner(
        [
            binary,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()[:500]}")
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON: {exc}") from exc
    return parse_ffprobe_json(payload)
