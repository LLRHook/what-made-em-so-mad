from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings


def video_dir(video_id: str) -> Path:
    return get_settings().videos_dir / video_id


def video_source_path(video_id: str) -> Path:
    return video_dir(video_id) / "source.mp4"


def video_thumbs_dir(video_id: str) -> Path:
    return video_dir(video_id) / "thumbs"


def video_roi_dir(video_id: str) -> Path:
    return video_dir(video_id) / "roi"


def video_identity_dir(video_id: str) -> Path:
    return video_dir(video_id) / "identity"


def video_masks_dir(video_id: str, job_id: str) -> Path:
    return video_dir(video_id) / "masks" / job_id


def video_diagnostics_dir(video_id: str) -> Path:
    return video_dir(video_id) / "diagnostics"


def clip_dir(clip_id: str) -> Path:
    return get_settings().clips_dir / clip_id


def clip_output_path(clip_id: str) -> Path:
    return clip_dir(clip_id) / "output.mp4"


def ensure_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
