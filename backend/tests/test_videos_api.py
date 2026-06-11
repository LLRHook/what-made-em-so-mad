from __future__ import annotations

import io

import pytest

from app.services import capabilities as caps_mod
from app.services import thumbnails as thumbs_mod
from app.services import videos as videos_svc
from app.services.ffprobe import ProbedMetadata


@pytest.fixture
def stub_ffmpeg_world(monkeypatch: pytest.MonkeyPatch):
    """Make capability probes return True and probe_file return canned metadata, without invoking real binaries."""
    monkeypatch.setattr(caps_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("app.api.videos.has_ffmpeg", lambda: True)
    monkeypatch.setattr("app.api.videos.has_ffprobe", lambda: True)
    monkeypatch.setattr(
        "app.api.videos.probe_file",
        lambda _path: ProbedMetadata(duration_seconds=12.0, fps=30.0, width=640, height=360, codec="h264"),
    )
    # Skip thumbnails entirely (they'd shell out to ffmpeg).
    monkeypatch.setattr("app.api.videos.generate_thumbnails", lambda *a, **k: [])


def test_upload_video_round_trip(client, stub_ffmpeg_world) -> None:
    content = b"fake-mp4-bytes-" * 64
    res = client.post(
        "/api/videos",
        files={"file": ("clip.mp4", io.BytesIO(content), "video/mp4")},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    video_id = body["video_id"]
    assert body["duplicate_of"] is None

    got = client.get(f"/api/videos/{video_id}")
    assert got.status_code == 200
    record = got.json()
    assert record["id"] == video_id
    assert record["source_url"] == f"/api/videos/{video_id}/source"
    assert record["duration_seconds"] == 12.0
    assert record["fps"] == 30.0
    assert record["width"] == 640
    assert record["height"] == 360

    source = client.get(record["source_url"])
    assert source.status_code == 200
    assert source.content == content


def test_upload_returns_503_when_ffmpeg_missing(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.videos.has_ffmpeg", lambda: False)
    monkeypatch.setattr("app.api.videos.has_ffprobe", lambda: True)
    res = client.post(
        "/api/videos",
        files={"file": ("clip.mp4", io.BytesIO(b"x"), "video/mp4")},
    )
    assert res.status_code == 503
    body = res.json()
    assert body["detail"]["error"] == "ffmpeg_missing"
    assert "setup_hint" in body["detail"]


def test_upload_returns_503_when_ffprobe_missing(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.videos.has_ffmpeg", lambda: True)
    monkeypatch.setattr("app.api.videos.has_ffprobe", lambda: False)
    res = client.post(
        "/api/videos",
        files={"file": ("clip.mp4", io.BytesIO(b"x"), "video/mp4")},
    )
    assert res.status_code == 503
    assert res.json()["detail"]["error"] == "ffprobe_missing"


def test_upload_rejects_unsupported_extension(client) -> None:
    res = client.post(
        "/api/videos",
        files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert res.status_code == 400
    assert res.json()["detail"]["error"] == "validation_failed"


def test_duplicate_upload_deduplicates(client, stub_ffmpeg_world) -> None:
    payload = b"same-bytes-every-time"
    res1 = client.post("/api/videos", files={"file": ("a.mp4", io.BytesIO(payload), "video/mp4")})
    assert res1.status_code == 201
    id1 = res1.json()["video_id"]

    res2 = client.post("/api/videos", files={"file": ("a-renamed.mp4", io.BytesIO(payload), "video/mp4")})
    assert res2.status_code == 201
    body2 = res2.json()
    assert body2["duplicate_of"] == id1
    assert body2["video_id"] == id1


def test_get_unknown_video_404(client) -> None:
    res = client.get("/api/videos/does-not-exist")
    assert res.status_code == 404
    assert res.json()["detail"]["error"] == "not_found"


def test_from_url_returns_501_when_ytdlp_missing(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.videos.has_yt_dlp", lambda: False)
    res = client.post("/api/videos/from-url", json={"url": "https://example.com/v"})
    assert res.status_code == 501
    assert res.json()["detail"]["error"] == "yt_dlp_missing"
