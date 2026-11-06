from __future__ import annotations

import ctypes
import json
import math
import random
from importlib import resources

import pytest

from app.core.errors import EvaluationNativeError, EvaluationNativeUnavailableError
from app.services import evaluation, interval_matching as native
from tests.reference import evaluation_legacy as legacy


def segment(start=0.0, end=10.0, confidence=1.0, label="laughing"):
    return {"start_seconds": start, "end_seconds": end, "confidence": confidence, "label": label}


@pytest.fixture(autouse=True)
def reset_native_loader():
    loader = native._load_library
    loader.cache_clear()
    yield
    loader.cache_clear()


def assert_match_parity(truth, predictions, min_iou=0.5):
    # Check object identities and order, including duplicate equal dictionaries.
    actual, actual_errors = evaluation._match_segments(truth, predictions, min_iou=min_iou)
    expected, expected_errors = legacy._match_segments(truth, predictions, min_iou=min_iou)
    assert [(id(a), id(p)) for a, p in actual] == [(id(a), id(p)) for a, p in expected]
    assert json.dumps(actual_errors) == json.dumps(expected_errors)


@pytest.mark.parametrize("truth,predictions,cutoff", [
    ([], [], 0.5),
    ([segment()], [], 0.5),
    ([], [segment()], 0.5),
    ([segment()], [segment(0.0, 5.0)], 0.5),
    ([segment()], [segment(0.0, 5.0)], math.nextafter(0.5, 1.0)),
    ([segment()], [segment(10.0, 20.0)], 0.0),
    ([segment()], [segment(0.0, 1.0)], 0.1),
    ([segment(), segment()], [segment(confidence=0.9), segment(confidence=0.9), segment()], 0.5),
    ([segment(0.0, 5.0), segment()], [segment(0.0, 5.0, 0.2), segment(confidence=0.9)], 0.5),
    ([segment(-10.0, -5.0)], [segment(-9.0, -6.0)], 0.5),
    ([segment(-0.0, 0.0)], [segment()], -1.0),
    ([segment(1e150, 2e150)], [segment(1e150, 2e150)], 0.5),
    ([segment()], [segment()], float("nan")),
    ([segment()] * 300, [segment()] * 300, 0.5),
])
def test_native_matches_legacy_boundaries_and_ties(truth, predictions, cutoff):
    assert_match_parity(truth, predictions, cutoff)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), -0.0])
def test_native_preserves_nonfinite_operand_order_and_confidence_sort(value):
    # Fixture JSON historically accepts NaN/Infinity. Do not silently change the
    # Python min/max or sorted semantics when moving the numeric loop to Rust.
    for field in ("start_seconds", "end_seconds", "confidence"):
        for side in ("truth", "prediction"):
            truth = [segment(), segment(20.0, 30.0), segment()]
            predictions = [segment(confidence=0.8), segment(20.0, 30.0), segment(confidence=0.9)]
            (truth if side == "truth" else predictions)[0][field] = value
            assert_match_parity(truth, predictions)


def test_deterministic_generated_sets_match_independent_oracle():
    rng = random.Random(67192)
    for _ in range(250):
        def generate():
            result = []
            for _ in range(rng.randrange(35)):
                start = rng.randrange(-10, 100) / 3
                result.append(segment(start, start + rng.randrange(1, 40) / 3, rng.choice([0.1, 0.9, 0.9, 1.0])))
            return result
        assert_match_parity(generate(), generate(), rng.choice([0.0, 0.1, 0.5, 1.0]))


def test_all_packaged_metrics_are_byte_identical_to_legacy():
    fixtures = json.loads(resources.files("app.evaluation").joinpath("fixtures.json").read_text())["fixtures"]
    actual = evaluation._aggregate_results([evaluation._score_fixture(f) for f in fixtures])
    expected = legacy._aggregate_results([legacy._score_fixture(f) for f in fixtures])
    assert json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)


