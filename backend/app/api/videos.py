from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, File, Response, UploadFile, status
from fastapi.responses import FileResponse

from app.core.errors import (
    FFmpegMissingError,
    FFprobeMissingError,
    NotFoundError,
    ValidationFailedError,
    YtDlpMissingError,
)
from app.core.config import get_settings
from app.core.paths import video_source_path, video_thumbs_dir
from app.domain.models import (
    CleanupArtifactsRequest,
    ClipResponse,
    FromUrlRequest,
    StorageUsageResponse,
    SuggestedClipsRequest,
    VideoImportResponse,
    VideoListEntry,
    VideoSummary,
    VideoUpdateRequest,
)
from app.services.capabilities import has_ffmpeg, has_ffprobe, has_yt_dlp
from app.services.ffprobe import probe_file
from app.services.thumbnails import generate_thumbnails
from app.services.videos import (
    delete_video,
    find_video_by_sha,
    get_video,
    insert_video,
    list_videos,
    reserve_video_id,
    sha256_file,
    update_video_title,
    write_upload_to_disk,
)
from app.services.analysis import ensure_auto_analysis
from app.services.clips import create_suggested_clips, list_clips_for_video
from app.services.storage import cleanup_video_artifacts

router = APIRouter(prefix="/api/videos", tags=["videos"])


@router.get("", response_model=list[VideoListEntry])
def list_all_videos() -> list[VideoListEntry]:
    """S-001: server-side project list. Returns every persisted video newest-first."""
    return list_videos()


@router.post("", response_model=VideoImportResponse, status_code=201)
async def import_local_video(file: UploadFile = File(...)) -> VideoImportResponse:
    if file.filename is None or not file.filename.lower().endswith((".mp4", ".mov", ".m4v", ".mkv")):
        raise ValidationFailedError("Only .mp4 / .mov / .m4v / .mkv files are accepted in M1.")
    if not has_ffprobe():
        raise FFprobeMissingError()
    if not has_ffmpeg():
        raise FFmpegMissingError()

    video_id = reserve_video_id()
    dest_path, sha = write_upload_to_disk(video_id, file.file)
    max_upload_bytes = get_settings().max_upload_bytes
    if max_upload_bytes is not None and dest_path.stat().st_size > max_upload_bytes:
        shutil.rmtree(dest_path.parent, ignore_errors=True)
        raise ValidationFailedError(f"upload exceeds configured maximum of {max_upload_bytes} bytes")

    duplicate = find_video_by_sha(sha)
    if duplicate is not None:
        # Roll back the temporary directory we just created.
        try:
            shutil.rmtree(dest_path.parent, ignore_errors=True)
        except OSError:
            pass
        return VideoImportResponse(video_id=duplicate.id, duplicate_of=duplicate.id)

    metadata = probe_file(dest_path)
    insert_video(
        video_id=video_id,
        source_type="local",
        source_uri=file.filename,
        sha256=sha,
        local_path=dest_path,
        metadata=metadata,
    )

    # Thumbnails are best-effort: if they fail the import still succeeds.
    try:
        generate_thumbnails(video_id, dest_path, metadata.duration_seconds)
    except Exception:  # noqa: BLE001
        pass

    # The first analysis pass is intentionally best-effort. It creates persisted
    # timeline segments immediately, then later SAM/face/expression providers can
    # replace this low-confidence baseline with stronger evidence.
    try:
        ensure_auto_analysis(video_id)
    except Exception:  # noqa: BLE001
        pass

    return VideoImportResponse(video_id=video_id)


