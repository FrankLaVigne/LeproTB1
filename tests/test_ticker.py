"""Unit tests for ticker.py — pure-function and snapshot-shape coverage."""

import pytest

import ticker


# --- decide_ring_color tests --------------------------------------------------


def test_decide_ring_color_first_sample_baseline_white():
    # No prior price -> white baseline, marked as ticked so it shows up live.
    color, ticked = ticker.decide_ring_color(None, 100.0, None)
    assert (color, ticked) == ("FFFFFF", True)


def test_decide_ring_color_up_from_baseline_white():
    color, ticked = ticker.decide_ring_color(100.0, 110.0, "FFFFFF")
    assert (color, ticked) == ("00FF00", True)


def test_decide_ring_color_down_from_green():
    color, ticked = ticker.decide_ring_color(110.0, 105.0, "00FF00")
    assert (color, ticked) == ("FF0000", True)


def test_decide_ring_color_flat_keeps_prior_color():
    color, ticked = ticker.decide_ring_color(100.0, 100.0, "00FF00")
    assert (color, ticked) == ("00FF00", False)


def test_decide_ring_color_fetch_fail_yellow():
    color, ticked = ticker.decide_ring_color(100.0, None, "00FF00")
    assert (color, ticked) == ("FFFF00", True)


def test_decide_ring_color_recover_from_yellow_with_uptick():
    color, ticked = ticker.decide_ring_color(100.0, 110.0, "FFFF00")
    assert (color, ticked) == ("00FF00", True)


def test_decide_ring_color_repeated_uptick_still_green_but_not_ticked():
    # If the prior color is already green and the move is again upward,
    # the color doesn't change so ticked must be False (no flash).
    color, ticked = ticker.decide_ring_color(100.0, 110.0, "00FF00")
    assert (color, ticked) == ("00FF00", False)


def test_decide_ring_color_fetch_fail_when_already_yellow_no_tick():
    color, ticked = ticker.decide_ring_color(100.0, None, "FFFF00")
    assert (color, ticked) == ("FFFF00", False)


# --- build_ticker_d50 tests ---------------------------------------------------


def _rings(outer="000000", middle="000000", inner="000000"):
    """Build a minimal rings dict matching the TickerSession.snapshot() shape."""
    return {
        "outer":  {"color": outer},
        "middle": {"color": middle},
        "inner":  {"color": inner},
    }


def test_build_ticker_d50_three_rings_steady_no_flash():
    # Outer green, middle red, inner yellow. No flash -> Steady tail,
    # per-ring multi-color palette.
    rings = _rings(outer="00FF00", middle="FF0000", inner="FFFF00")
    d50 = ticker.build_ticker_d50(rings, flash_color=None)
    assert d50 == ("N01:P1000300FF00FF0000FFFF00"
                   "F2100030058003E002EU3V3000640000E1;")


def test_build_ticker_d50_all_off_steady():
    rings = _rings()  # all black
    d50 = ticker.build_ticker_d50(rings, flash_color=None)
    # All three rings off -> single-color palette, single group of 196.
    assert d50 == "N01:P10001000000F21000100C4U3V3000640000E1;"


def test_build_ticker_d50_flash_color_overrides_per_ring():
    # During a flash the per-ring colors are hidden: single-color palette,
    # whole-lamp Breathe tail.
    import workshop
    rings = _rings(outer="00FF00", middle="FF0000", inner="FFFF00")
    d50 = ticker.build_ticker_d50(rings, flash_color="00FF00")
    expected = ("N01:P10001"
                + "00FF00"
                + "F21000100C4U3V3"
                + workshop.effect_tail("Breathe", 50)
                + ";")
    assert d50 == expected


def test_build_ticker_d50_one_ring_off_others_lit():
    # Outer green, middle off, inner red. Middle's black is a third palette entry.
    rings = _rings(outer="00FF00", middle="000000", inner="FF0000")
    d50 = ticker.build_ticker_d50(rings, flash_color=None)
    assert d50 == ("N01:P1000300FF00000000FF0000"
                   "F2100030058003E002EU3V3000640000E1;")


def test_build_ticker_d50_all_three_rings_same_color_compresses_to_one_group():
    # If somehow all three rings ended up green, RLE compression should fold
    # them into a single 196-LED group.
    rings = _rings(outer="00FF00", middle="00FF00", inner="00FF00")
    d50 = ticker.build_ticker_d50(rings, flash_color=None)
    assert d50 == "N01:P1000100FF00F21000100C4U3V3000640000E1;"
