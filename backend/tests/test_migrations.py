"""Migration idempotency tests for the lightweight DB bootstrap migrator."""
from __future__ import annotations

from app.db import database as db_module
from app.db.database import connection


def test_title_column_exists_after_init(isolated_settings) -> None:
    with connection() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(videos)").fetchall()}
    assert "title" in cols


def test_run_migrations_is_idempotent(isolated_settings) -> None:
    # Force a second pass.
    db_module._reset_init_cache_for_tests()
    with connection() as conn:
        before = {row[1] for row in conn.execute("PRAGMA table_info(videos)").fetchall()}

    db_module._reset_init_cache_for_tests()
    with connection() as conn:
        after = {row[1] for row in conn.execute("PRAGMA table_info(videos)").fetchall()}

    assert before == after  # no duplicate columns added
    assert "title" in after


def test_run_migrations_skips_existing_column(isolated_settings) -> None:
    """If `title` already exists, _run_migrations must not raise."""
    # First init creates the column.
    with connection() as conn:
        pass
    # Manually invoke the migrator a second time on the same connection.
    import sqlite3
    settings = isolated_settings
    conn = sqlite3.connect(settings.db_path)
    try:
        db_module._run_migrations(conn)  # should be a no-op
        cols = {row[1] for row in conn.execute("PRAGMA table_info(videos)").fetchall()}
        assert "title" in cols
    finally:
        conn.close()
