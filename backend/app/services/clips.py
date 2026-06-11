from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.core.errors import AppError, FFmpegExecutionError, NotFoundError, ValidationFailedError
from app.core.config import get_settings
from app.core.paths import clip_dir, clip_output_path
from app.db.database import connection
from app.domain.models import ClipResponse, SuggestedClipsRequest
from app.services.ffmpeg import build_clip_command, clamp_range, ffmpeg_binary, run_ffmpeg


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_clip_draft(
    *,
    video_id: str,
    start_seconds: float,
    end_seconds: float,
    labels: list[str],
    title: str | None,
) -> ClipResponse:
    if end_seconds <= start_seconds:
        raise ValidationFailedError("end_seconds must be greater than start_seconds")
    with connection() as conn:
        if conn.execute("SELECT 1 FROM videos WHERE id = ?", (video_id,)).fetchone() is None:
            raise NotFoundError("video")
        clip_id = uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO clips (id, job_id, video_id, start_seconds, end_seconds,
                               labels_json, title, status, output_path, error_message, created_at)
            VALUES (?, NULL, ?, ?, ?, ?, ?, 'draft', NULL, NULL, ?)
            """,
            (
                clip_id,
                video_id,
                float(start_seconds),
                float(end_seconds),
                json.dumps(labels),
                title,
                _now_iso(),
            ),
        )
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    return _row_to_response(row)


def list_clips_for_video(video_id: str) -> list[ClipResponse]:
    with connection() as conn:
        if conn.execute("SELECT 1 FROM videos WHERE id = ?", (video_id,)).fetchone() is None:
            raise NotFoundError("video")
        rows = conn.execute(
            "SELECT * FROM clips WHERE video_id = ? ORDER BY start_seconds, created_at",
            (video_id,),
        ).fetchall()
    return [_row_to_response(row) for row in rows]


def get_clip(clip_id: str) -> ClipResponse:
    with connection() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    if row is None:
        raise NotFoundError("clip")
    return _row_to_response(row)


def update_clip(clip_id: str, *, title: str | None = None, accepted: bool | None = None) -> ClipResponse:
    with connection() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if row is None:
            raise NotFoundError("clip")
        next_title = title if title is not None else row["title"]
        next_accepted = (1 if accepted else 0) if accepted is not None else int(row["accepted"] or 0)
        conn.execute(
            "UPDATE clips SET title = ?, accepted = ? WHERE id = ?",
            (next_title, next_accepted, clip_id),
        )
        updated = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    return _row_to_response(updated)


def delete_clip(clip_id: str) -> None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if row is None:
            raise NotFoundError("clip")
        conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    shutil.rmtree(clip_dir(clip_id), ignore_errors=True)


def create_suggested_clips(video_id: str, request: SuggestedClipsRequest) -> list[ClipResponse]:
    with connection() as conn:
        video = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        if video is None:
            raise NotFoundError("video")
        job = conn.execute(
            """
            SELECT *
            FROM analysis_jobs
            WHERE video_id = ? AND status = 'complete'
            ORDER BY completed_at DESC, created_at DESC
            LIMIT 1
            """,
            (video_id,),
        ).fetchone()
        if job is None:
            raise ValidationFailedError("run analysis before creating suggested clips")

        activity_rows = conn.execute(
            """
            SELECT id, start_seconds, end_seconds, label, confidence, evidence_json
            FROM activity_events
            WHERE job_id = ? AND confidence >= ?
            ORDER BY start_seconds
            """,
            (job["id"], request.min_confidence),
        ).fetchall()
        track_rows = conn.execute(
            """
            SELECT id, start_seconds, end_seconds, state AS label, confidence, evidence_json
            FROM track_segments
            WHERE job_id = ? AND confidence >= ?
            ORDER BY start_seconds
            """,
            (job["id"], request.min_confidence),
        ).fetchall()

    wanted = set(request.labels)
    raw = [
        _suggestion_source(row, source="activity")
        for row in activity_rows
        if row["label"] in wanted
    ]
    raw.extend(
        _suggestion_source(row, source="lifecycle")
        for row in track_rows
        if row["label"] in wanted
    )
    raw.sort(key=lambda item: (item["start_seconds"], -item["confidence"]))
    clusters = _cluster_suggestions(raw, merge_gap_seconds=request.merge_gap_seconds)

    created: list[ClipResponse] = []
    duration = video["duration_seconds"]
    with connection() as conn:
        for cluster in clusters[: request.limit]:
            start = max(0.0, cluster["start_seconds"] - request.pre_roll_seconds)
            end = cluster["end_seconds"] + request.post_roll_seconds
            if duration is not None:
                end = min(float(duration), end)
            if end <= start:
                continue
            clip_id = uuid.uuid4().hex
            title = f"Suggested {', '.join(cluster['labels'])} {start:.1f}-{end:.1f}s"
            conn.execute(
                """
                INSERT INTO clips
                  (id, job_id, video_id, start_seconds, end_seconds, labels_json,
                   title, suggestion_score, suggestion_reason, accepted, status,
                   output_path, error_message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'draft', NULL, NULL, ?)
                """,
                (
                    clip_id,
                    job["id"],
                    video_id,
                    start,
                    end,
                    json.dumps(cluster["labels"]),
                    title,
                    cluster["score"],
                    cluster["reason"],
                    _now_iso(),
                ),
            )
            row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
            created.append(_row_to_response(row))
    return created


def batch_export_clips(
    clip_ids: list[str],
    *,
    pre_roll_seconds: float,
    post_roll_seconds: float,
    mode: Literal["stream_copy_when_possible", "re_encode"],
) -> tuple[list[ClipResponse], str]:
    exported: list[ClipResponse] = []
    for clip_id in clip_ids:
        try:
            exported.append(
                export_clip(
                    clip_id,
                    pre_roll_seconds=pre_roll_seconds,
                    post_roll_seconds=post_roll_seconds,
                    mode=mode,
                )
            )
        except AppError:
            exported.append(get_clip(clip_id))
    manifest_path = _write_batch_manifest(exported, pre_roll_seconds=pre_roll_seconds, post_roll_seconds=post_roll_seconds, mode=mode)
    return exported, str(manifest_path)


def export_clip(
    clip_id: str,
    *,
    pre_roll_seconds: float,
    post_roll_seconds: float,
    mode: Literal["stream_copy_when_possible", "re_encode"],
    which=shutil.which,
) -> ClipResponse:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT c.*, v.local_path AS source_path, v.duration_seconds AS source_duration
            FROM clips c
            JOIN videos v ON v.id = c.video_id
            WHERE c.id = ?
            """,
            (clip_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError("clip")
        if row["status"] not in ("draft", "failed"):
            raise ValidationFailedError(f"clip is in status '{row['status']}' and cannot be re-exported")

        clip_range = clamp_range(
            row["start_seconds"],
            row["end_seconds"],
            pre_roll=pre_roll_seconds,
            post_roll=post_roll_seconds,
            video_duration=row["source_duration"],
        )

        binary = ffmpeg_binary(which=which)
        src = Path(row["source_path"])
        dest = clip_output_path(clip_id)
        dest.parent.mkdir(parents=True, exist_ok=True)

        conn.execute("UPDATE clips SET status='exporting', error_message=NULL WHERE id = ?", (clip_id,))
        conn.commit()

        cmd = build_clip_command(binary, src, dest, clip_range, mode=mode)

    try:
        run_ffmpeg(cmd)
    except FFmpegExecutionError as exc:
        with connection() as conn:
            conn.execute(
                "UPDATE clips SET status='failed', error_message=? WHERE id = ?",
                (str(exc.detail), clip_id),
            )
        raise

    with connection() as conn:
        conn.execute(
            "UPDATE clips SET status='exported', output_path=?, error_message=NULL WHERE id = ?",
            (str(dest), clip_id),
        )
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    return _row_to_response(row)


def _row_to_response(row: sqlite3.Row) -> ClipResponse:
    labels = json.loads(row["labels_json"]) if row["labels_json"] else []
    return ClipResponse(
        id=row["id"],
        video_id=row["video_id"],
        start_seconds=row["start_seconds"],
        end_seconds=row["end_seconds"],
        labels=labels,
        title=row["title"],
        suggestion_score=_optional_row_value(row, "suggestion_score"),
        suggestion_reason=_optional_row_value(row, "suggestion_reason"),
        accepted=bool(_optional_row_value(row, "accepted") or False),
        status=row["status"],
        output_path=row["output_path"],
        output_url=f"/api/clips/{row['id']}/download" if row["output_path"] else None,
        error_message=row["error_message"],
        created_at=row["created_at"],
    )


def _optional_row_value(row: sqlite3.Row, key: str) -> Any:
    if key not in row.keys():
        return None
    return row[key]


def _suggestion_source(row: sqlite3.Row, *, source: str) -> dict[str, Any]:
    return {
        "id": row["id"],
        "source": source,
        "start_seconds": float(row["start_seconds"]),
        "end_seconds": float(row["end_seconds"]),
        "label": row["label"],
        "confidence": float(row["confidence"]),
    }


def _cluster_suggestions(items: list[dict[str, Any]], *, merge_gap_seconds: float) -> list[dict[str, Any]]:
    if not items:
        return []
    clusters: list[dict[str, Any]] = []
    current = {
        "start_seconds": items[0]["start_seconds"],
        "end_seconds": items[0]["end_seconds"],
        "labels": [items[0]["label"]],
        "score": items[0]["confidence"],
        "source_ids": [items[0]["id"]],
    }
    for item in items[1:]:
        if item["start_seconds"] - current["end_seconds"] <= merge_gap_seconds:
            current["end_seconds"] = max(current["end_seconds"], item["end_seconds"])
            if item["label"] not in current["labels"]:
                current["labels"].append(item["label"])
            current["score"] = max(current["score"], item["confidence"])
            current["source_ids"].append(item["id"])
        else:
            clusters.append(_finish_cluster(current))
            current = {
                "start_seconds": item["start_seconds"],
                "end_seconds": item["end_seconds"],
                "labels": [item["label"]],
                "score": item["confidence"],
                "source_ids": [item["id"]],
            }
    clusters.append(_finish_cluster(current))
    clusters.sort(key=lambda item: (-item["score"], item["start_seconds"]))
    return clusters


def _finish_cluster(cluster: dict[str, Any]) -> dict[str, Any]:
    labels = sorted(cluster["labels"])
    cluster["labels"] = labels
    cluster["score"] = round(float(cluster["score"]), 3)
    cluster["reason"] = (
        f"Suggested from {', '.join(labels)} evidence across "
        f"{len(cluster['source_ids'])} timeline segment(s)."
    )
    return cluster


def _write_batch_manifest(
    clips: list[ClipResponse],
    *,
    pre_roll_seconds: float,
    post_roll_seconds: float,
    mode: str,
) -> Path:
    manifest_dir = get_settings().data_dir / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"batch-{uuid.uuid4().hex}.json"
    payload = {
        "created_at": _now_iso(),
        "settings": {
            "pre_roll_seconds": pre_roll_seconds,
            "post_roll_seconds": post_roll_seconds,
            "mode": mode,
        },
        "clips": [
            {
                "id": clip.id,
                "video_id": clip.video_id,
                "start_seconds": clip.start_seconds,
                "end_seconds": clip.end_seconds,
                "labels": clip.labels,
                "title": clip.title,
                "status": clip.status,
                "suggestion_score": clip.suggestion_score,
                "suggestion_reason": clip.suggestion_reason,
                "output_path": clip.output_path,
                "error_message": clip.error_message,
            }
            for clip in clips
        ],
    }
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return manifest_path
