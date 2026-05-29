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
