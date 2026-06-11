from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from threading import Lock

from app.core.config import Settings, get_settings

_init_lock = Lock()
_initialised_paths: set[Path] = set()


def _read_schema_sql() -> str:
    pkg = resources.files("app.db")
    return (pkg / "schema.sql").read_text(encoding="utf-8")


def _column_missing(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return column not in {row[1] for row in rows}


def _table_missing(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is None


# Lightweight, idempotent post-`executescript` migrations.
#
# Fresh installs pick new columns up via `CREATE TABLE IF NOT EXISTS` in
# schema.sql. Pre-existing databases get upgraded here. Each migration pairs a
# `predicate` (run it?) with the SQL to apply, so the whole list is safe to
# re-run on every startup. To add a migration, append a (predicate, sql) tuple.
_Predicate = Callable[[sqlite3.Connection], bool]
_MIGRATIONS: tuple[tuple[_Predicate, str], ...] = (
    (lambda c: _column_missing(c, "videos", "title"), "ALTER TABLE videos ADD COLUMN title TEXT"),
    (
        lambda c: _column_missing(c, "clips", "suggestion_score"),
        "ALTER TABLE clips ADD COLUMN suggestion_score REAL",
    ),
    (
        lambda c: _column_missing(c, "clips", "suggestion_reason"),
        "ALTER TABLE clips ADD COLUMN suggestion_reason TEXT",
    ),
    (
        lambda c: _column_missing(c, "clips", "accepted"),
        "ALTER TABLE clips ADD COLUMN accepted INTEGER NOT NULL DEFAULT 0",
    ),
    (
        lambda c: _table_missing(c, "transcript_segments"),
        """
        CREATE TABLE IF NOT EXISTS transcript_segments (
            id              TEXT    PRIMARY KEY,
            video_id        TEXT    NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
            start_seconds   REAL    NOT NULL,
            end_seconds     REAL    NOT NULL,
            text            TEXT    NOT NULL,
            source          TEXT    NOT NULL CHECK (source IN ('manual','whisper','imported')),
            confidence      REAL,
            created_at      TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_transcript_segments_video
            ON transcript_segments(video_id, start_seconds);
        """,
    ),
    (
        lambda c: _table_missing(c, "vad_segments"),
        """
        CREATE TABLE IF NOT EXISTS vad_segments (
            id                  TEXT    PRIMARY KEY,
            job_id              TEXT    NOT NULL REFERENCES analysis_jobs(id) ON DELETE CASCADE,
            start_seconds       REAL    NOT NULL,
            end_seconds         REAL    NOT NULL,
            speech_probability  REAL    NOT NULL,
            source              TEXT    NOT NULL,
            evidence_json       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_vad_segments_job
            ON vad_segments(job_id, start_seconds);
        """,
    ),
    (
        lambda c: _table_missing(c, "expression_features"),
        """
        CREATE TABLE IF NOT EXISTS expression_features (
            id              TEXT    PRIMARY KEY,
            job_id          TEXT    NOT NULL REFERENCES analysis_jobs(id) ON DELETE CASCADE,
            time_seconds    REAL    NOT NULL,
            bbox_json       TEXT,
            face_visible    INTEGER NOT NULL,
            mouth_open      REAL,
            smile           REAL,
            brow_raise      REAL,
            squint          REAL,
            head_pose_json  TEXT,
            confidence      REAL    NOT NULL,
            evidence_json   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_expression_features_job
            ON expression_features(job_id, time_seconds);
        """,
    ),
    (
        lambda c: _table_missing(c, "evaluation_runs"),
        """
        CREATE TABLE IF NOT EXISTS evaluation_runs (
            id                TEXT    PRIMARY KEY,
            fixture_set_path  TEXT    NOT NULL,
            metrics_json      TEXT    NOT NULL,
            created_at        TEXT    NOT NULL
        );
        """,
    ),
)


def _run_migrations(conn: sqlite3.Connection) -> None:
    for predicate, sql in _MIGRATIONS:
        if predicate(conn):
            conn.executescript(sql)
    conn.commit()


def _ensure_initialised(settings: Settings) -> None:
    db_path = settings.db_path
    with _init_lock:
        if db_path in _initialised_paths:
            return
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(_read_schema_sql())
            conn.commit()
            _run_migrations(conn)
        finally:
            conn.close()
        _initialised_paths.add(db_path)


def _reset_init_cache_for_tests() -> None:
    """Clear the initialised-paths memo so test fixtures get a fresh init."""
    with _init_lock:
        _initialised_paths.clear()


@contextmanager
def connection(settings: Settings | None = None) -> Iterator[sqlite3.Connection]:
    settings = settings or get_settings()
    _ensure_initialised(settings)
    conn = sqlite3.connect(settings.db_path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
