from __future__ import annotations

from app.services.ffprobe import parse_ffprobe_json


def test_parse_basic() -> None:
    payload = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
             "avg_frame_rate": "30/1", "r_frame_rate": "30/1"}
        ],
        "format": {"duration": "123.456"},
    }
    md = parse_ffprobe_json(payload)
    assert md.width == 1920
    assert md.height == 1080
    assert md.fps == 30.0
    assert md.codec == "h264"
    assert md.duration_seconds == 123.456


def test_parse_no_video_stream() -> None:
    payload = {"streams": [{"codec_type": "audio"}], "format": {"duration": "10"}}
    md = parse_ffprobe_json(payload)
    assert md.width is None
    assert md.height is None
    assert md.fps is None
    assert md.duration_seconds == 10.0


def test_parse_missing_format_uses_stream_duration() -> None:
    payload = {
        "streams": [
            {"codec_type": "video", "width": 1, "height": 1, "duration": "5.0", "avg_frame_rate": "0/0"}
        ],
        "format": {},
    }
    md = parse_ffprobe_json(payload)
    assert md.duration_seconds == 5.0
    assert md.fps is None


def test_parse_handles_zero_fps_string() -> None:
    payload = {
        "streams": [
            {"codec_type": "video", "width": 1, "height": 1, "avg_frame_rate": "0/0", "r_frame_rate": "0/0"}
        ],
        "format": {},
    }
    md = parse_ffprobe_json(payload)
    assert md.fps is None


def test_parse_fps_fraction() -> None:
    payload = {
        "streams": [
            {"codec_type": "video", "width": 1, "height": 1, "avg_frame_rate": "30000/1001"}
        ],
        "format": {},
    }
    md = parse_ffprobe_json(payload)
    assert md.fps is not None
    assert abs(md.fps - 29.97) < 0.01
