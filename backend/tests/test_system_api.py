from __future__ import annotations


def test_capabilities_endpoint_shape(client) -> None:
    res = client.get("/api/system/capabilities")
    assert res.status_code == 200
    body = res.json()
    for key in ("ffmpeg", "ffprobe", "yt_dlp", "sam3", "cuda", "mediapipe", "opencv_models"):
        assert key in body
    assert "available" in body["ffmpeg"]
    assert "yunet" in body["opencv_models"]
    assert "sface" in body["opencv_models"]


def test_setup_endpoint_shape(client) -> None:
    res = client.get("/api/system/setup")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["items"], list)
    assert isinstance(body["all_required_present"], bool)
    names = {item["name"] for item in body["items"]}
    assert {"ffmpeg", "ffprobe", "yt_dlp"}.issubset(names)


def test_health_endpoint(client) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
