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


# --- Animation + group_presets ----------------------------------------------


def test_group_presets_groups_same_signature(tmp_path):
    # Two presets with identical structure but different palette colors
    # should end up in one Animation group.
    a = _single_frame_preset("N01:P10001FF0000F21000100C4U3V3000640000E1;")
    a["name"] = "red"
    b = _single_frame_preset("N01:P10001"+"00FF00"+"F21000100C4U3V3000640000E1;")
    b["name"] = "green"
    (tmp_path / "red.json").write_text(json.dumps(a))
    (tmp_path / "green.json").write_text(json.dumps(b))

    groups = animations.group_presets(tmp_path)
    assert len(groups) == 1
    g = groups[0]
    assert len(g.members) == 2
    names = sorted(m.name for m in g.members)
    assert names == ["green", "red"]


def test_group_presets_separates_different_signatures(tmp_path):
    # Differentiate at the palette count digit so the difference falls
    # within the 40-char fingerprint cutoff.
    a = _single_frame_preset("N01:P10001FFFFFFF21000100C4U3V3000640000E1;")  # N=1
    a["name"] = "calm"
    b = _single_frame_preset("N01:P10003FFFFFFFFFFFFFFFFFFF210003005800066006CU3V3000640000E1;")  # N=3
    b["name"] = "pulse"
    (tmp_path / "calm.json").write_text(json.dumps(a))
    (tmp_path / "pulse.json").write_text(json.dumps(b))

    groups = animations.group_presets(tmp_path)
    assert len(groups) == 2


def test_group_presets_default_palette_from_first_member(tmp_path):
    p = _single_frame_preset("N01:P10002FF000000FF00F210002005800C0U3V3000640000E1;")
    p["name"] = "calm"
    (tmp_path / "calm.json").write_text(json.dumps(p))
    groups = animations.group_presets(tmp_path)
    assert len(groups) == 1
    # Two-color palette: FF0000 and 00FF00.
    assert groups[0].default_palette == ["FF0000", "00FF00"]


def test_group_presets_id_is_stable_hex(tmp_path):
    p = _single_frame_preset("N01:P10001FF0000F21000100C4U3V3000640000E1;")
    p["name"] = "x"
    (tmp_path / "x.json").write_text(json.dumps(p))
    groups = animations.group_presets(tmp_path)
    # ID is a short hex string (8 chars).
    assert len(groups[0].id) == 8
    assert all(c in "0123456789abcdef" for c in groups[0].id)


def test_group_presets_default_name_is_first_member(tmp_path):
    a = _single_frame_preset("N01:P10001FF0000F21000100C4U3V3000640000E1;")
    a["name"] = "alpha"
    b = _single_frame_preset("N01:P10001FF0000F21000100C4U3V3000640000E1;")
    b["name"] = "bravo"
    (tmp_path / "alpha.json").write_text(json.dumps(a))
    (tmp_path / "bravo.json").write_text(json.dumps(b))
    groups = animations.group_presets(tmp_path)
    # First-by-name member's name becomes the default group name.
    assert groups[0].name == "alpha"


def test_group_presets_empty_dir_returns_empty_list(tmp_path):
    assert animations.group_presets(tmp_path) == []


def test_group_presets_member_includes_frame_stats(tmp_path):
    # 3 frames; 2 share fingerprint (same N=1), 1 differs (N=3) — so unique=2.
    p = _multi_frame_preset([
        "N01:P10001FF0000F21000100C4U3V3000640000E1;",
        "N01:P10001FF0000F21000100C4U3V3000640000E1;",
        "N01:P10003FFFFFFFFFFFFFFFFFFF210003005800066006CU3V3000640000E1;",
    ])
    p["name"] = "x"
    (tmp_path / "x.json").write_text(json.dumps(p))
    groups = animations.group_presets(tmp_path)
    m = groups[0].members[0]
    assert m.frame_stats == {"total": 3, "unique": 2}


# --- apply_overrides --------------------------------------------------------


def _anim(id_: str, name: str, members: list) -> animations.Animation:
    return animations.Animation(id=id_, name=name, members=members)


def _member(name: str) -> animations.PresetMember:
    return animations.PresetMember(name=name, palette=[], frame_stats={"total": 1, "unique": 1})


def test_apply_overrides_renames_group():
    groups = [_anim("aaaaaaaa", "old", [_member("x")])]
    out = animations.apply_overrides(groups, {"aaaaaaaa": {"name": "new"}})
    assert len(out) == 1
    assert out[0].name == "new"


def test_apply_overrides_merges_alias_into_target():
    groups = [
        _anim("aaaaaaaa", "main", [_member("a")]),
        _anim("bbbbbbbb", "alias", [_member("b")]),
    ]
    out = animations.apply_overrides(groups, {"bbbbbbbb": {"alias_of": "aaaaaaaa"}})
    assert len(out) == 1
    merged = out[0]
    assert merged.id == "aaaaaaaa"
    member_names = sorted(m.name for m in merged.members)
    assert member_names == ["a", "b"]


def test_apply_overrides_merge_keeps_target_name():
    groups = [
        _anim("aaaaaaaa", "kept", [_member("a")]),
        _anim("bbbbbbbb", "discarded", [_member("b")]),
    ]
    out = animations.apply_overrides(groups, {"bbbbbbbb": {"alias_of": "aaaaaaaa"}})
    assert out[0].name == "kept"


