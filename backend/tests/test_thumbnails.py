from __future__ import annotations

from app.services.thumbnails import thumbnail_timestamps


def test_thumbnail_timestamps_basic() -> None:
    ts = thumbnail_timestamps(20.0, 5.0)
    # 0, 5, 10, 15, plus a final near-end frame
    assert ts[0] == 0.0
    assert 19.0 <= ts[-1] <= 20.0
    assert all(b >= a for a, b in zip(ts, ts[1:]))


def test_thumbnail_timestamps_zero_duration_yields_single_zero() -> None:
    assert thumbnail_timestamps(0.0, 5.0) == [0.0]
    assert thumbnail_timestamps(None, 5.0) == [0.0]


def test_thumbnail_timestamps_skips_useless_final_when_already_close() -> None:
    ts = thumbnail_timestamps(20.0, 5.0)
    # If we already sampled at >= duration-0.5 we don't double-up. Here 15 < 19.5 so a final near-end is added.
    assert ts[-1] != 15.0
