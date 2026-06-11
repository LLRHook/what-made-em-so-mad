from __future__ import annotations

import json
import uuid
from importlib import resources
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationFailedError
from app.db.database import connection
from app.domain.models import EvaluationRunResponse
from app.services.analysis import get_timeline, latest_timeline_for_video


def run_evaluation(fixture_set_path: str | None = None) -> EvaluationRunResponse:
    fixture_set_label, raw_json = _load_fixture_set(fixture_set_path)
    try:
        fixture_set = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValidationFailedError(f"invalid evaluation fixture JSON: {exc}") from exc

    fixtures = fixture_set.get("fixtures")
    if not isinstance(fixtures, list):
        raise ValidationFailedError("evaluation fixture set must contain a fixtures list")

    results = [_score_fixture(fixture) for fixture in fixtures]
    metrics = _aggregate_results(results)
    run_id = uuid.uuid4().hex
    created_at = _now_iso()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO evaluation_runs (id, fixture_set_path, metrics_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, fixture_set_label, json.dumps(metrics), created_at),
        )
    return EvaluationRunResponse(id=run_id, fixture_set_path=fixture_set_label, metrics=metrics, created_at=created_at)


def _load_fixture_set(fixture_set_path: str | None) -> tuple[str, str]:
    if fixture_set_path:
        path = Path(fixture_set_path)
        if not path.exists():
            raise NotFoundError("evaluation fixture set")
        return str(path), path.read_text(encoding="utf-8")

    data_path = get_settings().data_dir / "evaluation" / "fixtures.json"
    if data_path.exists():
        return str(data_path), data_path.read_text(encoding="utf-8")

    resource = resources.files("app.evaluation").joinpath("fixtures.json")
    return "packaged:app/evaluation/fixtures.json", resource.read_text(encoding="utf-8")