def test_apply_overrides_merge_target_can_be_renamed():
    # An alias merge + a rename on the target can coexist.
    groups = [
        _anim("aaaaaaaa", "auto", [_member("a")]),
        _anim("bbbbbbbb", "auto", [_member("b")]),
    ]
    overrides = {
        "aaaaaaaa": {"name": "Renamed Target"},
        "bbbbbbbb": {"alias_of": "aaaaaaaa"},
    }
    out = animations.apply_overrides(groups, overrides)
    assert len(out) == 1
    assert out[0].name == "Renamed Target"


def test_apply_overrides_alias_to_unknown_target_keeps_orphan():
    # If alias_of points at a non-existent id, leave the group as-is
    # rather than dropping it.
    groups = [_anim("aaaaaaaa", "lonely", [_member("a")])]
    out = animations.apply_overrides(groups, {"aaaaaaaa": {"alias_of": "zzzzzzzz"}})
    assert len(out) == 1
    assert out[0].id == "aaaaaaaa"


def test_apply_overrides_no_overrides_returns_groups_unchanged():
    groups = [_anim("aaaaaaaa", "x", [_member("a")])]
    out = animations.apply_overrides(groups, {})
    assert out == groups


def test_apply_overrides_none_dict_treated_as_empty():
    groups = [_anim("aaaaaaaa", "x", [_member("a")])]
    out = animations.apply_overrides(groups, None)
    assert out == groups


# --- HTTP layer --------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_animations_returns_grouped_list(tmp_path, monkeypatch):
    """GET /api/animations returns a JSON list of grouped animations."""
    from web import server as workshop

    # Point the server at a tmp presets dir + tmp overrides file.
    monkeypatch.setattr(workshop, "_PRESETS_DIR", tmp_path)
    monkeypatch.setattr(workshop, "_ANIMATIONS_OVERRIDES_PATH", tmp_path / "animations.json")

    # Write one preset.
    p = {"name": "alpha", "payload": {"d50": "N01:P10001FF0000F21000100C4U3V3000640000E1;"}}
    (tmp_path / "alpha.json").write_text(json.dumps(p))

    resp = await workshop.api_animations(None)
    body = json.loads(resp.body.decode() if isinstance(resp.body, bytes) else resp.body)
    assert "animations" in body
    assert len(body["animations"]) == 1
    assert body["animations"][0]["name"] == "alpha"


@pytest.mark.asyncio
async def test_api_animation_rename_persists_to_overrides(tmp_path, monkeypatch):
    """POST /api/animations/{id}/rename writes to animations.json."""
    from web import server as workshop

    monkeypatch.setattr(workshop, "_PRESETS_DIR", tmp_path)
    overrides_path = tmp_path / "animations.json"
    monkeypatch.setattr(workshop, "_ANIMATIONS_OVERRIDES_PATH", overrides_path)

    class _FakeReq:
        match_info = {"id": "deadbeef"}
        async def json(self): return {"name": "MyCustomName"}

    resp = await workshop.api_animation_rename(_FakeReq())
    body = json.loads(resp.body.decode() if isinstance(resp.body, bytes) else resp.body)
    assert body["ok"] is True
    assert overrides_path.exists()
    stored = json.loads(overrides_path.read_text())
    assert stored["deadbeef"]["name"] == "MyCustomName"


@pytest.mark.asyncio
async def test_api_animation_save_creates_recolored_preset(tmp_path, monkeypatch):
    """POST /api/animations/{id}/save writes a new preset file under presets/."""
    from web import server as workshop

    monkeypatch.setattr(workshop, "_PRESETS_DIR", tmp_path)
    monkeypatch.setattr(workshop, "_ANIMATIONS_OVERRIDES_PATH", tmp_path / "animations.json")
    monkeypatch.setattr(workshop, "_PROJECT_ROOT", tmp_path)  # path output uses relative_to

    # Source preset: 2 colors -> red + green.
    src_d50 = "N01:P10002FF000000FF00F210002005800066U3V3000640000E1;"
    src = {"name": "src", "payload": {"d50": src_d50}}
    (tmp_path / "src.json").write_text(json.dumps(src))

    # Find the animation id by running group_presets directly.
    groups = animations.group_presets(tmp_path)
    assert len(groups) == 1
    aid = groups[0].id

    class _FakeReq:
        match_info = {"id": aid}
        async def json(self):
            return {"name": "my-blue", "palette": ["0000FF", "FFFF00"]}

    resp = await workshop.api_animation_save(_FakeReq())
    body = json.loads(resp.body.decode() if isinstance(resp.body, bytes) else resp.body)
    assert body["ok"] is True, body
    out_path = tmp_path / "my-blue.json"
    assert out_path.exists()
    new = json.loads(out_path.read_text())
    # The new preset's d50 should contain the new colors and NOT the old ones.
    new_d50 = new["payload"]["d50"]
    assert "0000FF" in new_d50
    assert "FFFF00" in new_d50
    assert "FF0000" not in new_d50
    assert "00FF00" not in new_d50
