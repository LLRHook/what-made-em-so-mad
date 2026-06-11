from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Query, Response, status
from fastapi.responses import FileResponse

from app.domain.models import (
    AnalysisJobResponse,
    AnalysisProfile,
    AnalyzeVideoRequest,
    DiagnosticsExportResponse,
    DiagnosticsResponse,
    ExpressionFeatureResponse,
    FeedbackExportResponse,
    RoiCandidateResponse,
    SegmentMergeRequest,
    SegmentSplitRequest,
    SegmentUpdateRequest,
    StreamerConfirmRequest,
    StreamerProfileResponse,
    TimelineResponse,
    TranscriptionGenerateRequest,
    TranscriptImportRequest,
    TranscriptSegmentResponse,
    ActivityEventResponse,
    VadSegmentResponse,
)
from app.services.analysis import (
    cancel_analysis_job,
    confirm_streamer_profile,
    delete_activity_event,
    ensure_auto_analysis,
    export_feedback,
    get_analysis_job,
    get_timeline,
    get_roi_candidate_preview_path,
    generate_transcript,
    import_transcript_segments,
    latest_timeline_for_video,
    list_expression_features,
    list_roi_candidates,
    list_transcript_segments,
    list_vad_segments,
    merge_activity_events,
    queue_auto_analysis,
    run_analysis_job,
    split_activity_event,
    update_activity_event,
)
from app.services.diagnostics import build_diagnostics_response, export_diagnostics_bundle

router = APIRouter(prefix="/api", tags=["analysis"])


@router.post("/videos/{video_id}/analyze", response_model=TimelineResponse)
def analyze_video(
    video_id: str,
    body: AnalyzeVideoRequest | None = None,
    force: bool = Query(default=False),
    profile: AnalysisProfile | None = Query(default=None),
    transcription_enabled: bool | None = Query(default=None),
) -> TimelineResponse:
    payload = body or AnalyzeVideoRequest()
    return ensure_auto_analysis(
        video_id,
        force=force or payload.force,
        profile=profile or payload.profile,
        transcription_enabled=(
            payload.transcription_enabled if transcription_enabled is None else transcription_enabled
        ),
    )


@router.post("/videos/{video_id}/analysis-jobs", response_model=AnalysisJobResponse, status_code=status.HTTP_202_ACCEPTED)
def queue_video_analysis(
    video_id: str,
    background_tasks: BackgroundTasks,
    body: AnalyzeVideoRequest | None = None,
    force: bool = Query(default=False),
    profile: AnalysisProfile | None = Query(default=None),
    transcription_enabled: bool | None = Query(default=None),
    autostart: bool = Query(default=True),
) -> AnalysisJobResponse:
    payload = body or AnalyzeVideoRequest()
    job = queue_auto_analysis(
        video_id,
        force=force or payload.force,
        profile=profile or payload.profile,
        transcription_enabled=(
            payload.transcription_enabled if transcription_enabled is None else transcription_enabled
        ),
    )
    if autostart and job.status == "queued":
        background_tasks.add_task(run_analysis_job, job.id)
    return job


@router.get("/videos/{video_id}/timeline", response_model=TimelineResponse)
def read_video_timeline(video_id: str) -> TimelineResponse:
    return latest_timeline_for_video(video_id)


@router.get("/analysis-jobs/{job_id}", response_model=AnalysisJobResponse)
def read_analysis_job(job_id: str) -> AnalysisJobResponse:
    return get_analysis_job(job_id)


@router.post("/analysis-jobs/{job_id}/run", response_model=AnalysisJobResponse)
def run_queued_analysis_job(job_id: str) -> AnalysisJobResponse:
    return run_analysis_job(job_id)


@router.get("/analysis-jobs/{job_id}/timeline", response_model=TimelineResponse)
def read_analysis_timeline(job_id: str) -> TimelineResponse:
    return get_timeline(job_id)


@router.get("/analysis-jobs/{job_id}/vad", response_model=list[VadSegmentResponse])
def read_job_vad(job_id: str) -> list[VadSegmentResponse]:
    return list_vad_segments(job_id)


@router.get("/analysis-jobs/{job_id}/expression-features", response_model=list[ExpressionFeatureResponse])
def read_job_expression_features(job_id: str) -> list[ExpressionFeatureResponse]:
    return list_expression_features(job_id)


@router.post("/analysis-jobs/{job_id}/cancel", response_model=AnalysisJobResponse)
def cancel_job(job_id: str) -> AnalysisJobResponse:
    return cancel_analysis_job(job_id)


@router.get("/videos/{video_id}/roi-candidates", response_model=list[RoiCandidateResponse])
def read_roi_candidates(video_id: str) -> list[RoiCandidateResponse]:
    return list_roi_candidates(video_id)


@router.get("/videos/{video_id}/roi-candidates/{candidate_id}/preview")
def read_roi_candidate_preview(video_id: str, candidate_id: str) -> FileResponse:
    path = get_roi_candidate_preview_path(video_id, candidate_id)
    return FileResponse(path, media_type="image/jpeg", filename=path.name)


@router.post("/videos/{video_id}/streamer-profile", response_model=StreamerProfileResponse, status_code=201)
def confirm_streamer(video_id: str, body: StreamerConfirmRequest) -> StreamerProfileResponse:
    return confirm_streamer_profile(
        video_id,
        bbox=body.bbox,
        confirmed_time_seconds=body.confirmed_time_seconds,
        label=body.label,
    )


@router.get("/videos/{video_id}/transcript", response_model=list[TranscriptSegmentResponse])
def read_transcript(video_id: str) -> list[TranscriptSegmentResponse]:
    return list_transcript_segments(video_id)


@router.post("/videos/{video_id}/transcript", response_model=list[TranscriptSegmentResponse], status_code=201)
def import_transcript(video_id: str, body: TranscriptImportRequest) -> list[TranscriptSegmentResponse]:
    return import_transcript_segments(video_id, body.segments, replace_existing=body.replace_existing)


@router.post("/videos/{video_id}/transcribe", response_model=list[TranscriptSegmentResponse], status_code=201)
def transcribe_video(video_id: str, body: TranscriptionGenerateRequest | None = None) -> list[TranscriptSegmentResponse]:
    return generate_transcript(video_id, body or TranscriptionGenerateRequest())


@router.patch("/activity-events/{event_id}", response_model=ActivityEventResponse)
def update_event(event_id: str, body: SegmentUpdateRequest) -> ActivityEventResponse:
    return update_activity_event(event_id, body)


@router.delete("/activity-events/{event_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_event(event_id: str) -> Response:
    delete_activity_event(event_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/activity-events/{event_id}/split", response_model=list[ActivityEventResponse])
def split_event(event_id: str, body: SegmentSplitRequest) -> list[ActivityEventResponse]:
    return split_activity_event(event_id, body)


@router.post("/activity-events/merge", response_model=ActivityEventResponse)
def merge_events(body: SegmentMergeRequest) -> ActivityEventResponse:
    return merge_activity_events(body)


@router.get("/videos/{video_id}/feedback-export", response_model=FeedbackExportResponse)
def read_feedback_export(video_id: str) -> FeedbackExportResponse:
    return export_feedback(video_id)


@router.get("/videos/{video_id}/diagnostics", response_model=DiagnosticsResponse)
def read_diagnostics(video_id: str) -> DiagnosticsResponse:
    return build_diagnostics_response(video_id)


@router.post("/videos/{video_id}/diagnostics/export", response_model=DiagnosticsExportResponse, status_code=201)
def export_diagnostics(video_id: str) -> DiagnosticsExportResponse:
    return export_diagnostics_bundle(video_id)