def test_normalization_top_five_latency_and_rounding_match_legacy():
    # Duplicate truth, tied confidence, >5 predictions, out-of-order intervals,
    # invalid rows, strings and lifecycle label aliases all use the old rules.
    fixture = {
        "id": "synthetic-edge-cases", "duration_seconds": "60.0005",
        "ground_truth": {
            "activity_events": [segment(), segment(20, 23), None, {}, segment(1, 1)],
            "lifecycle_segments": [{"state": "returned", "start_seconds": "5", "end_seconds": "15"}],
        },
        "predictions": {
            "activity_events": [segment(i * 5, i * 5 + 10, 0.8) for i in range(8)],
            "lifecycle_segments": [{"state": "returned", "start_seconds": 4.3335, "end_seconds": 13.1115}],
        },
    }
    actual = evaluation._aggregate_results([evaluation._score_fixture(fixture)])
    expected = legacy._aggregate_results([legacy._score_fixture(fixture)])
    assert json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)


def test_native_is_an_actual_batch_ffi_call(monkeypatch):
    library = native._load_library()
    assert library.wmesm_intervals_abi_version() == 1
    calls = []
    original = library.wmesm_match_intervals_v1

    def invoke(*args):
        calls.append((args[1], args[3]))
        return original(*args)

    monkeypatch.setattr(library, "wmesm_match_intervals_v1", invoke)
    assert_match_parity([segment()] * 100, [segment()] * 100)
    assert calls == [(100, 100)]


def test_native_abi_rejects_short_or_null_buffers():
    library = native._load_library()
    interval = native._Interval(0, 10)
    out = native._Match()
    assert library.wmesm_match_intervals_v1(ctypes.byref(interval), 1, ctypes.byref(interval), 1, 0.5, ctypes.byref(out), 0) == -1
    assert library.wmesm_match_intervals_v1(None, 1, ctypes.byref(interval), 1, 0.5, ctypes.byref(out), 1) == -1


@pytest.mark.parametrize("suffix", [".cpython-313-darwin.so", ".cp312-win_amd64.pyd", ".cpython-312-x86_64-linux-gnu.so"])
def test_library_lookup_uses_interpreter_suffix_not_cwd_or_path(monkeypatch, tmp_path, suffix):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(native.sysconfig, "get_config_var", lambda key: suffix if key == "EXT_SUFFIX" else None)
    assert native._library_path().name == "_intervals" + suffix
    assert native._library_path().parent.name == "_native"
    assert native._library_path().is_absolute()


@pytest.mark.parametrize("failure", ["missing", "wrong_architecture", "missing_symbol", "wrong_abi"])
def test_loader_translates_native_availability_failures(monkeypatch, tmp_path, failure):
    if failure == "missing":
        monkeypatch.setattr(native, "_library_path", lambda: tmp_path / "missing.so")
    else:
        def load(_path):
            if failure == "wrong_architecture":
                raise OSError("incompatible architecture")
            if failure == "missing_symbol":
                return object()
            class Version:
                def __call__(self): return 99
            class Library:
                wmesm_intervals_abi_version = Version()
            return Library()
        monkeypatch.setattr(native.ctypes, "CDLL", load)
    with pytest.raises(EvaluationNativeUnavailableError) as exc:
        native.match_indices([segment()], [segment()], min_iou=0.5)
    assert exc.value.status_code == 503
    assert "setup_hint" in exc.value.detail


@pytest.mark.parametrize("bad_result", [-1, -2, 2, "bad_index", "duplicate", "out_of_order"])
def test_adapter_rejects_failed_or_malformed_native_output(monkeypatch, bad_result):
    class Library:
        def wmesm_match_intervals_v1(self, truth, nt, pred, np, cutoff, out, capacity):
            if bad_result == "bad_index":
                out[0] = native._Match(500, 0)
                return 1
            if bad_result == "duplicate":
                out[0], out[1] = native._Match(0, 0), native._Match(0, 1)
                return 2
            if bad_result == "out_of_order":
                out[0], out[1] = native._Match(0, 1), native._Match(1, 0)
                return 2
            return bad_result
    monkeypatch.setattr(native, "_load_library", lambda: Library())
    size = 2 if bad_result in ("duplicate", "out_of_order") else 1
    with pytest.raises(EvaluationNativeError):
        native.match_indices([segment()] * size, [segment()] * size, min_iou=0.5)