def get_evaluation_run(run_id: str) -> EvaluationRunResponse:
    with connection() as conn:
        row = conn.execute("SELECT * FROM evaluation_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise NotFoundError("evaluation run")
    return EvaluationRunResponse(
        id=row["id"],
        fixture_set_path=row["fixture_set_path"],
        metrics=json.loads(row["metrics_json"]),
        created_at=row["created_at"],
    )


def _score_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    fixture_id = str(fixture.get("id", "fixture"))
    duration = float(fixture.get("duration_seconds") or 0.0)
    ground_truth = fixture.get("ground_truth") or {}
    predictions = fixture.get("predictions")
    if predictions is None:
        predictions = _predictions_from_reference(fixture)

    activity_truth = _normalise_segments(ground_truth.get("activity_events", []))
    activity_pred = _normalise_segments((predictions or {}).get("activity_events", []))
    lifecycle_truth = _normalise_segments(ground_truth.get("lifecycle_segments", []), label_key="state")
    lifecycle_pred = _normalise_segments((predictions or {}).get("lifecycle_segments", []), label_key="state")
    return {
        "id": fixture_id,
        "duration_seconds": duration,
        "activity": _score_segment_set(activity_truth, activity_pred),
        "lifecycle": _score_segment_set(lifecycle_truth, lifecycle_pred),
    }


def _predictions_from_reference(fixture: dict[str, Any]) -> dict[str, Any]:
    if fixture.get("job_id"):
        timeline = get_timeline(str(fixture["job_id"]))
    elif fixture.get("video_id"):
        timeline = latest_timeline_for_video(str(fixture["video_id"]))
    else:
        return {"activity_events": [], "lifecycle_segments": []}
    return {
        "activity_events": [event.model_dump() for event in timeline.activity_events],
        "lifecycle_segments": [segment.model_dump() for segment in timeline.lifecycle_segments],
    }


def _normalise_segments(raw: Any, *, label_key: str = "label") -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = item.get(label_key) or item.get("label") or item.get("state")
        start = item.get("start_seconds")
        end = item.get("end_seconds")
        if label is None or start is None or end is None:
            continue
        if float(end) <= float(start):
            continue
        out.append(
            {
                "label": str(label),
                "start_seconds": float(start),
                "end_seconds": float(end),
                "confidence": float(item.get("confidence", 1.0)),
            }
        )
    return out


def _score_segment_set(truth: list[dict[str, Any]], pred: list[dict[str, Any]]) -> dict[str, Any]:
    labels = sorted({item["label"] for item in truth} | {item["label"] for item in pred})
    by_label: dict[str, dict[str, Any]] = {}
    all_boundary_errors: list[float] = []
    total_fp = 0
    for label in labels:
        t = [item for item in truth if item["label"] == label]
        p = [item for item in pred if item["label"] == label]
        matches, boundary_errors = _match_segments(t, p)
        tp = len(matches)
        fp = max(0, len(p) - tp)
        fn = max(0, len(t) - tp)
        total_fp += fp
        all_boundary_errors.extend(boundary_errors)
        precision = tp / len(p) if p else (1.0 if not t else 0.0)
        recall = tp / len(t) if t else (1.0 if not p else 0.0)
        f1 = 0.0 if precision + recall == 0 else (2 * precision * recall) / (precision + recall)
        by_label[label] = {
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
        }
    return {
        "labels": by_label,
        "mean_absolute_boundary_error_seconds": (
            round(sum(all_boundary_errors) / len(all_boundary_errors), 3) if all_boundary_errors else None
        ),
        "false_positive_segments": total_fp,
        "precision_at_top_5": {
            "laughing": _precision_at_top_k(truth, pred, label="laughing", k=5),
            "yelling/frustrated": _precision_at_top_k(truth, pred, label="yelling/frustrated", k=5),
        },
        "returned_detection_latency_seconds": _mean_start_latency(truth, pred, label="returned"),
    }


def _match_segments(
    truth: list[dict[str, Any]],
    pred: list[dict[str, Any]],
    *,
    min_iou: float = 0.5,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[float]]:
    available = set(range(len(truth)))
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    errors: list[float] = []
    for prediction in sorted(pred, key=lambda item: item.get("confidence", 0.0), reverse=True):
        best_index = None
        best_iou = 0.0
        for index in available:
            score = _iou_seconds(truth[index], prediction)
            if score > best_iou:
                best_iou = score
                best_index = index
        if best_index is not None and best_iou >= min_iou:
            actual = truth[best_index]
            available.remove(best_index)
            matches.append((actual, prediction))
            errors.append(abs(actual["start_seconds"] - prediction["start_seconds"]))
            errors.append(abs(actual["end_seconds"] - prediction["end_seconds"]))
    return matches, errors


def _iou_seconds(a: dict[str, Any], b: dict[str, Any]) -> float:
    overlap = max(0.0, min(a["end_seconds"], b["end_seconds"]) - max(a["start_seconds"], b["start_seconds"]))
    union = max(a["end_seconds"], b["end_seconds"]) - min(a["start_seconds"], b["start_seconds"])
    if union <= 0:
        return 0.0
    return overlap / union


def _precision_at_top_k(truth: list[dict[str, Any]], pred: list[dict[str, Any]], *, label: str, k: int) -> float | None:
    truth_label = [item for item in truth if item["label"] == label]
    pred_label = sorted(
        [item for item in pred if item["label"] == label],
        key=lambda item: item.get("confidence", 0.0),
        reverse=True,
    )[:k]
    if not pred_label:
        return None
    matches, _errors = _match_segments(truth_label, pred_label)
    return round(len(matches) / len(pred_label), 3)


def _mean_start_latency(truth: list[dict[str, Any]], pred: list[dict[str, Any]], *, label: str) -> float | None:
    truth_label = [item for item in truth if item["label"] == label]
    pred_label = [item for item in pred if item["label"] == label]
    matches, _errors = _match_segments(truth_label, pred_label, min_iou=0.1)
    if not matches:
        return None
    return round(sum(prediction["start_seconds"] - actual["start_seconds"] for actual, prediction in matches) / len(matches), 3)


def _aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    total_duration = sum(float(result.get("duration_seconds") or 0.0) for result in results)
    activity = _aggregate_group([result["activity"] for result in results], total_duration_seconds=total_duration)
    lifecycle = _aggregate_group([result["lifecycle"] for result in results], total_duration_seconds=total_duration)
    return {
        "fixture_count": len(results),
        "total_duration_seconds": round(total_duration, 3),
        "activity": activity,
        "lifecycle": lifecycle,
        "analysis_speed_per_video_hour": None,
        "fixtures": results,
    }


def _aggregate_group(groups: list[dict[str, Any]], *, total_duration_seconds: float) -> dict[str, Any]:
    labels = sorted({label for group in groups for label in group["labels"]})
    label_metrics: dict[str, Any] = {}
    for label in labels:
        tp = sum(group["labels"].get(label, {}).get("true_positives", 0) for group in groups)
        fp = sum(group["labels"].get(label, {}).get("false_positives", 0) for group in groups)
        fn = sum(group["labels"].get(label, {}).get("false_negatives", 0) for group in groups)
        precision = tp / (tp + fp) if tp + fp else (1.0 if fn == 0 else 0.0)
        recall = tp / (tp + fn) if tp + fn else (1.0 if fp == 0 else 0.0)
        f1 = 0.0 if precision + recall == 0 else (2 * precision * recall) / (precision + recall)
        label_metrics[label] = {
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
        }
    boundary_errors = [
        group["mean_absolute_boundary_error_seconds"]
        for group in groups
        if group["mean_absolute_boundary_error_seconds"] is not None
    ]
    false_positives = sum(group["false_positive_segments"] for group in groups)
    hours = total_duration_seconds / 3600.0 if total_duration_seconds > 0 else 0.0
    return {
        "labels": label_metrics,
        "mean_absolute_boundary_error_seconds": (
            round(sum(boundary_errors) / len(boundary_errors), 3) if boundary_errors else None
        ),
        "false_positive_segments_per_hour": round(false_positives / hours, 3) if hours > 0 else None,
        "precision_at_top_5": _aggregate_top_k(groups),
    }


def _aggregate_top_k(groups: list[dict[str, Any]]) -> dict[str, float | None]:
    labels = ("laughing", "yelling/frustrated")
    out: dict[str, float | None] = {}
    for label in labels:
        values = [
            group["precision_at_top_5"][label]
            for group in groups
            if group["precision_at_top_5"][label] is not None
        ]
        out[label] = round(sum(values) / len(values), 3) if values else None
    return out


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
