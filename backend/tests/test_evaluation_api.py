from __future__ import annotations

import json


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
