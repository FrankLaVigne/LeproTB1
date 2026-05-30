"""Unit tests for clock.py — pure-function and snapshot-shape coverage."""

from datetime import datetime

import pytest

import clock


# --- compute_positions tests --------------------------------------------------


def test_compute_positions_midnight_12h_all_zero():
    # 2026-01-01 00:00:00 -> seconds=0, minutes=0, hour=0 -> all positions 0.
    now = datetime(2026, 1, 1, 0, 0, 0)
    pos = clock.compute_positions(now, mode="12h")
    assert pos == {"outer": 0, "middle": 0, "inner": 0}


def test_compute_positions_noon_12h_inner_at_zero():
    # Noon: hour=12, 12 % 12 == 0 -> inner dot at 0 (top of inner ring).
    now = datetime(2026, 1, 1, 12, 0, 0)
    pos = clock.compute_positions(now, mode="12h")
    assert pos == {"outer": 0, "middle": 0, "inner": 0}


def test_compute_positions_noon_24h_inner_halfway():
    # 24h mode at 12:00 -> inner is halfway around 46 = 23.
    now = datetime(2026, 1, 1, 12, 0, 0)
    pos = clock.compute_positions(now, mode="24h")
    assert pos == {"outer": 0, "middle": 0, "inner": 23}


def test_compute_positions_seconds_advance():
    # 30 seconds -> outer at half of 88 = 44.
    now = datetime(2026, 1, 1, 0, 0, 30)
    pos = clock.compute_positions(now, mode="12h")
    assert pos["outer"] == 44


def test_compute_positions_minute_drifts_with_seconds():
    # minute=5 second=30 -> middle is between minute-5 and minute-6.
    # minute-5 alone: round(5 * 62/60) = round(5.166) = 5.
    # minute-5 + half-second drift: round(5.5 * 62/60) = round(5.683) = 6.
    now = datetime(2026, 1, 1, 0, 5, 30)
    pos = clock.compute_positions(now, mode="12h")
    assert pos["middle"] == 6


def test_compute_positions_hour_drifts_with_minutes():
    # 8:30 in 12h -> inner is between hour-8 and hour-9.
    # hour-8 alone: round(8 * 46/12) = round(30.666) = 31.
    # hour-8 + half-hour drift: round(8.5 * 46/12) = round(32.583) = 33.
    now = datetime(2026, 1, 1, 8, 30, 0)
    pos = clock.compute_positions(now, mode="12h")
    assert pos["inner"] == 33


def test_compute_positions_rejects_unknown_mode():
    with pytest.raises(ValueError):
        clock.compute_positions(datetime(2026, 1, 1, 0, 0, 0), mode="invalid")


def test_compute_positions_wraps_mod_ring_size():
    # The "% ring_size" guard catches edge cases where rounding lands at the
    # ring size (e.g., second=59 with fractional carry could round up to 88).
    now = datetime(2026, 1, 1, 0, 0, 59, 999_000)
    pos = clock.compute_positions(now, mode="12h")
    assert 0 <= pos["outer"] < 88
    assert 0 <= pos["middle"] < 62
    assert 0 <= pos["inner"] < 46


# --- build_clock_leds tests ---------------------------------------------------


def _colors(outer="FF0000", middle="00FF00", inner="0000FF"):
    return {"outer": outer, "middle": middle, "inner": inner}


def test_build_clock_leds_paints_exactly_three_lit_leds():
    positions = {"outer": 0, "middle": 0, "inner": 0}
    leds = clock.build_clock_leds(positions, _colors())
    assert len(leds) == 196
    lit = [(i, c) for i, c in enumerate(leds) if c is not None]
    assert len(lit) == 3


def test_build_clock_leds_uses_correct_colors_at_correct_indices():
    positions = {"outer": 5, "middle": 7, "inner": 9}
    leds = clock.build_clock_leds(positions, _colors())
    # Outer ring covers indices 0..87, middle 88..149, inner 150..195.
    assert leds[5] == "FF0000"
    assert leds[88 + 7] == "00FF00"
    assert leds[150 + 9] == "0000FF"
    # And everything else is None (off).
    other = [c for i, c in enumerate(leds)
             if i not in (5, 88 + 7, 150 + 9)]
    assert all(c is None for c in other)


def test_build_clock_leds_supports_zero_positions():
    positions = {"outer": 0, "middle": 0, "inner": 0}
    leds = clock.build_clock_leds(positions, _colors())
    assert leds[0] == "FF0000"
    assert leds[88] == "00FF00"
    assert leds[150] == "0000FF"


def test_build_clock_leds_supports_last_positions():
    # outer max = 87, middle max = 61, inner max = 45
    positions = {"outer": 87, "middle": 61, "inner": 45}
    leds = clock.build_clock_leds(positions, _colors())
    assert leds[87] == "FF0000"
    assert leds[149] == "00FF00"
    assert leds[195] == "0000FF"


# --- ClockSession state tests -------------------------------------------------


def test_clock_session_initial_snapshot_not_running():
    sess = clock.ClockSession(client=None, colors=_colors(), mode="12h")
    snap = sess.snapshot()
    assert snap["running"] is False
    assert snap["since"] is None
    assert snap["mode"] == "12h"
    assert snap["colors"] == _colors()
    assert snap["now_displayed"] is None


def test_clock_session_defaults_to_12h_with_red_green_blue():
    # Constructing with no colors at all -> defaults.
    sess = clock.ClockSession(client=None)
    snap = sess.snapshot()
    assert snap["mode"] == "12h"
    assert snap["colors"] == {"outer": "FF0000", "middle": "00FF00", "inner": "0000FF"}


def test_clock_session_rejects_bad_color():
    with pytest.raises(ValueError):
        clock.ClockSession(client=None,
                            colors={"outer": "ZZZ", "middle": "00FF00", "inner": "0000FF"})


def test_clock_session_rejects_bad_mode():
    with pytest.raises(ValueError):
        clock.ClockSession(client=None, mode="13h")


def test_clock_session_snapshot_json_serialisable():
    import json
    sess = clock.ClockSession(client=None, colors=_colors(), mode="24h")
    assert json.dumps(sess.snapshot())
