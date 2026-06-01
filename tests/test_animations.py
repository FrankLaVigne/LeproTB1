"""Tests for web.animations — dedup + grouping helpers for the Animations tab."""

import json
from pathlib import Path

import pytest

from web import animations


# --- frame_fingerprint -------------------------------------------------------


def test_frame_fingerprint_strips_palette_colors():
    # P10003 = 3 palette entries, then 3*6=18 hex chars of color data.
    # The fingerprint should replace those 18 chars with 18 'C's.
    d50 = "N01:P10003FF0000" + "00FF00" + "0000FF" + "F21000100C4U3V3000640000E1;"
    out = animations.frame_fingerprint(d50)
    assert out.startswith("N01:P10003CCCCCCCCCCCCCCCCCC")


def test_frame_fingerprint_truncates_to_40_chars():
    # A long d50 should be cut to 40 chars exactly.
    d50 = "N01:P10001FFFFFF" + "F2100010" * 20  # very long
    out = animations.frame_fingerprint(d50)
    assert len(out) == 40


def test_frame_fingerprint_short_d50_returned_as_is():
    # A short d50 should be returned unchanged (palette-stripped but no truncation).
    d50 = "N01:P10001FFFFFFEND"  # only 19 chars
    out = animations.frame_fingerprint(d50)
    assert len(out) <= 40
    assert "FFFFFF" not in out  # palette colors gone
    assert "CCCCCC" in out      # replaced with Cs


def test_frame_fingerprint_handles_empty_d50():
    assert animations.frame_fingerprint("") == ""


def test_frame_fingerprint_handles_d50_without_p1000():
    # An unusual d50 with no P1000 prefix (defensive — captures might have
    # other shapes). Should not crash; should return the truncated original.
    d50 = "WeirdFormat:noPalette:something"
    out = animations.frame_fingerprint(d50)
    assert len(out) <= 40
    assert out == d50[:40]


def test_frame_fingerprint_palette_count_preserved():
    # The N=3 palette-count digit stays visible after the strip.
    d50_3 = "N01:P10003FFFFFFFFFFFFFFFFFFF21000100C4U3V3000640000E1;"
    d50_1 = "N01:P10001FFFFFFF21000100C4U3V3000640000E1;"
    out_3 = animations.frame_fingerprint(d50_3)
    out_1 = animations.frame_fingerprint(d50_1)
    # Different palette sizes -> different fingerprints (otherwise we'd
    # collapse 1-color vs 3-color presets that share later structure).
    assert out_3[:9] == "N01:P10003"[:9]  # 'N01:P1000'
    assert out_3[9] == "3"
    assert out_1[9] == "1"
    assert out_3 != out_1
