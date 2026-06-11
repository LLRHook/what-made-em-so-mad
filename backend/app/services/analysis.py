from __future__ import annotations

import json
import math
import shutil
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import __version__
from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationFailedError
from app.core.paths import video_identity_dir, video_masks_dir, video_roi_dir
from app.db.database import connection
from app.domain.models import (
    ActivityEventResponse,
    AnalysisJobResponse,
    BBoxRequest,
    ExpressionFeatureResponse,
    FeedbackExportResponse,
    RoiCandidateResponse,
    SegmentMergeRequest,
    SegmentSplitRequest,
    SegmentUpdateRequest,
    StreamerProfileResponse,
    TimelineResponse,
    TranscriptionGenerateRequest,
    TrackSegmentResponse,
    TranscriptSegmentCreate,
    TranscriptSegmentResponse,
    VadSegmentResponse,
)

ANALYSIS_KIND = "auto_heuristic_segments"
ANALYSIS_VERSION = 1
WINDOW_SECONDS = 5.0
MIN_EVENT_SECONDS = 1.0
FACE_SAMPLE_COUNT = 24
FACE_SAMPLE_MAX_SECONDS = 180.0
PROFILE_WINDOW_SECONDS = {"fast": 5.0, "balanced": 2.5, "best": 1.0}


@dataclass(frozen=True)
class AudioWindow:
    start_seconds: float
    end_seconds: float
    rms: float


@dataclass(frozen=True)
class LabeledWindow:
    start_seconds: float
    end_seconds: float
    label: str
    confidence: float
    evidence: dict[str, Any]


@dataclass(frozen=True)
class StreamerCandidate:
    bbox: dict[str, Any]
    prompt: dict[str, Any]
    confidence: float
    method: str


@dataclass(frozen=True)
class VadInterval:
    start_seconds: float
    end_seconds: float
    speech_probability: float
    source: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class ExpressionSample:
    time_seconds: float
    bbox: dict[str, Any] | None
    face_visible: bool
    mouth_open: float | None
    smile: float | None
    brow_raise: float | None
    squint: float | None
    head_pose: dict[str, Any] | None
    confidence: float
    evidence: dict[str, Any]


@dataclass(frozen=True)
class IdentityArtifacts:
    face_crop_path: str | None
    embedding_path: str | None
    embedding: list[float] | None


def queue_auto_analysis(
    video_id: str,
    *,
    force: bool = False,
    profile: str = "balanced",
    transcription_enabled: bool = False,
) -> AnalysisJobResponse:
    """Create a queued analysis job without running it in the request thread."""
    video = _get_video_row(video_id)
    if not force:
        existing = _latest_reusable_job(video_id)
        if existing is not None:
            return _row_to_job(existing)
    row = _create_analysis_job(
        video,
        force=force,
        profile=profile,
        transcription_enabled=transcription_enabled,
        status="queued",
    )
    return _row_to_job(row)


def ensure_auto_analysis(
    video_id: str,
    *,
    force: bool = False,
    profile: str = "balanced",
    transcription_enabled: bool = False,
) -> TimelineResponse:
    """Create or return the latest automatic analysis for a video.

    This is intentionally a local, conservative baseline. It does not claim SAM,
    MediaPipe, or transcription are present. Optional face-ID assets are used
    when installed; otherwise evidence makes the fallback limitations explicit.
    """
    video = _get_video_row(video_id)
    if not force:
        existing = _latest_reusable_job(video_id)
        if existing is not None:
            return get_timeline(existing["id"])

    job = _create_analysis_job(
        video,
        force=force,
        profile=profile,
        transcription_enabled=transcription_enabled,
        status="running",
    )
    run_analysis_job(job["id"], raise_on_failure=True)
    return get_timeline(job["id"])


def run_analysis_job(job_id: str, *, raise_on_failure: bool = False) -> AnalysisJobResponse:
    """Run a queued/running analysis job and preserve partial rows on failure or cancellation."""
    with connection() as conn:
        job = conn.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise NotFoundError("analysis job")
        if job["status"] == "cancelled":
            return _row_to_job(job)
        if job["status"] == "complete":
            return _row_to_job(job)
        if job["status"] not in ("queued", "running"):
            raise ValidationFailedError(f"analysis job is already {job['status']}")
        params = _loads_json(job["params_json"], {})
        if params.get("kind") != ANALYSIS_KIND:
            raise ValidationFailedError("analysis job is not an automatic analysis job")
        started_at = job["started_at"] or _now_iso()
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status='running', started_at=?, progress_json=?
            WHERE id=?
            """,
            (
                started_at,
                json.dumps({"stage": "starting", "percent": 2.0}),
                job_id,
            ),
        )

    video = _get_video_row(job["video_id"])
    profile = str(params.get("profile") or "balanced")
    transcription_enabled = bool(params.get("transcription_enabled", False))
    force = bool(params.get("force", False))
    window_seconds = PROFILE_WINDOW_SECONDS.get(profile, PROFILE_WINDOW_SECONDS["balanced"])

    try:
        _update_job_progress(job_id, stage="detecting_streamer", percent=10.0)
        candidates = _detect_streamer_candidates(video)
        candidate = candidates[0] if candidates else _detect_streamer_candidate(video)
        stored_candidates = candidates or ([candidate] if candidate is not None else [])
        if stored_candidates:
            _store_roi_candidates(video, candidates=stored_candidates, replace=force)
        streamer_profile_id = _ensure_auto_streamer_profile(video, candidate=candidate, force=force)
        streamer_profile = _get_streamer_profile(streamer_profile_id)
        _update_job_params(
            job_id,
            {
                "streamer_identity": _loads_json(streamer_profile["prompt_json"], {}),
                "activity_source": "multimodal_vad_audio_expression_transcript",
            },
        )
        _raise_if_cancelled(job_id)

        _update_job_progress(job_id, stage="tracking_presence", percent=25.0)
        _insert_presence_segments(job_id, video, streamer_profile)
        _raise_if_cancelled(job_id)

        _update_job_progress(job_id, stage="detecting_speech", percent=45.0)
        vad_segments = _detect_vad_segments(video, window_seconds=window_seconds)
        _insert_vad_segments(job_id, vad_segments)
        _raise_if_cancelled(job_id)

        _update_job_progress(job_id, stage="sampling_expressions", percent=60.0)
        expression_features = _extract_expression_features(
            job_id,
            video,
            streamer_profile,
            sample_interval_seconds=max(1.0, window_seconds),
        )
        _raise_if_cancelled(job_id)

        _update_job_progress(job_id, stage="classifying_activity", percent=80.0)
        events = _classify_activity(
            video,
            streamer_profile,
            window_seconds=window_seconds,
            transcription_enabled=transcription_enabled,
            vad_segments=vad_segments,
            expression_features=expression_features,
        )
        _raise_if_cancelled(job_id)
        _insert_activity_events(job_id, events)
    except Exception as exc:  # noqa: BLE001
        with connection() as conn:
            current = conn.execute("SELECT status FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        if current is not None and current["status"] == "cancelled":
            return get_analysis_job(job_id)
        with connection() as conn:
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status='failed', error_message=?, progress_json=?, completed_at=?
                WHERE id=?
                """,
                (
                    str(exc)[:500],
                    json.dumps({"stage": "failed", "percent": 100.0}),
                    _now_iso(),
                    job_id,
                ),
            )
        if raise_on_failure:
            raise
        return get_analysis_job(job_id)

    with connection() as conn:
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status='complete', progress_json=?, completed_at=?
            WHERE id=?
            """,
            (
                json.dumps({"stage": "complete", "percent": 100.0}),
                _now_iso(),
                job_id,
            ),
        )

    return get_analysis_job(job_id)


def get_analysis_job(job_id: str) -> AnalysisJobResponse:
    with connection() as conn:
        row = conn.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise NotFoundError("analysis job")
    return _row_to_job(row)


def claim_next_analysis_job() -> AnalysisJobResponse | None:
    """Atomically claim the oldest queued automatic analysis job for a worker."""
    with connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT *
            FROM analysis_jobs
            WHERE status = 'queued'
              AND json_extract(params_json, '$.kind') = ?
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (ANALYSIS_KIND,),
        ).fetchone()
        if row is None:
            return None
        now = _now_iso()
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status='running', started_at=COALESCE(started_at, ?), progress_json=?
            WHERE id=? AND status='queued'
            """,
            (
                now,
                json.dumps({"stage": "worker_claimed", "percent": 1.0}),
                row["id"],
            ),
        )
        claimed = conn.execute("SELECT * FROM analysis_jobs WHERE id = ?", (row["id"],)).fetchone()
    return _row_to_job(claimed) if claimed is not None else None


def run_next_analysis_job() -> AnalysisJobResponse | None:
    claimed = claim_next_analysis_job()
    if claimed is None:
        return None
    return run_analysis_job(claimed.id)


def _create_analysis_job(
    video: sqlite3.Row,
    *,
    force: bool,
    profile: str,
    transcription_enabled: bool,
    status: str,
) -> sqlite3.Row:
    job_id = uuid.uuid4().hex
    now = _now_iso()
    window_seconds = PROFILE_WINDOW_SECONDS.get(profile, PROFILE_WINDOW_SECONDS["balanced"])
    started_at = now if status == "running" else None
    params = {
        "kind": ANALYSIS_KIND,
        "version": ANALYSIS_VERSION,
        "profile": profile,
        "force": force,
        "window_seconds": window_seconds,
        "transcription_enabled": transcription_enabled,
        "streamer_identity": None,
        "activity_source": "multimodal_vad_audio_expression_transcript",
    }
    versions = {
        "app": __version__,
        "analysis": f"{ANALYSIS_KIND}@{ANALYSIS_VERSION}",
        "ffmpeg": _tool_path("ffmpeg"),
        "sam3": None,
        "mediapipe": None,
        "vad": "silero_optional_or_energy_fallback@1",
        "expression": "opencv_haar_expression@1",
        "transcription": "manual_or_imported_segments@1" if transcription_enabled else None,
    }

    with connection() as conn:
        conn.execute(
            """
            INSERT INTO analysis_jobs
              (id, video_id, status, params_json, versions_json, progress_json,
               error_message, created_at, started_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL)
            """,
            (
                job_id,
                video["id"],
                status,
                json.dumps(params),
                json.dumps(versions),
                json.dumps({"stage": status, "percent": 0.0 if status == "queued" else 2.0}),
                now,
                started_at,
            ),
        )
        row = conn.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
    return row


