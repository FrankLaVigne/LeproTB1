# Animations Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 5th cockpit tab "Animations" that lists deduped motion patterns derived from `presets/*.json`, lets you rename groups, manually merge two groups that should be one, and recolor a group into a new saved preset.

**Architecture:** Pure dedup + grouping logic lives in a new `web/animations.py` module (fingerprint = first ~40 chars of palette-stripped d50 per frame, joined per preset, hashed). Manual overrides live in `animations.json` at the project root (gitignored). New routes on `web/server.py`: `GET /animations` (page), `GET /api/animations`, `POST /api/animations/{id}/rename`, `POST /api/animations/{id}/merge_into/{other_id}`, `POST /api/animations/{id}/save`. Recolor reuses the existing palette-substitution path that the Presets tab already uses for `api_preview`. The Animations tab is the 5th tab in the cockpit shell.

**Tech Stack:** Python 3.12, aiohttp, vanilla HTML/CSS/JS. No new deps.

---

## File Structure

- `web/animations.py` (**new**, ~180 lines) — pure module: `frame_fingerprint`, `preset_signature`, `per_preset_frame_stats`, `Animation` + `PresetMember` dataclasses, `group_presets(presets_dir)`, `apply_overrides(groups, overrides)`. Zero web/aiohttp imports — fully testable in isolation.
- `web/server.py` (**modify**, net ~+330 lines) — 4 new route handlers, new `_PANEL_ANIMATIONS` constant, 5th tab in shell (`_SHELL_TEMPLATE` + `_render_shell`), route registration. Reuses the existing palette-substitution helper from `api_preview` for the recolor path.
- `web/static/cockpit.css` (**modify**, +~45 lines) — animation-row + expanded-view styles, scoped under `.anim-` prefix to avoid collisions.
- `tests/test_animations.py` (**new**, ~120 lines) — pure-function tests on every helper in `web/animations.py`, plus 3 HTTP smoke tests for the new routes (rename persists, save creates a preset, /api/animations returns the grouped structure).
- `.gitignore` (**modify**, +1 line) — add `animations.json`.
- `README.md` (**modify**) — one paragraph in the Web UI (cockpit) section mentioning the Animations tab.

Nine tasks below — pure module first (TDD), then routes, then page UI + 5th tab, then docs.

---

### Task 1: `frame_fingerprint` pure function

**Files:**
- Create: `web/animations.py`
- Create: `tests/test_animations.py`

The fingerprint strips palette colors from the d50 string and truncates to 40 chars. Palette colors appear as `P1000{N}{N*6 hex chars}` — replace the hex with `C` repeated. Everything else (effect tail, lengths, mode bytes) stays.

- [ ] **Step 1: Create the failing test**

Create `/home/frank/lepro/tests/test_animations.py`:

```python
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
```

- [ ] **Step 2: Run the tests — confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_animations.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'web.animations'`.

- [ ] **Step 3: Implement the function**

Create `/home/frank/lepro/web/animations.py`:

```python
"""Dedup + grouping helpers for the Animations tab.

Given the captured presets in ``presets/*.json``, build a list of unique
animation groups. Each group's id is a hash of the per-frame fingerprint
sequence; presets that share an id are considered the same underlying
motion (palette varies, motion doesn't). See
``docs/superpowers/specs/2026-06-01-animations-tab-design.md`` for the
working hypothesis behind this design — the "preset = one animation"
framing is conjecture, not verified fact.
"""

from __future__ import annotations

import re

_FINGERPRINT_LEN = 40
# Match P1000<N><N*6 hex> exactly — N is a single digit.
_PALETTE_RE = re.compile(r"P1000(\d)([0-9A-Fa-f]+)")


def frame_fingerprint(d50: str) -> str:
    """Return the per-frame structural fingerprint of a d50 string.

    Strips the palette colors (the hex bytes inside ``P1000{N}{colors}``)
    and replaces them with C placeholders so that two frames with the same
    structure but different colors share a fingerprint. Truncated to
    ``_FINGERPRINT_LEN`` so late-frame randomness from the Lepro AI doesn't
    cause false negatives.
    """
    if not d50:
        return ""

    def replace(m: re.Match) -> str:
        n = int(m.group(1))
        existing = m.group(2)
        # Replace the first n*6 hex chars; keep anything after intact.
        keep = existing[n * 6:]
        return f"P1000{n}" + ("C" * (n * 6)) + keep

    stripped = _PALETTE_RE.sub(replace, d50, count=1)
    return stripped[:_FINGERPRINT_LEN]
```

- [ ] **Step 4: Run the tests — confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_animations.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures (186 + 6 = 192 passing).

- [ ] **Step 6: Commit**

```bash
git add web/animations.py tests/test_animations.py
git commit -m "feat(animations): frame_fingerprint (pure: d50 -> structure hash)"
```

---

### Task 2: `preset_signature` + `per_preset_frame_stats`

**Files:**
- Modify: `web/animations.py` (append)
- Modify: `tests/test_animations.py` (append)

A preset is either single-frame (`{"payload": {"d50": ...}}`) or multi-frame (`{"frames": [{"d50": ...}, ...]}`). The signature is the join of per-frame fingerprints with `|`. The frame_stats return `{total: int, unique: int}`.

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_animations.py`:

