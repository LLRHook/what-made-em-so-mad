from __future__ import annotations

import shutil
from pathlib import Path

from app.core.config import get_settings
from app.core.paths import video_thumbs_dir
from app.services.ffmpeg import build_thumbnail_command, ffmpeg_binary, run_ffmpeg


def thumbnail_timestamps(duration_seconds: float | None, interval: float) -> list[float]:
    if duration_seconds is None or duration_seconds <= 0 or interval <= 0:
        return [0.0]
    times: list[float] = []
    t = 0.0
    while t < duration_seconds:
        times.append(t)
        t += interval
    if not times or times[-1] < duration_seconds - 0.5:
        times.append(max(0.0, duration_seconds - 0.5))
    return times


def generate_thumbnails(
    video_id: str,
    src: Path,
    duration_seconds: float | None,
    *,
    which=shutil.which,
    interval: float | None = None,
    max_height: int | None = None,
) -> list[Path]:
    settings = get_settings()
    interval = interval if interval is not None else settings.thumbnail_interval_seconds
    max_height = max_height if max_height is not None else settings.thumbnail_max_height
    binary = ffmpeg_binary(which=which)
    out_dir = video_thumbs_dir(video_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    times = thumbnail_timestamps(duration_seconds, interval)
    outputs: list[Path] = []
    for index, ts in enumerate(times):
        dest = out_dir / f"{index:04d}.jpg"
        cmd = build_thumbnail_command(binary, src, dest, time_seconds=ts, max_height=max_height)
        run_ffmpeg(cmd, timeout=60)
        outputs.append(dest)
    return outputs
