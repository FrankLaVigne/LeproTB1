"""Tests for web.motions — the motion library engine.

Fixtures are real captured d50 strings from presets/ (see docs/D50_FORMAT.md).
The strongest tests use whole presets from disk as ground truth.
"""

import json
from pathlib import Path

from web import motions

PRESETS = Path(__file__).parent.parent / "presets"


def load_preset(name):
    return json.loads((PRESETS / f"{name}.json").read_text())


def frames(preset):
    if "frames" in preset:
        return [f["d50"] for f in preset["frames"]]
    return [preset["payload"]["d50"]]


# Real captured d50s.
N01_SOLID = "N01:P10001FFAA00F21000100C4U3V3000640000E1;"
N02_CHRISTMAS = ("N02:P10002FF0000008000U510F2100010f01V3001640396;"
                 "P600F210001s00000001U635ca000000000002000002020000R301111;")
N02_CYBERPUNK_TWIN = ("N02:P10006FF0040FF8000FFFF0059FFFF5959FFBF00FFU510F2100010f01V3001640396;"
                      "P600F210001s00000001U635ca000000000002000002020000R301011;")
N03_MULTIBLOCK = ("N03:P100028000FFFFC0CBU3F210001r0106fffe00000002V3002640394;"
                  "P600U3F210001r0103ffff00000001V30126401ca;"
                  "P1000100004dU3V3030640000R301111;")
PER_RING = ("#V:0358c4000000203ec4000000102ec400000000;"
            "#I00:N01:P1000500FF8059FFFF8000FFFF0080FF8000U200010001T2X2S20283O61418;"
            "#I01:N01:P1000500FF8059FFFF8000FFFF0080FF8000U200010001T2X2S22830O62830;"
            "#I02:N01:P1000500FF8059FFFF8000FFFF0080FF8000U200010001T2X2S28300O68300;")
P4_CYBERPUNK = "N01:P40005e500e500e500e500e500U504F2100010001S4009cX60000ffff00000000;"


# --- find_palette_blocks --------------------------------------------------------


def test_blocks_single():
    blocks = motions.find_palette_blocks(N01_SOLID)
    assert len(blocks) == 1
    assert blocks[0].count == 1
    assert blocks[0].colors == ["FFAA00"]


def test_blocks_multi_program():
    # N03 has two P1000 blocks (the P600 block has no colors).
    blocks = motions.find_palette_blocks(N03_MULTIBLOCK)
    assert len(blocks) == 2
    assert blocks[0].colors == ["8000FF", "FFC0CB"]
    assert blocks[1].colors == ["00004D"]


def test_blocks_per_ring():
    blocks = motions.find_palette_blocks(PER_RING)
    assert len(blocks) == 3
    for b in blocks:
        assert b.colors == ["00FF80", "59FFFF", "8000FF", "FF0080", "FF8000"]


def test_blocks_p4_not_matched():
    # P4000 blocks are not P1000 blocks.
    assert motions.find_palette_blocks(P4_CYBERPUNK) == []


def test_blocks_empty_and_none():
    assert motions.find_palette_blocks("") == []
    assert motions.find_palette_blocks(None) == []


# --- extract_palette -------------------------------------------------------------


def test_extract_palette_dedups_across_blocks():
    assert motions.extract_palette(PER_RING) == [
        "00FF80", "59FFFF", "8000FF", "FF0080", "FF8000"]


def test_extract_palette_orders_by_appearance():
    assert motions.extract_palette(N03_MULTIBLOCK) == ["8000FF", "FFC0CB", "00004D"]


# --- has_p4_block / is_recolorable / detect_format -------------------------------


def test_p4_detection():
    assert motions.has_p4_block(P4_CYBERPUNK) is True
    assert motions.has_p4_block(N02_CHRISTMAS) is False


def test_recolorable():
    assert motions.is_recolorable(N02_CHRISTMAS) is True
    assert motions.is_recolorable(P4_CYBERPUNK) is False
    assert motions.is_recolorable("") is False


def test_detect_format():
    assert motions.detect_format(N01_SOLID) == "N01"
    assert motions.detect_format(N02_CHRISTMAS) == "N02"
    assert motions.detect_format(N03_MULTIBLOCK) == "N03"
    assert motions.detect_format(PER_RING) == "per-ring"
    assert motions.detect_format("") == "unknown"


# --- motion_signature -------------------------------------------------------------


def test_signature_ignores_colors_same_count():
    """Same motion, same color count, different colors -> same strict sig."""
    a = "N01:P10002FF000000FF00F2100020058006CU3V3000640000E1;"
    b = "N01:P10002123456ABCDEFF2100020058006CU3V3000640000E1;"
    sa, la = motions.motion_signature(a)
    sb, lb = motions.motion_signature(b)
    assert sa == sb
    assert la == lb


def test_signature_differs_for_different_motion():
    sa, la = motions.motion_signature(N02_CHRISTMAS)
    sb, lb = motions.motion_signature(N03_MULTIBLOCK)
    assert sa != sb
    assert la != lb


def test_strict_differs_loose_matches_for_cross_palette_twin():
    """christmas[0] vs cyberpunk[1]: same motion, different palette size + R3 digit."""
    s_chr, l_chr = motions.motion_signature(N02_CHRISTMAS)
    s_cyb, l_cyb = motions.motion_signature(N02_CYBERPUNK_TWIN)
    assert s_chr != s_cyb          # strict keeps palette count -> differs
    assert l_chr == l_cyb          # loose masks count + R3 digits -> unifies