```python


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
    # 5 frames; 3 distinct fingerprints (frame 0 == frame 3 == frame 4).
    f_red   = "N01:P10001FF0000F21000100C4U3V3000640000E1;"
    f_green = "N01:P10001"+"00FF00"+"F21000100C4U3V3000640000E1;"
    f_blue  = "N01:P10001"+"0000FF"+"F21000100C4U3V3000640000E1;"
    preset = _multi_frame_preset([f_red, f_green, f_blue, f_red, f_red])
    # All five have the same palette-stripped fingerprint because they share
    # structure; we expect unique == 1.
    stats = animations.per_preset_frame_stats(preset)
    assert stats == {"total": 5, "unique": 1}


def test_per_preset_frame_stats_distinct_structures():
    # Two different effect tails -> two different fingerprints.
    f_steady  = "N01:P10001FFFFFFF21000100C4U3V3000640000E1;"
    f_breathe = "N01:P10001FFFFFFF21000100C4U3V3000640000E40088000000881664;"
    preset = _multi_frame_preset([f_steady, f_steady, f_breathe])
    stats = animations.per_preset_frame_stats(preset)
    assert stats == {"total": 3, "unique": 2}


def test_per_preset_frame_stats_empty_returns_zero_zero():
    assert animations.per_preset_frame_stats({}) == {"total": 0, "unique": 0}
    assert animations.per_preset_frame_stats({"frames": []}) == {"total": 0, "unique": 0}
```

- [ ] **Step 2: Run the tests — confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_animations.py -k "preset_signature or per_preset_frame_stats" -v`
Expected: FAIL with `AttributeError: module 'web.animations' has no attribute 'preset_signature'`.

- [ ] **Step 3: Implement both functions** — append to `web/animations.py`:

```python
def _preset_frames(preset: dict) -> list:
    """Return the list of frame dicts inside a preset, regardless of shape."""
    if "frames" in preset:
        return preset.get("frames") or []
    payload = preset.get("payload")
    return [payload] if payload else []


def preset_signature(preset: dict) -> str:
    """Pipe-joined per-frame fingerprints. Single-frame presets have no pipes."""
    frames = _preset_frames(preset)
    if not frames:
        return ""
    return "|".join(frame_fingerprint(f.get("d50", "")) for f in frames)


def per_preset_frame_stats(preset: dict) -> dict:
    """Count total frames and distinct frame fingerprints within one preset."""
    frames = _preset_frames(preset)
    if not frames:
        return {"total": 0, "unique": 0}
    fps = [frame_fingerprint(f.get("d50", "")) for f in frames]
    return {"total": len(fps), "unique": len(set(fps))}
```

- [ ] **Step 4: Run the tests — confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_animations.py -k "preset_signature or per_preset_frame_stats" -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add web/animations.py tests/test_animations.py
git commit -m "feat(animations): preset_signature + per_preset_frame_stats"
```

---

### Task 3: `Animation` dataclass + `group_presets`

**Files:**
- Modify: `web/animations.py` (append)
- Modify: `tests/test_animations.py` (append)

`group_presets(presets_dir)` scans `*.json` files, computes signatures, groups by sig, and returns a list of `Animation` records with stable ordering (by member name, alphabetical).

- [ ] **Step 1: Append the failing tests**

```python


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
    a = _single_frame_preset("N01:P10001FFFFFFF21000100C4U3V3000640000E1;")  # steady tail
    a["name"] = "calm"
    b = _single_frame_preset("N01:P10001FFFFFFF21000100C4U3V3000640000E40088000000881664;")  # breathe tail
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
    p = _multi_frame_preset([
        "N01:P10001FF0000F21000100C4U3V3000640000E1;",
        "N01:P10001FF0000F21000100C4U3V3000640000E1;",
        "N01:P10001FF0000F21000100C4U3V3000640000E40088000000881664;",
    ])
    p["name"] = "x"
    (tmp_path / "x.json").write_text(json.dumps(p))
    groups = animations.group_presets(tmp_path)
    m = groups[0].members[0]
    assert m.frame_stats == {"total": 3, "unique": 2}
```

- [ ] **Step 2: Run the tests — confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_animations.py -k "group_presets" -v`
Expected: FAIL on the first test with `AttributeError`.

- [ ] **Step 3: Implement** — append to `web/animations.py`:

```python
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class PresetMember:
    """One captured preset that maps to an Animation group."""

    name: str                   # filename stem
    palette: list               # 6-hex colors from the first frame
    frame_stats: dict           # {"total": N, "unique": K}


@dataclass
class Animation:
    """A deduped motion group: one or more presets sharing the same signature."""

    id: str                          # 8-char hex hash of the signature
    name: str                        # display name (auto from first member, override-able)
    members: list                    # list[PresetMember]
    default_palette: list = field(default_factory=list)


def _extract_first_palette(preset: dict) -> list:
    """Return the palette from the first frame as a list of 6-hex strings."""
    frames = _preset_frames(preset)
    if not frames:
        return []
    d50 = frames[0].get("d50", "")
    m = _PALETTE_RE.search(d50)
    if not m:
        return []
    n = int(m.group(1))
    colors = m.group(2)[:n * 6]
    return [colors[i * 6:i * 6 + 6].upper() for i in range(n)]


