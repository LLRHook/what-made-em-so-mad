from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.paths import video_diagnostics_dir
from app.domain.models import (
    ActivityEventResponse,
    DiagnosticsExportResponse,
    DiagnosticsResponse,
    ExpressionFeatureResponse,
    RoiCandidateResponse,
    TimelineResponse,
    VadSegmentResponse,
)
from app.services.analysis import (
    latest_timeline_for_video,
    list_expression_features,
    list_roi_candidates,
    list_transcript_segments,
    list_vad_segments,
)
from app.services.capabilities import probe_capabilities
from app.services.storage import storage_usage
from app.services.videos import get_video


def build_diagnostics_response(video_id: str) -> DiagnosticsResponse:
    usage = storage_usage()
    timeline = latest_timeline_for_video(video_id)
    roi_candidates = list_roi_candidates(video_id)
    vad_segments = list_vad_segments(timeline.job.id)
    expression_features = list_expression_features(timeline.job.id)
    return DiagnosticsResponse(
        video=get_video(video_id),
        timeline=timeline,
        roi_candidates=roi_candidates,
        transcripts=list_transcript_segments(video_id),
        vad_segments=vad_segments,
        expression_features=expression_features,
        capabilities=probe_capabilities(),
        storage={
            "data_dir": usage.data_dir,
            "total_bytes": usage.total_bytes,
            "videos_bytes": usage.videos_bytes,
            "clips_bytes": usage.clips_bytes,
            "database_bytes": usage.database_bytes,
        },
        artifacts=_artifact_index(
            timeline=timeline,
            roi_candidates=roi_candidates,
            vad_segments=vad_segments,
            expression_features=expression_features,
        ),
    )


def export_diagnostics_bundle(video_id: str) -> DiagnosticsExportResponse:
    diagnostics = build_diagnostics_response(video_id)
    created_at = _now_iso()
    safe_timestamp = created_at.replace(":", "").replace("-", "")
    out_dir = video_diagnostics_dir(video_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{diagnostics.timeline.job.id}-{safe_timestamp}.diagnostics.json"
    bundle = diagnostics.model_dump(mode="json")
    bundle["export"] = {
        "created_at": created_at,
        "format": "wmesm_diagnostics_bundle@1",
    }
    out_path.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
    return DiagnosticsExportResponse(
        video_id=video_id,
        job_id=diagnostics.timeline.job.id,
        artifact_path=str(out_path),
        created_at=created_at,
        summary={
            "lifecycle_segments": len(diagnostics.timeline.lifecycle_segments),
            "activity_events": len(diagnostics.timeline.activity_events),
            "roi_candidates": len(diagnostics.roi_candidates),
            "vad_segments": len(diagnostics.vad_segments),
            "expression_features": len(diagnostics.expression_features),
            "artifact_format": "wmesm_diagnostics_bundle@1",
        },
    )


def _artifact_index(
    *,
    timeline: TimelineResponse,
    roi_candidates: list[RoiCandidateResponse],
    vad_segments: list[VadSegmentResponse],
    expression_features: list[ExpressionFeatureResponse],
) -> dict[str, Any]:
    return {
        "identity": _identity_artifacts(timeline),
        "roi_candidate_overlays": _roi_candidate_overlays(timeline.job.video_id, roi_candidates),
        "track_mask_artifacts": [
            {
                "segment_id": segment.id,
                "state": segment.state,
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
                "bbox": segment.bbox,
                "artifact_ref": segment.artifact_ref,
            }
            for segment in timeline.lifecycle_segments
            if segment.artifact_ref
        ],
        "loudness_graph": _loudness_graph(timeline.activity_events),
        "vad_summary": _vad_summary(vad_segments),
        "expression_summary": _expression_summary(expression_features),
        "fusion_contributions": [_fusion_contribution(event) for event in timeline.activity_events],
    }


def _identity_artifacts(timeline: TimelineResponse) -> dict[str, Any]:
    if timeline.streamer_profile is None:
        return {}
    return {
        "streamer_profile_id": timeline.streamer_profile.id,
        "confirmed_by": timeline.streamer_profile.confirmed_by,
        "roi_bbox": timeline.streamer_profile.roi_bbox,
        "face_crop_path": timeline.streamer_profile.face_crop_path,
        "embedding_path": timeline.streamer_profile.embedding_path,
    }


def _roi_candidate_overlays(video_id: str, candidates: list[RoiCandidateResponse]) -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": candidate.id,
            "bbox": candidate.bbox,
            "score": candidate.score,
            "preview_path": candidate.preview_path,
            "preview_url": f"/api/videos/{video_id}/roi-candidates/{candidate.id}/preview"
            if candidate.preview_path
            else None,
            "evidence": candidate.evidence,
        }
        for candidate in candidates
    ]


def _loudness_graph(events: list[ActivityEventResponse]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for event in events:
        signals = _event_signals(event)
        if "window_rms" not in signals:
            continue
        samples.append(
            {
                "start_seconds": event.start_seconds,
                "end_seconds": event.end_seconds,
                "label": event.label,
                "confidence": event.confidence,
                "window_rms": signals.get("window_rms"),
                "baseline_median_rms": signals.get("baseline_median_rms"),
                "baseline_p75_rms": signals.get("baseline_p75_rms"),
                "baseline_p90_rms": signals.get("baseline_p90_rms"),
            }
        )
    return samples


def _vad_summary(vad_segments: list[VadSegmentResponse]) -> dict[str, Any]:
    if not vad_segments:
        return {"count": 0, "avg_speech_probability": None, "sources": []}
    probabilities = [float(segment.speech_probability) for segment in vad_segments]
    return {
        "count": len(vad_segments),
        "avg_speech_probability": round(sum(probabilities) / len(probabilities), 3),
        "sources": sorted({segment.source for segment in vad_segments}),
    }


def _expression_summary(expression_features: list[ExpressionFeatureResponse]) -> dict[str, Any]:
    if not expression_features:
        return {"count": 0, "face_visible_count": 0}
    visible = [sample for sample in expression_features if sample.face_visible]
    smiles = [float(sample.smile) for sample in expression_features if sample.smile is not None]
    mouth_open = [float(sample.mouth_open) for sample in expression_features if sample.mouth_open is not None]
    return {
        "count": len(expression_features),
        "face_visible_count": len(visible),
        "avg_smile": round(sum(smiles) / len(smiles), 3) if smiles else None,
        "avg_mouth_open": round(sum(mouth_open) / len(mouth_open), 3) if mouth_open else None,
    }


def _fusion_contribution(event: ActivityEventResponse) -> dict[str, Any]:
    context = event.evidence.get("context", {})
    return {
        "event_id": event.id,
        "label": event.label,
        "confidence": event.confidence,
        "summary": context.get("summary"),
        "signals": _event_signals(event),
    }


def _event_signals(event: ActivityEventResponse) -> dict[str, Any]:
    context = event.evidence.get("context", {})
    signals = context.get("signals")
    return signals if isinstance(signals, dict) else {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
