from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.core.errors import FFmpegExecutionError, FFmpegMissingError


@dataclass(frozen=True)
class ClipRange:
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)


def clamp_range(
    raw_start: float,
    raw_end: float,
    *,
    pre_roll: float,
    post_roll: float,
    video_duration: float | None,
) -> ClipRange:
    """Apply pre/post roll padding and clamp to [0, duration].

    If `video_duration` is None (no metadata), only the lower bound is clamped.
    """
    if raw_end <= raw_start:
        raise ValueError("end_seconds must be greater than start_seconds")
    start = max(0.0, raw_start - max(0.0, pre_roll))
    end = raw_end + max(0.0, post_roll)
    if video_duration is not None:
        end = min(end, video_duration)
        if end <= start:
            end = min(video_duration, raw_end)
            start = max(0.0, end - 0.1)
    return ClipRange(start_seconds=start, end_seconds=end)


def build_clip_command(
    binary: str,
    src: Path,
    dest: Path,
    clip: ClipRange,
    *,
    mode: Literal["stream_copy_when_possible", "re_encode"],
) -> list[str]:
    """Build an ffmpeg argv for extracting `clip` from `src` into `dest`.

    Stream-copy is fast and lossless but only frame-accurate at keyframes.
    Re-encode is slower and lossy but produces an exact cut. The caller
    chooses; the planner here only constructs the args.
    """
    common = [
        binary,
        "-nostdin",
        "-y",
        "-ss", f"{clip.start_seconds:.3f}",
        "-i", str(src),
        "-t", f"{clip.duration_seconds:.3f}",
    ]
    if mode == "stream_copy_when_possible":
        return common + ["-c", "copy", "-movflags", "+faststart", str(dest)]
    return common + [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "160k",
        "-movflags", "+faststart",
        str(dest),
    ]


def build_thumbnail_command(
    binary: str,
    src: Path,
    dest: Path,
    *,
    time_seconds: float,
    max_height: int,
) -> list[str]:
    return [
        binary,
        "-nostdin",
        "-y",
        "-ss", f"{time_seconds:.3f}",
        "-i", str(src),
        "-frames:v", "1",
        "-vf", f"scale=-2:{max_height}",
        "-q:v", "5",
        str(dest),
    ]


def ffmpeg_binary(*, which=shutil.which) -> str:
    binary = which("ffmpeg")
    if not binary:
        raise FFmpegMissingError()
    return binary


def run_ffmpeg(args: list[str], *, runner=subprocess.run, timeout: int = 600) -> None:
    proc = runner(args, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise FFmpegExecutionError(proc.stderr or "")