@router.post("/from-url", response_model=VideoImportResponse, status_code=201)
def import_from_url(body: FromUrlRequest) -> VideoImportResponse:
    if not has_yt_dlp():
        raise YtDlpMissingError()
    if not has_ffprobe():
        raise FFprobeMissingError()
    if not has_ffmpeg():
        raise FFmpegMissingError()

    video_id = reserve_video_id()
    dest_path = video_source_path(video_id)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    binary = shutil.which("yt-dlp")
    if binary is None:
        raise YtDlpMissingError()
    cmd = [
        binary,
        "--no-playlist",
        "--merge-output-format",
        "mp4",
        "-o",
        str(dest_path),
        body.url,
    ]
    if body.cookies_path:
        cmd[1:1] = ["--cookies", body.cookies_path]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, check=False)
    except OSError as exc:
        raise ValidationFailedError(f"yt-dlp could not start: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValidationFailedError("yt-dlp timed out") from exc
    if completed.returncode != 0 or not dest_path.exists():
        shutil.rmtree(dest_path.parent, ignore_errors=True)
        detail = (completed.stderr or completed.stdout or "yt-dlp failed").strip()[:500]
        raise ValidationFailedError(detail)
    max_upload_bytes = get_settings().max_upload_bytes
    if max_upload_bytes is not None and dest_path.stat().st_size > max_upload_bytes:
        shutil.rmtree(dest_path.parent, ignore_errors=True)
        raise ValidationFailedError(f"download exceeds configured maximum of {max_upload_bytes} bytes")

    sha = sha256_file(dest_path)
    duplicate = find_video_by_sha(sha)
    if duplicate is not None:
        shutil.rmtree(dest_path.parent, ignore_errors=True)
        return VideoImportResponse(video_id=duplicate.id, duplicate_of=duplicate.id)

    metadata = probe_file(dest_path)
    insert_video(
        video_id=video_id,
        source_type="url",
        source_uri=body.url,
        sha256=sha,
        local_path=dest_path,
        metadata=metadata,
    )
    try:
        generate_thumbnails(video_id, dest_path, metadata.duration_seconds)
    except Exception:  # noqa: BLE001
        pass
    try:
        ensure_auto_analysis(video_id)
    except Exception:  # noqa: BLE001
        pass
    return VideoImportResponse(video_id=video_id)


@router.get("/{video_id}", response_model=VideoSummary)
def read_video(video_id: str) -> VideoSummary:
    return get_video(video_id)


@router.patch("/{video_id}", response_model=VideoSummary)
def rename_video(video_id: str, body: VideoUpdateRequest) -> VideoSummary:
    """Update the user-supplied display title. `title=null` clears it."""
    return update_video_title(video_id, body.title)


@router.delete("/{video_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def remove_video(video_id: str) -> Response:
    """Delete the video, every dependent row, and every owned on-disk artifact."""
    delete_video(video_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{video_id}/clips", response_model=list[ClipResponse])
def list_video_clips(video_id: str) -> list[ClipResponse]:
    return list_clips_for_video(video_id)


@router.post("/{video_id}/suggested-clips", response_model=list[ClipResponse], status_code=201)
def suggest_video_clips(video_id: str, body: SuggestedClipsRequest | None = None) -> list[ClipResponse]:
    return create_suggested_clips(video_id, body or SuggestedClipsRequest())


@router.post("/{video_id}/cleanup", response_model=StorageUsageResponse)
def cleanup_video(video_id: str, body: CleanupArtifactsRequest) -> StorageUsageResponse:
    return cleanup_video_artifacts(video_id, body)


@router.get("/{video_id}/source")
def read_video_source(video_id: str) -> FileResponse:
    path = video_source_path(video_id)
    if not path.exists():
        raise NotFoundError("video source")
    return FileResponse(path, media_type="video/mp4", filename=f"{video_id}.mp4")


@router.get("/{video_id}/thumbnails/{filename}")
def read_video_thumbnail(video_id: str, filename: str) -> FileResponse:
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.lower().endswith(".jpg"):
        raise NotFoundError("thumbnail")
    path = video_thumbs_dir(video_id) / safe_name
    if not path.exists():
        raise NotFoundError("thumbnail")
    return FileResponse(path, media_type="image/jpeg", filename=safe_name)
