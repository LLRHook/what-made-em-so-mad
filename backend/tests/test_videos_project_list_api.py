"""S-001: server-side project list API."""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services import capabilities as caps_mod
from app.services.ffprobe import ProbedMetadata


@pytest.fixture
def stubbed_world(monkeypatch: pytest.MonkeyPatch):
    """Make capability probes report ffmpeg/ffprobe present and the analysis side-effect a no-op.

    The S-001 list/rename/delete behavior is the focus here, not auto-analysis.
    """
    monkeypatch.setattr(caps_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("app.api.videos.has_ffmpeg", lambda: True)
    monkeypatch.setattr("app.api.videos.has_ffprobe", lambda: True)
    monkeypatch.setattr(
        "app.api.videos.probe_file",
        lambda _path: ProbedMetadata(duration_seconds=20.0, fps=30.0, width=1280, height=720, codec="h264"),
    )
    monkeypatch.setattr("app.api.videos.generate_thumbnails", lambda *a, **k: [])
    monkeypatch.setattr("app.api.videos.ensure_auto_analysis", lambda *a, **k: None)


def _upload(client, *, filename: str, body: bytes) -> str:
    res = client.post(
        "/api/videos",
        files={"file": (filename, io.BytesIO(body), "video/mp4")},
    )
    assert res.status_code == 201, res.text
    return res.json()["video_id"]


# ----- GET /api/videos -----


def test_list_videos_empty(client, isolated_settings) -> None:
    res = client.get("/api/videos")
    assert res.status_code == 200
    assert res.json() == []


def test_list_videos_returns_newest_first_with_shape(client, stubbed_world) -> None:
    id_first = _upload(client, filename="alpha.mp4", body=b"alpha-bytes" * 16)
    id_second = _upload(client, filename="beta.mp4", body=b"beta-bytes" * 16)

    res = client.get("/api/videos")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    assert [entry["id"] for entry in body] == [id_second, id_first]

    first_entry = body[0]
    assert first_entry["source_uri"] == "beta.mp4"
    assert first_entry["title"] is None
    assert first_entry["duration_seconds"] == 20.0
    assert first_entry["width"] == 1280
    assert first_entry["height"] == 720
    assert first_entry["source_url"] == f"/api/videos/{id_second}/source"
    assert first_entry["thumbnails"] == []
    assert first_entry["latest_job"] is None
    assert first_entry["clip_count"] == 0


def test_list_videos_surfaces_latest_job_and_clip_count(client, stubbed_world, monkeypatch) -> None:
    # Let auto-analysis run for this test so we can verify latest_job surfacing.
    monkeypatch.setattr("app.api.videos.ensure_auto_analysis", lambda *a, **k: None)  # keep no-op
    video_id = _upload(client, filename="vod.mp4", body=b"vod-bytes" * 32)

    # Insert a synthetic analysis job + a clip directly so the test is decoupled
    # from the heuristic pipeline.
    from app.db.database import connection
    from datetime import datetime, timezone
    import json, uuid

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    job_id = uuid.uuid4().hex
    clip_id = uuid.uuid4().hex
    with connection() as conn:
        conn.execute(
            """INSERT INTO analysis_jobs (id, video_id, status, params_json, versions_json,
                       progress_json, error_message, created_at, started_at, completed_at)
               VALUES (?, ?, 'complete', ?, ?, NULL, NULL, ?, ?, ?)""",
            (job_id, video_id, json.dumps({}), json.dumps({}), now, now, now),
        )
        conn.execute(
            """INSERT INTO clips (id, job_id, video_id, start_seconds, end_seconds,
                       labels_json, title, status, output_path, error_message, created_at)
               VALUES (?, NULL, ?, 1.0, 2.0, '[]', NULL, 'draft', NULL, NULL, ?)""",
            (clip_id, video_id, now),
        )

    res = client.get("/api/videos")
    assert res.status_code == 200
    entry = next(e for e in res.json() if e["id"] == video_id)
    assert entry["clip_count"] == 1
    assert entry["latest_job"] is not None
    assert entry["latest_job"]["id"] == job_id
    assert entry["latest_job"]["status"] == "complete"
    assert entry["latest_job"]["error_message"] is None


# ----- PATCH /api/videos/{id} -----


def test_rename_video_happy_path(client, stubbed_world) -> None:
    video_id = _upload(client, filename="raw.mp4", body=b"x" * 100)

    res = client.patch(f"/api/videos/{video_id}", json={"title": "  My Best Clip  "})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["title"] == "My Best Clip"  # trimmed

    # Verify it shows up in the list view too.
    listed = next(e for e in client.get("/api/videos").json() if e["id"] == video_id)
    assert listed["title"] == "My Best Clip"


def test_rename_video_can_clear_back_to_null(client, stubbed_world) -> None:
    video_id = _upload(client, filename="raw.mp4", body=b"x" * 100)
    client.patch(f"/api/videos/{video_id}", json={"title": "Temp"})
    res = client.patch(f"/api/videos/{video_id}", json={"title": None})
    assert res.status_code == 200
    assert res.json()["title"] is None


def test_rename_video_rejects_whitespace_only(client, stubbed_world) -> None:
    video_id = _upload(client, filename="raw.mp4", body=b"x" * 100)
    res = client.patch(f"/api/videos/{video_id}", json={"title": "    "})
    assert res.status_code == 422


def test_rename_video_rejects_too_long(client, stubbed_world) -> None:
    video_id = _upload(client, filename="raw.mp4", body=b"x" * 100)
    res = client.patch(f"/api/videos/{video_id}", json={"title": "a" * 121})
    assert res.status_code == 422


def test_rename_unknown_video_returns_404(client, isolated_settings) -> None:
    res = client.patch("/api/videos/no-such-id", json={"title": "x"})
    assert res.status_code == 404


# ----- DELETE /api/videos/{id} -----


def test_delete_video_removes_row_and_artifacts(client, stubbed_world) -> None:
    video_id = _upload(client, filename="raw.mp4", body=b"contents-on-disk" * 32)

    # Create a clip via the API so we exercise the cascade.
    clip = client.post(
        "/api/clips",
        json={"video_id": video_id, "start_seconds": 1.0, "end_seconds": 2.0},
    ).json()

    settings = get_settings()
    video_dir = settings.videos_dir / video_id
    clip_dir = settings.clips_dir / clip["id"]
    assert video_dir.exists()
    clip_dir.mkdir(parents=True, exist_ok=True)  # simulate exported clip dir
    (clip_dir / "output.mp4").write_bytes(b"clip-bytes")

    res = client.delete(f"/api/videos/{video_id}")
    assert res.status_code == 204
    assert res.content == b""

    # DB row gone.
    assert client.get(f"/api/videos/{video_id}").status_code == 404
    # Clip cascaded.
    assert client.get(f"/api/clips/{clip['id']}").status_code == 404
    # On-disk artifacts gone.
    assert not video_dir.exists()
    assert not clip_dir.exists()


def test_delete_unknown_video_returns_404(client, isolated_settings) -> None:
    res = client.delete("/api/videos/no-such-id")
    assert res.status_code == 404


def test_delete_is_not_silently_idempotent(client, stubbed_world) -> None:
    """A second DELETE on a now-missing id should 404, not 204.

    This is the safer choice: idempotent-404 makes accidental re-issued deletes
    visible to clients instead of silently succeeding against a deleted resource.
    """
    video_id = _upload(client, filename="raw.mp4", body=b"x" * 100)
    assert client.delete(f"/api/videos/{video_id}").status_code == 204
    assert client.delete(f"/api/videos/{video_id}").status_code == 404