def group_presets(presets_dir: Path) -> list:
    """Scan presets_dir for *.json files, group by motion signature.

    Returns a list of ``Animation`` records sorted by id for stable output.
    Members within each group are sorted by name (alphabetical) and the
    first-by-name member's name becomes the group's default name.
    """
    presets_dir = Path(presets_dir)
    by_sig: dict = {}
    for path in sorted(presets_dir.glob("*.json")):
        try:
            preset = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        sig = preset_signature(preset)
        if not sig:
            continue
        by_sig.setdefault(sig, []).append((path.stem, preset))

    out = []
    for sig, items in sorted(by_sig.items()):
        items.sort(key=lambda p: p[0])  # alphabetical by stem
        sig_id = hashlib.sha1(sig.encode()).hexdigest()[:8]
        first_name, first_preset = items[0]
        members = [
            PresetMember(
                name=name,
                palette=_extract_first_palette(preset),
                frame_stats=per_preset_frame_stats(preset),
            )
            for name, preset in items
        ]
        out.append(Animation(
            id=sig_id,
            name=first_name,
            members=members,
            default_palette=_extract_first_palette(first_preset),
        ))
    out.sort(key=lambda a: a.id)
    return out
```

- [ ] **Step 4: Run the tests — confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_animations.py -k "group_presets" -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add web/animations.py tests/test_animations.py
git commit -m "feat(animations): Animation + PresetMember dataclasses + group_presets"
```

---

### Task 4: `apply_overrides` — rename + merge support

**Files:**
- Modify: `web/animations.py` (append)
- Modify: `tests/test_animations.py` (append)

