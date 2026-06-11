from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from app.services import capabilities as caps_mod


def _fake_which(found: set[str]):
    return lambda name: f"/usr/bin/{name}" if name in found else None


def test_probe_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = caps_mod._probe_binary("ffmpeg", which=_fake_which(set()))
    assert entry.available is False
    assert entry.reason == "binary_not_on_path"


def test_probe_binary_present(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_a, **_kw) -> SimpleNamespace:
        return SimpleNamespace(stdout="ffmpeg version 7.0.0\nmore\n", stderr="", returncode=0)

    monkeypatch.setattr(caps_mod.subprocess, "run", fake_run)
    entry = caps_mod._probe_binary("ffmpeg", which=_fake_which({"ffmpeg"}))
    assert entry.available is True
    assert entry.version is not None and entry.version.startswith("ffmpeg version")
    assert entry.path == "/usr/bin/ffmpeg"


def test_probe_binary_subprocess_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a, **_kw):
        raise OSError("nope")

    monkeypatch.setattr(caps_mod.subprocess, "run", boom)
    entry = caps_mod._probe_binary("ffmpeg", which=_fake_which({"ffmpeg"}))
    assert entry.available is False
    assert entry.reason and entry.reason.startswith("probe_failed:")


def test_probe_package_missing() -> None:
    entry = caps_mod._probe_package("definitely_not_a_real_package_xyzzy")
    assert entry.available is False
    assert entry.reason == "package_not_installed"


def test_probe_package_present() -> None:
    # `fastapi` is a hard dependency, so it must be importable here.
    entry = caps_mod._probe_package("fastapi")
    assert entry.available is True


def test_probe_capabilities_shape() -> None:
    caps = caps_mod.probe_capabilities(which=_fake_which(set()))
    payload = caps.model_dump()
    assert set(payload.keys()) == {"ffmpeg", "ffprobe", "yt_dlp", "sam3", "cuda", "mediapipe", "opencv_models"}
    assert payload["opencv_models"] == {"yunet": False, "sface": False}


def test_probe_opencv_models_detects_installed_assets(isolated_settings, monkeypatch: pytest.MonkeyPatch) -> None:
    model_dir = isolated_settings.models_dir / "opencv"
    model_dir.mkdir(parents=True)
    (model_dir / "face_detection_yunet_2023mar.onnx").write_bytes(b"model")
    (model_dir / "face_recognition_sface_2021dec.onnx").write_bytes(b"model")
    monkeypatch.setattr(caps_mod, "_opencv_has_api", lambda _name: True)
    status = caps_mod._probe_opencv_models()
    assert status.yunet is True
    assert status.sface is True


def test_build_setup_response_marks_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(caps_mod.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="x\n", stderr="", returncode=0))
    caps = caps_mod.probe_capabilities(which=_fake_which({"ffmpeg", "ffprobe"}))
    setup = caps_mod.build_setup_response(caps)
    by_name = {i.name: i for i in setup.items}
    assert by_name["ffmpeg"].required is True
    assert by_name["ffmpeg"].available is True
    assert by_name["yt_dlp"].required is False
    assert setup.all_required_present is True


def test_build_setup_response_blocks_when_required_missing() -> None:
    caps = caps_mod.probe_capabilities(which=_fake_which(set()))
    setup = caps_mod.build_setup_response(caps)
    assert setup.all_required_present is False
    ffmpeg = next(i for i in setup.items if i.name == "ffmpeg")
    assert ffmpeg.hint is not None and "ffmpeg" in ffmpeg.hint.lower()
