"""Tests for workshop."""

import pytest

import workshop


def test_extract_palette_single_frame_one_color():
    preset = {"payload": {"d50": "N01:P10001FF0000F2100010019U3V3000640000E1;"}}
    assert workshop.extract_palette(preset) == ["FF0000"]


def test_extract_palette_single_frame_multi_color():
    preset = {"payload": {"d50": "N02:P10003FF000000FF000000FF59U510...;P600...;"}}
    assert workshop.extract_palette(preset) == ["FF0000", "00FF00", "0000FF"]


def test_extract_palette_multi_frame_dedups_and_orders_by_first_occurrence():
    preset = {"frames": [
        {"d50": "N01:P10002AAA000BBB111;"},
        {"d50": "N01:P10002BBB111CCC222;"},  # BBB111 already seen, CCC222 is new
    ]}
    assert workshop.extract_palette(preset) == ["AAA000", "BBB111", "CCC222"]


def test_extract_palette_normalizes_to_uppercase():
    preset = {"payload": {"d50": "N01:P40005e500e500e500e500e500U504F2...;"}}
    assert workshop.extract_palette(preset) == ["E500E5"]


def test_extract_palette_handles_per_ring_format():
    preset = {"payload": {"d50":
        "#V:0358c4000000003ec4000000002ec400000000;"
        "#I00:N01:P100038000FFFFC0CB8000FFU701...;"
        "#I01:N01:P100038000FFFFC0CB8000FFU701...;"
        "#I02:N01:P100038000FFFFC0CB8000FFU701...;"
    }}
    # Same palette repeated per ring → dedup keeps first-occurrence order
    assert workshop.extract_palette(preset) == ["8000FF", "FFC0CB"]


def test_extract_palette_empty_preset_returns_empty_list():
    assert workshop.extract_palette({"frames": []}) == []
    assert workshop.extract_palette({"payload": {"d50": ""}}) == []


# --- apply_color_map tests ----------------------------------------------------


def test_apply_color_map_single_frame():
    preset = {"name": "x", "payload": {"d1": 1, "d2": 2,
                                       "d50": "N01:P10001FF0000F2100010019U3V3;"}}
    out = workshop.apply_color_map(preset, {"FF0000": "00FF00"})
    assert out["payload"]["d50"] == "N01:P1000100FF00F2100010019U3V3;"
    # Original untouched (deep copy)
    assert preset["payload"]["d50"] == "N01:P10001FF0000F2100010019U3V3;"


def test_apply_color_map_multi_frame():
    preset = {"name": "x", "frames": [
        {"d2": 2, "d50": "N01:P10001FF0000;"},
        {"d2": 2, "d50": "N01:P10002FF0000FFC0CB;"},
    ]}
    out = workshop.apply_color_map(preset, {"FF0000": "00FF00", "FFC0CB": "0000FF"})
    assert out["frames"][0]["d50"] == "N01:P1000100FF00;"
    assert out["frames"][1]["d50"] == "N01:P1000200FF000000FF;"


def test_apply_color_map_case_insensitive_input_uppercase_output():
    preset = {"payload": {"d50": "N01:P10001ff0000;"}}
    out = workshop.apply_color_map(preset, {"ff0000": "ff00ff"})
    assert out["payload"]["d50"] == "N01:P10001FF00FF;"


def test_apply_color_map_unknown_old_color_is_noop():
    preset = {"payload": {"d50": "N01:P10001FF0000;"}}
    out = workshop.apply_color_map(preset, {"BADBAD": "00FF00"})
    assert out["payload"]["d50"] == "N01:P10001FF0000;"


def test_apply_color_map_empty_map_is_deep_copy():
    preset = {"name": "x", "payload": {"d50": "N01:P10001FF0000;"}}
    out = workshop.apply_color_map(preset, {})
    assert out == preset
    assert out is not preset
    assert out["payload"] is not preset["payload"]


# --- _sanitize_name tests -----------------------------------------------------


def test_sanitize_name_passes_simple_kebab():
    assert workshop._sanitize_name("cyberpunk-warm") == "cyberpunk-warm"


def test_sanitize_name_rejects_empty():
    with pytest.raises(ValueError):
        workshop._sanitize_name("")


def test_sanitize_name_rejects_uppercase():
    with pytest.raises(ValueError):
        workshop._sanitize_name("CyberpunkWarm")


def test_sanitize_name_rejects_spaces():
    with pytest.raises(ValueError):
        workshop._sanitize_name("cyber punk")


def test_sanitize_name_rejects_path_traversal():
    with pytest.raises(ValueError):
        workshop._sanitize_name("../escape")
    with pytest.raises(ValueError):
        workshop._sanitize_name("a/b")
