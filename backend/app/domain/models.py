from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ---------- system ----------

class CapabilityEntry(BaseModel):
    available: bool
    version: str | None = None
    reason: str | None = None
    path: str | None = None
    device: str | None = None


class OpenCVModelsStatus(BaseModel):
    yunet: bool
    sface: bool


class CapabilitiesResponse(BaseModel):
    ffmpeg: CapabilityEntry
    ffprobe: CapabilityEntry
    yt_dlp: CapabilityEntry
    sam3: CapabilityEntry
    cuda: CapabilityEntry
    mediapipe: CapabilityEntry
    opencv_models: OpenCVModelsStatus


class SetupItem(BaseModel):
    name: str
    available: bool
    required: bool
    hint: str | None = None


class SetupResponse(BaseModel):
    items: list[SetupItem]
    all_required_present: bool


# ---------- videos ----------

class VideoMetadata(BaseModel):
    duration_seconds: float | None = None
    fps: float | None = None
    width: int | None = None
    height: int | None = None


class VideoSummary(BaseModel):
    id: str
    source_type: Literal["local", "url"]
    source_uri: str
    title: str | None = None
    sha256: str
    local_path: str
    source_url: str
    duration_seconds: float | None = None
    fps: float | None = None
    width: int | None = None
    height: int | None = None
    created_at: str
    thumbnails: list[str] = Field(default_factory=list)


class VideoLatestJob(BaseModel):
    id: str
    status: Literal["queued", "running", "blocked", "failed", "complete", "cancelled"]
    completed_at: str | None = None
    error_message: str | None = None


class VideoListEntry(BaseModel):
    id: str
    source_type: Literal["local", "url"]
    source_uri: str
    title: str | None = None
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    created_at: str
    source_url: str
    thumbnails: list[str] = Field(default_factory=list)
    latest_job: VideoLatestJob | None = None
    clip_count: int = 0


class VideoUpdateRequest(BaseModel):
    """PATCH /api/videos/{id} body. `title=None` clears the override; an empty/whitespace title is rejected."""

    title: str | None = None

    @field_validator("title")
    @classmethod
    def _normalize_title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("title must not be empty or whitespace-only")
        if len(trimmed) > 120:
            raise ValueError("title must be 120 characters or fewer")
        return trimmed


class VideoImportResponse(BaseModel):
    video_id: str
    status: Literal["imported"] = "imported"
    duplicate_of: str | None = None


class FromUrlRequest(BaseModel):
    url: str
    cookies_path: str | None = None


# ---------- analysis ----------

AnalysisProfile = Literal["fast", "balanced", "best"]


