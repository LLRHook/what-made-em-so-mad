from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.ffprobe import ProbedMetadata


@pytest.fixture
def imported_video_id(client, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr("app.api.videos.has_ffmpeg", lambda: True)
    monkeypatch.setattr("app.api.videos.has_ffprobe", lambda: True)
    monkeypatch.setattr(
        "app.api.videos.probe_file",
        lambda _p: ProbedMetadata(duration_seconds=60.0, fps=30.0, width=1280, height=720, codec="h264"),
    )
    monkeypatch.setattr("app.api.videos.generate_thumbnails", lambda *a, **k: [])
    res = client.post(
        "/api/videos",
        files={"file": ("v.mp4", io.BytesIO(b"x" * 1024), "video/mp4")},
    )
    assert res.status_code == 201
    return res.json()["video_id"]


def test_create_clip_draft(client, imported_video_id) -> None:
    res = client.post(
        "/api/clips",
        json={"video_id": imported_video_id, "start_seconds": 10.0, "end_seconds": 20.0, "labels": ["laughing"]},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "draft"
    assert body["labels"] == ["laughing"]
    assert body["output_path"] is None
    assert body["output_url"] is None


def test_create_clip_rejects_inverted_range(client, imported_video_id) -> None:
    res = client.post(
        "/api/clips",
        json={"video_id": imported_video_id, "start_seconds": 30.0, "end_seconds": 10.0},
    )
    # Pydantic 2 raises 422 for validator errors before our endpoint runs.
    assert res.status_code == 422


def test_update_and_delete_clip(client, imported_video_id) -> None:
    draft = client.post(
        "/api/clips",
        json={"video_id": imported_video_id, "start_seconds": 5.0, "end_seconds": 10.0},
    ).json()

    updated = client.patch(f"/api/clips/{draft['id']}", json={"title": "  Keeper  ", "accepted": True})
    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "Keeper"
    assert updated.json()["accepted"] is True

    deleted = client.delete(f"/api/clips/{draft['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/clips/{draft['id']}").status_code == 404


def test_create_clip_unknown_video(client) -> None:
    res = client.post(
        "/api/clips",
        json={"video_id": "ghost", "start_seconds": 0.0, "end_seconds": 1.0},
    )
    assert res.status_code == 404


def test_export_clip_happy_path(client, imported_video_id, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.clips.has_ffmpeg", lambda: True)
    monkeypatch.setattr("app.services.clips.ffmpeg_binary", lambda which=None: "/usr/bin/ffmpeg")

    def fake_run_ffmpeg(cmd):
        Path(cmd[-1]).write_bytes(b"clip-bytes")

    monkeypatch.setattr("app.services.clips.run_ffmpeg", fake_run_ffmpeg)

    draft = client.post(
        "/api/clips",
        json={"video_id": imported_video_id, "start_seconds": 5.0, "end_seconds": 15.0},
    ).json()
    res = client.post(
        f"/api/clips/{draft['id']}/export",
        json={"pre_roll_seconds": 1.0, "post_roll_seconds": 1.0, "mode": "stream_copy_when_possible"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "exported"
    assert body["output_path"] is not None
    assert body["output_url"] == f"/api/clips/{draft['id']}/download"

    download = client.get(body["output_url"])
    assert download.status_code == 200
    assert download.content == b"clip-bytes"


def test_export_clip_missing_ffmpeg(client, imported_video_id, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.clips.has_ffmpeg", lambda: False)
    draft = client.post(
        "/api/clips",
        json={"video_id": imported_video_id, "start_seconds": 0.0, "end_seconds": 1.0},
    ).json()
    res = client.post(f"/api/clips/{draft['id']}/export", json={})
    assert res.status_code == 503
    assert res.json()["detail"]["error"] == "ffmpeg_missing"


def test_export_failure_marks_clip_failed(client, imported_video_id, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.errors import FFmpegExecutionError

    monkeypatch.setattr("app.api.clips.has_ffmpeg", lambda: True)
    monkeypatch.setattr("app.services.clips.ffmpeg_binary", lambda which=None: "/usr/bin/ffmpeg")

    def explode(*_a, **_kw):
        raise FFmpegExecutionError("synthetic failure")

    monkeypatch.setattr("app.services.clips.run_ffmpeg", explode)

    draft = client.post(
        "/api/clips",
        json={"video_id": imported_video_id, "start_seconds": 0.0, "end_seconds": 1.0},
    ).json()
    res = client.post(f"/api/clips/{draft['id']}/export", json={})
    assert res.status_code == 500

    got = client.get(f"/api/clips/{draft['id']}")
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "failed"
    assert body["error_message"] is not None


def test_suggested_clips_and_batch_export(client, imported_video_id, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db.database import connection
    from datetime import datetime, timezone
    import json, uuid

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    job_id = uuid.uuid4().hex
    with connection() as conn:
        conn.execute("DELETE FROM analysis_jobs WHERE video_id = ?", (imported_video_id,))
        conn.execute(
            """INSERT INTO analysis_jobs (id, video_id, status, params_json, versions_json,
                       progress_json, error_message, created_at, started_at, completed_at)
               VALUES (?, ?, 'complete', '{}', '{}', NULL, NULL, ?, ?, ?)""",
            (job_id, imported_video_id, now, now, now),
        )
        conn.execute(
            """INSERT INTO activity_events (id, job_id, start_seconds, end_seconds, label,
                       confidence, source, evidence_json)
               VALUES (?, ?, 10.0, 14.0, 'laughing', 0.88, 'fusion', '{}')""",
            (uuid.uuid4().hex, job_id),
        )

    suggested = client.post(f"/api/videos/{imported_video_id}/suggested-clips", json={"min_confidence": 0.5})
    assert suggested.status_code == 201, suggested.text
    clips = suggested.json()
    assert len(clips) == 1
    assert clips[0]["status"] == "draft"
    assert clips[0]["suggestion_score"] == 0.88
    assert "laughing" in clips[0]["labels"]

    monkeypatch.setattr("app.api.clips.has_ffmpeg", lambda: True)
    monkeypatch.setattr("app.services.clips.ffmpeg_binary", lambda which=None: "/usr/bin/ffmpeg")

    def fake_run_ffmpeg(cmd):
        Path(cmd[-1]).write_bytes(b"clip-bytes")

    monkeypatch.setattr("app.services.clips.run_ffmpeg", fake_run_ffmpeg)

    exported = client.post(
        "/api/clips/batch-export",
        json={
            "clip_ids": [clips[0]["id"]],
            "pre_roll_seconds": 0.0,
            "post_roll_seconds": 0.0,
            "mode": "stream_copy_when_possible",
        },
    )
    assert exported.status_code == 200, exported.text
    assert exported.json()["clips"][0]["status"] == "exported"
    manifest_path = Path(exported.json()["manifest_path"])
    assert manifest_path.exists()
    assert "laughing" in manifest_path.read_text(encoding="utf-8")


def test_get_unknown_clip(client) -> None:
    res = client.get("/api/clips/no-such")
    assert res.status_code == 404