The overrides dict shape: `{"<animation_id>": {"name": "Tour", "alias_of": "<other_id>"}}`. Either field is optional. If `alias_of` is set, the group merges into the target group (members union, alias's name discarded unless target has no name override).

- [ ] **Step 1: Append the failing tests**

```python


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
```

- [ ] **Step 2: Run the tests — confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_animations.py -k "apply_overrides" -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement** — append to `web/animations.py`:

```python
def apply_overrides(groups: list, overrides: Optional[dict]) -> list:
    """Apply rename + alias_of overrides to a list of Animation groups.

    Overrides shape::
        {"<animation_id>": {"name": "Tour", "alias_of": "<other_id>"}}

    Either field is optional. If ``alias_of`` is set AND points at a real
    other group in the list, the aliased group's members are folded into
    the target's members and the alias is dropped from output. The target's
    own ``name`` override (if any) wins; the alias's name is discarded.
    """
    if not overrides:
        return list(groups)

    by_id = {g.id: g for g in groups}
    drop_ids = set()
    for src_id, ov in overrides.items():
        target_id = (ov or {}).get("alias_of")
        if not target_id or target_id not in by_id or src_id not in by_id:
            continue
        if src_id == target_id:
            continue
        target = by_id[target_id]
        source = by_id[src_id]
        target.members = list(target.members) + list(source.members)
        # Re-sort target members for stable output.
        target.members.sort(key=lambda m: m.name)
        drop_ids.add(src_id)

    out = []
    for g in groups:
        if g.id in drop_ids:
            continue
        name_override = (overrides.get(g.id) or {}).get("name")
        if name_override:
            g.name = name_override
        out.append(g)
    return out
```

- [ ] **Step 4: Run the tests — confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_animations.py -k "apply_overrides" -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add web/animations.py tests/test_animations.py
git commit -m "feat(animations): apply_overrides (manual rename + merge)"
```

---

### Task 5: Backend routes — `/api/animations`, rename, merge, save

**Files:**
- Modify: `web/server.py`
- Modify: `.gitignore`

This task adds the four `/api/animations*` routes plus the page route `GET /animations`. The page itself is a placeholder for now (Task 6 lands the real UI). Adding the page route here lets us wire it into the shell's tab strip in Task 7 without a second edit pass.

- [ ] **Step 1: Add the import + module-level overrides path**

Find the existing `_HERE` / `_PROJECT_ROOT` / `_PRESETS_DIR` block in `web/server.py` (around line 274). Right after it (or right after the `_SHELL_TEMPLATE` block introduced by the cockpit redesign), add:

```python
# --- Animations tab — see docs/superpowers/specs/2026-06-01-animations-tab-design.md
from web import animations as _animations_mod

_ANIMATIONS_OVERRIDES_PATH = _PROJECT_ROOT / "animations.json"


def _read_animation_overrides() -> dict:
    """Read animations.json if it exists, return {} otherwise."""
    if not _ANIMATIONS_OVERRIDES_PATH.exists():
        return {}
    try:
        return json.loads(_ANIMATIONS_OVERRIDES_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_animation_overrides(d: dict) -> None:
    _ANIMATIONS_OVERRIDES_PATH.write_text(json.dumps(d, indent=2) + "\n")


def _grouped_animations() -> list:
    """Return the override-applied list of Animation groups."""
    groups = _animations_mod.group_presets(_PRESETS_DIR)
    return _animations_mod.apply_overrides(groups, _read_animation_overrides())


def _animation_to_json(anim) -> dict:
    return {
        "id": anim.id,
        "name": anim.name,
        "default_palette": anim.default_palette,
        "members": [
            {"name": m.name, "palette": m.palette, "frame_stats": m.frame_stats}
            for m in anim.members
        ],
    }
```

- [ ] **Step 2: Add the page handler + placeholder constant**

Put these right after the helpers from Step 1:

```python
async def index_animations(_req):
    return web.Response(text=_render_shell("animations", _PANEL_ANIMATIONS, "Animations"),
                        content_type="text/html")


# Replaced by the real UI in Task 6.
_PANEL_ANIMATIONS = "<p>animations panel loading...</p>"
```

- [ ] **Step 3: Add the four API handlers**

Right after `index_animations`, add:

```python
async def api_animations(_req):
    """Return the deduped + override-applied animation catalog."""
    groups = _grouped_animations()
    return web.json_response({"animations": [_animation_to_json(g) for g in groups]})


async def api_animation_rename(req):
    """Set a custom display name for an animation group."""
    aid = req.match_info["id"]
    try:
        body = await req.json()
        name = str(body.get("name") or "").strip()
        if not name:
            return web.json_response({"ok": False, "error": "name required"}, status=400)
        overrides = _read_animation_overrides()
        entry = overrides.setdefault(aid, {})
        entry["name"] = name
        _write_animation_overrides(overrides)
        return web.json_response({"ok": True})
    except (ValueError, KeyError, TypeError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def api_animation_merge(req):
    """Declare animation {id} as an alias of {other_id}; the two groups fold into one."""
    aid = req.match_info["id"]
    target = req.match_info["other_id"]
    if aid == target:
        return web.json_response({"ok": False, "error": "cannot merge into self"}, status=400)
    overrides = _read_animation_overrides()
    entry = overrides.setdefault(aid, {})
    entry["alias_of"] = target
    _write_animation_overrides(overrides)
    return web.json_response({"ok": True})


async def api_animation_save(req):
    """Recolor an animation's default preset with a user-supplied palette
    and save the result as a new file in presets/."""
    aid = req.match_info["id"]
    try:
        body = await req.json()
        name = _sanitize_name(body["name"])
        palette = list(body.get("palette") or [])
        groups = _grouped_animations()
        anim = next((g for g in groups if g.id == aid), None)
        if anim is None:
            return web.json_response({"ok": False, "error": f"animation {aid!r} not found"}, status=404)
        if len(palette) != len(anim.default_palette):
            return web.json_response(
                {"ok": False,
                 "error": f"palette length {len(palette)} != animation palette length {len(anim.default_palette)}"},
                status=400)
        for c in palette:
            if not _HEX6.match(c):
                return web.json_response({"ok": False, "error": f"color {c!r} is not 6-hex"}, status=400)
        # Source preset: the first member's file on disk.
        source_name = anim.members[0].name
        source_path = _PRESETS_DIR / f"{source_name}.json"
        if not source_path.exists():
            return web.json_response({"ok": False, "error": f"source preset {source_name} missing"}, status=500)
        source = json.loads(source_path.read_text())
        recolored = _recolor_preset(source, anim.default_palette, palette)
        recolored["name"] = name
        recolored["description"] = f"Recolored variant of {source_name} via Animations tab."
        recolored["prompt"] = f"animations:{aid}"
        from datetime import date
        recolored["captured"] = date.today().isoformat()
        out_path = _PRESETS_DIR / f"{name}.json"
        if out_path.exists():
            return web.json_response({"ok": False, "error": f"preset {name!r} already exists"}, status=400)
        out_path.write_text(json.dumps(recolored, indent=2) + "\n")
        return web.json_response({"ok": True, "path": str(out_path.relative_to(_PROJECT_ROOT))})
    except (KeyError, ValueError, TypeError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
```

- [ ] **Step 4: Add `_recolor_preset` helper if missing**

If `_recolor_preset` doesn't already exist as a standalone helper (it might be inline in `api_preview` today), extract it. Grep first:

```bash
grep -n "_recolor_preset\|def recolor\b\|recolor.*preset" web/server.py | head -5
```

If there's no standalone `_recolor_preset(preset, old_palette, new_palette) -> dict`, extract one from the existing `api_preview` logic. The substitution rule (proven by the project's existing palette-isolation experiments): for each color in `old_palette`, replace every occurrence of that color in EVERY frame's `d50` palette section with the corresponding new color. The existing palette regex `_PALETTE_RE` from `web/animations.py` (Task 1) can help locate the palette section in each d50.

Minimal `_recolor_preset` implementation, place it near `_sanitize_name` in `web/server.py`:

```python
def _recolor_preset(preset: dict, old_palette: list, new_palette: list) -> dict:
    """Return a deep copy of ``preset`` with palette colors mapped old → new.

    Only the P1000<N><colors> section of each frame's d50 is modified;
    motion/length/effect fields are untouched. The mapping is positional:
    ``old_palette[i]`` → ``new_palette[i]``.
    """
    import copy
    out = copy.deepcopy(preset)
    if len(old_palette) != len(new_palette):
        raise ValueError("palette length mismatch")
    mapping = {old.upper(): new.upper() for old, new in zip(old_palette, new_palette)}

    def remap_d50(d50: str) -> str:
        if not d50:
            return d50
        m = re.search(r"P1000(\d)([0-9A-Fa-f]+)", d50)
        if not m:
            return d50
        n = int(m.group(1))
        head = d50[:m.end(1)]   # everything up to and including the count digit
        original = m.group(2)
        block = original[:n * 6]
        tail = d50[m.end(1) + len(block):]
        # Map each 6-hex slot through the mapping.
        new_block = ""
        for i in range(n):
            slot = block[i * 6:i * 6 + 6].upper()
            new_block += mapping.get(slot, slot)
        return head + new_block + tail

    if "frames" in out:
        for f in out.get("frames") or []:
            f["d50"] = remap_d50(f.get("d50", ""))
    elif "payload" in out:
        out["payload"]["d50"] = remap_d50(out["payload"].get("d50", ""))
    return out
```

Make sure `import re` is at the top of `web/server.py` (it should be — the file already uses regex elsewhere; if not, add it).

- [ ] **Step 5: Register the routes**

In `build_app`'s `app.add_routes([...])` list, add these five entries (after the existing `web.get("/clock", index_clock),` line is a good spot):

```python
        web.get("/animations", index_animations),
        web.get("/api/animations", api_animations),
        web.post(r"/api/animations/{id}/rename", api_animation_rename),
        web.post(r"/api/animations/{id}/merge_into/{other_id}", api_animation_merge),
        web.post(r"/api/animations/{id}/save", api_animation_save),
```

- [ ] **Step 6: Add `animations.json` to .gitignore**

Append to `/home/frank/lepro/.gitignore`:

```
animations.json
```

- [ ] **Step 7: Smoke-test routes count + endpoints**

Run:
```bash
.venv/bin/python -c "
import workshop_server  # ignored; just ensure import works
" 2>/dev/null || true
.venv/bin/python -c "
from web import server
app = server.build_app()
routes = sorted(r.method + ' ' + str(r.resource.canonical) for r in app.router.routes())
need = ['GET /animations', 'GET /api/animations',
        'POST /api/animations/{id}/rename',
        'POST /api/animations/{id}/merge_into/{other_id}',
        'POST /api/animations/{id}/save']
for n in need:
    assert n in routes, f'missing: {n}'
print('all 5 animation routes registered; total routes:', len(routes))
"
```

Expected: prints `all 5 animation routes registered; total routes: 35` (or similar — was 33 + 5 minus implicit-HEAD overlaps).

- [ ] **Step 8: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 9: Commit**

```bash
git add web/server.py .gitignore
git commit -m "feat(animations): backend routes + _recolor_preset helper + .gitignore"
```

---

### Task 6: Page UI — `_PANEL_ANIMATIONS`

**Files:**
- Modify: `web/server.py` (replace placeholder `_PANEL_ANIMATIONS` with the full inline HTML/CSS/JS)
- Modify: `web/static/cockpit.css` (append .anim-* styles)

The panel renders the animation list, each row expandable to a recolor form. Vanilla DOM manipulation, fetch for API calls. No external JS deps.

- [ ] **Step 1: Add .anim-* styles to cockpit.css**

Append to `/home/frank/lepro/web/static/cockpit.css`:

```css
/* === Animations tab ====================================================== */

.anim-row {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: var(--gap);
  margin-bottom: var(--gap-sm);
  display: grid;
  grid-template-columns: 1fr auto;
  gap: var(--gap-sm) var(--gap);
  align-items: center;
}
.anim-row .anim-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}
.anim-row .anim-meta {
  grid-column: 1 / -1;
  display: flex;
  gap: var(--gap-sm);
  align-items: center;
  font-size: 11px;
  color: var(--text-dim);
}
.anim-row .anim-palette {
  display: flex;
  gap: 4px;
}
.anim-row .anim-palette .swatch {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 1px solid var(--border);
}
.anim-row .anim-actions {
  display: flex;
  gap: var(--gap-xs);
}
.anim-row .anim-actions button {
  padding: 6px 12px;
  border: 1px solid var(--border);
  background: var(--panel-hi);
  color: var(--text);
  border-radius: var(--r-sm);
  font-size: 11px;
}
.anim-row .anim-actions button:hover { background: var(--accent-soft); color: var(--accent); }

.anim-expanded {
  grid-column: 1 / -1;
  padding-top: var(--gap);
  margin-top: var(--gap);
  border-top: 1px solid var(--border);
  display: none;
  flex-direction: column;
  gap: var(--gap);
}
.anim-row.expanded .anim-expanded { display: flex; }

.anim-pickers { display: flex; flex-wrap: wrap; gap: var(--gap-sm); }
.anim-pickers input[type=color] {
  width: 40px; height: 32px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: none;
  padding: 0;
  cursor: pointer;
}

.anim-saverow {
  display: flex;
  gap: var(--gap-sm);
  align-items: center;
}
.anim-saverow input[type=text] {
  flex: 1;
  padding: 6px 10px;
  border: 1px solid var(--border);
  background: var(--panel-hi);
  color: var(--text);
  border-radius: var(--r-sm);
  font: inherit;
}

.anim-variants {
  font-size: 12px;
  color: var(--text-dim);
}
.anim-variants .v {
  display: inline-block;
  margin: 2px 4px 2px 0;
  padding: 2px 8px;
  background: var(--panel-hi);
  border-radius: var(--r-pill);
  font: 11px ui-monospace, monospace;
}

#anim-empty { color: var(--text-dim); text-align: center; padding: var(--gap-xl); font-style: italic; }
#anim-status { font-size: 12px; color: var(--text-dim); margin-top: var(--gap); min-height: 1.2em; }
```

- [ ] **Step 2: Replace `_PANEL_ANIMATIONS` with the full inline page**

Find the line in `web/server.py`:

```python
_PANEL_ANIMATIONS = "<p>animations panel loading...</p>"
```

Replace with:

```python
_PANEL_ANIMATIONS = """
<div id="anim-list"></div>
<div id="anim-empty" style="display:none">
  No animations yet. Capture some via
  <code>python -m cli.main capture --seconds 90</code>
  and they'll appear here grouped by motion pattern.
</div>
<div id="anim-status"></div>

<script type="module">
const $ = s => document.querySelector(s);
const list = $('#anim-list');
const empty = $('#anim-empty');
const status = $('#anim-status');

function setStatus(msg, isError) {
  status.textContent = msg || '';
  status.style.color = isError ? 'var(--danger)' : '';
}

async function postJSON(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

function paletteSwatches(palette) {
  return palette.map(c => `<span class="swatch" style="background:#${c}"></span>`).join('');
}

function buildRow(anim) {
  const variantCount = anim.members.length;
  const firstStats = (anim.members[0] || {}).frame_stats || {total: 0, unique: 0};
  const stats = firstStats.total > 1
    ? `${firstStats.total} frames (${firstStats.unique} unique)`
    : `${firstStats.total} frame`;

  // Build expanded recolor form.
  const pickerInputs = anim.default_palette.map((c, i) =>
    `<input type="color" value="#${c}" data-idx="${i}">`).join('');
  const variantPills = anim.members.map(m =>
    `<span class="v">${m.name}</span>`).join('');

  const row = document.createElement('div');
  row.className = 'anim-row';
  row.dataset.id = anim.id;
  row.innerHTML = `
    <div class="anim-title" data-action="toggle">${anim.name}</div>
    <div class="anim-actions">
      <button data-action="play">▶ Play</button>
      <button data-action="toggle">✎ Edit</button>
    </div>
    <div class="anim-meta">
      <div class="anim-palette">${paletteSwatches(anim.default_palette)}</div>
      <span>${stats}</span>
      <span>·</span>
      <span>${variantCount} variant${variantCount === 1 ? '' : 's'}</span>
    </div>
    <div class="anim-expanded">
      <div>
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:4px">RENAME</div>
        <div class="anim-saverow">
          <input type="text" data-role="rename" value="${anim.name}">
          <button data-action="rename">Save name</button>
        </div>
      </div>
      <div>
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:4px">RECOLOR PALETTE</div>
        <div class="anim-pickers">${pickerInputs}</div>
      </div>
      <div>
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:4px">SAVE AS NEW PRESET</div>
        <div class="anim-saverow">
          <input type="text" data-role="save-name" placeholder="my-variant">
          <button data-action="save">💾 Save</button>
        </div>
      </div>
      <div>
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:4px">VARIANTS</div>
        <div class="anim-variants">${variantPills}</div>
      </div>
    </div>
  `;
  return row;
}

function attachHandlers(row, anim) {
  // Toggle expansion.
  for (const el of row.querySelectorAll('[data-action="toggle"]')) {
    el.addEventListener('click', () => row.classList.toggle('expanded'));
  }
  // Play: previews the first member preset on the lamp via existing /api/preview.
  row.querySelector('[data-action="play"]').addEventListener('click', async (e) => {
    e.stopPropagation();
    const sourceName = anim.members[0].name;
    const j = await postJSON('/api/preview', {base_name: sourceName});
    setStatus(j.ok === false ? ('error: ' + j.error) : `playing ${sourceName}…`, j.ok === false);
  });
  // Rename.
  row.querySelector('[data-action="rename"]').addEventListener('click', async () => {
    const name = row.querySelector('[data-role="rename"]').value.trim();
    if (!name) { setStatus('name required', true); return; }
    const j = await postJSON(`/api/animations/${anim.id}/rename`, {name});
    if (j.ok === false) { setStatus('error: ' + j.error, true); return; }
    setStatus(`renamed to ${name}`);
    await loadAnimations();
  });
  // Save (recolor).
  row.querySelector('[data-action="save"]').addEventListener('click', async () => {
    const newName = row.querySelector('[data-role="save-name"]').value.trim();
    if (!newName) { setStatus('save name required', true); return; }
    const palette = Array.from(row.querySelectorAll('.anim-pickers input[type=color]'))
      .sort((a, b) => parseInt(a.dataset.idx, 10) - parseInt(b.dataset.idx, 10))
      .map(input => input.value.replace('#', '').toUpperCase());
    const j = await postJSON(`/api/animations/${anim.id}/save`,
                              {name: newName, palette});
    if (j.ok === false) { setStatus('error: ' + j.error, true); return; }
    setStatus(`saved → ${j.path}`);
    await loadAnimations();
  });
}

async function loadAnimations() {
  try {
    const r = await fetch('/api/animations');
    const j = await r.json();
    list.innerHTML = '';
    const items = j.animations || [];
    if (items.length === 0) {
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';
    for (const anim of items) {
      const row = buildRow(anim);
      attachHandlers(row, anim);
      list.appendChild(row);
    }
  } catch (e) {
    setStatus('failed to load animations: ' + e.message, true);
  }
}

loadAnimations();
</script>
"""
```

- [ ] **Step 3: Smoke-test the page constant**

```bash
.venv/bin/python -c "
from web import server
p = server._PANEL_ANIMATIONS
for marker in ('anim-list', 'anim-empty', 'anim-status',
               'loadAnimations', '/api/animations',
               'data-action=\"play\"', 'data-action=\"rename\"',
               'data-action=\"save\"', 'data-action=\"toggle\"'):
    assert marker in p, 'missing: ' + repr(marker)
print('page constant has all required markers')
"
```

Expected: prints `page constant has all required markers`.

- [ ] **Step 4: Full suite**

`.venv/bin/python -m pytest -q` — 0 failures.

- [ ] **Step 5: Commit**

```bash
git add web/server.py web/static/cockpit.css
git commit -m "feat(animations): full _PANEL_ANIMATIONS UI + .anim-* styles"
```

---

### Task 7: 5th tab in the cockpit shell

**Files:**
- Modify: `web/server.py`

The cockpit shell template's tab strip currently has 4 tabs (presets/diy/ticker/clock). Add Animations as a 5th and update `_render_shell` to accept `"animations"` as a valid active key.

- [ ] **Step 1: Find the shell template tab strip**

`grep -n 'cls_presets\|cls_diy\|cls_ticker\|cls_clock' web/server.py | head -10`

The `_SHELL_TEMPLATE` constant has a nav block like:

```html
<a href="/" {cls_presets}>🎨 Presets</a>
<a href="/diy" {cls_diy}>✏️ DIY</a>
<a href="/ticker" {cls_ticker}>📈 Ticker</a>
<a href="/clock" {cls_clock}>⏰ Clock</a>
```

(The exact emoji form might be HTML entities — match what's there.)

- [ ] **Step 2: Add the Animations tab anchor**

Add this anchor RIGHT AFTER the Clock anchor in `_SHELL_TEMPLATE`:

```html
<a href="/animations" {cls_animations}>&#x1F39E;&#xFE0F; Animations</a>
```

(`&#x1F39E;` is 🎞, the film-frames glyph — visually distinct from the other tab emojis.)

- [ ] **Step 3: Update `_render_shell`**

Find the `_render_shell` function. Its `active_classes` dict has 4 keys; add the 5th:

```python
    active_classes = {
        "presets": "",
        "diy": "",
        "ticker": "",
        "clock": "",
        "animations": "",
    }
```

And in the call to `_SHELL_TEMPLATE.format(...)`, add the new placeholder:

```python
    return _SHELL_TEMPLATE.format(
        title=title,
        panel=panel_html,
        cls_presets=active_classes["presets"],
        cls_diy=active_classes["diy"],
        cls_ticker=active_classes["ticker"],
        cls_clock=active_classes["clock"],
        cls_animations=active_classes["animations"],
    )
```

- [ ] **Step 4: Update the shell tests in `tests/test_cockpit_shell.py`**

Append:

```python


def test_render_shell_links_to_animations_tab():
    out = workshop._render_shell(active="animations", panel_html="", title="Animations")
    assert 'href="/animations"' in out
    assert 'href="/animations" class="active"' in out


def test_render_shell_animations_tab_present_on_other_pages():
    # Even when not active, the Animations link must appear on every page.
    out = workshop._render_shell(active="presets", panel_html="", title="Presets")
    assert 'href="/animations"' in out
```

- [ ] **Step 5: Run the shell tests + full suite**

```bash
.venv/bin/python -m pytest tests/test_cockpit_shell.py -v
.venv/bin/python -m pytest -q
```

Expected: both green; full suite has all previous tests + the two new ones.

- [ ] **Step 6: Smoke-test the page renders end-to-end**

```bash
pkill -f "mcphost.server" 2>/dev/null; sleep 1
pkill -f "web.server"    2>/dev/null; sleep 1
nohup .venv/bin/python -u -m web.server > /tmp/anim-smoke.log 2>&1 &
SERVER_PID=$!
sleep 6
ss -tlnp 2>/dev/null | grep 8081
curl -s -o /dev/null -m 3 -w "/animations -> %{http_code}\n"      http://127.0.0.1:8081/animations
curl -s -o /dev/null -m 3 -w "/api/animations -> %{http_code}\n"  http://127.0.0.1:8081/api/animations
curl -s -m 3 http://127.0.0.1:8081/api/animations | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('animation groups:', len(d.get('animations', [])))
"
echo "--- check 5th tab is in served HTML ---"
curl -s http://127.0.0.1:8081/ | grep -c 'href="/animations"'
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
```

Expected: `/animations -> 200`; `/api/animations -> 200`; `animation groups:` shows however many unique motions group_presets() finds (likely 5-6 given the 6 captured presets); 5th-tab grep count ≥ 1 on the Presets page (proving the tab appears across all pages, not just the Animations one).

- [ ] **Step 7: Commit**

```bash
git add web/server.py tests/test_cockpit_shell.py
git commit -m "feat(cockpit): add 5th tab (Animations) to shell"
```

---

### Task 8: HTTP smoke tests for the new routes

**Files:**
- Modify: `tests/test_animations.py` (append)

Pure-function tests in Tasks 1-4 cover the business logic. This task adds 3 minimal HTTP-layer tests proving the routes wire up correctly and persist what they claim.

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_animations.py`:

```python


# --- HTTP layer --------------------------------------------------------------


import pytest


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

    # Source preset: 2 colors -> red + green.
    src_d50 = "N01:P10002FF000000FF00F210002005800066U3V3000640000E1;"
    src = {"name": "src", "payload": {"d50": src_d50}}
    (tmp_path / "src.json").write_text(json.dumps(src))

    # Find the animation id by running group_presets directly.
    from web import animations as anim_mod
    groups = anim_mod.group_presets(tmp_path)
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
```

- [ ] **Step 2: Run the new tests**

```bash
.venv/bin/python -m pytest tests/test_animations.py -k "api_" -v
```

Expected: PASS (3 tests).

- [ ] **Step 3: Full suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: 0 failures.

- [ ] **Step 4: Commit**

```bash
git add tests/test_animations.py
git commit -m "test(animations): HTTP smoke tests for the new routes"
```

---

### Task 9: README + final verify

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Find the cockpit section**

`grep -n "## Web UI (cockpit)" README.md`

It should be around line 141. The four tabs are listed as bullets (🎨 Presets / ✏️ DIY / 📈 Ticker / ⏰ Clock). We're adding a 5th.

- [ ] **Step 2: Add the Animations tab bullet**

Find the existing list block in the README's `## Web UI (cockpit)` section:

```markdown
- **Right panel:** four tabs.
  - **🎨 Presets** — browse / recolor / preview / save captured presets
    ...
  - **✏️ DIY** — ...
  - **📈 Ticker** — ...
  - **⏰ Clock** — ...
```

Replace `four tabs.` with `five tabs.` and add this new bullet after the Clock bullet (and before the next paragraph that starts "While the ticker or clock is running"):

```markdown
  - **🎞 Animations** — the deduped catalog of motion patterns derived from
    your `presets/*.json` library. Click a row to pick new colors and save
    the result as a new preset. Useful when you've captured the same
    Lepro-AI prompt twice with different palettes and want to see they're
    the same motion underneath. Manual rename and merge available via
    `animations.json` (gitignored, written by the tab's UI).
```

- [ ] **Step 3: Verify it landed in the right place**

```bash
grep -B2 -A2 "Animations" README.md | head -20
```

Expected: the new bullet appears within the cockpit section, NOT inside `## Protocol notes` or the `## Files` section further down.

- [ ] **Step 4: Final full suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: 0 failures.

- [ ] **Step 5: Final smoke**

```bash
pkill -f "web.server" 2>/dev/null; sleep 1
nohup .venv/bin/python -u -m web.server > /tmp/anim-final.log 2>&1 &
SERVER_PID=$!
sleep 6
for path in / /diy /ticker /clock /animations /api/animations /api/cockpit/active /static/cockpit.css /static/cockpit.js; do
  curl -s -o /dev/null -m 3 -w "  $path -> %{http_code}\n" "http://127.0.0.1:8081$path"
done
echo "--- /api/animations response ---"
curl -s -m 3 http://127.0.0.1:8081/api/animations | python3 -m json.tool | head -30
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
```

Expected: every path 200; `/api/animations` returns a JSON body listing however many unique motions group_presets() finds across your existing presets.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: document the Animations tab in the cockpit section"
```

---

## Self-Review

**Spec coverage:**
- Pure `frame_fingerprint` (palette stripping + truncation) → Task 1 ✓
- Pure `preset_signature` (per-frame fingerprint join) → Task 2 ✓
- Pure `per_preset_frame_stats` (total + unique counts) → Task 2 ✓
- `Animation` + `PresetMember` dataclasses → Task 3 ✓
- `group_presets(presets_dir)` (scan + group + sort) → Task 3 ✓
- `apply_overrides(groups, overrides)` (rename + merge alias_of) → Task 4 ✓
- `GET /api/animations`, `GET /animations`, rename, merge, save routes → Task 5 ✓
- `_recolor_preset(preset, old_palette, new_palette)` helper → Task 5 (extracted as part of the route work since save needs it) ✓
- `_PANEL_ANIMATIONS` HTML/CSS/JS with row/expanded view/recolor/save/rename → Task 6 ✓
- `.anim-*` styles in cockpit.css → Task 6 ✓
- 5th tab in the cockpit shell template + `_render_shell` updated → Task 7 ✓
- `animations.json` in .gitignore → Task 5 ✓
- HTTP-layer smoke tests → Task 8 ✓
- README updated → Task 9 ✓

**Placeholder scan:** no TBD/TODO/"similar to". Each task has explicit code blocks for all changes. The `_recolor_preset` task (Task 5 Step 4) explicitly says to grep first and only extract if not already present — that's a conditional, not a placeholder; the included reference implementation covers the case where it doesn't exist.

**Type consistency:**
- `Animation(id, name, members, default_palette)` — same across Tasks 3, 4, 5, 6 ✓
- `PresetMember(name, palette, frame_stats)` — same across Tasks 3, 4, 5, 6 ✓
- `frame_stats: {"total": int, "unique": int}` — consistent everywhere ✓
- Overrides format `{"id": {"name": str, "alias_of": str}}` — consistent across Tasks 4, 5 ✓
- Routes: id is a path parameter `{id}` everywhere ✓
- The recolor API body shape `{name: str, palette: list[str]}` — consistent between Task 5 (handler) and Task 6 (UI fetch call) and Task 8 (test) ✓
- DOM hook IDs (`anim-list`, `anim-empty`, `anim-status`, `data-action="play"/"toggle"/"rename"/"save"`) — consistent between Task 6 (template) and Task 6 (JS) ✓

**Notes for the implementer:**
- The `_recolor_preset` helper in Task 5 may already exist in `web/server.py` (the existing Presets tab does recoloring on preview). If it does, reuse it rather than duplicating. The signature in the plan matches what the new save route needs.
- The `import` line `from web import animations as _animations_mod` in Task 5 may sit inside the existing `_HERE`/`_PROJECT_ROOT` block region. Match the surrounding style — the existing code already uses bare module-level imports at the top, not inside function bodies, so this fits.
- The `_PALETTE_RE` regex from `web/animations.py` (Task 1) and the regex inside `_recolor_preset` (Task 5) are functionally identical but defined separately. That's fine for v1; if it grates, a future cleanup could share the constant.
- Task 7's emoji choice (🎞 = `&#x1F39E;`) is intentionally distinct from Presets (🎨), DIY (✏️), Ticker (📈), Clock (⏰). If it renders poorly in the target browser, the previously-used emoji 📼 (`&#x1F4FC;`) is a fallback.