def _update_job_progress(job_id: str, *, stage: str, percent: float) -> None:
    with connection() as conn:
        conn.execute(
            "UPDATE analysis_jobs SET progress_json = ? WHERE id = ?",
            (json.dumps({"stage": stage, "percent": percent}), job_id),
        )


def _update_job_params(job_id: str, patch: dict[str, Any]) -> None:
    with connection() as conn:
        row = conn.execute("SELECT params_json FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise NotFoundError("analysis job")
        params = _loads_json(row["params_json"], {})
        params.update(patch)
        conn.execute("UPDATE analysis_jobs SET params_json = ? WHERE id = ?", (json.dumps(params), job_id))


def latest_timeline_for_video(video_id: str, *, create_if_missing: bool = True) -> TimelineResponse:
    _get_video_row(video_id)
    job = _latest_job(video_id)
    if job is None:
        if not create_if_missing:
            raise NotFoundError("analysis job")
        return ensure_auto_analysis(video_id)
    return get_timeline(job["id"])


def get_timeline(job_id: str) -> TimelineResponse:
    with connection() as conn:
        job = conn.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise NotFoundError("analysis job")
        profile = conn.execute(
            """
            SELECT sp.*
            FROM streamer_profiles sp
            WHERE sp.video_id = ?
            ORDER BY CASE sp.confirmed_by WHEN 'user' THEN 0 ELSE 1 END,
                     sp.created_at DESC
            LIMIT 1
            """,
            (job["video_id"],),
        ).fetchone()
        tracks = conn.execute(
            "SELECT * FROM track_segments WHERE job_id = ? ORDER BY start_seconds",
            (job_id,),
        ).fetchall()
        events = conn.execute(
            "SELECT * FROM activity_events WHERE job_id = ? ORDER BY start_seconds",
            (job_id,),
        ).fetchall()

    return TimelineResponse(
        job=_row_to_job(job),
        streamer_profile=_row_to_profile(profile) if profile is not None else None,
        lifecycle_segments=[_row_to_track(row) for row in tracks],
        activity_events=[_row_to_event(row) for row in events],
    )


def cancel_analysis_job(job_id: str) -> AnalysisJobResponse:
    with connection() as conn:
        row = conn.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise NotFoundError("analysis job")
        if row["status"] not in ("queued", "running"):
            raise ValidationFailedError(f"analysis job is already {row['status']}")
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status = 'cancelled', completed_at = ?, progress_json = ?
            WHERE id = ?
            """,
            (
                _now_iso(),
                json.dumps({"stage": "cancelled", "percent": 100.0}),
                job_id,
            ),
        )
        updated = conn.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(updated)


def confirm_streamer_profile(
    video_id: str,
    *,
    bbox: BBoxRequest,
    confirmed_time_seconds: float,
    label: str,
) -> StreamerProfileResponse:
    video = _get_video_row(video_id)
    bbox_payload = bbox.model_dump()
    _validate_bbox(bbox_payload, video)
    profile_id = uuid.uuid4().hex
    prompt = {
        "type": "user_confirmed_roi",
        "label": label,
        "confidence": 0.95,
        "reason": "User confirmed the streamer region manually.",
    }
    artifacts = _create_identity_artifacts(video, bbox_payload, confirmed_time_seconds, profile_id)
    now = _now_iso()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO streamer_profiles
              (id, video_id, confirmed_by, confirmed_time_seconds, prompt_json,
               roi_bbox_json, face_crop_path, embedding_path, created_at)
            VALUES (?, ?, 'user', ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                video_id,
                confirmed_time_seconds,
                json.dumps(prompt),
                json.dumps(bbox_payload),
                artifacts.face_crop_path,
                artifacts.embedding_path,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM streamer_profiles WHERE id = ?", (profile_id,)).fetchone()
    return _row_to_profile(row)


def update_activity_event(event_id: str, body: SegmentUpdateRequest) -> ActivityEventResponse:
    with connection() as conn:
        row = conn.execute("SELECT * FROM activity_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise NotFoundError("activity event")
        start = body.start_seconds if body.start_seconds is not None else row["start_seconds"]
        end = body.end_seconds if body.end_seconds is not None else row["end_seconds"]
        if end <= start:
            raise ValidationFailedError("end_seconds must be greater than start_seconds")
        label = body.label if body.label is not None else row["label"]
        confidence = body.confidence if body.confidence is not None else row["confidence"]
        evidence = _loads_json(row["evidence_json"], {})
        if body.evidence_note:
            evidence["user_note"] = body.evidence_note
        conn.execute(
            """
            UPDATE activity_events
            SET start_seconds = ?, end_seconds = ?, label = ?, confidence = ?, evidence_json = ?
            WHERE id = ?
            """,
            (start, end, label, confidence, json.dumps(evidence), event_id),
        )
        _insert_annotation(
            conn,
            job_id=row["job_id"],
            target_type="activity_event",
            target_id=event_id,
            annotation_type="edit",
            payload={"before": _row_payload(row), "after": {"start_seconds": start, "end_seconds": end, "label": label}},
        )
        updated = conn.execute("SELECT * FROM activity_events WHERE id = ?", (event_id,)).fetchone()
    return _row_to_event(updated)


def delete_activity_event(event_id: str) -> None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM activity_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise NotFoundError("activity event")
        _insert_annotation(
            conn,
            job_id=row["job_id"],
            target_type="activity_event",
            target_id=event_id,
            annotation_type="reject",
            payload={"deleted": _row_payload(row)},
        )
        conn.execute("DELETE FROM activity_events WHERE id = ?", (event_id,))


def split_activity_event(event_id: str, body: SegmentSplitRequest) -> list[ActivityEventResponse]:
    with connection() as conn:
        row = conn.execute("SELECT * FROM activity_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise NotFoundError("activity event")
        if not (row["start_seconds"] < body.split_seconds < row["end_seconds"]):
            raise ValidationFailedError("split_seconds must be inside the event range")
        new_id = uuid.uuid4().hex
        conn.execute("UPDATE activity_events SET end_seconds = ? WHERE id = ?", (body.split_seconds, event_id))
        conn.execute(
            """
            INSERT INTO activity_events
              (id, job_id, start_seconds, end_seconds, label, confidence, source, evidence_json)
            VALUES (?, ?, ?, ?, ?, ?, 'user', ?)
            """,
            (
                new_id,
                row["job_id"],
                body.split_seconds,
                row["end_seconds"],
                row["label"],
                row["confidence"],
                row["evidence_json"],
            ),
        )
        _insert_annotation(
            conn,
            job_id=row["job_id"],
            target_type="activity_event",
            target_id=event_id,
            annotation_type="split",
            payload={"split_seconds": body.split_seconds, "created_event_id": new_id},
        )
        rows = conn.execute(
            "SELECT * FROM activity_events WHERE id IN (?, ?) ORDER BY start_seconds",
            (event_id, new_id),
        ).fetchall()
    return [_row_to_event(item) for item in rows]


def merge_activity_events(body: SegmentMergeRequest) -> ActivityEventResponse:
    ids = list(dict.fromkeys(body.segment_ids))
    if len(ids) < 2:
        raise ValidationFailedError("at least two distinct segment ids are required")
    placeholders = ",".join("?" for _ in ids)
    with connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM activity_events WHERE id IN ({placeholders}) ORDER BY start_seconds",
            ids,
        ).fetchall()
        if len(rows) != len(ids):
            raise NotFoundError("activity event")
        job_ids = {row["job_id"] for row in rows}
        if len(job_ids) != 1:
            raise ValidationFailedError("cannot merge events from different jobs")
        keep_id = rows[0]["id"]
        label = body.label or (rows[0]["label"] if len({r["label"] for r in rows}) == 1 else "mixed")
        confidence = round(sum(float(row["confidence"]) for row in rows) / len(rows), 3)
        evidence = {
            "merged_from": [row["id"] for row in rows],
            "labels": [row["label"] for row in rows],
            "claim_type": "user merged activity event",
        }
        start = min(float(row["start_seconds"]) for row in rows)
        end = max(float(row["end_seconds"]) for row in rows)
        conn.execute(
            """
            UPDATE activity_events
            SET start_seconds = ?, end_seconds = ?, label = ?, confidence = ?, source = 'user', evidence_json = ?
            WHERE id = ?
            """,
            (start, end, label, confidence, json.dumps(evidence), keep_id),
        )
        for row in rows[1:]:
            conn.execute("DELETE FROM activity_events WHERE id = ?", (row["id"],))
        _insert_annotation(
            conn,
            job_id=rows[0]["job_id"],
            target_type="activity_event",
            target_id=keep_id,
            annotation_type="merge",
            payload={"merged_ids": ids, "kept_id": keep_id},
        )
        updated = conn.execute("SELECT * FROM activity_events WHERE id = ?", (keep_id,)).fetchone()
    return _row_to_event(updated)


def export_feedback(video_id: str) -> FeedbackExportResponse:
    _get_video_row(video_id)
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT ua.*
            FROM user_annotations ua
            JOIN analysis_jobs aj ON aj.id = ua.job_id
            WHERE aj.video_id = ?
            ORDER BY ua.created_at
            """,
            (video_id,),
        ).fetchall()
    return FeedbackExportResponse(video_id=video_id, annotations=[_row_payload(row) for row in rows])


