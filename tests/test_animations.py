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


# --- preset_signature --------------------------------------------------------


def _single_frame_preset(d50: str) -> dict:
    return {"name": "fake", "payload": {"d50": d50}}


def _multi_frame_preset(d50s: list) -> dict:
    return {"name": "fake", "frames": [{"d50": s} for s in d50s]}


def test_preset_signature_single_frame():
    preset = _single_frame_preset("N01:P10001FFFFFFF21000100C4U3V3000640000E1;")
    sig = animations.preset_signature(preset)
    # No pipes for single-frame presets.
    assert "|" not in sig
    assert sig == animations.frame_fingerprint(preset["payload"]["d50"])


def test_preset_signature_multi_frame_joined_with_pipe():
    preset = _multi_frame_preset([
        "N01:P10001FFFFFFF21000100C4U3V3000640000E1;",
        "N01:P10001FF0000F21000100C4U3V3000640000E1;",
    ])
    sig = animations.preset_signature(preset)
    assert sig.count("|") == 1
    # The two fingerprints, joined by |.
    fp_a = animations.frame_fingerprint(preset["frames"][0]["d50"])
    fp_b = animations.frame_fingerprint(preset["frames"][1]["d50"])
    assert sig == f"{fp_a}|{fp_b}"


def test_preset_signature_empty_preset_returns_empty():
    assert animations.preset_signature({}) == ""
    assert animations.preset_signature({"frames": []}) == ""


# --- per_preset_frame_stats --------------------------------------------------


def test_per_preset_frame_stats_single_frame_is_one_one():
    preset = _single_frame_preset("N01:P10001FFFFFFF21000100C4U3V3000640000E1;")
    assert animations.per_preset_frame_stats(preset) == {"total": 1, "unique": 1}


def test_per_preset_frame_stats_counts_unique_frame_fingerprints():
    # 5 frames; all 5 share the same palette-stripped fingerprint
    # (different colors don't change the fingerprint).
    f_red   = "N01:P10001FF0000F21000100C4U3V3000640000E1;"
    f_green = "N01:P10001" + "00FF00" + "F21000100C4U3V3000640000E1;"
    f_blue  = "N01:P10001" + "0000FF" + "F21000100C4U3V3000640000E1;"
    preset = _multi_frame_preset([f_red, f_green, f_blue, f_red, f_red])
    stats = animations.per_preset_frame_stats(preset)
    assert stats == {"total": 5, "unique": 1}


def test_per_preset_frame_stats_distinct_structures():
    # Two different palette sizes -> two different fingerprints (the palette
    # count digit lands inside the 40-char fingerprint window).
    f_one_color   = "N01:P10001FFFFFFF21000100C4U3V3000640000E1;"
    f_three_color = "N01:P10003FFFFFFFFFFFFFFFFFFF21000100C4U3;"
    preset = _multi_frame_preset([f_one_color, f_one_color, f_three_color])
    stats = animations.per_preset_frame_stats(preset)
    assert stats == {"total": 3, "unique": 2}


def test_per_preset_frame_stats_empty_returns_zero_zero():
    assert animations.per_preset_frame_stats({}) == {"total": 0, "unique": 0}
    assert animations.per_preset_frame_stats({"frames": []}) == {"total": 0, "unique": 0}
