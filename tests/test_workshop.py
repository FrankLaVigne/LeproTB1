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
