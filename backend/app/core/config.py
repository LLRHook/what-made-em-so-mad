from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WMESM_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    data_dir: Path = Field(default=_DEFAULT_REPO_ROOT / "data")
    models_dir: Path = Field(default=_DEFAULT_REPO_ROOT / "models")
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])
    thumbnail_interval_seconds: float = 5.0
    thumbnail_max_height: int = 180
    default_pre_roll_seconds: float = 2.0
    default_post_roll_seconds: float = 3.0
    access_token: str | None = None
    max_upload_bytes: int | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "wmesm.sqlite"

    @property
    def videos_dir(self) -> Path:
        return self.data_dir / "videos"

    @property
    def clips_dir(self) -> Path:
        return self.data_dir / "clips"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_for_tests(override: Settings | None = None) -> None:
    """Allow tests to replace the cached settings singleton."""
    global _settings
    _settings = override
