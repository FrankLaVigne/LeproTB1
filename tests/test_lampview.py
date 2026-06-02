"""Tests for web.lampview — pure d50/d-field → 196-LED color decoding."""

from web import lampview


# Real captured d50s (see docs/D50_FORMAT.md).
SOLID_ORANGE = "N01:P10001FFAA00F21000100C4U3V3000640000E1;"
THREE_RINGS = "N01:P10003FFFFFF0000FFFFFF00F2100030058003E002EU3V3000640000E1;"
N02_CHRISTMAS = ("N02:P10002FF0000008000U510F2100010f01V3001640396;"
                 "P600F210001s00000001U635ca000000000002000002020000R301111;")
PER_RING_HULK = ("#V:0358c4000000203ec4000000102ec400000000;"
                 "#I00:N01:P10006808000A6A63AD9FFB3FFAC593AA63AA6A63AU200010001T2X2S20283O61418;")


# --- parse_d50_n01 ------------------------------------------------------------


def test_parse_solid_orange_gives_196_orange_leds():
    physical = lampview.parse_d50_n01(SOLID_ORANGE)
    assert physical == ["FFAA00"] * 196


def test_parse_three_ring_paint():
    physical = lampview.parse_d50_n01(THREE_RINGS)
    assert physical is not None
    assert physical[:88] == ["FFFFFF"] * 88       # outer: white
    assert physical[88:150] == ["0000FF"] * 62    # middle: blue
    assert physical[150:] == ["FFFF00"] * 46      # inner: yellow


def test_parse_lowercase_hex_is_uppercased():
    physical = lampview.parse_d50_n01(
        "N01:P10001ffaa00F21000100C4U3V3000640000E1;")
    assert physical == ["FFAA00"] * 196


def test_parse_n02_returns_none():
    assert lampview.parse_d50_n01(N02_CHRISTMAS) is None


def test_parse_per_ring_capture_returns_none():
    assert lampview.parse_d50_n01(PER_RING_HULK) is None


def test_parse_rejects_none_empty_and_non_string():
    assert lampview.parse_d50_n01(None) is None
    assert lampview.parse_d50_n01("") is None
    assert lampview.parse_d50_n01(42) is None


def test_parse_rejects_lengths_not_summing_to_196():
    # 0xC3 = 195 LEDs — one short.
    assert lampview.parse_d50_n01(
        "N01:P10001FF0000F21000100C3U3V3000640000E1;") is None


# --- unrotate_to_page ----------------------------------------------------------


def test_unrotate_inverts_server_rotation():
    from web.server import apply_lamp_rotation
    page = [f"{i:06X}" for i in range(196)]
    assert lampview.unrotate_to_page(apply_lamp_rotation(page)) == page


def test_unrotate_outer_offset():
    # Physical LED 31 is page LED 0 on the outer ring (rotation +31).
    physical = ["000000"] * 196
    physical[31] = "FF0000"
    page = lampview.unrotate_to_page(physical)
    assert page[0] == "FF0000"


def test_unrotate_middle_and_inner_offsets():
    # Middle ring: page 0 (abs 88) = physical 88 + 22. Inner: page 0 (abs 150) = physical 150 + 4.
    physical = ["000000"] * 196
    physical[88 + 22] = "00FF00"
    physical[150 + 4] = "0000FF"
    page = lampview.unrotate_to_page(physical)
    assert page[88] == "00FF00"
    assert page[150] == "0000FF"


# --- hsv_hex_to_rgb -------------------------------------------------------------


def test_hsv_red():
    # hue 0, sat 1000, val 1000 → pure red
    assert lampview.hsv_hex_to_rgb("000003E803E8") == "FF0000"


def test_hsv_green():
    # hue 120 (0x0078), full sat/val → pure green
    assert lampview.hsv_hex_to_rgb("007803E803E8") == "00FF00"


def test_hsv_cyan():
    # hue 180 (0x00B4) → cyan
    assert lampview.hsv_hex_to_rgb("00B403E803E8") == "00FFFF"


def test_hsv_half_value_is_darker():
    # hue 0, sat 1000, val 500 → half-brightness red
    assert lampview.hsv_hex_to_rgb("000003E801F4") == "800000"


def test_hsv_rejects_garbage():
    assert lampview.hsv_hex_to_rgb(None) is None
    assert lampview.hsv_hex_to_rgb("xyz") is None
    assert lampview.hsv_hex_to_rgb("0000") is None


# --- cct_to_rgb -----------------------------------------------------------------


def test_cct_warm_end():
    assert lampview.cct_to_rgb(0) == "FFC58F"


def test_cct_cool_end():
    assert lampview.cct_to_rgb(1000) == "EBF2FF"


def test_cct_clamps_out_of_range():
    assert lampview.cct_to_rgb(-50) == lampview.cct_to_rgb(0)
    assert lampview.cct_to_rgb(5000) == lampview.cct_to_rgb(1000)


# --- fields_to_leds -------------------------------------------------------------


def test_fields_segmented_n01_returns_page_space():
    leds = lampview.fields_to_leds({"d1": 1, "d2": 2, "d50": SOLID_ORANGE})
    assert leds == ["FFAA00"] * 196


def test_fields_segmented_three_rings_is_unrotated():
    # The d50 is physical-space; fields_to_leds returns page space. With
    # uniform per-ring colors rotation is a no-op, so rings stay intact.
    leds = lampview.fields_to_leds({"d2": 2, "d50": THREE_RINGS})
    assert leds[:88] == ["FFFFFF"] * 88
    assert leds[88:150] == ["0000FF"] * 62
    assert leds[150:] == ["FFFF00"] * 46


def test_fields_segmented_undecodable_returns_none():
    assert lampview.fields_to_leds({"d2": 2, "d50": N02_CHRISTMAS}) is None


def test_fields_rgb_mode_fills_all_leds():
    leds = lampview.fields_to_leds({"d2": 1, "d5": "000003E803E8"})
    assert leds == ["FF0000"] * 196


def test_fields_white_mode_fills_all_leds():
    leds = lampview.fields_to_leds({"d2": 0, "d4": 0})
    assert leds == ["FFC58F"] * 196


def test_fields_special_effect_mode_returns_none():
    assert lampview.fields_to_leds({"d2": 3, "d60": "20700004E0000"}) is None


def test_fields_empty_or_missing_returns_none():
    assert lampview.fields_to_leds({}) is None
    assert lampview.fields_to_leds(None) is None
    assert lampview.fields_to_leds({"d2": 1}) is None       # RGB mode, no d5
    assert lampview.fields_to_leds({"d2": 0}) is None       # white mode, no d4
