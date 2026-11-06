from __future__ import annotations

import json
from importlib import resources

import pytest

from app.db.database import connection
from app.services import interval_matching as native
from tests.reference import evaluation_legacy as legacy
from tests.reference.synthetic_timeline import seed_timeline


def test_evaluation_run_scores_fixture_predictions(client, tmp_path) -> None:
    fixture_path = tmp_path / "fixtures.json"
    fixture_path.write_text(
        json.dumps(
            {
                "fixtures": [
                    {
                        "id": "basic",
                        "duration_seconds": 60.0,
                        "ground_truth": {
                            "activity_events": [
                                {"label": "talking", "start_seconds": 0.0, "end_seconds": 10.0},
                                {"label": "laughing", "start_seconds": 20.0, "end_seconds": 25.0},
                            ],
                            "lifecycle_segments": [
                                {"state": "present", "start_seconds": 0.0, "end_seconds": 60.0},
                            ],
                        },
                        "predictions": {
                            "activity_events": [
                                {"label": "talking", "start_seconds": 1.0, "end_seconds": 9.0, "confidence": 0.9},
                                {"label": "laughing", "start_seconds": 20.0, "end_seconds": 24.0, "confidence": 0.8},
                                {"label": "focused", "start_seconds": 30.0, "end_seconds": 35.0, "confidence": 0.4},
                            ],
                            "lifecycle_segments": [
                                {"state": "present", "start_seconds": 0.0, "end_seconds": 60.0, "confidence": 0.9},
                            ],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    res = client.post("/api/evaluation/run", json={"fixture_set_path": str(fixture_path)})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["fixture_set_path"] == str(fixture_path)
    assert body["metrics"]["fixture_count"] == 1
    assert body["metrics"]["activity"]["labels"]["talking"]["f1"] == 1.0
    assert body["metrics"]["activity"]["labels"]["focused"]["false_positives"] == 1
    assert body["metrics"]["lifecycle"]["labels"]["present"]["recall"] == 1.0

    got = client.get(f"/api/evaluation/runs/{body['id']}")
    assert got.status_code == 200, got.text
    assert got.json()["metrics"]["fixture_count"] == 1


def test_evaluation_run_uses_packaged_default_fixtures(client) -> None:
    res = client.post("/api/evaluation/run", json={})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["fixture_set_path"] == "packaged:app/evaluation/fixtures.json"
    assert body["metrics"]["fixture_count"] == 9
    assert body["metrics"]["activity"]["labels"]["laughing"]["precision"] == 1.0
    assert "returned" in body["metrics"]["lifecycle"]["labels"]


def test_evaluation_api_and_saved_metrics_match_frozen_oracle(client):
    fixtures = json.loads(resources.files("app.evaluation").joinpath("fixtures.json").read_text())["fixtures"]
    expected = legacy._aggregate_results([legacy._score_fixture(fixture) for fixture in fixtures])
    response = client.post("/api/evaluation/run", json={})
    assert response.status_code == 201, response.text
    body = response.json()
    assert json.dumps(body["metrics"], sort_keys=True) == json.dumps(expected, sort_keys=True)
    saved = client.get(f"/api/evaluation/runs/{body['id']}")
    assert saved.status_code == 200
    assert saved.json() == body


@pytest.mark.parametrize("reference", ["job_id", "video_id"])
def test_evaluation_reads_existing_timeline_metadata_through_native(client, tmp_path, reference):
    seed_timeline()
    truth = {
        "activity_events": [{"label": "laughing", "start_seconds": 0, "end_seconds": 10}],
        "lifecycle_segments": [{"state": "returned", "start_seconds": 4, "end_seconds": 14}],
    }
    fixture = {"id": "reference", "duration_seconds": 60, "ground_truth": truth,
               reference: "synthetic-job" if reference == "job_id" else "synthetic-video"}
    path = tmp_path / "reference.json"
    path.write_text(json.dumps({"fixtures": [fixture]}))
    expected = legacy._aggregate_results([legacy._score_fixture(fixture)])
    response = client.post("/api/evaluation/run", json={"fixture_set_path": str(path)})
    assert response.status_code == 201, response.text
    assert response.json()["metrics"] == expected
    assert expected["fixtures"][0]["lifecycle"]["returned_detection_latency_seconds"] == 1.0
    with connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM analysis_jobs").fetchone()[0] == 1
        assert conn.execute("SELECT status FROM analysis_jobs").fetchone()[0] == "complete"
        assert conn.execute("SELECT COUNT(*) FROM activity_events").fetchone()[0] == 1


def test_missing_native_does_not_prevent_boot_or_write_partial_evaluation(isolated_settings, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.services.capabilities import probe_capabilities

    native._load_library.cache_clear()
    monkeypatch.setattr(native, "_library_path", lambda: tmp_path / "missing-native.so")
    # Keep every optional capability absent; no real binary/model probes.
    monkeypatch.setattr("app.api.system.probe_capabilities", lambda: probe_capabilities(which=lambda _: None))
    try:
        with TestClient(create_app()) as client:
            assert client.get("/api/health").status_code == 200
            assert client.get("/api/system/capabilities").status_code == 200
            response = client.post("/api/evaluation/run", json={})
            assert response.status_code == 503
            assert response.json()["detail"]["error"] == "evaluation_native_unavailable"
            assert "setup_hint" in response.json()["detail"]
            assert str(tmp_path) not in response.text
            with connection() as conn:
                assert conn.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0] == 0
    finally:
        native._load_library.cache_clear()


def test_native_failure_is_typed_and_does_not_persist(client, monkeypatch):
    class FailedLibrary:
        def wmesm_match_intervals_v1(self, *args): return -2
    monkeypatch.setattr(native, "_load_library", lambda: FailedLibrary())
    response = client.post("/api/evaluation/run", json={})
    assert response.status_code == 500
    assert response.json()["detail"]["error"] == "evaluation_native_failed"
    with connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0] == 0


@pytest.mark.parametrize("contents,status", [("{", 400), ('{"no_fixtures": []}', 400), (None, 404)])
def test_fixture_load_errors_remain_typed_and_do_not_persist(client, tmp_path, contents, status):
    path = tmp_path / "invalid.json"
    if contents is not None:
        path.write_text(contents)
    response = client.post("/api/evaluation/run", json={"fixture_set_path": str(path)})
    assert response.status_code == status
    with connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0] == 0
