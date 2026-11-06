"""One in-process batch call per segment set, with Python-owned buffers only."""
from __future__ import annotations

import ctypes
import sysconfig
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.errors import EvaluationNativeError, EvaluationNativeUnavailableError


class _Interval(ctypes.Structure):
    _fields_ = [("start", ctypes.c_double), ("end", ctypes.c_double)]


class _Match(ctypes.Structure):
    _fields_ = [("truth_index", ctypes.c_size_t), ("prediction_index", ctypes.c_size_t)]


def _library_path() -> Path:
    # Use this interpreter's extension suffix, never PATH, cwd, or a glob that
    # might pick another interpreter's binary. Wheel tags enforce the platform.
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    if not suffix:
        raise EvaluationNativeUnavailableError()
    return Path(__file__).resolve().parents[1] / "_native" / f"_intervals{suffix}"


@lru_cache(maxsize=1)
def _load_library() -> ctypes.CDLL:
    try:
        library = ctypes.CDLL(str(_library_path()))
        library.wmesm_intervals_abi_version.argtypes = []
        library.wmesm_intervals_abi_version.restype = ctypes.c_uint32
        if library.wmesm_intervals_abi_version() != 1:
            raise EvaluationNativeUnavailableError()
        library.wmesm_match_intervals_v1.argtypes = [
            ctypes.POINTER(_Interval), ctypes.c_size_t,
            ctypes.POINTER(_Interval), ctypes.c_size_t,
            ctypes.c_double, ctypes.POINTER(_Match), ctypes.c_size_t,
        ]
        library.wmesm_match_intervals_v1.restype = ctypes.c_ssize_t
    except (OSError, AttributeError) as exc:
        raise EvaluationNativeUnavailableError() from exc
    return library


def match_indices(
    truth: list[dict[str, Any]], predictions: list[dict[str, Any]], *, min_iou: float,
) -> list[tuple[int, int]]:
    """Match already normalized, confidence-ordered predictions to truth indices."""
    if not truth or not predictions:
        return []
    library = _load_library()
    actual = (_Interval * len(truth))(*(_Interval(item["start_seconds"], item["end_seconds"]) for item in truth))
    predicted = (_Interval * len(predictions))(*(_Interval(item["start_seconds"], item["end_seconds"]) for item in predictions))
    capacity = min(len(truth), len(predictions))
    out = (_Match * capacity)()
    count = library.wmesm_match_intervals_v1(actual, len(actual), predicted, len(predicted), min_iou, out, capacity)
    if count < 0 or count > capacity:
        raise EvaluationNativeError()
    indices = [(out[i].truth_index, out[i].prediction_index) for i in range(count)]
    if (any(t >= len(truth) or p >= len(predictions) for t, p in indices)
            or len({t for t, _ in indices}) != count
            or any(indices[i][1] >= indices[i + 1][1] for i in range(count - 1))):
        raise EvaluationNativeError()
    return indices
