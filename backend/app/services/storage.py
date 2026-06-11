from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationFailedError
from app.core.paths import clip_dir, video_diagnostics_dir, video_dir, video_roi_dir, video_thumbs_dir
from app.db.database import connection
from app.domain.models import CleanupArtifactsRequest, StorageUsageResponse


def storage_usage() -> StorageUsageResponse:
    settings = get_settings()
    videos: list[dict[str, Any]] = []
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT v.id, v.source_uri, v.title, v.created_at,
                   COUNT(c.id) AS clip_count
            FROM videos v
            LEFT JOIN clips c ON c.video_id = v.id
            GROUP BY v.id
            ORDER BY v.created_at DESC
            """
        ).fetchall()
    for row in rows:
        v_dir = video_dir(row["id"])
        videos.append(
            {
                "id": row["id"],
                "source_uri": row["source_uri"],
                "title": row["title"],
                "created_at": row["created_at"],
                "clip_count": int(row["clip_count"] or 0),
                "bytes": _dir_size(v_dir),
                "thumbnails_bytes": _dir_size(video_thumbs_dir(row["id"])),
            }
        )

    videos_bytes = _dir_size(settings.videos_dir)
    clips_bytes = _dir_size(settings.clips_dir)
    database_bytes = _file_size(settings.db_path)
    return StorageUsageResponse(
        data_dir=str(settings.data_dir),
        total_bytes=videos_bytes + clips_bytes + database_bytes,
        videos_bytes=videos_bytes,
        clips_bytes=clips_bytes,
        database_bytes=database_bytes,
        videos=videos,
    )


def cleanup_video_artifacts(video_id: str, body: CleanupArtifactsRequest) -> StorageUsageResponse:
    if not body.thumbnails and not body.clips and not body.diagnostics:
        raise ValidationFailedError("select at least one artifact type to clean up")

    with connection() as conn:
        if conn.execute("SELECT 1 FROM videos WHERE id = ?", (video_id,)).fetchone() is None:
            raise NotFoundError("video")
        clip_rows = conn.execute(
            "SELECT id, status FROM clips WHERE video_id = ?",
            (video_id,),
        ).fetchall()

    if body.thumbnails:
        shutil.rmtree(video_thumbs_dir(video_id), ignore_errors=True)

    if body.diagnostics:
        shutil.rmtree(video_diagnostics_dir(video_id), ignore_errors=True)
        shutil.rmtree(video_roi_dir(video_id), ignore_errors=True)
        shutil.rmtree(video_dir(video_id) / "masks", ignore_errors=True)
        with connection() as conn:
            conn.execute("UPDATE roi_candidates SET preview_path = NULL WHERE video_id = ?", (video_id,))
            conn.execute(
                """
                UPDATE track_segments
                SET artifact_ref = NULL
                WHERE job_id IN (SELECT id FROM analysis_jobs WHERE video_id = ?)
                """,
                (video_id,),
            )

    if body.clips:
        cleaned_clip_ids: list[str] = []
        for clip in clip_rows:
            if body.failed_clips_only and clip["status"] != "failed":
                continue
            shutil.rmtree(clip_dir(clip["id"]), ignore_errors=True)
            cleaned_clip_ids.append(clip["id"])
        if cleaned_clip_ids:
            placeholders = ",".join("?" for _ in cleaned_clip_ids)
            with connection() as conn:
                conn.execute(
                    f"""
                    UPDATE clips
                    SET output_path = NULL,
                        status = CASE WHEN status = 'exported' THEN 'draft' ELSE status END
                    WHERE id IN ({placeholders})
                    """,
                    cleaned_clip_ids,
                )

    return storage_usage()


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += _file_size(item)
    return total
