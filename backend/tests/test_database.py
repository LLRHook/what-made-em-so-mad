from __future__ import annotations

from app.db.database import connection


def test_schema_creates_all_tables(isolated_settings) -> None:
    with connection() as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    names = {r["name"] for r in rows}
    expected = {
        "videos", "analysis_jobs", "roi_candidates", "streamer_profiles",
        "track_segments", "activity_events", "clips", "user_annotations",
    }
    assert expected.issubset(names)


def test_schema_is_idempotent(isolated_settings) -> None:
    with connection() as conn:
        before = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    # Second open invokes _ensure_initialised again; should not raise.
    with connection() as conn:
        after = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert len(before) == len(after)


def test_foreign_keys_enabled(isolated_settings) -> None:
    with connection() as conn:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()
    assert fk[0] == 1