def test_purple_pink_vs_white_blue_full_corpus():
    """All 35 frames of the lamp-verified recolor pair share strict signatures."""
    pp = frames(load_preset("purple-pink-tour"))
    wb = frames(load_preset("white-blue-tour"))
    assert len(pp) == len(wb) == 35
    for a, b in zip(pp, wb):
        assert motions.motion_signature(a) == motions.motion_signature(b)


def test_signatures_are_stable_hashes():
    s1, l1 = motions.motion_signature(N01_SOLID)
    s2, l2 = motions.motion_signature(N01_SOLID)
    assert (s1, l1) == (s2, l2)
    assert len(s1) == 40 and len(l1) == 40    # sha1 hex


def test_signature_handles_none_and_non_string():
    """All public functions tolerate garbage input — signatures of degenerate
    input are stable (not crashes)."""
    assert motions.motion_signature(None) == motions.motion_signature("")
    assert motions.motion_signature(42) == motions.motion_signature("")
    assert motions.has_p4_block(None) is False
    assert motions.has_p4_block(42) is False


def test_signature_p4_only_frame_strict_equals_loose():
    """A P4-only frame has no P1000 blocks to mask, so strict == loose
    (modulo R3 masking, absent here)."""
    s, l = motions.motion_signature(P4_CYBERPUNK)
    assert s == l


def test_blocks_invalid_hex_after_count_is_skipped():
    """A P1000 match whose following chars aren't valid hex colors is not a block."""
    assert motions.find_palette_blocks("N01:P10002FF00;U3V3") == []


# --- remap_colors (ground truth) ---------------------------------------------------


def test_remap_ground_truth_white_blue_tour():
    """The strongest test in this suite: white-blue-tour.json was created from
    purple-pink-tour.json by exactly this operation and verified on the lamp."""
    pp = frames(load_preset("purple-pink-tour"))
    wb = frames(load_preset("white-blue-tour"))
    mapping = {"8000FF": "FFFFFF", "FFC0CB": "0000FF"}
    for purple, expected in zip(pp, wb):
        assert motions.remap_colors(purple, mapping) == expected


def test_remap_untouched_outside_blocks():
    """Motion fields that happen to contain hex-like strings are never modified."""
    # 'ffff' appears in the X6 motion field here — must stay untouched.
    d50 = "N01:P10002FF00000000FFF2100020058006CU3X6ff02ffff000640000E1;"
    out = motions.remap_colors(d50, {"FF0000": "00FF00", "0000FF": "FFFFFF"})
    assert out == "N01:P1000200FF00FFFFFFF2100020058006CU3X6ff02ffff000640000E1;"


def test_remap_case_insensitive_match_uppercase_write():
    d50 = "N01:P10001ffaa00F21000100C4U3V3000640000E1;"
    out = motions.remap_colors(d50, {"FFAA00": "00ff00"})
    assert out == "N01:P1000100FF00F21000100C4U3V3000640000E1;"


def test_remap_unmapped_colors_keep_original_bytes():
    # lowercase 'ffffff' not in mapping -> stays lowercase.
    d50 = "N02:P10001ffffffR6X6000200000102ffffU504T2F70101000000064S20130W610000009801c8;"
    out = motions.remap_colors(d50, {"8000FF": "FF0000"})
    assert out == d50


# --- recolor -----------------------------------------------------------------------


def test_recolor_cycles_palette():
    """A 3-color motion recolored with 2 colors cycles: [A, B, A]."""
    out = motions.recolor(N03_MULTIBLOCK, ["111111", "222222"])
    blocks = motions.find_palette_blocks(out)
    assert blocks[0].colors == ["111111", "222222"]    # 8000FF->1, FFC0CB->2
    assert blocks[1].colors == ["111111"]              # 00004D -> cycles back to 1


def test_recolor_per_ring_consistency():
    """All three ring sections get the same substitution."""
    out = motions.recolor(PER_RING, ["AA0000", "00BB00"])
    blocks = motions.find_palette_blocks(out)
    assert len(blocks) == 3
    assert blocks[0].colors == blocks[1].colors == blocks[2].colors
    # 5 distinct colors cycled onto 2: [AA0000, 00BB00, AA0000, 00BB00, AA0000]
    assert blocks[0].colors == ["AA0000", "00BB00", "AA0000", "00BB00", "AA0000"]


def test_recolor_preserves_motion_signature():
    """Recoloring never changes a motion's identity."""
    out = motions.recolor(N02_CHRISTMAS, ["123456", "654321"])
    assert motions.motion_signature(out) == motions.motion_signature(N02_CHRISTMAS)


def test_recolor_rejects_p4():
    import pytest
    with pytest.raises(ValueError, match="P4"):
        motions.recolor(P4_CYBERPUNK, ["FF0000"])


def test_recolor_rejects_bad_input():
    import pytest
    with pytest.raises(ValueError):
        motions.recolor(N01_SOLID, [])                  # empty palette
    with pytest.raises(ValueError):
        motions.recolor(N01_SOLID, ["not-hex"])         # bad color
    with pytest.raises(ValueError):
        motions.recolor("U3V3000640000E1;", ["FF0000"])  # no palette blocks
