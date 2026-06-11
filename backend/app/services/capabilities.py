from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from app.core.config import get_settings
from app.domain.models import (
    CapabilitiesResponse,
    CapabilityEntry,
    OpenCVModelsStatus,
    SetupItem,
    SetupResponse,
)

# Type alias for which() so tests can pass a fake.
WhichFn = Callable[[str], str | None]


def _probe_binary(name: str, which: WhichFn = shutil.which) -> CapabilityEntry:
    path = which(name)
    if not path:
        return CapabilityEntry(available=False, reason="binary_not_on_path")
    try:
        proc = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        out = proc.stdout or proc.stderr
        version = out.splitlines()[0] if out else "unknown"
    except (OSError, subprocess.SubprocessError) as exc:
        return CapabilityEntry(available=False, reason=f"probe_failed:{exc.__class__.__name__}")
    return CapabilityEntry(available=True, version=version, path=path)


def _probe_package(name: str) -> CapabilityEntry:
    if importlib.util.find_spec(name) is None:
        return CapabilityEntry(available=False, reason="package_not_installed")
    return CapabilityEntry(available=True)


def _probe_cuda() -> CapabilityEntry:
    if importlib.util.find_spec("torch") is None:
        return CapabilityEntry(available=False, reason="torch_not_installed")
    try:
        import torch  # type: ignore  # noqa: PLC0415

        if not torch.cuda.is_available():
            return CapabilityEntry(available=False, reason="cuda_not_available")
        return CapabilityEntry(available=True, device=torch.cuda.get_device_name(0))
    except Exception as exc:  # noqa: BLE001
        return CapabilityEntry(available=False, reason=f"torch_error:{exc.__class__.__name__}")


def probe_capabilities(which: WhichFn = shutil.which) -> CapabilitiesResponse:
    return CapabilitiesResponse(
        ffmpeg=_probe_binary("ffmpeg", which=which),
        ffprobe=_probe_binary("ffprobe", which=which),
        yt_dlp=_probe_binary("yt-dlp", which=which),
        sam3=_probe_package("sam3"),
        cuda=_probe_cuda(),
        mediapipe=_probe_package("mediapipe"),
        opencv_models=_probe_opencv_models(),
    )


def _probe_opencv_models() -> OpenCVModelsStatus:
    if importlib.util.find_spec("cv2") is None:
        return OpenCVModelsStatus(yunet=False, sface=False)
    model_dir = get_settings().models_dir / "opencv"
    return OpenCVModelsStatus(
        yunet=_opencv_has_api("FaceDetectorYN_create") and _has_model_file(model_dir, "face_detection_yunet*.onnx"),
        sface=_opencv_has_api("FaceRecognizerSF_create") and _has_model_file(model_dir, "face_recognition_sface*.onnx"),
    )


def _has_model_file(model_dir: Path, pattern: str) -> bool:
    return model_dir.exists() and any(path.is_file() for path in model_dir.glob(pattern))


def _opencv_has_api(name: str) -> bool:
    try:
        import cv2  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return False
    return hasattr(cv2, name)


def build_setup_response(caps: CapabilitiesResponse) -> SetupResponse:
    items: list[SetupItem] = []

    def add(name: str, entry: CapabilityEntry, required: bool, hint: str | None) -> None:
        items.append(SetupItem(name=name, available=entry.available, required=required, hint=hint if not entry.available else None))

    add(
        "ffmpeg", caps.ffmpeg, required=True,
        hint="Install ffmpeg and ensure it is on PATH. See docs/setup.md.",
    )
    add(
        "ffprobe", caps.ffprobe, required=True,
        hint="Install ffmpeg (ffprobe ships with it) and ensure it is on PATH. See docs/setup.md.",
    )
    add(
        "yt_dlp", caps.yt_dlp, required=False,
        hint="Install yt-dlp to enable URL ingestion. See docs/setup.md.",
    )
    add(
        "mediapipe", caps.mediapipe, required=False,
        hint="Install the 'mediapipe' Python package when working on M5 expression features.",
    )
    add(
        "cuda", caps.cuda, required=False,
        hint="Install PyTorch with CUDA support for GPU acceleration. Required for SAM 3 in M3.",
    )
    add(
        "sam3", caps.sam3, required=False,
        hint="Install SAM 3 externally per docs/setup.md. Required for streamer tracking in M3.",
    )

    all_required_present = all(item.available for item in items if item.required)
    return SetupResponse(items=items, all_required_present=all_required_present)


# Convenience accessors used by routes / services that need a hard check.

def has_ffmpeg(which: WhichFn = shutil.which) -> bool:
    return which("ffmpeg") is not None


def has_ffprobe(which: WhichFn = shutil.which) -> bool:
    return which("ffprobe") is not None


def has_yt_dlp(which: WhichFn = shutil.which) -> bool:
    return which("yt-dlp") is not None


def capabilities_dict() -> dict[str, Any]:
    return probe_capabilities().model_dump()