class BBoxRequest(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    coordinate_space: str = "source_pixels"


class AnalyzeVideoRequest(BaseModel):
    profile: AnalysisProfile = "balanced"
    force: bool = False
    transcription_enabled: bool = False


class StreamerConfirmRequest(BaseModel):
    bbox: BBoxRequest
    confirmed_time_seconds: float = Field(default=0.0, ge=0)
    label: str = Field(default="primary_streamer", min_length=1, max_length=80)


class RoiCandidateResponse(BaseModel):
    id: str
    video_id: str
    sample_start_seconds: float
    sample_end_seconds: float
    bbox: dict[str, Any]
    score: float
    evidence: dict[str, Any] = Field(default_factory=dict)
    preview_path: str | None = None


class TranscriptSegmentCreate(BaseModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    text: str = Field(min_length=1)
    source: Literal["manual", "whisper", "imported"] = "manual"
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("end_seconds")
    @classmethod
    def end_after_start(cls, v: float, info) -> float:  # type: ignore[no-untyped-def]
        start = info.data.get("start_seconds")
        if start is not None and v <= start:
            raise ValueError("end_seconds must be greater than start_seconds")
        return v


class TranscriptImportRequest(BaseModel):
    segments: list[TranscriptSegmentCreate]
    replace_existing: bool = True


class TranscriptionGenerateRequest(BaseModel):
    model_size: str = Field(default="small", min_length=1, max_length=80)
    device: Literal["cpu", "cuda", "auto"] = "cpu"
    compute_type: str = Field(default="int8", min_length=1, max_length=40)
    replace_existing: bool = True


class TranscriptSegmentResponse(BaseModel):
    id: str
    video_id: str
    start_seconds: float
    end_seconds: float
    text: str
    source: Literal["manual", "whisper", "imported"]
    confidence: float | None = None
    created_at: str


class SegmentUpdateRequest(BaseModel):
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, gt=0)
    label: str | None = Field(default=None, min_length=1, max_length=80)
    state: str | None = Field(default=None, min_length=1, max_length=80)
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence_note: str | None = Field(default=None, max_length=500)


class SegmentSplitRequest(BaseModel):
    split_seconds: float = Field(gt=0)


class SegmentMergeRequest(BaseModel):
    segment_ids: list[str] = Field(min_length=2)
    label: str | None = Field(default=None, min_length=1, max_length=80)


class FeedbackExportResponse(BaseModel):
    video_id: str
    annotations: list[dict[str, Any]]


class VadSegmentResponse(BaseModel):
    id: str
    job_id: str
    start_seconds: float
    end_seconds: float
    speech_probability: float
    source: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class ExpressionFeatureResponse(BaseModel):
    id: str
    job_id: str
    time_seconds: float
    bbox: dict[str, Any] | None = None
    face_visible: bool
    mouth_open: float | None = None
    smile: float | None = None
    brow_raise: float | None = None
    squint: float | None = None
    head_pose: dict[str, Any] | None = None
    confidence: float
    evidence: dict[str, Any] = Field(default_factory=dict)


class EvaluationRunRequest(BaseModel):
    fixture_set_path: str | None = None


class EvaluationRunResponse(BaseModel):
    id: str
    fixture_set_path: str
    metrics: dict[str, Any]
    created_at: str


class AnalysisJobResponse(BaseModel):
    id: str
    video_id: str
    status: Literal["queued", "running", "blocked", "failed", "complete", "cancelled"]
    params: dict[str, Any]
    versions: dict[str, Any]
    progress: dict[str, Any] | None = None
    error_message: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None


class StreamerProfileResponse(BaseModel):
    id: str
    video_id: str
    confirmed_by: Literal["user", "auto", "user_reacquire"]
    confirmed_time_seconds: float
    prompt: dict[str, Any]
    roi_bbox: dict[str, Any] | None = None
    face_crop_path: str | None = None
    embedding_path: str | None = None
    created_at: str


class TrackSegmentResponse(BaseModel):
    id: str
    job_id: str
    start_seconds: float
    end_seconds: float
    state: str
    confidence: float
    bbox: dict[str, Any] | None = None
    artifact_ref: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class ActivityEventResponse(BaseModel):
    id: str
    job_id: str
    start_seconds: float
    end_seconds: float
    label: str
    confidence: float
    source: Literal["fusion", "user", "imported"]
    evidence: dict[str, Any] = Field(default_factory=dict)


class TimelineResponse(BaseModel):
    job: AnalysisJobResponse
    streamer_profile: StreamerProfileResponse | None
    lifecycle_segments: list[TrackSegmentResponse]
    activity_events: list[ActivityEventResponse]


# ---------- clips ----------

class ClipCreateRequest(BaseModel):
    video_id: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    labels: list[str] = Field(default_factory=list)
    title: str | None = None

    @field_validator("end_seconds")
    @classmethod
    def end_after_start(cls, v: float, info) -> float:  # type: ignore[no-untyped-def]
        start = info.data.get("start_seconds")
        if start is not None and v <= start:
            raise ValueError("end_seconds must be greater than start_seconds")
        return v


class ClipExportRequest(BaseModel):
    format: Literal["mp4"] = "mp4"
    pre_roll_seconds: float = Field(default=2.0, ge=0)
    post_roll_seconds: float = Field(default=3.0, ge=0)
    mode: Literal["stream_copy_when_possible", "re_encode"] = "stream_copy_when_possible"


class ClipUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    accepted: bool | None = None

    @field_validator("title")
    @classmethod
    def _normalize_title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("title must not be empty or whitespace-only")
        return trimmed


class ClipResponse(BaseModel):
    id: str
    video_id: str
    start_seconds: float
    end_seconds: float
    labels: list[str]
    title: str | None
    suggestion_score: float | None = None
    suggestion_reason: str | None = None
    accepted: bool = False
    status: Literal["draft", "exporting", "exported", "failed"]
    output_path: str | None
    output_url: str | None
    error_message: str | None
    created_at: str


class SuggestedClipsRequest(BaseModel):
    labels: list[str] = Field(default_factory=lambda: ["laughing", "yelling/frustrated", "returned"])
    min_confidence: float = Field(default=0.45, ge=0, le=1)
    pre_roll_seconds: float = Field(default=2.0, ge=0)
    post_roll_seconds: float = Field(default=3.0, ge=0)
    merge_gap_seconds: float = Field(default=8.0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class BatchExportRequest(BaseModel):
    clip_ids: list[str] = Field(min_length=1)
    pre_roll_seconds: float = Field(default=2.0, ge=0)
    post_roll_seconds: float = Field(default=3.0, ge=0)
    mode: Literal["stream_copy_when_possible", "re_encode"] = "stream_copy_when_possible"


class BatchExportResponse(BaseModel):
    clips: list[ClipResponse]
    manifest_path: str | None = None


# ---------- storage ----------

class StorageUsageResponse(BaseModel):
    data_dir: str
    total_bytes: int
    videos_bytes: int
    clips_bytes: int
    database_bytes: int
    videos: list[dict[str, Any]] = Field(default_factory=list)


class CleanupArtifactsRequest(BaseModel):
    thumbnails: bool = False
    clips: bool = False
    diagnostics: bool = False
    failed_clips_only: bool = False


class DiagnosticsResponse(BaseModel):
    video: VideoSummary
    timeline: TimelineResponse
    roi_candidates: list[RoiCandidateResponse]
    transcripts: list[TranscriptSegmentResponse]
    vad_segments: list[VadSegmentResponse] = Field(default_factory=list)
    expression_features: list[ExpressionFeatureResponse] = Field(default_factory=list)
    capabilities: CapabilitiesResponse
    storage: dict[str, Any]
    artifacts: dict[str, Any] = Field(default_factory=dict)


class DiagnosticsExportResponse(BaseModel):
    video_id: str
    job_id: str
    artifact_path: str
    created_at: str
    summary: dict[str, Any] = Field(default_factory=dict)
