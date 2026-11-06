"""SQLite metadata only; no media files or ingestion/inference operations."""
import json

from app.db.database import connection
from app.services.analysis import ANALYSIS_KIND


def seed_timeline():
    with connection() as conn:
        conn.execute(
            """INSERT INTO videos
            (id, source_type, source_uri, sha256, local_path, duration_seconds, fps, width, height, created_at)
            VALUES ('synthetic-video', 'local', 'synthetic://metadata', 'synthetic-hash', 'synthetic.mp4', 60, 30, 1280, 720, '2026-01-01T00:00:00Z')"""
        )
        conn.execute(
            """INSERT INTO analysis_jobs (id, video_id, status, params_json, versions_json, created_at)
            VALUES ('synthetic-job', 'synthetic-video', 'complete', ?, '{}', '2026-01-01T00:00:00Z')""",
            (json.dumps({"kind": ANALYSIS_KIND}),),
        )
        conn.execute(
            """INSERT INTO activity_events (id, job_id, start_seconds, end_seconds, label, confidence, source)
            VALUES ('synthetic-event', 'synthetic-job', 0, 10, 'laughing', 0.9, 'fusion')"""
        )
        conn.execute(
            """INSERT INTO track_segments (id, job_id, start_seconds, end_seconds, state, confidence)
            VALUES ('synthetic-track', 'synthetic-job', 5, 15, 'returned', 0.7)"""
        )
