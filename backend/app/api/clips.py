from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Response, status
from fastapi.responses import FileResponse

from app.core.errors import FFmpegMissingError, NotFoundError, ValidationFailedError
from app.domain.models import BatchExportRequest, BatchExportResponse, ClipCreateRequest, ClipExportRequest, ClipResponse, ClipUpdateRequest
from app.services.capabilities import has_ffmpeg
from app.services.clips import batch_export_clips, create_clip_draft, delete_clip, export_clip, get_clip, update_clip

router = APIRouter(prefix="/api/clips", tags=["clips"])


@router.post("", response_model=ClipResponse, status_code=201)
def create_draft(body: ClipCreateRequest) -> ClipResponse:
    return create_clip_draft(
        video_id=body.video_id,
        start_seconds=body.start_seconds,
        end_seconds=body.end_seconds,
        labels=body.labels,
        title=body.title,
    )


@router.get("/{clip_id}", response_model=ClipResponse)
def read_clip(clip_id: str) -> ClipResponse:
    return get_clip(clip_id)


@router.patch("/{clip_id}", response_model=ClipResponse)
def patch_clip(clip_id: str, body: ClipUpdateRequest) -> ClipResponse:
    return update_clip(clip_id, title=body.title, accepted=body.accepted)


@router.delete("/{clip_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def reject_clip(clip_id: str) -> Response:
    delete_clip(clip_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/batch-export", response_model=BatchExportResponse)
def trigger_batch_export(body: BatchExportRequest) -> BatchExportResponse:
    if not has_ffmpeg():
        raise FFmpegMissingError()
    clips, manifest_path = batch_export_clips(
        body.clip_ids,
        pre_roll_seconds=body.pre_roll_seconds,
        post_roll_seconds=body.post_roll_seconds,
        mode=body.mode,
    )
    return BatchExportResponse(
        clips=clips,
        manifest_path=manifest_path,
    )


@router.post("/{clip_id}/export", response_model=ClipResponse)
def trigger_export(clip_id: str, body: ClipExportRequest | None = None) -> ClipResponse:
    if not has_ffmpeg():
        raise FFmpegMissingError()
    payload = body or ClipExportRequest()
    return export_clip(
        clip_id,
        pre_roll_seconds=payload.pre_roll_seconds,
        post_roll_seconds=payload.post_roll_seconds,
        mode=payload.mode,
    )


@router.get("/{clip_id}/download")
def download_clip(clip_id: str) -> FileResponse:
    clip = get_clip(clip_id)
    if clip.status != "exported" or clip.output_path is None:
        raise ValidationFailedError("clip has not been exported")
    path = Path(clip.output_path)
    if not path.exists():
        raise NotFoundError("clip file")
    return FileResponse(path, media_type="video/mp4", filename=f"{clip_id}.mp4")
