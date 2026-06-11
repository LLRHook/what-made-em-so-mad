from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.errors import FFmpegExecutionError, FFmpegMissingError
from app.services.ffmpeg import (
    ClipRange,
    build_clip_command,
    build_thumbnail_command,
    clamp_range,
    ffmpeg_binary,
    run_ffmpeg,
)


def test_clamp_range_basic() -> None:
    r = clamp_range(10.0, 20.0, pre_roll=2.0, post_roll=3.0, video_duration=100.0)
    assert r.start_seconds == 8.0
    assert r.end_seconds == 23.0
    assert r.duration_seconds == 15.0


def test_clamp_range_at_video_start() -> None:
    r = clamp_range(1.0, 5.0, pre_roll=5.0, post_roll=2.0, video_duration=100.0)
    assert r.start_seconds == 0.0
    assert r.end_seconds == 7.0


def test_clamp_range_at_video_end() -> None:
    r = clamp_range(95.0, 98.0, pre_roll=1.0, post_roll=10.0, video_duration=100.0)
    assert r.start_seconds == 94.0
    assert r.end_seconds == 100.0


def test_clamp_range_unknown_duration() -> None:
    r = clamp_range(10.0, 20.0, pre_roll=2.0, post_roll=3.0, video_duration=None)
    assert r.start_seconds == 8.0
    assert r.end_seconds == 23.0


def test_clamp_range_rejects_inverted() -> None:
    with pytest.raises(ValueError):
        clamp_range(10.0, 10.0, pre_roll=0.0, post_roll=0.0, video_duration=100.0)


def test_build_clip_command_stream_copy(tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    dest = tmp_path / "out.mp4"
    cmd = build_clip_command(
        "/usr/bin/ffmpeg",
        src,
        dest,
        ClipRange(2.0, 7.5),
        mode="stream_copy_when_possible",
    )
    assert cmd[0] == "/usr/bin/ffmpeg"
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
    assert Path(cmd[-1]) == dest
    assert "-ss" in cmd and cmd[cmd.index("-ss") + 1] == "2.000"
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "5.500"


def test_build_clip_command_re_encode(tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    dest = tmp_path / "out.mp4"
    cmd = build_clip_command(
        "/usr/bin/ffmpeg",
        src,
        dest,
        ClipRange(0.0, 1.0),
        mode="re_encode",
    )
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "libx264"
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "aac"


def test_build_thumbnail_command(tmp_path: Path) -> None:
    cmd = build_thumbnail_command(
        "/usr/bin/ffmpeg",
        tmp_path / "in.mp4",
        tmp_path / "out.jpg",
        time_seconds=12.0,
        max_height=180,
    )
    assert "-frames:v" in cmd and cmd[cmd.index("-frames:v") + 1] == "1"
    assert "-vf" in cmd and cmd[cmd.index("-vf") + 1] == "scale=-2:180"


def test_ffmpeg_binary_missing() -> None:
    with pytest.raises(FFmpegMissingError):
        ffmpeg_binary(which=lambda _: None)


def test_run_ffmpeg_failure_maps_to_error() -> None:
    def fail(*_a, **_kw) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="bad input")

    with pytest.raises(FFmpegExecutionError) as exc:
        run_ffmpeg(["ffmpeg"], runner=fail)
    assert "bad input" in str(exc.value.detail)


def test_run_ffmpeg_success() -> None:
    def ok(*_a, **_kw) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    run_ffmpeg(["ffmpeg"], runner=ok)  # no exception