def _raise_if_cancelled(job_id: str) -> None:
    with connection() as conn:
        row = conn.execute("SELECT status FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is not None and row["status"] == "cancelled":
        raise ValidationFailedError("analysis job was cancelled")


def _validate_bbox(bbox: dict[str, Any], video: sqlite3.Row) -> None:
    width = int(video["width"] or 0)
    height = int(video["height"] or 0)
    if width <= 0 or height <= 0:
        return
    if bbox["x"] + bbox["width"] > width or bbox["y"] + bbox["height"] > height:
        raise ValidationFailedError("bbox must fit inside the source video dimensions")


def _insert_annotation(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    target_type: str,
    target_id: str,
    annotation_type: str,
    payload: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO user_annotations
          (id, job_id, target_type, target_id, annotation_type, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid.uuid4().hex,
            job_id,
            target_type,
            target_id,
            annotation_type,
            json.dumps(payload),
            _now_iso(),
        ),
    )


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    for key in ("params_json", "versions_json", "progress_json", "prompt_json", "roi_bbox_json", "bbox_json", "evidence_json", "payload_json"):
        if key in payload:
            payload[key[:-5] if key.endswith("_json") else key] = _loads_json(payload[key], payload[key])
    return payload


def _get_video_row(video_id: str) -> sqlite3.Row:
    with connection() as conn:
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    if row is None:
        raise NotFoundError("video")
    return row


def _latest_job(video_id: str) -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute(
            """
            SELECT *
            FROM analysis_jobs
            WHERE video_id = ?
              AND json_extract(params_json, '$.kind') = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (video_id, ANALYSIS_KIND),
        ).fetchone()


def _latest_reusable_job(video_id: str) -> sqlite3.Row | None:
    with connection() as conn:
        return conn.execute(
            """
            SELECT *
            FROM analysis_jobs
            WHERE video_id = ?
              AND json_extract(params_json, '$.kind') = ?
              AND status IN ('queued', 'running', 'complete')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (video_id, ANALYSIS_KIND),
        ).fetchone()


def _fallback_streamer_candidate(video: sqlite3.Row) -> StreamerCandidate:
    bbox = {
        "x": 0,
        "y": 0,
        "width": video["width"],
        "height": video["height"],
        "coordinate_space": "source_pixels",
        "fallback": "full_frame",
    }
    prompt = {
        "type": "auto_full_frame_fallback",
        "label": "primary_streamer_candidate",
        "confidence": 0.35,
        "reason": "No recurring face was detected from sampled frames; using the full video frame as a conservative automatic identity anchor.",
        "limitations": [
            "This is not face recognition.",
            "This does not distinguish streamer audio from game, alert, or guest audio.",
            "A later SAM/face confirmation pass should replace this profile.",
        ],
    }
    return StreamerCandidate(bbox=bbox, prompt=prompt, confidence=0.35, method="auto_full_frame_fallback")


def _detect_streamer_candidate(video: sqlite3.Row) -> StreamerCandidate | None:
    candidates = _detect_streamer_candidates(video)
    return candidates[0] if candidates else None


def _detect_streamer_candidates(video: sqlite3.Row, *, limit: int = 5) -> list[StreamerCandidate]:
    try:
        import cv2  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []

    video_path = Path(video["local_path"])
    if not video_path.exists():
        return []

    cascade_path = Path(getattr(cv2.data, "haarcascades", "")) / "haarcascade_frontalface_default.xml"
    if not cascade_path.exists():
        return []
    cascade = cv2.CascadeClassifier(str(cascade_path))
    if cascade.empty():
        return []

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return []
    try:
        duration = _duration(video)
        sample_end = max(1.0, min(duration, FACE_SAMPLE_MAX_SECONDS))
        sample_count = max(1, min(FACE_SAMPLE_COUNT, int(math.ceil(sample_end))))
        times = [sample_end * (i + 0.5) / sample_count for i in range(sample_count)]
        detections: list[dict[str, Any]] = []
        for time_seconds in times:
            capture.set(cv2.CAP_PROP_POS_MSEC, time_seconds * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            frame_h, frame_w = frame.shape[:2]
            scale = min(1.0, 960.0 / max(frame_w, frame_h))
            if scale < 1.0:
                frame = cv2.resize(frame, (int(frame_w * scale), int(frame_h * scale)))
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.equalizeHist(gray)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
            inv_scale = 1.0 / scale
            for (x, y, w, h) in faces:
                detections.append(
                    {
                        "time_seconds": round(time_seconds, 3),
                        "x": int(round(x * inv_scale)),
                        "y": int(round(y * inv_scale)),
                        "width": int(round(w * inv_scale)),
                        "height": int(round(h * inv_scale)),
                        "frame_width": frame_w,
                        "frame_height": frame_h,
                    }
                )
    finally:
        capture.release()

    if not detections:
        return []

    clusters = _face_clusters(detections)
    if not clusters:
        return []
    sample_count = max(1, min(FACE_SAMPLE_COUNT, int(math.ceil(min(_duration(video), FACE_SAMPLE_MAX_SECONDS)))))
    candidates: list[StreamerCandidate] = []
    for rank, cluster in enumerate(clusters[:limit], start=1):
        frame_width = int(video["width"] or cluster[0]["frame_width"])
        frame_height = int(video["height"] or cluster[0]["frame_height"])
        face_bbox = _cluster_bbox(cluster, frame_width=frame_width, frame_height=frame_height, expand=1.0)
        roi_bbox = _cluster_bbox(cluster, frame_width=frame_width, frame_height=frame_height, expand=2.8)
        coverage = min(1.0, len(cluster) / sample_count)
        avg_area = sum(d["width"] * d["height"] for d in cluster) / len(cluster)
        area_ratio = avg_area / max(1, frame_width * frame_height)
        stability = _cluster_stability(cluster)
        overlay_score = _overlay_geometry_score(roi_bbox, frame_width=frame_width, frame_height=frame_height)
        confidence = round(min(0.9, 0.28 + coverage * 0.32 + stability * 0.18 + overlay_score * 0.12 + min(0.1, area_ratio * 20)), 3)
        prompt = {
            "type": "opencv_haar_face_candidate",
            "label": "primary_streamer_candidate" if rank == 1 else f"streamer_candidate_{rank}",
            "rank": rank,
            "confidence": confidence,
            "reason": "OpenCV Haar face detection found a recurring face-like region in sampled frames.",
            "face_bbox": face_bbox,
            "roi_bbox": roi_bbox,
            "sampled_frames": sample_count,
            "matched_frames": len(cluster),
            "detections_total": len(detections),
            "representative_time_seconds": cluster[len(cluster) // 2]["time_seconds"],
            "scoring": {
                "coverage": round(coverage, 3),
                "stability": round(stability, 3),
                "overlay_geometry": round(overlay_score, 3),
                "area_ratio": round(area_ratio, 5),
            },
            "limitations": [
                "This identifies a recurring face candidate, not a verified biometric identity.",
                "Haar cascades can miss rotated, tiny, occluded, or stylized facecam faces.",
                "User confirmation promotes a candidate to the tracked streamer target.",
            ],
        }
        candidates.append(StreamerCandidate(bbox=roi_bbox, prompt=prompt, confidence=confidence, method="opencv_haar_face_candidate"))
    candidates.sort(key=lambda item: item.confidence, reverse=True)
    return candidates


def list_roi_candidates(video_id: str) -> list[RoiCandidateResponse]:
    _get_video_row(video_id)
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM roi_candidates WHERE video_id = ? ORDER BY score DESC",
            (video_id,),
        ).fetchall()
    return [_row_to_roi_candidate(row) for row in rows]


def get_roi_candidate_preview_path(video_id: str, candidate_id: str) -> Path:
    _get_video_row(video_id)
    with connection() as conn:
        row = conn.execute(
            "SELECT preview_path FROM roi_candidates WHERE id = ? AND video_id = ?",
            (candidate_id, video_id),
        ).fetchone()
    if row is None or not row["preview_path"]:
        raise NotFoundError("ROI candidate preview")
    path = Path(row["preview_path"])
    if not path.exists():
        raise NotFoundError("ROI candidate preview")
    return path


def _store_roi_candidates(video: sqlite3.Row, *, candidates: list[StreamerCandidate], replace: bool) -> None:
    if not candidates:
        return
    if replace:
        with connection() as conn:
            conn.execute("DELETE FROM roi_candidates WHERE video_id = ?", (video["id"],))
    for candidate in candidates:
        _store_roi_candidate(video, candidate=candidate, replace=False)


def _store_roi_candidate(video: sqlite3.Row, *, candidate: StreamerCandidate, replace: bool) -> None:
    video_id = video["id"]
    with connection() as conn:
        if replace:
            conn.execute("DELETE FROM roi_candidates WHERE video_id = ?", (video_id,))
        exists = conn.execute(
            """
            SELECT 1
            FROM roi_candidates
            WHERE video_id = ?
              AND json_extract(bbox_json, '$.x') = ?
              AND json_extract(bbox_json, '$.y') = ?
              AND json_extract(bbox_json, '$.width') = ?
              AND json_extract(bbox_json, '$.height') = ?
            """,
            (
                video_id,
                candidate.bbox.get("x"),
                candidate.bbox.get("y"),
                candidate.bbox.get("width"),
                candidate.bbox.get("height"),
            ),
        ).fetchone()
        if exists is not None:
            return
        duration = _duration(video)
        candidate_id = uuid.uuid4().hex
        preview_path = _save_roi_preview(video, candidate=candidate, candidate_id=candidate_id)
        conn.execute(
            """
            INSERT INTO roi_candidates
              (id, video_id, sample_start_seconds, sample_end_seconds, bbox_json,
               score, evidence_json, preview_path)
            VALUES (?, ?, 0.0, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                video_id,
                min(duration, FACE_SAMPLE_MAX_SECONDS),
                json.dumps(candidate.bbox),
                candidate.confidence,
                json.dumps(candidate.prompt),
                preview_path,
            ),
        )


def _save_roi_preview(video: sqlite3.Row, *, candidate: StreamerCandidate, candidate_id: str) -> str | None:
    try:
        import cv2  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    path = Path(video["local_path"])
    if not path.exists():
        return None
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return None
    try:
        time_seconds = float(candidate.prompt.get("representative_time_seconds") or 0.0)
        capture.set(cv2.CAP_PROP_POS_MSEC, time_seconds * 1000.0)
        ok, frame = capture.read()
        if not ok or frame is None:
            return None
        frame_h, frame_w = frame.shape[:2]
        bbox = candidate.bbox
        x = max(0, min(frame_w - 1, int(bbox.get("x", 0))))
        y = max(0, min(frame_h - 1, int(bbox.get("y", 0))))
        w = max(1, min(frame_w - x, int(bbox.get("width", frame_w))))
        h = max(1, min(frame_h - y, int(bbox.get("height", frame_h))))
        crop = frame[y : y + h, x : x + w]
        out_dir = video_roi_dir(video["id"])
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{candidate_id}.jpg"
        if cv2.imwrite(str(out_path), crop):
            return str(out_path)
    finally:
        capture.release()
    return None


def _best_face_cluster(detections: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    clusters = _face_clusters(detections)
    return clusters[0] if clusters else None


def _face_clusters(detections: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    clusters: list[list[dict[str, Any]]] = []
    for detection in detections:
        center_x = (detection["x"] + detection["width"] / 2.0) / max(1, detection["frame_width"])
        center_y = (detection["y"] + detection["height"] / 2.0) / max(1, detection["frame_height"])
        matched: list[dict[str, Any]] | None = None
        for cluster in clusters:
            avg_x = sum((d["x"] + d["width"] / 2.0) / max(1, d["frame_width"]) for d in cluster) / len(cluster)
            avg_y = sum((d["y"] + d["height"] / 2.0) / max(1, d["frame_height"]) for d in cluster) / len(cluster)
            if abs(center_x - avg_x) <= 0.18 and abs(center_y - avg_y) <= 0.18:
                matched = cluster
                break
        if matched is None:
            clusters.append([detection])
        else:
            matched.append(detection)

    clusters.sort(
        key=lambda c: (
            len(c),
            _cluster_stability(c),
            sum(d["width"] * d["height"] for d in c) / len(c),
        ),
        reverse=True,
    )
    return clusters


def _cluster_stability(cluster: list[dict[str, Any]]) -> float:
    if len(cluster) <= 1:
        return 0.2
    xs = [(d["x"] + d["width"] / 2.0) / max(1, d["frame_width"]) for d in cluster]
    ys = [(d["y"] + d["height"] / 2.0) / max(1, d["frame_height"]) for d in cluster]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    spread = math.sqrt(sum((x - x_mean) ** 2 + (y - y_mean) ** 2 for x, y in zip(xs, ys)) / len(xs))
    return max(0.0, min(1.0, 1.0 - spread * 6.0))


def _overlay_geometry_score(bbox: dict[str, Any], *, frame_width: int, frame_height: int) -> float:
    center_x = (bbox["x"] + bbox["width"] / 2.0) / max(1, frame_width)
    center_y = (bbox["y"] + bbox["height"] / 2.0) / max(1, frame_height)
    corner_distance = min(
        math.sqrt((center_x - x) ** 2 + (center_y - y) ** 2)
        for x, y in ((0, 0), (1, 0), (0, 1), (1, 1))
    )
    corner_score = max(0.0, min(1.0, 1.0 - corner_distance * 2.0))
    area_ratio = (bbox["width"] * bbox["height"]) / max(1, frame_width * frame_height)
    size_score = 1.0 if 0.005 <= area_ratio <= 0.25 else 0.45
    return round((corner_score * 0.65) + (size_score * 0.35), 3)


def _cluster_bbox(
    cluster: list[dict[str, Any]],
    *,
    frame_width: int,
    frame_height: int,
    expand: float,
) -> dict[str, Any]:
    avg_x = sum(d["x"] for d in cluster) / len(cluster)
    avg_y = sum(d["y"] for d in cluster) / len(cluster)
    avg_w = sum(d["width"] for d in cluster) / len(cluster)
    avg_h = sum(d["height"] for d in cluster) / len(cluster)
    center_x = avg_x + avg_w / 2.0
    center_y = avg_y + avg_h / 2.0
    width = max(avg_w, avg_h) * expand
    height = max(avg_h, avg_w) * expand
    x = max(0, min(frame_width - 1, int(round(center_x - width / 2.0))))
    y = max(0, min(frame_height - 1, int(round(center_y - height / 2.0))))
    w = max(1, min(frame_width - x, int(round(width))))
    h = max(1, min(frame_height - y, int(round(height))))
    return {
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "coordinate_space": "source_pixels",
        "method": "opencv_haar_face_candidate",
    }


def _get_streamer_profile(profile_id: str) -> sqlite3.Row:
    with connection() as conn:
        row = conn.execute("SELECT * FROM streamer_profiles WHERE id = ?", (profile_id,)).fetchone()
    if row is None:
        raise NotFoundError("streamer profile")
    return row


def _ensure_auto_streamer_profile(video: sqlite3.Row, *, candidate: StreamerCandidate | None, force: bool) -> str:
    video_id = video["id"]
    with connection() as conn:
        user_confirmed = conn.execute(
            """
            SELECT id
            FROM streamer_profiles
            WHERE video_id = ? AND confirmed_by = 'user'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (video_id,),
        ).fetchone()
        if user_confirmed is not None:
            return user_confirmed["id"]

        existing = conn.execute(
            """
            SELECT id, prompt_json
            FROM streamer_profiles
            WHERE video_id = ? AND confirmed_by = 'auto'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (video_id,),
        ).fetchone()
        existing_prompt = _loads_json(existing["prompt_json"], {}) if existing is not None else {}
        existing_is_visual = existing_prompt.get("type") == "opencv_haar_face_candidate"
        if existing is not None and (existing_is_visual or candidate is None) and not force:
            return existing["id"]

        profile_id = uuid.uuid4().hex
        fallback = _fallback_streamer_candidate(video)
        selected = candidate or fallback
        artifacts = _create_identity_artifacts(video, selected.bbox, 0.0, profile_id)
        now = _now_iso()
        conn.execute(
            """
            INSERT INTO streamer_profiles
              (id, video_id, confirmed_by, confirmed_time_seconds, prompt_json,
               roi_bbox_json, face_crop_path, embedding_path, created_at)
            VALUES (?, ?, 'auto', 0.0, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                video_id,
                json.dumps(selected.prompt),
                json.dumps(selected.bbox),
                artifacts.face_crop_path,
                artifacts.embedding_path,
                now,
            ),
        )
    return profile_id


def _create_identity_artifacts(video: sqlite3.Row, bbox: dict[str, Any], time_seconds: float, profile_id: str) -> IdentityArtifacts:
    try:
        import cv2  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return IdentityArtifacts(face_crop_path=None, embedding_path=None, embedding=None)

    video_path = Path(video["local_path"])
    if not video_path.exists():
        return IdentityArtifacts(face_crop_path=None, embedding_path=None, embedding=None)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return IdentityArtifacts(face_crop_path=None, embedding_path=None, embedding=None)
    crop = None
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, time_seconds) * 1000.0)
        ok, frame = capture.read()
        if ok and frame is not None:
            crop = _crop_frame_to_bbox(frame, bbox)
    finally:
        capture.release()
    if crop is None or crop.size == 0:
        return IdentityArtifacts(face_crop_path=None, embedding_path=None, embedding=None)

    out_dir = video_identity_dir(video["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    crop_path = out_dir / f"{profile_id}.jpg"
    face_crop_path = str(crop_path) if cv2.imwrite(str(crop_path), crop) else None
    sface_embedding = _sface_embedding(cv2, crop)
    embedding = sface_embedding or _image_embedding(cv2, crop)
    embedding_kind = "opencv_sface" if sface_embedding is not None else "hsv_histogram"
    embedding_path = out_dir / f"{profile_id}.embedding.json"
    embedding_path.write_text(
        json.dumps(
            {
                "kind": embedding_kind,
                "version": 1,
                "video_id": video["id"],
                "profile_id": profile_id,
                "source_time_seconds": time_seconds,
                "bbox": bbox,
                "embedding": embedding,
                "model_path": str(_opencv_model_path("face_recognition_sface*.onnx")) if embedding_kind == "opencv_sface" else None,
                "limitations": (
                    "OpenCV SFace embedding from user-installed model assets."
                    if embedding_kind == "opencv_sface"
                    else "Scoped per-video color/texture embedding; replace with SFace when model assets are installed."
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return IdentityArtifacts(face_crop_path=face_crop_path, embedding_path=str(embedding_path), embedding=embedding)


def _crop_frame_to_bbox(frame: Any, bbox: dict[str, Any]) -> Any | None:
    frame_h, frame_w = frame.shape[:2]
    x = max(0, min(frame_w - 1, int(bbox.get("x", 0))))
    y = max(0, min(frame_h - 1, int(bbox.get("y", 0))))
    w = max(1, min(frame_w - x, int(bbox.get("width", frame_w))))
    h = max(1, min(frame_h - y, int(bbox.get("height", frame_h))))
    return frame[y : y + h, x : x + w]


def _image_embedding(cv2: Any, image: Any) -> list[float]:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 4, 4], [0, 180, 0, 256, 0, 256])
    hist = cv2.normalize(hist, hist).flatten()
    return [round(float(value), 6) for value in hist.tolist()]


def _sface_embedding(cv2: Any, image: Any) -> list[float] | None:
    model_path = _opencv_model_path("face_recognition_sface*.onnx")
    if model_path is None or not hasattr(cv2, "FaceRecognizerSF_create"):
        return None
    try:
        recognizer = cv2.FaceRecognizerSF_create(str(model_path), "")
        aligned = cv2.resize(image, (112, 112))
        raw = recognizer.feature(aligned)
        normalised = cv2.normalize(raw, raw).flatten()
        return [round(float(value), 6) for value in normalised.tolist()]
    except Exception:  # noqa: BLE001
        return None


def _opencv_model_path(pattern: str) -> Path | None:
    model_dir = get_settings().models_dir / "opencv"
    if not model_dir.exists():
        return None
    matches = sorted(path for path in model_dir.glob(pattern) if path.is_file())
    return matches[0] if matches else None


def _load_identity_embedding(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = payload.get("embedding")
    if not isinstance(raw, list):
        return None
    return {
        "kind": str(payload.get("kind") or "hsv_histogram"),
        "embedding": [float(value) for value in raw if isinstance(value, int | float)],
    }


def _embedding_similarity(a: Any, b: Any) -> float | None:
    a_values = _embedding_values(a)
    b_values = _embedding_values(b)
    if not a_values or not b_values or len(a_values) != len(b_values):
        return None
    if _embedding_kind(a) != _embedding_kind(b):
        return None
    dot = sum(x * y for x, y in zip(a_values, b_values))
    norm_a = math.sqrt(sum(x * x for x in a_values))
    norm_b = math.sqrt(sum(y * y for y in b_values))
    if norm_a <= 0 or norm_b <= 0:
        return None
    return round(max(0.0, min(1.0, dot / (norm_a * norm_b))), 3)


def _embedding_values(payload: Any) -> list[float] | None:
    if isinstance(payload, dict):
        raw = payload.get("embedding")
    else:
        raw = payload
    if not isinstance(raw, list):
        return None
    return [float(value) for value in raw if isinstance(value, int | float)]


def _embedding_kind(payload: Any) -> str:
    if isinstance(payload, dict):
        return str(payload.get("kind") or "hsv_histogram")
    return "hsv_histogram"


def _embedding_for_kind(cv2: Any, image: Any, kind: str) -> dict[str, Any]:
    if kind == "opencv_sface":
        sface = _sface_embedding(cv2, image)
        if sface is not None:
            return {"kind": "opencv_sface", "embedding": sface}
    return {"kind": "hsv_histogram", "embedding": _image_embedding(cv2, image)}


def _write_mask_artifact(
    *,
    video_id: str,
    job_id: str,
    bbox: dict[str, Any] | None,
    start_seconds: float,
    end_seconds: float,
    state: str,
    source: str,
) -> str | None:
    if not bbox:
        return None
    try:
        x = int(bbox["x"])
        y = int(bbox["y"])
        width = int(bbox["width"])
        height = int(bbox["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    artifact_id = uuid.uuid4().hex
    out_dir = video_masks_dir(video_id, job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{artifact_id}.mask.json"
    payload = {
        "kind": "rectangular_mask",
        "version": 1,
        "provider": "opencv_bbox_mask_fallback",
        "source": source,
        "video_id": video_id,
        "job_id": job_id,
        "state": state,
        "start_seconds": start_seconds,
        "end_seconds": end_seconds,
        "bbox": {**bbox, "x": x, "y": y, "width": width, "height": height},
        "polygon": [
            [x, y],
            [x + width, y],
            [x + width, y + height],
            [x, y + height],
        ],
        "limitations": "Rectangular mask fallback. Replace artifact with SAM mask when a SAM provider is installed.",
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(out_path)


def _insert_presence_segments(job_id: str, video: sqlite3.Row, streamer_profile: sqlite3.Row) -> None:
    duration = _duration(video)
    prompt = _loads_json(streamer_profile["prompt_json"], {})
    bbox = _loads_json(streamer_profile["roi_bbox_json"], None)
    method = str(prompt.get("type", "unknown"))
    confidence = float(prompt.get("confidence", 0.35))
    identity_embedding = _load_identity_embedding(streamer_profile["embedding_path"])
    embedding_kind = _embedding_kind(identity_embedding) if identity_embedding is not None else None
    identity_description = "OpenCV SFace" if embedding_kind == "opencv_sface" else "per-video histogram fallback"
    if method == "opencv_haar_face_candidate":
        summary = "OpenCV face detection found a recurring face-like region and marked it as the current streamer candidate."
        limitations = [
            "Mask artifact is a rectangular fallback, not a SAM segmentation mask.",
            f"Identity evidence uses {identity_description}.",
            "Use user confirmation to promote this from candidate to confirmed streamer.",
        ]
    else:
        summary = "No recurring face candidate was detected yet, so the app marks the whole frame as the current streamer candidate."
        limitations = [
            "Mask artifact is a rectangular fallback, not a SAM segmentation mask.",
            "No confirmed face identity is available.",
            "Use this as a baseline timeline marker until confirmation/tracking is implemented.",
        ]
    tracked = _track_presence_windows(video, bbox, identity_embedding=identity_embedding) if bbox else []
    tracked_by_sampling = bool(tracked)
    if not tracked:
        tracked = [{"start_seconds": 0.0, "end_seconds": duration, "state": "present", "confidence": confidence}]
    with connection() as conn:
        for segment in tracked:
            state = segment["state"]
            segment_bbox = segment.get("bbox") or bbox
            artifact_ref = _write_mask_artifact(
                video_id=video["id"],
                job_id=job_id,
                bbox=segment_bbox,
                start_seconds=segment["start_seconds"],
                end_seconds=segment["end_seconds"],
                state=state,
                source=str(segment.get("method", "fallback_full_video")),
            )
            evidence = {
                "streamer_profile_id": streamer_profile["id"],
                "identity": {
                    "label": str(prompt.get("label", "primary_streamer_candidate")),
                    "method": method,
                    "confidence": confidence,
                    "reason": prompt.get("reason"),
                    "face_crop_path": streamer_profile["face_crop_path"],
                    "embedding_path": streamer_profile["embedding_path"],
                    "embedding_kind": embedding_kind,
                    "match_score": segment.get("identity_match_score"),
                },
                "context": {
                    "summary": summary if state == "present" else f"OpenCV sampling marked this interval as {state}.",
                    "tracker": segment.get("method") or ("opencv_haar_temporal_sampling" if tracked_by_sampling else "fallback_full_video"),
                },
                "limitations": limitations,
            }
            conn.execute(
                """
                INSERT INTO track_segments
                  (id, job_id, start_seconds, end_seconds, state, confidence,
                   bbox_json, artifact_ref, evidence_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    job_id,
                    segment["start_seconds"],
                    segment["end_seconds"],
                    state,
                    segment["confidence"],
                    json.dumps(segment_bbox),
                    artifact_ref,
                    json.dumps(evidence),
                ),
            )


def _track_presence_windows(
    video: sqlite3.Row,
    bbox: dict[str, Any],
    *,
    identity_embedding: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    try:
        import cv2  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    video_path = Path(video["local_path"])
    if not video_path.exists():
        return []
    cascade_path = Path(getattr(cv2.data, "haarcascades", "")) / "haarcascade_frontalface_default.xml"
    if not cascade_path.exists():
        return []
    cascade = cv2.CascadeClassifier(str(cascade_path))
    if cascade.empty():
        return []
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return []
    duration = _duration(video)
    interval = max(5.0, duration / 60.0)
    samples = [min(duration, i * interval) for i in range(max(1, int(math.ceil(duration / interval))))]
    windows: list[dict[str, Any]] = []
    try:
        for index, start in enumerate(samples):
            end = samples[index + 1] if index + 1 < len(samples) else duration
            if end <= start:
                continue
            capture.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
            ok, frame = capture.read()
            present = False
            method = "opencv_roi_face"
            segment_bbox = bbox
            identity_score = None
            if ok and frame is not None:
                present = _frame_has_face_in_bbox(cv2, cascade, frame, bbox)
                if not present and identity_embedding is not None:
                    match = _find_identity_match_in_frame(cv2, cascade, frame, identity_embedding)
                    if match is not None:
                        present = True
                        method = match["method"]
                        segment_bbox = match["bbox"]
                        identity_score = match["score"]
            windows.append(
                {
                    "start_seconds": start,
                    "end_seconds": end,
                    "state": "present" if present else "lost",
                    "confidence": 0.76 if identity_score is not None else (0.68 if present else 0.45),
                    "bbox": segment_bbox,
                    "method": method if present else "opencv_roi_lost",
                    "identity_match_score": identity_score,
                }
            )
    finally:
        capture.release()
    return _merge_presence_windows(windows)


def _find_identity_match_in_frame(
    cv2: Any,
    cascade: Any,
    frame: Any,
    identity_embedding: dict[str, Any],
    *,
    threshold: float = 0.72,
) -> dict[str, Any] | None:
    frame_h, frame_w = frame.shape[:2]
    scale = min(1.0, 960.0 / max(frame_w, frame_h))
    working = cv2.resize(frame, (int(frame_w * scale), int(frame_h * scale))) if scale < 1.0 else frame
    gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
    inv_scale = 1.0 / scale
    best: dict[str, Any] | None = None
    for (x, y, w, h) in faces:
        bx = int(round(x * inv_scale))
        by = int(round(y * inv_scale))
        bw = int(round(w * inv_scale))
        bh = int(round(h * inv_scale))
        crop = frame[by : min(frame_h, by + bh), bx : min(frame_w, bx + bw)]
        if crop.size == 0:
            continue
        embedding_kind = _embedding_kind(identity_embedding)
        candidate_embedding = _embedding_for_kind(cv2, crop, embedding_kind)
        score = _embedding_similarity(identity_embedding, candidate_embedding)
        if score is None:
            continue
        if best is None or score > best["score"]:
            method = "sface_identity_reacquire" if embedding_kind == "opencv_sface" else "histogram_identity_reacquire"
            best = {
                "score": score,
                "method": method,
                "bbox": {
                    "x": bx,
                    "y": by,
                    "width": bw,
                    "height": bh,
                    "coordinate_space": "source_pixels",
                    "method": method,
                },
            }
    if best is not None and best["score"] >= threshold:
        return best
    return None


def _frame_has_face_in_bbox(cv2: Any, cascade: Any, frame: Any, bbox: dict[str, Any]) -> bool:
    frame_h, frame_w = frame.shape[:2]
    scale = min(1.0, 960.0 / max(frame_w, frame_h))
    if scale < 1.0:
        frame = cv2.resize(frame, (int(frame_w * scale), int(frame_h * scale)))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
    inv_scale = 1.0 / scale
    x0 = float(bbox["x"])
    y0 = float(bbox["y"])
    x1 = x0 + float(bbox["width"])
    y1 = y0 + float(bbox["height"])
    for (x, y, w, h) in faces:
        cx = (x + w / 2.0) * inv_scale
        cy = (y + h / 2.0) * inv_scale
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            return True
    return False


def _merge_presence_windows(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not windows:
        return []
    merged: list[dict[str, Any]] = [dict(windows[0])]
    for window in windows[1:]:
        current = merged[-1]
        if current["state"] == window["state"] and abs(current["end_seconds"] - window["start_seconds"]) < 0.001:
            current["end_seconds"] = window["end_seconds"]
            current["confidence"] = round(max(current["confidence"], window["confidence"]), 3)
            if window.get("identity_match_score") is not None:
                current["identity_match_score"] = max(
                    float(current.get("identity_match_score") or 0.0),
                    float(window["identity_match_score"]),
                )
                current["method"] = window.get("method", current.get("method"))
                current["bbox"] = window.get("bbox", current.get("bbox"))
        else:
            merged.append(dict(window))
    saw_absence = False
    for window in merged:
        if window["state"] == "lost":
            if window["end_seconds"] - window["start_seconds"] >= 30.0:
                window["state"] = "AFK"
            saw_absence = True
        elif saw_absence and window["state"] == "present":
            window["state"] = "returned"
            saw_absence = False
    return merged


def _detect_vad_segments(video: sqlite3.Row, *, window_seconds: float) -> list[VadInterval]:
    path = Path(video["local_path"])
    duration = _duration(video)
    if not path.exists():
        return []
    silero = _silero_vad_segments(path, duration)
    if silero:
        return silero
    return _energy_vad_segments(path, duration, window_seconds=window_seconds)


def _silero_vad_segments(path: Path, duration: float) -> list[VadInterval]:
    try:
        from silero_vad import get_speech_timestamps, load_silero_vad, read_audio  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []

    model = load_silero_vad()

    try:
        wav = read_audio(str(path), sampling_rate=16000)
        timestamps = get_speech_timestamps(wav, model, sampling_rate=16000, return_seconds=True)
    except Exception:  # noqa: BLE001
        return []

    segments: list[VadInterval] = []
    for item in timestamps:
        start = float(item.get("start", 0.0))
        end = float(item.get("end", start))
        if end <= start:
            continue
        segments.append(
            VadInterval(
                start_seconds=max(0.0, start),
                end_seconds=min(duration, end),
                speech_probability=float(item.get("confidence", item.get("probability", 0.85))),
                source="silero_vad",
                evidence={"model": "silero_vad", "sampling_rate": 16000},
            )
        )
    return _merge_vad_intervals(segments)


def _energy_vad_segments(path: Path, duration: float, *, window_seconds: float) -> list[VadInterval]:
    windows = _audio_windows(path, duration, window_seconds=window_seconds)
    if not windows:
        return []
    nonzero = [w.rms for w in windows if w.rms > 0]
    median = _percentile(nonzero, 50) if nonzero else 0.0
    p75 = _percentile(nonzero, 75) if nonzero else 0.0
    speech_floor = max(median * 1.2, p75 * 0.7, 0.012)
    intervals: list[VadInterval] = []
    for window in windows:
        probability = 0.0 if window.rms <= 0 else min(1.0, window.rms / max(speech_floor * 1.6, 0.001))
        if probability < 0.35:
            continue
        intervals.append(
            VadInterval(
                start_seconds=window.start_seconds,
                end_seconds=window.end_seconds,
                speech_probability=round(probability, 3),
                source="energy_vad_fallback",
                evidence={
                    "window_rms": round(window.rms, 6),
                    "baseline_median_rms": round(median, 6),
                    "baseline_p75_rms": round(p75, 6),
                    "speech_floor": round(speech_floor, 6),
                    "limitation": "Energy fallback estimates speech likelihood; install silero-vad for model VAD.",
                },
            )
        )
    return _merge_vad_intervals(intervals)


def _merge_vad_intervals(intervals: list[VadInterval], *, max_gap_seconds: float = 0.25) -> list[VadInterval]:
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda item: item.start_seconds)
    merged: list[VadInterval] = [ordered[0]]
    for interval in ordered[1:]:
        current = merged[-1]
        if interval.start_seconds - current.end_seconds <= max_gap_seconds and interval.source == current.source:
            probability = round(max(current.speech_probability, interval.speech_probability), 3)
            evidence = dict(current.evidence)
            evidence["merged"] = True
            merged[-1] = VadInterval(
                start_seconds=current.start_seconds,
                end_seconds=max(current.end_seconds, interval.end_seconds),
                speech_probability=probability,
                source=current.source,
                evidence=evidence,
            )
        else:
            merged.append(interval)
    return merged


def _insert_vad_segments(job_id: str, segments: list[VadInterval]) -> None:
    if not segments:
        return
    with connection() as conn:
        for segment in segments:
            conn.execute(
                """
                INSERT INTO vad_segments
                  (id, job_id, start_seconds, end_seconds, speech_probability, source, evidence_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    job_id,
                    segment.start_seconds,
                    segment.end_seconds,
                    segment.speech_probability,
                    segment.source,
                    json.dumps(segment.evidence),
                ),
            )


def _extract_expression_features(
    job_id: str,
    video: sqlite3.Row,
    streamer_profile: sqlite3.Row,
    *,
    sample_interval_seconds: float,
) -> list[ExpressionSample]:
    try:
        import cv2  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []

    video_path = Path(video["local_path"])
    if not video_path.exists():
        return []

    bbox = _loads_json(streamer_profile["roi_bbox_json"], None)
    if not bbox:
        return []

    face_path = Path(getattr(cv2.data, "haarcascades", "")) / "haarcascade_frontalface_default.xml"
    smile_path = Path(getattr(cv2.data, "haarcascades", "")) / "haarcascade_smile.xml"
    eye_path = Path(getattr(cv2.data, "haarcascades", "")) / "haarcascade_eye.xml"
    if not face_path.exists():
        return []
    face_cascade = cv2.CascadeClassifier(str(face_path))
    smile_cascade = cv2.CascadeClassifier(str(smile_path)) if smile_path.exists() else None
    eye_cascade = cv2.CascadeClassifier(str(eye_path)) if eye_path.exists() else None
    if face_cascade.empty():
        return []

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return []
    duration = _duration(video)
    samples: list[ExpressionSample] = []
    try:
        count = max(1, int(math.ceil(duration / sample_interval_seconds)))
        for index in range(count):
            time_seconds = min(duration, index * sample_interval_seconds)
            capture.set(cv2.CAP_PROP_POS_MSEC, time_seconds * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            sample = _expression_sample_from_frame(
                cv2,
                face_cascade,
                smile_cascade,
                eye_cascade,
                frame,
                bbox,
                time_seconds,
            )
            samples.append(sample)
    finally:
        capture.release()

    _insert_expression_features(job_id, samples)
    return samples


def _expression_sample_from_frame(
    cv2: Any,
    face_cascade: Any,
    smile_cascade: Any | None,
    eye_cascade: Any | None,
    frame: Any,
    bbox: dict[str, Any],
    time_seconds: float,
) -> ExpressionSample:
    frame_h, frame_w = frame.shape[:2]
    x = max(0, min(frame_w - 1, int(bbox.get("x", 0))))
    y = max(0, min(frame_h - 1, int(bbox.get("y", 0))))
    w = max(1, min(frame_w - x, int(bbox.get("width", frame_w))))
    h = max(1, min(frame_h - y, int(bbox.get("height", frame_h))))
    roi = frame[y : y + h, x : x + w]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
    if len(faces) == 0:
        return ExpressionSample(
            time_seconds=time_seconds,
            bbox=None,
            face_visible=False,
            mouth_open=None,
            smile=None,
            brow_raise=None,
            squint=None,
            head_pose=None,
            confidence=0.1,
            evidence={"source": "opencv_haar_expression", "reason": "no face detected inside streamer ROI"},
        )

    fx, fy, fw, fh = max(faces, key=lambda item: item[2] * item[3])
    face_gray = gray[fy : fy + fh, fx : fx + fw]
    lower_face = face_gray[int(fh * 0.45) :, :]
    smiles = []
    if smile_cascade is not None and not smile_cascade.empty():
        smiles = smile_cascade.detectMultiScale(lower_face, scaleFactor=1.5, minNeighbors=12, minSize=(12, 8))
    eyes = []
    if eye_cascade is not None and not eye_cascade.empty():
        upper_face = face_gray[: int(fh * 0.55), :]
        eyes = eye_cascade.detectMultiScale(upper_face, scaleFactor=1.1, minNeighbors=5, minSize=(8, 8))

    smile_score = min(1.0, len(smiles) / 2.0)
    mouth_open = _mouth_open_proxy(lower_face)
    squint = _squint_proxy(eyes, fw)
    brow_raise = _brow_raise_proxy(eyes, fh)
    head_pose = {
        "center_x_ratio": round((fx + fw / 2.0) / max(1, w), 3),
        "center_y_ratio": round((fy + fh / 2.0) / max(1, h), 3),
        "face_width_ratio": round(fw / max(1, w), 3),
    }
    return ExpressionSample(
        time_seconds=time_seconds,
        bbox={
            "x": int(x + fx),
            "y": int(y + fy),
            "width": int(fw),
            "height": int(fh),
            "coordinate_space": "source_pixels",
        },
        face_visible=True,
        mouth_open=mouth_open,
        smile=round(smile_score, 3),
        brow_raise=brow_raise,
        squint=squint,
        head_pose=head_pose,
        confidence=0.55 + min(0.35, (fw * fh) / max(1, w * h)),
        evidence={
            "source": "opencv_haar_expression",
            "smile_detections": len(smiles),
            "eye_detections": len(eyes),
            "limitations": "Haar-based expression features are approximate; MediaPipe landmarks should replace them when installed.",
        },
    )


def _mouth_open_proxy(lower_face: Any) -> float:
    if lower_face.size == 0:
        return 0.0
    height, width = lower_face.shape[:2]
    center = lower_face[int(height * 0.2) : int(height * 0.85), int(width * 0.2) : int(width * 0.8)]
    if center.size == 0:
        return 0.0
    dark_fraction = float((center < 55).sum()) / float(center.size)
    return round(min(1.0, dark_fraction * 4.0), 3)


def _squint_proxy(eyes: Any, face_width: int) -> float | None:
    if len(eyes) == 0:
        return None
    avg_eye_height = sum(float(eye[3]) for eye in eyes) / len(eyes)
    ratio = avg_eye_height / max(1, face_width)
    return round(max(0.0, min(1.0, 1.0 - ratio * 8.0)), 3)


def _brow_raise_proxy(eyes: Any, face_height: int) -> float | None:
    if len(eyes) == 0:
        return None
    avg_eye_y = sum(float(eye[1]) for eye in eyes) / len(eyes)
    ratio = avg_eye_y / max(1, face_height)
    return round(max(0.0, min(1.0, 1.0 - ratio * 3.0)), 3)


def _insert_expression_features(job_id: str, samples: list[ExpressionSample]) -> None:
    if not samples:
        return
    with connection() as conn:
        for sample in samples:
            conn.execute(
                """
                INSERT INTO expression_features
                  (id, job_id, time_seconds, bbox_json, face_visible, mouth_open,
                   smile, brow_raise, squint, head_pose_json, confidence, evidence_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    job_id,
                    sample.time_seconds,
                    json.dumps(sample.bbox) if sample.bbox is not None else None,
                    1 if sample.face_visible else 0,
                    sample.mouth_open,
                    sample.smile,
                    sample.brow_raise,
                    sample.squint,
                    json.dumps(sample.head_pose) if sample.head_pose is not None else None,
                    sample.confidence,
                    json.dumps(sample.evidence),
                ),
            )


def _classify_activity(
    video: sqlite3.Row,
    streamer_profile: sqlite3.Row,
    *,
    window_seconds: float,
    transcription_enabled: bool,
    vad_segments: list[VadInterval],
    expression_features: list[ExpressionSample],
) -> list[LabeledWindow]:
    duration = _duration(video)
    audio_windows = _audio_windows(Path(video["local_path"]), duration, window_seconds=window_seconds)
    transcripts = _transcript_segments_for_video(video["id"]) if transcription_enabled else []
    if not audio_windows:
        evidence = _base_evidence(
            streamer_profile=streamer_profile,
            label="focused",
            confidence=0.25,
            context_summary="No analyzable audio was available, so this fallback marks the interval as a low-confidence focused/quiet candidate.",
            signals={"audio_available": False},
        )
        return [LabeledWindow(0.0, duration, "focused", 0.25, evidence)]

    nonzero = [w.rms for w in audio_windows if w.rms > 0]
    median = _percentile(nonzero, 50) if nonzero else 0.0
    p75 = _percentile(nonzero, 75) if nonzero else 0.0
    p90 = _percentile(nonzero, 90) if nonzero else 0.0
    labels = [
        _label_window(
            w,
            median=median,
            p75=p75,
            p90=p90,
            streamer_profile=streamer_profile,
            transcript_cues=_transcript_cues_for_window(w, transcripts),
            vad_cues=_vad_cues_for_window(w, vad_segments),
            expression_cues=_expression_cues_for_window(w, expression_features),
        )
        for w in audio_windows
    ]
    return _merge_labeled_windows(labels)


def _audio_windows(path: Path, duration: float, *, window_seconds: float = WINDOW_SECONDS) -> list[AudioWindow]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or not path.exists():
        return []

    sample_rate = 16000
    bytes_per_sample = 2
    chunk_seconds = window_seconds
    chunk_bytes = int(sample_rate * bytes_per_sample * chunk_seconds)
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "pipe:1",
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        return []

    windows: list[AudioWindow] = []
    assert proc.stdout is not None
    try:
        index = 0
        while True:
            data = proc.stdout.read(chunk_bytes)
            if not data:
                break
            if len(data) % bytes_per_sample:
                data = data[:-1]
            rms = _pcm16_rms(data)
            start = index * chunk_seconds
            end = min(duration, start + chunk_seconds)
            if end - start >= MIN_EVENT_SECONDS:
                windows.append(AudioWindow(start, end, rms))
            index += 1
    finally:
        stderr = proc.stderr.read() if proc.stderr is not None else b""
        returncode = proc.wait(timeout=10)

    if returncode != 0 and not windows:
        _ = stderr
        return []
    return windows


def _pcm16_rms(data: bytes) -> float:
    if not data:
        return 0.0
    count = len(data) // 2
    if count == 0:
        return 0.0
    total = 0
    for i in range(0, len(data), 2):
        sample = int.from_bytes(data[i : i + 2], byteorder="little", signed=True)
        total += sample * sample
    return math.sqrt(total / count) / 32768.0


def _vad_cues_for_window(window: AudioWindow, segments: list[VadInterval]) -> dict[str, Any]:
    overlaps: list[tuple[VadInterval, float]] = []
    for segment in segments:
        overlap = max(0.0, min(window.end_seconds, segment.end_seconds) - max(window.start_seconds, segment.start_seconds))
        if overlap > 0:
            overlaps.append((segment, overlap))
    if not overlaps:
        return {"speech_probability": 0.0, "source": None, "coverage": 0.0}
    duration = max(0.001, window.end_seconds - window.start_seconds)
    weighted_probability = sum(segment.speech_probability * overlap for segment, overlap in overlaps) / duration
    coverage = min(1.0, sum(overlap for _segment, overlap in overlaps) / duration)
    source = max(overlaps, key=lambda item: item[1])[0].source
    return {
        "speech_probability": round(min(1.0, weighted_probability), 3),
        "source": source,
        "coverage": round(coverage, 3),
    }


def _expression_cues_for_window(window: AudioWindow, samples: list[ExpressionSample]) -> dict[str, Any]:
    matching = [sample for sample in samples if window.start_seconds <= sample.time_seconds < window.end_seconds]
    if not matching:
        return {"face_visible": False, "available": False}
    visible = [sample for sample in matching if sample.face_visible]
    if not visible:
        return {"face_visible": False, "available": True, "sample_count": len(matching)}
    smile_values = [sample.smile for sample in visible if sample.smile is not None]
    mouth_values = [sample.mouth_open for sample in visible if sample.mouth_open is not None]
    squint_values = [sample.squint for sample in visible if sample.squint is not None]
    return {
        "available": True,
        "sample_count": len(matching),
        "face_visible": True,
        "visible_ratio": round(len(visible) / len(matching), 3),
        "smile": round(max(smile_values), 3) if smile_values else None,
        "mouth_open": round(max(mouth_values), 3) if mouth_values else None,
        "squint": round(max(squint_values), 3) if squint_values else None,
    }


def _label_window(
    window: AudioWindow,
    *,
    median: float,
    p75: float,
    p90: float,
    streamer_profile: sqlite3.Row,
    transcript_cues: dict[str, Any] | None = None,
    vad_cues: dict[str, Any] | None = None,
    expression_cues: dict[str, Any] | None = None,
) -> LabeledWindow:
    rms = window.rms
    loud_floor = max(p90, median * 2.5, 0.05)
    speech_floor = max(median * 1.2, 0.012)
    burst_floor = max(median * 1.8, 0.025)
    vad_probability = float((vad_cues or {}).get("speech_probability") or 0.0)
    face_visible = bool((expression_cues or {}).get("face_visible") or False)
    smile = float((expression_cues or {}).get("smile") or 0.0)
    mouth_open = float((expression_cues or {}).get("mouth_open") or 0.0)

    if rms >= loud_floor and vad_probability >= 0.55:
        label = "yelling/frustrated"
        confidence = min(0.88, 0.6 + vad_probability * 0.2 + (rms - loud_floor) * 2.0)
        summary = "VAD indicates speech while audio energy is high relative to this video's baseline."
        kind = "vad_high_audio_energy"
    elif rms >= burst_floor and rms >= p75 and (smile >= 0.4 or mouth_open >= 0.45):
        label = "laughing"
        confidence = 0.64 if smile < 0.7 else 0.78
        summary = "Audio has a raised-energy burst and visual expression features suggest a smile or open mouth."
        kind = "audio_expression_laughter_candidate"
    elif vad_probability >= 0.45:
        label = "talking"
        confidence = min(0.78, 0.42 + vad_probability * 0.35)
        summary = "Voice activity detection marks this interval as likely speech."
        kind = "vad_speech"
    elif rms >= loud_floor:
        label = "focused"
        confidence = 0.32
        summary = "Audio is loud but VAD does not indicate speech, so this is treated as non-speech audio requiring review."
        kind = "loud_non_speech_audio"
    else:
        label = "focused"
        visual_bonus = 0.1 if face_visible and vad_probability < 0.2 else 0.0
        confidence = min(0.58, (0.42 if rms <= max(median, 0.006) else 0.35) + visual_bonus)
        summary = "Speech likelihood is low; focused is based on quiet/non-speech audio and available visual stability."
        kind = "low_speech_likelihood"

    if transcript_cues is not None and transcript_cues["text"]:
        cue_label = transcript_cues["label"]
        if cue_label == "laughing":
            label = "laughing"
            confidence = max(confidence, 0.74)
            summary = "Transcript text contains laughter cues in this interval."
            kind = "transcript_laughter_cue"
        elif cue_label == "yelling/frustrated":
            label = "yelling/frustrated"
            confidence = max(confidence, 0.68)
            summary = "Transcript text and punctuation contain high-intensity speech cues in this interval."
            kind = "transcript_intensity_cue"
        elif cue_label == "talking" and label == "focused":
            label = "talking"
            confidence = max(confidence, 0.62)
            summary = "Transcript text overlaps this interval, so the focused candidate is promoted to talking."
            kind = "transcript_speech_cue"

    evidence = _base_evidence(
        streamer_profile=streamer_profile,
        label=label,
        confidence=confidence,
        context_summary=summary,
        signals={
            "audio_available": True,
            "window_rms": round(rms, 6),
            "baseline_median_rms": round(median, 6),
            "baseline_p75_rms": round(p75, 6),
            "baseline_p90_rms": round(p90, 6),
            "vad_likelihood": round(vad_probability, 3),
            "vad_source": (vad_cues or {}).get("source"),
            "transcript_text": transcript_cues["text"] if transcript_cues else None,
            "expression": expression_cues,
            "context_kind": kind,
        },
    )
    return LabeledWindow(window.start_seconds, window.end_seconds, label, confidence, evidence)


def _base_evidence(
    *,
    streamer_profile: sqlite3.Row,
    label: str,
    confidence: float,
    context_summary: str,
    signals: dict[str, Any],
) -> dict[str, Any]:
    prompt = _loads_json(streamer_profile["prompt_json"], {})
    streamer_confidence = float(prompt.get("confidence", 0.35))
    limitations = [
        "VAD can still confuse speech with some vocals, alerts, or game audio; review low-confidence intervals.",
    ]
    expression = signals.get("expression")
    if not isinstance(expression, dict) or not expression.get("available"):
        limitations.append("No expression feature was available for this interval.")
    if not signals.get("transcript_text"):
        limitations.append("No transcript cue was available for this interval.")
    return {
        "streamer": {
            "profile_id": streamer_profile["id"],
            "identification": prompt.get("type", "unknown"),
            "label": prompt.get("label", "primary_streamer_candidate"),
            "confidence": streamer_confidence,
        },
        "emotion": {
            "label": label,
            "confidence": round(confidence, 3),
            "claim_type": "observable activity candidate",
        },
        "context": {
            "summary": context_summary,
            "signals": signals,
        },
        "limitations": limitations,
    }


def _merge_labeled_windows(windows: list[LabeledWindow]) -> list[LabeledWindow]:
    if not windows:
        return []
    merged: list[LabeledWindow] = []
    current = windows[0]
    for window in windows[1:]:
        if window.label == current.label and abs(window.start_seconds - current.end_seconds) < 0.001:
            confidence = round((current.confidence + window.confidence) / 2.0, 3)
            evidence = dict(current.evidence)
            evidence["context"] = dict(evidence.get("context", {}))
            evidence["context"]["summary"] = _merge_summary(current.label)
            evidence["context"]["merged_windows"] = True
            current = LabeledWindow(current.start_seconds, window.end_seconds, current.label, confidence, evidence)
        else:
            merged.append(current)
            current = window
    merged.append(current)
    return merged


def _merge_summary(label: str) -> str:
    if label == "yelling/frustrated":
        return "Sustained high audio energy relative to the video's baseline."
    if label == "talking":
        return "Sustained above-baseline audio energy consistent with speech activity."
    if label == "laughing":
        return "Clustered raised-energy audio bursts; weak laughter candidate without expression/transcript corroboration."
    return "Sustained low audio energy relative to the video's baseline."


def import_transcript_segments(
    video_id: str,
    segments: list[TranscriptSegmentCreate],
    *,
    replace_existing: bool,
) -> list[TranscriptSegmentResponse]:
    _get_video_row(video_id)
    now = _now_iso()
    with connection() as conn:
        if replace_existing:
            conn.execute("DELETE FROM transcript_segments WHERE video_id = ?", (video_id,))
        for segment in segments:
            conn.execute(
                """
                INSERT INTO transcript_segments
                  (id, video_id, start_seconds, end_seconds, text, source, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    video_id,
                    segment.start_seconds,
                    segment.end_seconds,
                    segment.text.strip(),
                    segment.source,
                    segment.confidence,
                    now,
                ),
            )
    return list_transcript_segments(video_id)


def generate_transcript(video_id: str, request: TranscriptionGenerateRequest) -> list[TranscriptSegmentResponse]:
    video = _get_video_row(video_id)
    try:
        from faster_whisper import WhisperModel  # type: ignore  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        raise ValidationFailedError("faster-whisper is not installed; import transcript segments manually or install it locally") from exc

    device = "auto" if request.device == "auto" else request.device
    model = WhisperModel(request.model_size, device=device, compute_type=request.compute_type)
    segments, _info = model.transcribe(
        str(video["local_path"]),
        vad_filter=True,
        word_timestamps=True,
    )
    generated = [
        TranscriptSegmentCreate(
            start_seconds=float(segment.start),
            end_seconds=float(segment.end),
            text=str(segment.text).strip(),
            source="whisper",
            confidence=None,
        )
        for segment in segments
        if str(segment.text).strip()
    ]
    return import_transcript_segments(video_id, generated, replace_existing=request.replace_existing)


def list_transcript_segments(video_id: str) -> list[TranscriptSegmentResponse]:
    _get_video_row(video_id)
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM transcript_segments WHERE video_id = ? ORDER BY start_seconds",
            (video_id,),
        ).fetchall()
    return [_row_to_transcript(row) for row in rows]


def list_vad_segments(job_id: str) -> list[VadSegmentResponse]:
    with connection() as conn:
        if conn.execute("SELECT 1 FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone() is None:
            raise NotFoundError("analysis job")
        rows = conn.execute(
            "SELECT * FROM vad_segments WHERE job_id = ? ORDER BY start_seconds",
            (job_id,),
        ).fetchall()
    return [_row_to_vad(row) for row in rows]


def list_expression_features(job_id: str) -> list[ExpressionFeatureResponse]:
    with connection() as conn:
        if conn.execute("SELECT 1 FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone() is None:
            raise NotFoundError("analysis job")
        rows = conn.execute(
            "SELECT * FROM expression_features WHERE job_id = ? ORDER BY time_seconds",
            (job_id,),
        ).fetchall()
    return [_row_to_expression(row) for row in rows]


def _transcript_segments_for_video(video_id: str) -> list[sqlite3.Row]:
    with connection() as conn:
        return conn.execute(
            "SELECT * FROM transcript_segments WHERE video_id = ? ORDER BY start_seconds",
            (video_id,),
        ).fetchall()


def _transcript_cues_for_window(window: AudioWindow, transcripts: list[sqlite3.Row]) -> dict[str, Any]:
    matching = [
        row
        for row in transcripts
        if row["start_seconds"] < window.end_seconds and row["end_seconds"] > window.start_seconds
    ]
    text = " ".join(row["text"] for row in matching).strip()
    lowered = text.lower()
    label = "talking" if text else None
    laughter_terms = ("haha", "hehe", "lol", "lmao", "laugh", "[laugh")
    if any(term in lowered for term in laughter_terms):
        label = "laughing"
    elif "!" in text or any(word.isupper() and len(word) >= 4 for word in text.split()):
        label = "yelling/frustrated"
    return {"text": text or None, "label": label}


def _insert_activity_events(job_id: str, events: list[LabeledWindow]) -> None:
    with connection() as conn:
        for event in events:
            conn.execute(
                """
                INSERT INTO activity_events
                  (id, job_id, start_seconds, end_seconds, label, confidence,
                   source, evidence_json)
                VALUES (?, ?, ?, ?, ?, ?, 'fusion', ?)
                """,
                (
                    uuid.uuid4().hex,
                    job_id,
                    event.start_seconds,
                    event.end_seconds,
                    event.label,
                    event.confidence,
                    json.dumps(event.evidence),
                ),
            )


def _duration(video: sqlite3.Row) -> float:
    raw = video["duration_seconds"]
    if raw is None or raw <= 0:
        return WINDOW_SECONDS
    return float(raw)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (percentile / 100.0)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _tool_path(name: str) -> str | None:
    return shutil.which(name)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _loads_json(raw: str | None, fallback: Any) -> Any:
    if raw is None:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


def _row_to_job(row: sqlite3.Row) -> AnalysisJobResponse:
    return AnalysisJobResponse(
        id=row["id"],
        video_id=row["video_id"],
        status=row["status"],
        params=_loads_json(row["params_json"], {}),
        versions=_loads_json(row["versions_json"], {}),
        progress=_loads_json(row["progress_json"], None),
        error_message=row["error_message"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _row_to_profile(row: sqlite3.Row) -> StreamerProfileResponse:
    return StreamerProfileResponse(
        id=row["id"],
        video_id=row["video_id"],
        confirmed_by=row["confirmed_by"],
        confirmed_time_seconds=row["confirmed_time_seconds"],
        prompt=_loads_json(row["prompt_json"], {}),
        roi_bbox=_loads_json(row["roi_bbox_json"], None),
        face_crop_path=row["face_crop_path"],
        embedding_path=row["embedding_path"],
        created_at=row["created_at"],
    )


def _row_to_track(row: sqlite3.Row) -> TrackSegmentResponse:
    return TrackSegmentResponse(
        id=row["id"],
        job_id=row["job_id"],
        start_seconds=row["start_seconds"],
        end_seconds=row["end_seconds"],
        state=row["state"],
        confidence=row["confidence"],
        bbox=_loads_json(row["bbox_json"], None),
        artifact_ref=row["artifact_ref"],
        evidence=_loads_json(row["evidence_json"], {}),
    )


def _row_to_event(row: sqlite3.Row) -> ActivityEventResponse:
    return ActivityEventResponse(
        id=row["id"],
        job_id=row["job_id"],
        start_seconds=row["start_seconds"],
        end_seconds=row["end_seconds"],
        label=row["label"],
        confidence=row["confidence"],
        source=row["source"],
        evidence=_loads_json(row["evidence_json"], {}),
    )


def _row_to_roi_candidate(row: sqlite3.Row) -> RoiCandidateResponse:
    return RoiCandidateResponse(
        id=row["id"],
        video_id=row["video_id"],
        sample_start_seconds=row["sample_start_seconds"],
        sample_end_seconds=row["sample_end_seconds"],
        bbox=_loads_json(row["bbox_json"], {}),
        score=row["score"],
        evidence=_loads_json(row["evidence_json"], {}),
        preview_path=row["preview_path"],
    )


def _row_to_transcript(row: sqlite3.Row) -> TranscriptSegmentResponse:
    return TranscriptSegmentResponse(
        id=row["id"],
        video_id=row["video_id"],
        start_seconds=row["start_seconds"],
        end_seconds=row["end_seconds"],
        text=row["text"],
        source=row["source"],
        confidence=row["confidence"],
        created_at=row["created_at"],
    )


def _row_to_vad(row: sqlite3.Row) -> VadSegmentResponse:
    return VadSegmentResponse(
        id=row["id"],
        job_id=row["job_id"],
        start_seconds=row["start_seconds"],
        end_seconds=row["end_seconds"],
        speech_probability=row["speech_probability"],
        source=row["source"],
        evidence=_loads_json(row["evidence_json"], {}),
    )


def _row_to_expression(row: sqlite3.Row) -> ExpressionFeatureResponse:
    return ExpressionFeatureResponse(
        id=row["id"],
        job_id=row["job_id"],
        time_seconds=row["time_seconds"],
        bbox=_loads_json(row["bbox_json"], None),
        face_visible=bool(row["face_visible"]),
        mouth_open=row["mouth_open"],
        smile=row["smile"],
        brow_raise=row["brow_raise"],
        squint=row["squint"],
        head_pose=_loads_json(row["head_pose_json"], None),
        confidence=row["confidence"],
        evidence=_loads_json(row["evidence_json"], {}),
    )
