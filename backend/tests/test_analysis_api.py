from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from app.services.analysis import AudioWindow
from app.services.analysis import ExpressionSample
from app.services.analysis import IdentityArtifacts
from app.services.analysis import run_next_analysis_job
from app.services.analysis import StreamerCandidate
from app.services.analysis import VadInterval
from app.services.analysis import _embedding_similarity
from app.services.ffprobe import ProbedMetadata


@pytest.fixture
def analysis_video(client, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr("app.api.videos.has_ffmpeg", lambda: True)
    monkeypatch.setattr("app.api.videos.has_ffprobe", lambda: True)
    monkeypatch.setattr(
        "app.api.videos.probe_file",
        lambda _p: ProbedMetadata(duration_seconds=20.0, fps=30.0, width=1280, height=720, codec="h264"),
    )
    monkeypatch.setattr("app.api.videos.generate_thumbnails", lambda *a, **k: [])
    monkeypatch.setattr(
        "app.services.analysis._audio_windows",
        lambda *_a, **_k: [
            AudioWindow(0.0, 5.0, 0.002),
            AudioWindow(5.0, 10.0, 0.01),
            AudioWindow(10.0, 15.0, 0.04),
            AudioWindow(15.0, 20.0, 0.12),
        ],
    )
    monkeypatch.setattr(
        "app.services.analysis._detect_streamer_candidate",
        lambda _video: StreamerCandidate(
            bbox={
                "x": 900,
                "y": 40,
                "width": 300,
                "height": 240,
                "coordinate_space": "source_pixels",
                "method": "opencv_haar_face_candidate",
            },
            prompt={
                "type": "opencv_haar_face_candidate",
                "label": "primary_streamer_candidate",
                "confidence": 0.66,
                "reason": "test face candidate",
            },
            confidence=0.66,
            method="opencv_haar_face_candidate",
        ),
    )
    res = client.post("/api/videos", files={"file": ("vod.mp4", io.BytesIO(b"fake-video"), "video/mp4")})
    assert res.status_code == 201, res.text
    return res.json()["video_id"]


def test_upload_creates_automatic_timeline(client, analysis_video) -> None:
    res = client.get(f"/api/videos/{analysis_video}/timeline")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["job"]["status"] == "complete"
    assert body["streamer_profile"]["confirmed_by"] == "auto"
    assert body["streamer_profile"]["prompt"]["label"] == "primary_streamer_candidate"
    assert body["streamer_profile"]["prompt"]["type"] == "opencv_haar_face_candidate"
    assert body["lifecycle_segments"][0]["state"] == "present"
    assert body["lifecycle_segments"][0]["evidence"]["identity"]["method"] == "opencv_haar_face_candidate"
    assert body["lifecycle_segments"][0]["bbox"]["x"] == 900

    labels = {event["label"] for event in body["activity_events"]}
    assert {"focused", "talking", "yelling/frustrated"}.issubset(labels)
    loud_event = next(event for event in body["activity_events"] if event["label"] == "yelling/frustrated")
    assert "context" in loud_event["evidence"]
    assert "high" in loud_event["evidence"]["context"]["summary"].lower()


def test_force_reanalysis_creates_a_new_job(client, analysis_video) -> None:
    first = client.get(f"/api/videos/{analysis_video}/timeline").json()
    second = client.post(f"/api/videos/{analysis_video}/analyze?force=true")
    assert second.status_code == 200, second.text
    assert second.json()["job"]["id"] != first["job"]["id"]


def test_queued_analysis_job_can_be_read_and_cancelled(client, analysis_video) -> None:
    queued = client.post(f"/api/videos/{analysis_video}/analysis-jobs?force=true&autostart=false")
    assert queued.status_code == 202, queued.text
    job = queued.json()
    assert job["status"] == "queued"
    assert job["progress"]["stage"] == "queued"

    fetched = client.get(f"/api/analysis-jobs/{job['id']}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["status"] == "queued"

    cancelled = client.post(f"/api/analysis-jobs/{job['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"

    timeline = client.get(f"/api/analysis-jobs/{job['id']}/timeline")
    assert timeline.status_code == 200, timeline.text
    assert timeline.json()["job"]["status"] == "cancelled"
    assert timeline.json()["lifecycle_segments"] == []
    assert timeline.json()["activity_events"] == []


def test_queued_analysis_job_runs_to_completion(client, analysis_video) -> None:
    queued = client.post(
        f"/api/videos/{analysis_video}/analysis-jobs?force=true&autostart=false",
        json={"profile": "fast", "transcription_enabled": False},
    )
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["id"]

    run = client.post(f"/api/analysis-jobs/{job_id}/run")
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["status"] == "complete"
    assert body["progress"]["stage"] == "complete"
    assert body["params"]["profile"] == "fast"
    assert body["params"]["streamer_identity"]["label"] == "primary_streamer_candidate"

    timeline = client.get(f"/api/analysis-jobs/{job_id}/timeline")
    assert timeline.status_code == 200, timeline.text
    assert timeline.json()["job"]["status"] == "complete"
    assert timeline.json()["lifecycle_segments"]
    assert timeline.json()["activity_events"]


def test_analysis_job_autostarts_in_background(client, analysis_video) -> None:
    queued = client.post(f"/api/videos/{analysis_video}/analysis-jobs?force=true")
    assert queued.status_code == 202, queued.text
    assert queued.json()["status"] == "queued"

    fetched = client.get(f"/api/analysis-jobs/{queued.json()['id']}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["status"] == "complete"


def test_worker_runs_oldest_queued_analysis_job(client, analysis_video) -> None:
    first = client.post(f"/api/videos/{analysis_video}/analysis-jobs?force=true&autostart=false").json()
    second = client.post(f"/api/videos/{analysis_video}/analysis-jobs?force=true&autostart=false").json()

    completed = run_next_analysis_job()
    assert completed is not None
    assert completed.id == first["id"]
    assert completed.status == "complete"

    remaining = client.get(f"/api/analysis-jobs/{second['id']}")
    assert remaining.status_code == 200, remaining.text
    assert remaining.json()["status"] == "queued"


def test_worker_claim_is_single_winner_without_native_or_media(isolated_settings, monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.db.database import connection
    from app.services.analysis import claim_next_analysis_job, queue_auto_analysis
    from app.services import interval_matching as native
    from tests.reference.synthetic_timeline import seed_timeline

    seed_timeline()
    queued = queue_auto_analysis("synthetic-video", force=True)
    monkeypatch.setattr(native, "_library_path", lambda: tmp_path / "missing-native.so")
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=5)
        return claim_next_analysis_job()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert [job.id for job in results if job is not None] == [queued.id]
    assert results.count(None) == 1
    with connection() as conn:
        assert conn.execute("SELECT status FROM analysis_jobs WHERE id=?", (queued.id,)).fetchone()[0] == "running"
        assert conn.execute("SELECT COUNT(*) FROM activity_events").fetchone()[0] == 1


def test_identity_embedding_similarity_respects_provider_kind() -> None:
    assert _embedding_similarity(
        {"kind": "opencv_sface", "embedding": [1.0, 0.0]},
        {"kind": "hsv_histogram", "embedding": [1.0, 0.0]},
    ) is None
    assert _embedding_similarity(
        {"kind": "opencv_sface", "embedding": [1.0, 0.0]},
        {"kind": "opencv_sface", "embedding": [1.0, 0.0]},
    ) == 1.0


def test_manual_streamer_confirmation_and_roi_candidates(client, analysis_video, isolated_settings, monkeypatch: pytest.MonkeyPatch) -> None:
    candidates = client.get(f"/api/videos/{analysis_video}/roi-candidates")
    assert candidates.status_code == 200, candidates.text
    assert candidates.json()[0]["bbox"]["x"] == 900

    identity_dir = isolated_settings.data_dir / "identity-test"
    identity_dir.mkdir(parents=True, exist_ok=True)
    embedding_path = identity_dir / "streamer.embedding.json"
    embedding_path.write_text('{"embedding": [1.0, 0.0]}', encoding="utf-8")
    crop_path = identity_dir / "streamer.jpg"
    crop_path.write_bytes(b"jpg")
    monkeypatch.setattr(
        "app.services.analysis._create_identity_artifacts",
        lambda *_a, **_k: IdentityArtifacts(
            face_crop_path=str(crop_path),
            embedding_path=str(embedding_path),
            embedding=[1.0, 0.0],
        ),
    )

    res = client.post(
        f"/api/videos/{analysis_video}/streamer-profile",
        json={
            "bbox": {"x": 800, "y": 20, "width": 320, "height": 260},
            "confirmed_time_seconds": 3.5,
            "label": "streamer",
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["confirmed_by"] == "user"
    assert body["prompt"]["type"] == "user_confirmed_roi"
    assert body["roi_bbox"]["width"] == 320
    assert body["face_crop_path"] == str(crop_path)
    assert body["embedding_path"] == str(embedding_path)

    analyzed = client.post(f"/api/videos/{analysis_video}/analyze?force=true")
    assert analyzed.status_code == 200, analyzed.text
    assert analyzed.json()["streamer_profile"]["confirmed_by"] == "user"
    assert analyzed.json()["lifecycle_segments"][0]["bbox"]["x"] == 800
    artifact_ref = analyzed.json()["lifecycle_segments"][0]["artifact_ref"]
    assert artifact_ref is not None
    assert "rectangular_mask" in Path(artifact_ref).read_text(encoding="utf-8")


def test_transcript_cues_strengthen_activity_labels(client, analysis_video) -> None:
    transcript = client.post(
        f"/api/videos/{analysis_video}/transcript",
        json={
            "segments": [
                {
                    "start_seconds": 0.0,
                    "end_seconds": 5.0,
                    "text": "haha that was wild",
                    "source": "manual",
                    "confidence": 0.9,
                }
            ]
        },
    )
    assert transcript.status_code == 201, transcript.text

    res = client.post(f"/api/videos/{analysis_video}/analyze?force=true&transcription_enabled=true")
    assert res.status_code == 200, res.text
    laughing = [event for event in res.json()["activity_events"] if event["label"] == "laughing"]
    assert laughing
    assert laughing[0]["confidence"] >= 0.7
    assert laughing[0]["evidence"]["context"]["signals"]["transcript_text"] == "haha that was wild"


def test_vad_and_expression_features_drive_activity_evidence(client, analysis_video, monkeypatch: pytest.MonkeyPatch) -> None:
    vad = [
        VadInterval(
            start_seconds=5.0,
            end_seconds=10.0,
            speech_probability=0.92,
            source="test_vad",
            evidence={"test": True},
        )
    ]
    expressions = [
        ExpressionSample(
            time_seconds=15.0,
            bbox={"x": 900, "y": 40, "width": 120, "height": 120},
            face_visible=True,
            mouth_open=0.7,
            smile=0.8,
            brow_raise=0.2,
            squint=0.1,
            head_pose={"center_x_ratio": 0.5},
            confidence=0.9,
            evidence={"test": True},
        )
    ]

    monkeypatch.setattr("app.services.analysis._detect_vad_segments", lambda *_a, **_k: vad)

    def fake_expression(job_id, *_a, **_k):
        from app.services.analysis import _insert_expression_features

        _insert_expression_features(job_id, expressions)
        return expressions

    monkeypatch.setattr("app.services.analysis._extract_expression_features", fake_expression)

    res = client.post(f"/api/videos/{analysis_video}/analyze?force=true")
    assert res.status_code == 200, res.text
    body = res.json()

    talking = next(event for event in body["activity_events"] if event["start_seconds"] == 5.0)
    assert talking["label"] == "talking"
    assert talking["evidence"]["context"]["signals"]["vad_source"] == "test_vad"

    laughing = next(event for event in body["activity_events"] if event["start_seconds"] == 15.0)
    assert laughing["label"] == "laughing"
    assert laughing["evidence"]["context"]["signals"]["expression"]["smile"] == 0.8

    vad_res = client.get(f"/api/analysis-jobs/{body['job']['id']}/vad")
    assert vad_res.status_code == 200, vad_res.text
    assert vad_res.json()[0]["source"] == "test_vad"

    expression_res = client.get(f"/api/analysis-jobs/{body['job']['id']}/expression-features")
    assert expression_res.status_code == 200, expression_res.text
    assert expression_res.json()[0]["smile"] == 0.8


def test_diagnostics_export_writes_artifact_bundle(client, analysis_video) -> None:
    timeline = client.get(f"/api/videos/{analysis_video}/timeline").json()

    diagnostics = client.get(f"/api/videos/{analysis_video}/diagnostics")
    assert diagnostics.status_code == 200, diagnostics.text
    assert diagnostics.json()["artifacts"]["fusion_contributions"]
    assert diagnostics.json()["artifacts"]["loudness_graph"]

    exported = client.post(f"/api/videos/{analysis_video}/diagnostics/export")
    assert exported.status_code == 201, exported.text
    body = exported.json()
    assert body["job_id"] == timeline["job"]["id"]
    artifact_path = Path(body["artifact_path"])
    assert artifact_path.exists()

    bundle = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert bundle["export"]["format"] == "wmesm_diagnostics_bundle@1"
    assert bundle["timeline"]["job"]["id"] == timeline["job"]["id"]
    assert bundle["artifacts"]["identity"]["streamer_profile_id"] == timeline["streamer_profile"]["id"]
    assert bundle["artifacts"]["fusion_contributions"]

    cleaned = client.post(f"/api/videos/{analysis_video}/cleanup", json={"diagnostics": True})
    assert cleaned.status_code == 200, cleaned.text
    assert not artifact_path.exists()


def test_segment_edit_split_merge_and_feedback_export(client, analysis_video) -> None:
    timeline = client.get(f"/api/videos/{analysis_video}/timeline").json()
    event = timeline["activity_events"][0]
    event_id = event["id"]

    edited = client.patch(
        f"/api/activity-events/{event_id}",
        json={"label": "silent_reaction", "confidence": 0.8, "evidence_note": "reviewed"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["label"] == "silent_reaction"
    assert edited.json()["evidence"]["user_note"] == "reviewed"

    midpoint = (edited.json()["start_seconds"] + edited.json()["end_seconds"]) / 2
    split = client.post(f"/api/activity-events/{event_id}/split", json={"split_seconds": midpoint})
    assert split.status_code == 200, split.text
    split_ids = [item["id"] for item in split.json()]
    assert len(split_ids) == 2

    merged = client.post("/api/activity-events/merge", json={"segment_ids": split_ids, "label": "focused"})
    assert merged.status_code == 200, merged.text
    assert merged.json()["label"] == "focused"

    feedback = client.get(f"/api/videos/{analysis_video}/feedback-export")
    assert feedback.status_code == 200, feedback.text
    annotation_types = {item["annotation_type"] for item in feedback.json()["annotations"]}
    assert {"edit", "split", "merge"}.issubset(annotation_types)
