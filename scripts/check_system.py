"""Standalone system capability check.

Prints a table of detected and missing tools/models. Intentionally has zero
runtime dependencies beyond the standard library so it works before a venv
is set up.

Mirrors the shape of ``GET /api/system/capabilities`` so the CLI output and
the HTTP response stay aligned. The actual probing logic lives in
``backend/app/services/capabilities.py``; this script re-implements it
narrowly to avoid importing the backend package.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]


def _binary(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    if not path:
        return {"available": False, "reason": "binary_not_on_path"}
    try:
        proc = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        version = (proc.stdout or proc.stderr).splitlines()[0] if (proc.stdout or proc.stderr) else "unknown"
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "reason": f"probe_failed: {exc.__class__.__name__}"}
    return {"available": True, "version": version, "path": path}


def _package(name: str) -> dict[str, Any]:
    spec = importlib.util.find_spec(name)
    if spec is None:
        return {"available": False, "reason": "package_not_installed"}
    return {"available": True}


def _cuda() -> dict[str, Any]:
    torch_spec = importlib.util.find_spec("torch")
    if torch_spec is None:
        return {"available": False, "reason": "torch_not_installed"}
    try:
        import torch  # type: ignore  # noqa: PLC0415

        if not torch.cuda.is_available():
            return {"available": False, "reason": "cuda_not_available"}
        return {"available": True, "device": torch.cuda.get_device_name(0)}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"torch_error: {exc.__class__.__name__}"}


def _opencv_has_api(name: str) -> bool:
    if importlib.util.find_spec("cv2") is None:
        return False
    try:
        import cv2  # type: ignore  # noqa: PLC0415
    except Exception:
        return False
    return hasattr(cv2, name)


def _opencv_models() -> dict[str, bool]:
    model_dir = REPO_ROOT / "models" / "opencv"
    return {
        "yunet": _opencv_has_api("FaceDetectorYN_create") and any(model_dir.glob("face_detection_yunet*.onnx")),
        "sface": _opencv_has_api("FaceRecognizerSF_create") and any(model_dir.glob("face_recognition_sface*.onnx")),
    }


def probe() -> dict[str, Any]:
    return {
        "ffmpeg":   _binary("ffmpeg"),
        "ffprobe":  _binary("ffprobe"),
        "yt_dlp":   _binary("yt-dlp"),
        "mediapipe": _package("mediapipe"),
        "cuda":     _cuda(),
        "sam3":     _package("sam3"),
        "opencv_models": _opencv_models(),
    }


def _row(label: str, status: str, detail: str) -> str:
    return f"  {label:<14}  {status:<10}  {detail}"


def main() -> int:
    caps = probe()
    if "--json" in sys.argv:
        print(json.dumps(caps, indent=2))
        return 0

    print("What Made 'Em So Mad - system capability check\n")
    print(_row("component", "status", "detail"))
    print(_row("-" * 14, "-" * 10, "-" * 40))

    for name in ("ffmpeg", "ffprobe", "yt_dlp", "mediapipe", "cuda", "sam3"):
        entry = caps[name]
        if entry.get("available"):
            detail = entry.get("version") or entry.get("device") or "ok"
        else:
            detail = entry.get("reason", "unknown")
        print(_row(name, "OK" if entry.get("available") else "MISSING", detail))

    yunet = caps["opencv_models"]["yunet"]
    sface = caps["opencv_models"]["sface"]
    print(_row("yunet model", "OK" if yunet else "MISSING", "models/opencv/face_detection_yunet_*.onnx"))
    print(_row("sface model", "OK" if sface else "MISSING", "models/opencv/face_recognition_sface_*.onnx"))

    missing = [k for k, v in caps.items() if isinstance(v, dict) and not v.get("available", True)]
    if missing:
        print("\nMissing components are optional for app boot but required for the features listed in docs/setup.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
