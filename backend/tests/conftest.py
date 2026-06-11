from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings, reset_settings_for_tests
from app.db import database as db_module


@pytest.fixture
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Point the app at a throwaway data directory for the duration of a test."""
    s = Settings(data_dir=tmp_path / "data", models_dir=tmp_path / "models")
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.models_dir.mkdir(parents=True, exist_ok=True)
    reset_settings_for_tests(s)
    db_module._reset_init_cache_for_tests()
    yield s
    reset_settings_for_tests(None)
    db_module._reset_init_cache_for_tests()


@pytest.fixture
def client(isolated_settings: Settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
