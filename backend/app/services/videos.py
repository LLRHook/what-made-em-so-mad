from __future__ import annotations

import shutil
import sqlite3
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from app.core.errors import NotFoundError
from app.core.paths import clip_dir, video_dir, video_source_path, video_thumbs_dir
from app.db.database import connection
from app.domain.models import (
    VideoLatestJob,
    VideoListEntry,
    VideoSummary,
)
from app.services.ffprobe import ProbedMetadata
from app.services.hashing import sha256_stream_to_file


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_video_by_sha(sha256: str) -> VideoSummary | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM videos WHERE sha256 = ?", (sha256,)).fetchone()
    if row is None:
        return None
    return _row_to_summary(row, thumbnails_for(row["id"]))


def insert_video(
    *,
    video_id: str,
    source_type: str,
    source_uri: str,
    sha256: str,
    local_path: Path,
    metadata: ProbedMetadata,
) -> str:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO videos (id, source_type, source_uri, sha256, local_path,
                                duration_seconds, fps, width, height, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video_id,
                source_type,
                source_uri,
                sha256,
                str(local_path),
                metadata.duration_seconds,
                metadata.fps,
                metadata.width,
                metadata.height,
                _now_iso(),
            ),
        )
    return video_id


def get_video(video_id: str) -> VideoSummary:
    with connection() as conn:
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    if row is None:
        raise NotFoundError("video")
    return _row_to_summary(row, thumbnails_for(video_id))


def thumbnails_for(video_id: str) -> list[str]:
    out = video_thumbs_dir(video_id)
    if not out.exists():
        return []
    return [f"/api/videos/{video_id}/thumbnails/{p.name}" for p in sorted(out.glob("*.jpg"))]


def _row_to_summary(row: sqlite3.Row, thumbs: list[str]) -> VideoSummary:
    return VideoSummary(
        id=row["id"],
        source_type=row["source_type"],
        source_uri=row["source_uri"],
        title=_row_title(row),
        sha256=row["sha256"],
        local_path=row["local_path"],
        source_url=f"/api/videos/{row['id']}/source",
        duration_seconds=row["duration_seconds"],
        fps=row["fps"],
        width=row["width"],
        height=row["height"],
        created_at=row["created_at"],
        thumbnails=thumbs,
    )


def _row_title(row: sqlite3.Row) -> str | None:
    """Return the row's title if the column exists and is set, otherwise None.

    `sqlite3.Row.keys()` lets us be tolerant of rows from older DBs whose
    migration has not yet run.
    """
    if "title" not in row.keys():
        return None
    value = row["title"]
    return value if value else None


def reserve_video_id() -> str:
    return uuid.uuid4().hex


def write_upload_to_disk(video_id: str, src: BinaryIO) -> tuple[Path, str]:
    """Stream the upload to its on-disk source path and return (path, sha256)."""
    dest = video_source_path(video_id)
    sha = sha256_stream_to_file(src, dest)
    return dest, sha


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ----- S-001: server-side project list -----

_LIST_QUERY = """
    SELECT v.id              AS video_id,
           v.source_type     AS source_type,
           v.source_uri      AS source_uri,
           v.title           AS title,
           v.duration_seconds AS duration_seconds,
           v.width           AS width,
           v.height          AS height,
           v.created_at      AS created_at,
           j.id              AS job_id,
           j.status          AS job_status,
           j.completed_at    AS job_completed_at,
           j.error_message   AS job_error_message,
           (SELECT COUNT(*) FROM clips c WHERE c.video_id = v.id) AS clip_count
      FROM videos v
      LEFT JOIN analysis_jobs j
        ON j.id = (
            SELECT id FROM analysis_jobs
            WHERE video_id = v.id
            ORDER BY created_at DESC
            LIMIT 1
        )
     ORDER BY v.created_at DESC, v.rowid DESC
"""


def list_videos() -> list[VideoListEntry]:
    with connection() as conn:
        rows = conn.execute(_LIST_QUERY).fetchall()
    return [_row_to_list_entry(row) for row in rows]


def _row_to_list_entry(row: sqlite3.Row) -> VideoListEntry:
    video_id = row["video_id"]
    latest: VideoLatestJob | None = None
    if row["job_id"] is not None:
        latest = VideoLatestJob(
            id=row["job_id"],
            status=row["job_status"],
            completed_at=row["job_completed_at"],
            error_message=row["job_error_message"],
        )
    return VideoListEntry(
        id=video_id,
        source_type=row["source_type"],
        source_uri=row["source_uri"],
        title=row["title"] or None,
        duration_seconds=row["duration_seconds"],
        width=row["width"],
        height=row["height"],
        created_at=row["created_at"],
        source_url=f"/api/videos/{video_id}/source",
        thumbnails=thumbnails_for(video_id),
        latest_job=latest,
        clip_count=int(row["clip_count"] or 0),
    )


def update_video_title(video_id: str, title: str | None) -> VideoSummary:
    with connection() as conn:
        existing = conn.execute("SELECT 1 FROM videos WHERE id = ?", (video_id,)).fetchone()
        if existing is None:
            raise NotFoundError("video")
        conn.execute("UPDATE videos SET title = ? WHERE id = ?", (title, video_id))
    return get_video(video_id)


def delete_video(video_id: str) -> None:
    """Delete the video row (cascading to all child rows) and all owned on-disk artifacts.

    Disk cleanup is best-effort: if it fails partway through, the DB row is already
    gone so the API remains the source of truth for what exists.
    """
    with connection() as conn:
        if conn.execute("SELECT 1 FROM videos WHERE id = ?", (video_id,)).fetchone() is None:
            raise NotFoundError("video")
        owned_clip_ids = [
            row["id"]
            for row in conn.execute("SELECT id FROM clips WHERE video_id = ?", (video_id,)).fetchall()
        ]
        conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))

    # Filesystem cleanup runs after the DB commit so a partial filesystem failure
    # cannot leave a stale DB row pointing at vanished bytes.
    shutil.rmtree(video_dir(video_id), ignore_errors=True)
    for clip_id in owned_clip_ids:
        shutil.rmtree(clip_dir(clip_id), ignore_errors=True)
