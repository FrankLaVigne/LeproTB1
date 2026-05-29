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


# --- _run_preview tests -------------------------------------------------------

import asyncio


class _FakeClient:
    """Records every send_raw call as a list of (did, payload-dict) tuples."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def send_raw(self, d: dict, did: str | None = None):
        self.calls.append((did, d))


@pytest.mark.asyncio
async def test_run_preview_single_frame_publishes_once_then_idles():
    client = _FakeClient()
    preset = {"payload": {"d1": 1, "d2": 2, "d50": "N01:P10001FF0000;"}}
    task = asyncio.create_task(workshop._run_preview(preset, "abc", client))
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert len(client.calls) == 1
    did, payload = client.calls[0]
    assert did == "abc"
    assert payload["d50"] == "N01:P10001FF0000;"
    assert payload["d1"] == 1


@pytest.mark.asyncio
async def test_run_preview_multi_frame_cycles_with_duration():
    client = _FakeClient()
    preset = {
        "frame_duration_ms": 50,
        "frames": [
            {"d2": 2, "d50": "N01:P10001FF0000;"},
            {"d2": 2, "d50": "N01:P1000100FF00;"},
        ],
    }
    task = asyncio.create_task(workshop._run_preview(preset, "abc", client))
    await asyncio.sleep(0.25)  # ~5 frames at 50ms each
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    d50s = {payload["d50"] for _, payload in client.calls}
    assert d50s == {"N01:P10001FF0000;", "N01:P1000100FF00;"}
    assert len(client.calls) >= 2


# --- segments_to_leds tests ---------------------------------------------------


def test_segments_to_leds_outer_first():
    assert list(workshop.segments_to_leds("outer", 0)) == list(range(0, 4))


def test_segments_to_leds_outer_last():
    assert list(workshop.segments_to_leds("outer", 21)) == list(range(84, 88))


def test_segments_to_leds_middle_first():
    assert list(workshop.segments_to_leds("middle", 0)) == list(range(88, 92))


def test_segments_to_leds_middle_last_two_are_five_LEDs():
    # 13 segments of 4 then 2 segments of 5: indices 13, 14
    assert list(workshop.segments_to_leds("middle", 13)) == list(range(140, 145))
    assert list(workshop.segments_to_leds("middle", 14)) == list(range(145, 150))


def test_segments_to_leds_inner_first():
    assert list(workshop.segments_to_leds("inner", 0)) == list(range(150, 154))


def test_segments_to_leds_inner_last_two_are_five_LEDs():
    # 9 segments of 4 then 2 segments of 5: indices 9, 10
    assert list(workshop.segments_to_leds("inner", 9)) == list(range(186, 191))
    assert list(workshop.segments_to_leds("inner", 10)) == list(range(191, 196))


def test_segments_to_leds_unknown_ring_raises():
    with pytest.raises(ValueError):
        workshop.segments_to_leds("middlering", 0)


def test_segments_to_leds_out_of_range_raises():
    with pytest.raises(IndexError):
        workshop.segments_to_leds("outer", 22)
    with pytest.raises(IndexError):
        workshop.segments_to_leds("middle", 15)
    with pytest.raises(IndexError):
        workshop.segments_to_leds("inner", 11)


def test_segments_total_coverage_is_196():
    total = 0
    for ring, count in [("outer", 22), ("middle", 15), ("inner", 11)]:
        for i in range(count):
            total += len(workshop.segments_to_leds(ring, i))
    assert total == 196


# --- effect_tail tests --------------------------------------------------------


def test_effect_tail_steady_no_speed_field():
    # Steady is the only effect with no {sp} field.
    assert workshop.effect_tail("Steady", 50) == "000640000E1"


def test_effect_tail_breathe_at_speed_50():
    # Breathe uses {sp} twice in the tail.
    tail = workshop.effect_tail("Breathe", 50)
    assert tail.startswith("000640000E4")
    assert tail.endswith("1664")
    # the two {sp} segments are identical 4-hex strings
    assert tail[11:15] == tail[19:23]


def test_effect_tail_gradient_has_C2O6():
    tail = workshop.effect_tail("Gradient", 50)
    assert tail.startswith("100640000E3")
    assert "C2O6" in tail


def test_effect_tail_leftward_format():
    tail = workshop.effect_tail("Leftward", 50)
    assert tail.startswith("00164")
    assert tail.endswith("E1")
    assert len(tail) == 11  # 00164 (5) + sp (4) + E1 (2)


def test_effect_tail_rightward_format():
    tail = workshop.effect_tail("Rightward", 50)
    assert tail.startswith("00264")
    assert tail.endswith("E1")


def test_effect_tail_circle_format():
    tail = workshop.effect_tail("Circle", 50)
    assert tail.startswith("100640000E1C2O6")


def test_effect_tail_speed_zero_special_case():
    # Speed 0 should still produce a valid tail with the well-known "1000" speed slot.
    tail = workshop.effect_tail("Leftward", 0)
    assert tail.startswith("00164")
    assert tail.endswith("E1")


def test_effect_tail_speed_clamps_above_100():
    # Anything > 100 should clamp to 100 (same speed-hex as 100).
    assert workshop.effect_tail("Leftward", 500) == workshop.effect_tail("Leftward", 100)


def test_effect_tail_unknown_effect_raises():
    with pytest.raises(ValueError):
        workshop.effect_tail("WiggleJiggle", 50)
