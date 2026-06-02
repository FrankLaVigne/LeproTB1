# Motion Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A frame-level motion catalog for the Lepro TB1 — every unique animation program captured from the app, deduped by palette-independent signature, playable on the lamp in any user-chosen palette.

**Architecture:** One new pure-Python engine module (`web/motions.py`: palette parsing, signatures, recolor, catalog merge) + four new endpoints and a new Motions tab on the existing workshop server (`web/server.py`) + a project-root `motions.json` catalog database. Capture saves auto-merge into the catalog. Design spec: `docs/superpowers/specs/2026-06-02-motion-library-design.md`.

**Tech Stack:** Python 3.12, aiohttp (existing), pytest + pytest-asyncio (asyncio_mode=auto). No new dependencies.

**Conventions:** All commands run from `/home/frank/lepro`. Python is `.venv/bin/python`. Commit style: `feat(motions): ...`, `fix(...)`, `docs: ...`. All commits end with the trailer:

```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

**Key corpus facts the code relies on** (verified 2026-06-02, see spec):
- Palette blocks are `P1000` + single count digit + count×6 hex chars; multiple blocks per d50 (multi-program N02/N03, per-ring `#I00/#I01/#I02` sections).
- `P4000{digit}` blocks (cyberpunk[21]) have unconfirmed structure → never parsed/recolored.
- `R3` is followed by exactly 5 digits in all 130 corpus occurrences.
- `white-blue-tour.json` = `purple-pink-tour.json` with `8000FF→FFFFFF`, `FFC0CB→0000FF` substituted in palette blocks only (lamp-verified) — the recolor ground truth.

---

### Task 1: `web/motions.py` — palette blocks, formats, signatures

**Files:**
- Create: `web/motions.py`
- Create: `tests/test_motions.py`

- [ ] **Step 1: Write the failing tests**

Create `/home/frank/lepro/tests/test_motions.py`:

```python
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
```

- [ ] **Step 2: Run the tests — confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_motions.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'web.motions'`.

- [ ] **Step 3: Create `web/motions.py`**

Create `/home/frank/lepro/web/motions.py`:

```python
"""Motion library engine — palette-independent identity + recoloring for d50 programs.

A "motion" is one unique animation program: a d50 string with its palette factored
out. The Lepro app renders the same finite set of motion programs in whatever colors
its AI picks; this module gives each program a stable identity (so we can catalog
them) and re-renders any of them in any palette (so the catalog beats the app).

Pure functions + a small JSON-file catalog. No MQTT, no aiohttp.
Spec: docs/superpowers/specs/2026-06-02-motion-library-design.md
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

# A P1000 palette block: 'P1000' + single count digit + count*6 hex color chars.
_P1000_RE = re.compile(r"P1000(\d)")
# Cyberpunk 'P4' variant (P40005e500e5...) — detected, never parsed (structure
# unconfirmed; see docs/D50_FORMAT.md).
_P4_RE = re.compile(r"P4000\d")
# The R3 flags field: R3 + exactly 5 digits (corpus-verified 2026-06-02, 130/130).
# Masked in loose signatures because it varies with palette size for the same motion.
_R3_RE = re.compile(r"R3\d{5}")
_HEX6_RE = re.compile(r"[0-9A-Fa-f]{6}")


@dataclass
class PaletteBlock:
    """One P1000 color block inside a d50 string."""

    start: int        # index of the first color char (right after the count digit)
    count: int
    colors: list      # uppercased 6-hex strings, in block order


def find_palette_blocks(d50) -> list[PaletteBlock]:
    """Locate every P1000 palette block in a d50 string."""
    if not d50 or not isinstance(d50, str):
        return []
    blocks = []
    for m in _P1000_RE.finditer(d50):
        n = int(m.group(1))
        start = m.end()
        chunk = d50[start:start + n * 6]
        if len(chunk) == n * 6 and all(
                _HEX6_RE.fullmatch(chunk[i * 6:(i + 1) * 6]) for i in range(n)):
            blocks.append(PaletteBlock(
                start=start, count=n,
                colors=[chunk[i * 6:(i + 1) * 6].upper() for i in range(n)]))
    return blocks


def extract_palette(d50) -> list[str]:
    """Distinct palette colors in order of first appearance, uppercased."""
    seen: list[str] = []
    for block in find_palette_blocks(d50):
        for c in block.colors:
            if c not in seen:
                seen.append(c)
    return seen


def has_p4_block(d50) -> bool:
    """True if the d50 contains the unconfirmed P4 palette variant."""
    return bool(_P4_RE.search(d50 or ""))


def is_recolorable(d50) -> bool:
    """A d50 is recolorable iff it has at least one P1000 block and no P4 block."""
    return bool(find_palette_blocks(d50)) and not has_p4_block(d50)


def detect_format(d50) -> str:
    """Classify a d50: 'per-ring', 'N01', 'N02', 'N03', or 'unknown'."""
    if not d50 or not isinstance(d50, str):
        return "unknown"
    if "#I0" in d50:
        return "per-ring"
    m = re.search(r"N(\d\d):", d50)
    return f"N{m.group(1)}" if m else "unknown"


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()


def _masked(d50: str, *, mask_counts: bool) -> str:
    """d50 with palette colors masked; optionally counts + R3 digits too."""
    blocks = find_palette_blocks(d50)
    parts = []
    pos = 0
    for b in blocks:
        before = d50[pos:b.start]
        if mask_counts:
            # The count digit is the last char before the colors start.
            parts.append(before[:-1] + "N")
            parts.append("######")               # fixed width regardless of count
        else:
            parts.append(before)
            parts.append("#" * (b.count * 6))    # width preserves count info
        pos = b.start + b.count * 6
    parts.append(d50[pos:])
    out = "".join(parts)
    if mask_counts:
        out = _R3_RE.sub("R3#####", out)
    return out


def motion_signature(d50) -> tuple[str, str]:
    """Return (strict, loose) SHA-1 signatures for a d50 string.

    strict: palette colors masked. Two captures of the same motion with the same
            palette SIZE share a strict signature.
    loose:  strict + palette counts and R3 flag digits masked. Two captures of the
            same motion with ANY palette share a loose signature.
    """
    return (_sha1(_masked(d50, mask_counts=False)),
            _sha1(_masked(d50, mask_counts=True)))
```

- [ ] **Step 4: Run the tests — confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_motions.py -v`
Expected: all PASS (16 tests).

Note: `test_blocks_per_ring` expects colors `["00FF80", ...]` — read carefully: the
raw string has `P1000500FF80...`, i.e. count=5 then colors starting `00FF80`. If this
test fails, check the block parse boundary, not the test.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all PASS (324 existing + 16 new = 340).

- [ ] **Step 6: Commit**

```bash
git add web/motions.py tests/test_motions.py
git commit -m "feat(motions): palette blocks, format detection, strict/loose motion signatures"
```

---

### Task 2: `web/motions.py` — remap_colors + recolor

**Files:**
- Modify: `web/motions.py` (append after `motion_signature`)
- Modify: `tests/test_motions.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `/home/frank/lepro/tests/test_motions.py`:

```python
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
```

- [ ] **Step 2: Run the tests — confirm the new ones fail**

Run: `.venv/bin/python -m pytest tests/test_motions.py -v`
Expected: the 16 Task-1 tests PASS; the 10 new tests FAIL with `AttributeError: module 'web.motions' has no attribute 'remap_colors'`.

- [ ] **Step 3: Append the implementation to `web/motions.py`**

Append to `/home/frank/lepro/web/motions.py`:

```python
# --- recoloring -----------------------------------------------------------------


def remap_colors(d50: str, mapping: dict) -> str:
    """Replace palette-block colors per an explicit old→new mapping.

    Case-insensitive matching; replacements written uppercase; unmapped colors and
    every byte outside palette blocks untouched. This is exactly the operation that
    produced presets/white-blue-tour.json from purple-pink-tour.json (lamp-verified
    2026-05-28) — see test_remap_ground_truth_white_blue_tour.
    """
    norm = {k.upper(): v.upper() for k, v in mapping.items()}
    blocks = find_palette_blocks(d50)
    parts = []
    pos = 0
    for b in blocks:
        parts.append(d50[pos:b.start])
        for i in range(b.count):
            original = d50[b.start + i * 6:b.start + (i + 1) * 6]
            replacement = norm.get(original.upper())
            parts.append(replacement if replacement is not None else original)
        pos = b.start + b.count * 6
    parts.append(d50[pos:])
    return "".join(parts)


def recolor(d50: str, new_palette: list) -> str:
    """Re-render a motion in a new palette.

    The d50's distinct colors (order of appearance) map onto new_palette, cycling
    if the motion has more distinct colors than the palette provides. Substitutes
    every distinct color, including incidental program colors — the user's palette
    fully owns the motion (see spec, Section 1).
    """
    if has_p4_block(d50):
        raise ValueError("d50 contains a P4 block (structure unconfirmed); not recolorable")
    distinct = extract_palette(d50)
    if not distinct:
        raise ValueError("d50 has no palette blocks; nothing to recolor")
    if not new_palette:
        raise ValueError("new_palette must contain at least one color")
    palette = []
    for c in new_palette:
        cu = str(c).upper()
        if not _HEX6_RE.fullmatch(cu):
            raise ValueError(f"color {c!r} is not a 6-hex color")
        palette.append(cu)
    mapping = {old: palette[i % len(palette)] for i, old in enumerate(distinct)}
    return remap_colors(d50, mapping)
```

- [ ] **Step 4: Run the tests — confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_motions.py -v`
Expected: all 26 PASS. If `test_remap_ground_truth_white_blue_tour` fails, diff the
first failing frame character-by-character — do NOT weaken the test; the operation
must be byte-exact.

- [ ] **Step 5: Run the full suite, then commit**

Run: `.venv/bin/python -m pytest -q`
Expected: 350 pass.

```bash
git add web/motions.py tests/test_motions.py
git commit -m "feat(motions): remap_colors + recolor, ground-truthed against white-blue-tour"
```

---

### Task 3: `web/motions.py` — catalog merge, rebuild, CLI + initial backfill

**Files:**
- Modify: `web/motions.py` (append)
- Modify: `tests/test_motions.py` (append)
- Create (generated): `motions.json` (project root)

- [ ] **Step 1: Write the failing tests**

Append to `/home/frank/lepro/tests/test_motions.py`:

```python
# --- catalog merge / rebuild --------------------------------------------------------


def make_preset(*d50s, captured="2026-06-02"):
    return {"captured": captured, "frames": [{"d2": 2, "d50": d} for d in d50s]}


def test_merge_new_motions():
    catalog = {"motions": []}
    result = motions.merge_preset(catalog, make_preset(N01_SOLID, N02_CHRISTMAS), "test-a")
    assert result == {"new": 2, "known": 0, "total": 2}
    assert catalog["motions"][0]["id"] == "motion-001"
    assert catalog["motions"][1]["id"] == "motion-002"
    assert catalog["motions"][0]["reference"]["d50"] == N01_SOLID
    assert catalog["motions"][0]["sources"] == ["test-a[0]"]


def test_merge_known_motion_appends_source():
    catalog = {"motions": []}
    motions.merge_preset(catalog, make_preset(N02_CHRISTMAS), "first")
    result = motions.merge_preset(catalog, make_preset(N02_CYBERPUNK_TWIN), "second")
    # Cross-palette twin: loose sig matches -> known, not new.
    assert result == {"new": 0, "known": 1, "total": 1}
    entry = catalog["motions"][0]
    assert entry["sources"] == ["first[0]", "second[0]"]
    assert len(entry["strict_variants"]) == 2       # different palette sizes
    assert entry["reference"]["d50"] == N02_CHRISTMAS  # first-seen wins


def test_merge_is_idempotent():
    catalog = {"motions": []}
    motions.merge_preset(catalog, make_preset(N01_SOLID), "p")
    snapshot = json.dumps(catalog, sort_keys=True)
    motions.merge_preset(catalog, make_preset(N01_SOLID), "p")
    assert json.dumps(catalog, sort_keys=True) == snapshot


def test_merge_preserves_names():
    catalog = {"motions": []}
    motions.merge_preset(catalog, make_preset(N01_SOLID), "p")
    catalog["motions"][0]["name"] = "my favorite"
    motions.merge_preset(catalog, make_preset(N01_SOLID), "p2")
    assert catalog["motions"][0]["name"] == "my favorite"


def test_merge_skips_frames_without_palette_blocks():
    catalog = {"motions": []}
    result = motions.merge_preset(
        catalog, make_preset(P4_CYBERPUNK, "", N01_SOLID), "mixed")
    # P4 frame IS cataloged (detected, non-recolorable); empty d50 is skipped.
    assert result["total"] == 2
    p4_entry = next(m for m in catalog["motions"]
                    if m["reference"]["d50"] == P4_CYBERPUNK)
    assert p4_entry["recolorable"] is False


def test_merge_per_ring_format_recorded():
    catalog = {"motions": []}
    motions.merge_preset(catalog, make_preset(PER_RING), "rings")
    assert catalog["motions"][0]["format"] == "per-ring"
    assert catalog["motions"][0]["recolorable"] is True


def test_rebuild_catalog_real_presets(tmp_path):
    """Rebuild against the real presets/ directory: deterministic, idempotent,
    and unifies the known cross-palette twins."""
    catalog_path = tmp_path / "motions.json"
    result1 = motions.rebuild_catalog(PRESETS, catalog_path)
    assert result1["total"] > 0
    # purple-pink + white-blue must contribute identical motions (35 shared),
    # so total motions < total frames.
    assert result1["total"] < 141
    # Rebuild again: nothing new, same catalog bytes.
    snapshot = catalog_path.read_text()
    result2 = motions.rebuild_catalog(PRESETS, catalog_path)
    assert result2["new"] == 0
    assert result2["total"] == result1["total"]
    assert catalog_path.read_text() == snapshot


def test_rebuild_skips_catalog_file_itself(tmp_path):
    """motions.json must never be ingested as a preset, even if it sits in presets_dir."""
    pdir = tmp_path / "presets"
    pdir.mkdir()
    (pdir / "real.json").write_text(json.dumps(make_preset(N01_SOLID)))
    (pdir / "motions.json").write_text(json.dumps({"motions": []}))
    result = motions.rebuild_catalog(pdir, pdir / "motions.json")
    assert result["total"] == 1


def test_load_catalog_missing_file(tmp_path):
    assert motions.load_catalog(tmp_path / "nope.json") == {"motions": []}
```

- [ ] **Step 2: Run the tests — confirm the new ones fail**

Run: `.venv/bin/python -m pytest tests/test_motions.py -v`
Expected: 26 pass, 9 new FAIL with `AttributeError: ... no attribute 'merge_preset'`.

- [ ] **Step 3: Append the catalog implementation to `web/motions.py`**

Append to `/home/frank/lepro/web/motions.py`:

```python
# --- catalog --------------------------------------------------------------------

CATALOG_FILENAME = "motions.json"


def load_catalog(path) -> dict:
    """Read a catalog file; a missing file is an empty catalog."""
    path = Path(path)
    if not path.exists():
        return {"motions": []}
    return json.loads(path.read_text())


def save_catalog(catalog: dict, path) -> None:
    Path(path).write_text(json.dumps(catalog, indent=2) + "\n")


def _preset_frames(preset: dict) -> list:
    """Frames of a preset, regardless of single-payload vs frames shape."""
    if "frames" in preset:
        return preset.get("frames") or []
    payload = preset.get("payload")
    return [payload] if payload else []


def merge_preset(catalog: dict, preset: dict, preset_name: str) -> dict:
    """Merge one preset's frames into the catalog (mutates ``catalog``).

    Dedup key is the loose signature. New motions get auto-IDs in discovery
    order; known motions accumulate sources and strict variants. User-assigned
    ``name`` fields are never touched. Returns ``{"new", "known", "total"}``.
    """
    by_loose = {m["loose_sig"]: m for m in catalog["motions"]}
    new = known = 0
    for idx, frame in enumerate(_preset_frames(preset)):
        d50 = (frame or {}).get("d50")
        if not d50 or not isinstance(d50, str):
            continue
        # P4-only frames have no P1000 blocks but are still real motions —
        # catalog them (non-recolorable). Frames with no palette-ish content at
        # all (no P1000, no P4) can't be identified and are skipped.
        if not find_palette_blocks(d50) and not has_p4_block(d50):
            continue
        strict, loose = motion_signature(d50)
        source = f"{preset_name}[{idx}]"
        entry = by_loose.get(loose)
        if entry is None:
            entry = {
                "id": f"motion-{len(catalog['motions']) + 1:03d}",
                "name": None,
                "loose_sig": loose,
                "strict_variants": [strict],
                "reference": {
                    "d50": d50,
                    "palette": extract_palette(d50),
                    "source": source,
                },
                "format": detect_format(d50),
                "recolorable": is_recolorable(d50),
                "sources": [source],
                "first_seen": preset.get("captured") or "",
            }
            catalog["motions"].append(entry)
            by_loose[loose] = entry
            new += 1
        else:
            if source not in entry["sources"]:
                entry["sources"].append(source)
            if strict not in entry["strict_variants"]:
                entry["strict_variants"].append(strict)
            known += 1
    return {"new": new, "known": known, "total": len(catalog["motions"])}


def rebuild_catalog(presets_dir, catalog_path) -> dict:
    """Scan ``presets_dir`` for *.json presets and merge every one into the catalog.

    Deterministic (sorted filename order) and idempotent. The catalog file itself
    is never ingested even if it lives inside presets_dir.
    """
    presets_dir = Path(presets_dir)
    catalog = load_catalog(catalog_path)
    totals = {"new": 0, "known": 0}
    for path in sorted(presets_dir.glob("*.json")):
        if path.name == CATALOG_FILENAME:
            continue
        try:
            preset = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        result = merge_preset(catalog, preset, path.stem)
        totals["new"] += result["new"]
        totals["known"] += result["known"]
    save_catalog(catalog, catalog_path)
    return {**totals, "total": len(catalog["motions"])}


def main() -> None:
    """CLI: ``python -m web.motions`` — rebuild the catalog from presets/."""
    root = Path(__file__).resolve().parent.parent
    result = rebuild_catalog(root / "presets", root / CATALOG_FILENAME)
    print(f"catalog: {result['total']} motions "
          f"({result['new']} new, {result['known']} known frames this run)")


if __name__ == "__main__":
    main()
```

NOTE: `merge_preset` catalogs P4-only frames (they're real motions, just not
recolorable), but `test_merge_skips_frames_without_palette_blocks` expects exactly
this. Read both carefully — the skip is only for frames with NO palette content.

- [ ] **Step 4: Run the tests — confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_motions.py -v`
Expected: all 35 PASS.

- [ ] **Step 5: Run the real backfill and inspect**

```bash
.venv/bin/python -m web.motions
.venv/bin/python -c "
import json
c = json.load(open('motions.json'))
ms = c['motions']
print('total motions:', len(ms))
print('recolorable:', sum(1 for m in ms if m['recolorable']))
by_fmt = {}
for m in ms: by_fmt[m['format']] = by_fmt.get(m['format'], 0) + 1
print('by format:', by_fmt)
multi = [m['id'] for m in ms if len(m['sources']) > 1]
print('motions seen in multiple captures:', len(multi))
"
```

Expected: total motions around 55-65 (95 strict signatures collapse under loose
dedup); a majority recolorable; several motions with multiple sources. Record the
exact numbers in the commit message.

- [ ] **Step 6: Run the full suite, commit code + catalog**

Run: `.venv/bin/python -m pytest -q`
Expected: 359 pass.

```bash
git add web/motions.py tests/test_motions.py motions.json
git commit -m "feat(motions): catalog merge/rebuild + CLI; initial backfill (N motions from 7 presets)"
```

(Replace N with the real number from Step 5.)

---

### Task 4: Server endpoints + capture-save hook + active-mode label

**Files:**
- Modify: `web/server.py`
- Create: `tests/test_motions_api.py`

- [ ] **Step 1: Write the failing tests**

Create `/home/frank/lepro/tests/test_motions_api.py`:

```python
"""Tests for the /api/motions endpoints + capture-save integration."""

import asyncio
import json

import pytest

from web import motions
from web import server as workshop


N01_SOLID = "N01:P10001FFAA00F21000100C4U3V3000640000E1;"
N02_CHRISTMAS = ("N02:P10002FF0000008000U510F2100010f01V3001640396;"
                 "P600F210001s00000001U635ca000000000002000002020000R301111;")


class _FakeDevice:
    did = "dev1"


class _FakeClient:
    def __init__(self):
        self.sent = []
        self.state = {"dev1": {"d1": 1}}

    def _dev(self, _did):
        return _FakeDevice()

    async def send_raw(self, payload, did=None):
        self.sent.append((payload, did))


class _FakeReq:
    def __init__(self, body=None, **match_info):
        self._body = body if body is not None else {}
        self.match_info = match_info

    async def json(self):
        return self._body


def _body(resp):
    return json.loads(resp.body.decode() if isinstance(resp.body, bytes) else resp.body)


@pytest.fixture
def catalog_file(tmp_path, monkeypatch):
    """Point the server at a temp catalog seeded with two motions."""
    path = tmp_path / "motions.json"
    catalog = {"motions": []}
    motions.merge_preset(catalog, {"captured": "2026-06-02",
                                   "frames": [{"d50": N01_SOLID}, {"d50": N02_CHRISTMAS}]},
                         "seed")
    motions.save_catalog(catalog, path)
    monkeypatch.setattr(workshop, "_MOTIONS_CATALOG_PATH", path)
    return path


@pytest.fixture
def quiet_lamp(monkeypatch):
    """Fake client, no sessions, no preview running."""
    client = _FakeClient()
    monkeypatch.setattr(workshop, "_client", client)
    monkeypatch.setattr(workshop, "_capture_session", None)
    monkeypatch.setattr(workshop, "_clock_session", None)
    monkeypatch.setattr(workshop, "_ticker_session", None)
    monkeypatch.setattr(workshop, "_preview_task", None)
    monkeypatch.setattr(workshop, "_preview_name", None)
    return client


async def test_get_motions(catalog_file, quiet_lamp):
    body = _body(await workshop.api_motions(None))
    assert body["total"] == 2
    assert body["named"] == 0
    assert body["motions"][0]["id"] == "motion-001"


async def test_rename_motion(catalog_file, quiet_lamp):
    resp = await workshop.api_motion_rename(
        _FakeReq({"name": "solid orange"}, id="motion-001"))
    assert _body(resp)["ok"] is True
    body = _body(await workshop.api_motions(None))
    assert body["motions"][0]["name"] == "solid orange"
    assert body["named"] == 1


async def test_rename_unknown_motion_404(catalog_file, quiet_lamp):
    resp = await workshop.api_motion_rename(
        _FakeReq({"name": "x"}, id="motion-999"))
    assert resp.status == 404


async def test_rename_requires_name(catalog_file, quiet_lamp):
    resp = await workshop.api_motion_rename(_FakeReq({}, id="motion-001"))
    assert resp.status == 400


async def test_play_recolors_and_sends(catalog_file, quiet_lamp, monkeypatch):
    sent_presets = []

    async def fake_run_preview(preset, did, client):
        sent_presets.append(preset)

    monkeypatch.setattr(workshop, "_run_preview", fake_run_preview)
    resp = await workshop.api_motion_play(
        _FakeReq({"palette": ["00FF00"]}, id="motion-001"))
    assert _body(resp)["ok"] is True
    await asyncio.sleep(0)    # let the created task run
    assert len(sent_presets) == 1
    d50 = sent_presets[0]["payload"]["d50"]
    assert "00FF00" in d50 and "FFAA00" not in d50    # recolored
    # Active mode label reflects the motion.
    active = workshop._active_mode()
    assert active["mode"] == "motion"
    assert "motion-001" in active["label"]


async def test_play_without_palette_uses_original(catalog_file, quiet_lamp, monkeypatch):
    sent_presets = []

    async def fake_run_preview(preset, did, client):
        sent_presets.append(preset)

    monkeypatch.setattr(workshop, "_run_preview", fake_run_preview)
    resp = await workshop.api_motion_play(_FakeReq({}, id="motion-001"))
    assert _body(resp)["ok"] is True
    await asyncio.sleep(0)
    assert "FFAA00" in sent_presets[0]["payload"]["d50"]    # original colors


async def test_play_unknown_motion_404(catalog_file, quiet_lamp):
    resp = await workshop.api_motion_play(_FakeReq({}, id="motion-999"))
    assert resp.status == 404


async def test_play_blocked_by_ticker(catalog_file, quiet_lamp, monkeypatch):
    class _RunningTicker:
        running = True
    monkeypatch.setattr(workshop, "_ticker_session", _RunningTicker())
    from aiohttp import web as aioweb
    with pytest.raises(aioweb.HTTPConflict):
        await workshop.api_motion_play(_FakeReq({}, id="motion-001"))


async def test_rebuild_endpoint(catalog_file, quiet_lamp, monkeypatch):
    monkeypatch.setattr(workshop, "_PRESETS_DIR", workshop._PRESETS_DIR)  # real presets
    resp = await workshop.api_motions_rebuild(None)
    body = _body(resp)
    assert body["ok"] is True
    assert body["total"] >= 2    # seed motions + everything from real presets


async def test_preset_preview_still_labeled_preset(catalog_file, quiet_lamp, monkeypatch):
    """api_preview must keep reporting mode='preset' after the _preview_kind change."""
    async def fake_run_preview(preset, did, client):
        pass

    monkeypatch.setattr(workshop, "_run_preview", fake_run_preview)
    monkeypatch.setattr(workshop, "_load_preset",
                        lambda name: {"name": name, "payload": {"d50": N01_SOLID}})
    monkeypatch.setattr(workshop, "apply_color_map", lambda preset, cmap: preset)
    resp = await workshop.api_preview(_FakeReq({"base_name": "christmas"}))
    assert _body(resp)["ok"] is True
    active = workshop._active_mode()
    assert active["mode"] == "preset"


async def test_route_registration():
    app = workshop.build_app()
    routes = {(r.method, r.resource.canonical) for r in app.router.routes()
              if r.resource is not None}
    assert ("GET", "/api/motions") in routes
    assert ("POST", "/api/motions/rebuild") in routes
    assert ("POST", "/api/motions/{id}/play") in routes
    assert ("POST", "/api/motions/{id}/rename") in routes
    assert ("GET", "/motions") in routes
```

- [ ] **Step 2: Run the tests — confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_motions_api.py -v`
Expected: FAIL with `AttributeError: module 'web.server' has no attribute '_MOTIONS_CATALOG_PATH'` (or `api_motions`).

- [ ] **Step 3: Add the motions section to `web/server.py`**

In `/home/frank/lepro/web/server.py`, find the Animations-tab section marker
(`# --- Animations tab — see docs/...`, ~line 345). Directly BEFORE that comment
line, insert:

```python
# --- Motions tab — see docs/superpowers/specs/2026-06-02-motion-library-design.md
from web import motions as _motions_mod

_MOTIONS_CATALOG_PATH = _PROJECT_ROOT / "motions.json"

# What kind of preview _preview_task is running: "preset" (api_preview) or
# "motion" (api_motion_play). Read by _active_mode() for the banner label.
_preview_kind: str = "preset"


def _load_motion_catalog() -> dict:
    return _motions_mod.load_catalog(_MOTIONS_CATALOG_PATH)


def _find_motion(catalog: dict, motion_id: str):
    return next((m for m in catalog["motions"] if m["id"] == motion_id), None)


async def api_motions(_req):
    """The motion catalog + counts, for the Motions tab."""
    catalog = _load_motion_catalog()
    entries = catalog["motions"]
    named = sum(1 for m in entries if m.get("name"))
    return web.json_response(
        {"motions": entries, "total": len(entries), "named": named})


async def api_motions_rebuild(_req):
    """Rescan presets/ and merge new frames into the catalog."""
    result = _motions_mod.rebuild_catalog(_PRESETS_DIR, _MOTIONS_CATALOG_PATH)
    return web.json_response({"ok": True, **result})


async def api_motion_rename(req):
    """Set a motion's display name."""
    mid = req.match_info["id"]
    try:
        body = await req.json()
        name = str(body.get("name") or "").strip()
        if not name:
            return web.json_response({"ok": False, "error": "name required"}, status=400)
        catalog = _load_motion_catalog()
        entry = _find_motion(catalog, mid)
        if entry is None:
            return web.json_response(
                {"ok": False, "error": f"motion {mid!r} not found"}, status=404)
        entry["name"] = name
        _motions_mod.save_catalog(catalog, _MOTIONS_CATALOG_PATH)
        return web.json_response({"ok": True})
    except (ValueError, KeyError, TypeError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def api_motion_play(req):
    """Recolor a motion with the request palette and run it on the lamp.

    Reuses the preset-preview task slot, so the same mutex rules apply
    (409 while capture/clock/ticker runs; /api/stop stops it).
    """
    global _preview_task, _preview_name, _preview_kind
    mid = req.match_info["id"]
    try:
        body = await req.json()
        _check_ticker_mutex()
        _check_clock_mutex()
        _check_capture_mutex()
        catalog = _load_motion_catalog()
        entry = _find_motion(catalog, mid)
        if entry is None:
            return web.json_response(
                {"ok": False, "error": f"motion {mid!r} not found"}, status=404)
        d50 = entry["reference"]["d50"]
        palette = [c for c in (body.get("palette") or []) if c]
        if palette and entry.get("recolorable", True):
            d50 = _motions_mod.recolor(d50, palette)
        # A single-payload pseudo-preset drives _run_preview exactly like a
        # one-frame preset preview.
        pseudo = {"name": entry.get("name") or entry["id"],
                  "payload": {"d1": 1, "d2": 2, "d50": d50}}
        did = _client._dev(None).did
        if _preview_task and not _preview_task.done():
            _preview_task.cancel()
            try:
                await _preview_task
            except asyncio.CancelledError:
                pass
        _preview_task = asyncio.create_task(_run_preview(pseudo, did, _client))
        _preview_name = entry.get("name") or entry["id"]
        _preview_kind = "motion"
        return web.json_response({"ok": True})
    except web.HTTPConflict:
        raise
    except (LeproError, ValueError, KeyError, TypeError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
```

- [ ] **Step 4: Set `_preview_kind` in `api_preview` and use it in `_active_mode`**

In `api_preview` (~line 1501), the line:

```python
        _preview_name = recolored.get("name") or body.get("base_name") or "(unnamed)"
```

becomes (add the global to the function's `global` statement too —
`global _preview_task, _preview_name, _preview_kind`):

```python
        _preview_name = recolored.get("name") or body.get("base_name") or "(unnamed)"
        _preview_kind = "preset"
```

In `_active_mode()` (the shared helper from the TUI project), the preset branch:

```python
    if _preview_task is not None and not _preview_task.done():
        nm = _preview_name or "?"
        return {"mode": "preset", "label": f"\U0001F3A8 Preset — {nm}"}
```

becomes:

```python
    if _preview_task is not None and not _preview_task.done():
        nm = _preview_name or "?"
        if _preview_kind == "motion":
            return {"mode": "motion", "label": f"\U0001F3A8 Motion — {nm}"}
        return {"mode": "preset", "label": f"\U0001F3A8 Preset — {nm}"}
```

- [ ] **Step 5: Add the capture-save hook**

In `api_captures_save` (~line 453), directly after the line
`out_path.write_text(json.dumps(preset, indent=2) + "\n")`, add:

```python
        # Merge the new capture's frames into the motion catalog (the Motions
        # tab's "N new motions discovered" feedback).
        motion_catalog = _motions_mod.load_catalog(_MOTIONS_CATALOG_PATH)
        motion_result = _motions_mod.merge_preset(motion_catalog, preset, name)
        _motions_mod.save_catalog(motion_catalog, _MOTIONS_CATALOG_PATH)
```

And extend its response dict (the `return web.json_response({...})` near the end of
the function) with one more key after `"matched_animation": matched,`:

```python
            "motions": {"new": motion_result["new"],
                        "known": motion_result["known"],
                        "catalog_total": motion_result["total"]},
```

- [ ] **Step 6: Register the routes**

In `build_app()`, directly after the line
`web.post("/api/captures/save", api_captures_save),` add:

```python
        web.get("/motions", index_motions),
        web.get("/api/motions", api_motions),
        web.post("/api/motions/rebuild", api_motions_rebuild),
        web.post(r"/api/motions/{id}/play", api_motion_play),
        web.post(r"/api/motions/{id}/rename", api_motion_rename),
```

`index_motions` doesn't exist until Task 6. To keep this task self-contained and
green, add a minimal placeholder right after `api_motion_play` (Task 6 replaces it):

```python
async def index_motions(_req):
    """Motions tab page. Placeholder panel until the UI task lands."""
    return web.Response(text=_render_shell("motions", "<div>Motions UI coming soon</div>", "Motions"),
                        content_type="text/html")
```

AND add `"motions"` to the shell so `_render_shell("motions", ...)` doesn't raise:
in `_render_shell`, add `"motions": "",` to the `active_classes` dict, and in
`_SHELL_TEMPLATE` add the nav link after the Animations one:

```html
      <a href="/motions" {cls_motions}>&#x1F300; Motions</a>
```

and add `cls_motions=active_classes["motions"],` to the `_SHELL_TEMPLATE.format(...)`
call in `_render_shell`.

- [ ] **Step 7: Run the new tests, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_motions_api.py -v`
Expected: all 12 PASS.

Run: `.venv/bin/python -m pytest -q`
Expected: all PASS (371 total). Watch `tests/test_cockpit_shell.py` — if it asserts
an exact tab list, update that test to include the Motions tab (that is an expected,
in-scope test change; mention it in the commit).

- [ ] **Step 8: Commit**

```bash
git add web/server.py tests/test_motions_api.py tests/test_cockpit_shell.py
git commit -m "feat(motions): catalog endpoints, motion play via preview slot, capture-save merge hook"
```

---

### Task 5: Fix `_recolor_preset` multi-block bug by delegating to the engine

**Files:**
- Modify: `web/server.py` (`_recolor_preset`, ~line 107)
- Modify: `tests/test_motions.py` (append — it tests engine behavior through the server's wrapper)

- [ ] **Step 1: Write the failing test**

Append to `/home/frank/lepro/tests/test_motions.py`:

```python
# --- server._recolor_preset delegation ----------------------------------------------


def test_server_recolor_preset_handles_per_ring_frames():
    """_recolor_preset previously only recolored the FIRST palette block; per-ring
    frames have 3+. After delegating to motions.remap_colors all blocks change."""
    from web.server import _recolor_preset
    preset = {"frames": [{"d2": 2, "d50": PER_RING}]}
    out = _recolor_preset(
        preset,
        ["00FF80", "59FFFF", "8000FF", "FF0080", "FF8000"],
        ["111111", "222222", "333333", "444444", "555555"])
    blocks = motions.find_palette_blocks(out["frames"][0]["d50"])
    assert len(blocks) == 3
    for b in blocks:    # ALL ring sections recolored, not just the first
        assert b.colors == ["111111", "222222", "333333", "444444", "555555"]


def test_server_recolor_preset_still_positional():
    """Existing behavior must hold: positional old->new mapping, same length required."""
    from web.server import _recolor_preset
    import pytest as _pytest
    preset = {"frames": [{"d50": N01_SOLID}]}
    out = _recolor_preset(preset, ["FFAA00"], ["00FF00"])
    assert "00FF00" in out["frames"][0]["d50"]
    with _pytest.raises(ValueError):
        _recolor_preset(preset, ["FFAA00"], ["00FF00", "0000FF"])    # length mismatch
```

- [ ] **Step 2: Run — confirm the per-ring test fails**

Run: `.venv/bin/python -m pytest tests/test_motions.py -v -k recolor_preset`
Expected: `test_server_recolor_preset_handles_per_ring_frames` FAILS (only first
block recolored); `test_server_recolor_preset_still_positional` PASSES.

- [ ] **Step 3: Refactor `_recolor_preset`**

In `/home/frank/lepro/web/server.py`, replace the body of `_recolor_preset`'s inner
`remap_d50` function (keep the outer function signature, docstring intent, deepcopy,
and length check exactly as they are):

```python
def _recolor_preset(preset: dict, old_palette: list, new_palette: list) -> dict:
    """Return a deep copy of ``preset`` with palette colors mapped old → new.

    Every P1000<N><colors> block of each frame's d50 is remapped (delegates to
    web.motions.remap_colors); motion/length/effect fields are untouched. The
    mapping is positional: ``old_palette[i]`` → ``new_palette[i]``.
    """
    out = copy.deepcopy(preset)
    if len(old_palette) != len(new_palette):
        raise ValueError("palette length mismatch")
    mapping = {old.upper(): new.upper() for old, new in zip(old_palette, new_palette)}

    def remap_d50(d50: str) -> str:
        if not d50:
            return d50
        return _motions_mod.remap_colors(d50, mapping)

    if "frames" in out:
        for f in out.get("frames") or []:
            f["d50"] = remap_d50(f.get("d50", ""))
    elif "payload" in out:
        out["payload"]["d50"] = remap_d50(out["payload"].get("d50", ""))
    return out
```

NOTE: `_recolor_preset` is defined at ~line 107, BEFORE the
`from web import motions as _motions_mod` import added in Task 4 (~line 345). Python
resolves the name at call time, not definition time, so this is fine — but verify
the import exists at module scope (it does, Task 4 added it).

- [ ] **Step 4: Run the tests — all pass, existing animation tests included**

Run: `.venv/bin/python -m pytest tests/test_motions.py tests/test_animation.py tests/test_animations.py tests/test_server.py -v`
Expected: all PASS — the delegation must not change behavior for
single-block frames (the only kind the existing tests cover).

Run: `.venv/bin/python -m pytest -q`
Expected: all PASS (373 total).

- [ ] **Step 5: Commit**

```bash
git add web/server.py tests/test_motions.py
git commit -m "fix(server): _recolor_preset now recolors every palette block (per-ring bug)"
```

---

### Task 6: The Motions tab UI

**Files:**
- Modify: `web/server.py` (replace placeholder `index_motions` + add `_PANEL_MOTIONS`; update `_PANEL_ANIMATIONS` counter)
- Modify: `tests/test_cockpit_shell.py` (add Motions tab coverage, following whatever pattern that file uses for the other tabs)

- [ ] **Step 1: Add `_PANEL_MOTIONS` and the real `index_motions`**

In `/home/frank/lepro/web/server.py`, replace the placeholder `index_motions` from
Task 4 with:

```python
async def index_motions(_req):
    return web.Response(text=_render_shell("motions", _PANEL_MOTIONS, "Motions"),
                        content_type="text/html")
```

Then add `_PANEL_MOTIONS` directly after the `_PANEL_ANIMATIONS` string constant
(~line 770, before the shell-template section):

```python
_PANEL_MOTIONS = """
<style>
  /* Feature-specific styles for the Motions panel. Generic chrome lives in
     /static/cockpit.css. Classes prefixed .motion- to avoid collisions. */
  .motion-toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
                    padding: 10px 0; border-bottom: 1px solid var(--line, #2a2a3a); }
  .motion-toolbar .counts { color: var(--text-dim, #999); font-size: 13px; }
  .motion-palette { display: flex; align-items: center; gap: 6px; }
  .motion-palette input[type=color] { width: 36px; height: 28px; border: none;
                                      background: none; cursor: pointer; }
  .motion-palette button { padding: 2px 8px; }
  .motion-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
                 gap: 10px; padding-top: 12px; }
  .motion-card { border: 1px solid var(--line, #2a2a3a); border-radius: 8px;
                 padding: 10px; display: flex; flex-direction: column; gap: 6px; }
  .motion-card.playing { border-color: var(--accent, #6cf); box-shadow: 0 0 6px var(--accent, #6cf); }
  .motion-card .mname { font-weight: 600; cursor: pointer; }
  .motion-card .mname input { width: 100%; }
  .motion-card .mmeta { font-size: 11px; color: var(--text-dim, #999); }
  .motion-card .mswatches span { display: inline-block; width: 14px; height: 14px;
                                 border-radius: 3px; margin-right: 3px; vertical-align: middle; }
  .motion-card .badge { display: inline-block; font-size: 10px; padding: 1px 6px;
                        border-radius: 8px; background: var(--line, #2a2a3a); margin-left: 6px; }
  .motion-status { padding: 8px 0; font-size: 13px; min-height: 20px; }
</style>

<div class="motion-toolbar">
  <div class="motion-palette" id="motion-palette">
    <span style="font-size:12px;color:var(--text-dim)">PALETTE</span>
    <input type="color" value="#ff0000">
    <input type="color" value="#0080ff">
    <button id="palette-add">+</button>
    <button id="palette-remove">−</button>
  </div>
  <button id="motion-next-unnamed">▶ next unnamed</button>
  <button id="motion-rebuild">⟳ Rebuild catalog</button>
  <div class="counts" id="motion-counts">—</div>
</div>
<div class="motion-status" id="motion-status"></div>
<div class="motion-grid" id="motion-grid"></div>
<div id="motion-empty" style="display:none">
  No motions cataloged yet. Hit ⟳ Rebuild catalog to scan presets/, or capture
  animations from the Animations tab.
</div>

<script type="module">
const $ = s => document.querySelector(s);
const grid = $('#motion-grid');
const counts = $('#motion-counts');
const statusEl = $('#motion-status');
const paletteBar = $('#motion-palette');
const emptyEl = $('#motion-empty');

let playingId = null;

function setStatus(msg, isError) {
  statusEl.textContent = msg || '';
  statusEl.style.color = isError ? 'var(--danger, #f66)' : '';
}

async function postJSON(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

function currentPalette() {
  return Array.from(paletteBar.querySelectorAll('input[type=color]'))
    .map(i => i.value.replace('#', '').toUpperCase());
}

$('#palette-add').addEventListener('click', () => {
  const inputs = paletteBar.querySelectorAll('input[type=color]');
  if (inputs.length >= 9) return;
  const el = document.createElement('input');
  el.type = 'color';
  el.value = '#ffffff';
  inputs[inputs.length - 1].after(el);
});

$('#palette-remove').addEventListener('click', () => {
  const inputs = paletteBar.querySelectorAll('input[type=color]');
  if (inputs.length > 1) inputs[inputs.length - 1].remove();
});

function swatches(palette) {
  return (palette || []).map(c => `<span style="background:#${c}"></span>`).join('');
}

function buildCard(m) {
  const card = document.createElement('div');
  card.className = 'motion-card' + (m.id === playingId ? ' playing' : '');
  card.dataset.id = m.id;
  const displayName = m.name || m.id;
  const recolorBadge = m.recolorable ? '' : '<span class="badge">original colors only</span>';
  card.innerHTML = `
    <div class="mname" title="click to rename">${displayName}${recolorBadge}</div>
    <div class="mmeta">${m.format} · ${m.reference.palette.length} colors ·
         seen in ${m.sources.length} capture${m.sources.length === 1 ? '' : 's'}</div>
    <div class="mswatches">${swatches(m.reference.palette)}</div>
    <div><button data-action="play">▶ Play</button></div>
  `;
  card.querySelector('[data-action="play"]').addEventListener('click', () => playMotion(m));
  card.querySelector('.mname').addEventListener('click', () => startRename(card, m));
  return card;
}

function startRename(card, m) {
  const nameEl = card.querySelector('.mname');
  const current = m.name || '';
  nameEl.innerHTML = `<input type="text" value="${current}" placeholder="${m.id}">`;
  const input = nameEl.querySelector('input');
  input.focus();
  input.addEventListener('keydown', async (e) => {
    if (e.key === 'Enter') {
      const name = input.value.trim();
      if (!name) { loadMotions(); return; }
      const j = await postJSON(`/api/motions/${m.id}/rename`, {name});
      setStatus(j.ok === false ? ('error: ' + j.error) : `named: ${name}`, j.ok === false);
      loadMotions();
    } else if (e.key === 'Escape') {
      loadMotions();
    }
  });
  input.addEventListener('blur', () => loadMotions());
}

async function playMotion(m) {
  const j = await postJSON(`/api/motions/${m.id}/play`,
                           m.recolorable ? {palette: currentPalette()} : {});
  if (j.ok === false) { setStatus('error: ' + j.error, true); return; }
  playingId = m.id;
  setStatus(`playing ${m.name || m.id}…`);
  loadMotions();
}

let motionsCache = [];

async function loadMotions() {
  try {
    const r = await fetch('/api/motions');
    const j = await r.json();
    motionsCache = j.motions || [];
    counts.textContent = `${j.total} motions · ${j.named} named`;
    grid.innerHTML = '';
    emptyEl.style.display = motionsCache.length ? 'none' : 'block';
    for (const m of motionsCache) grid.appendChild(buildCard(m));
  } catch (e) {
    setStatus('failed to load motions: ' + e.message, true);
  }
}

$('#motion-rebuild').addEventListener('click', async () => {
  setStatus('rebuilding…');
  const j = await postJSON('/api/motions/rebuild', {});
  if (j.ok === false) { setStatus('error: ' + j.error, true); return; }
  setStatus(`catalog rebuilt: ${j.total} motions (${j.new} new)`);
  loadMotions();
});

$('#motion-next-unnamed').addEventListener('click', async () => {
  const next = motionsCache.find(m => !m.name);
  if (!next) { setStatus('every motion is named 🎉'); return; }
  await playMotion(next);
  // After play, open the rename input on that card so the user can type
  // what they see on the lamp.
  const card = grid.querySelector(`[data-id="${next.id}"]`);
  if (card) startRename(card, next);
});

loadMotions();
</script>
"""
```

- [ ] **Step 2: Update the Animations tab counter**

In `_PANEL_ANIMATIONS` (the string constant, ~line 501):

The HTML line:

```html
    <strong id="capture-count">—</strong> unique animations / ~72 target
```

becomes:

```html
    <strong id="capture-count">—</strong> unique motions cataloged
```

In its `<script type="module">` block, the line `const TARGET_TOTAL = 72;` is
deleted, and in `loadAnimations()` the line:

```javascript
    captureCount.textContent = items.length;
```

becomes:

```javascript
    // The counter shows frame-level motion count (the real catalog), not
    // preset-group count.
    try {
      const mj = await (await fetch('/api/motions')).json();
      captureCount.textContent = mj.total;
    } catch { captureCount.textContent = items.length; }
```

And in `submitSave()`, after the existing `matched_animation` notice handling,
surface the new-motions info — the block:

```javascript
  if (j.matched_animation) {
    const m = j.matched_animation;
    setNotice(`Saved as ${name} → matches ${m.name} (now ${m.variant_count} variants)`, 'matched');
  } else {
    setNotice(`Saved as ${name} → new animation`, 'ok');
  }
```

becomes:

```javascript
  const motionsInfo = j.motions
    ? ` · ${j.motions.new} new motion${j.motions.new === 1 ? '' : 's'} (catalog: ${j.motions.catalog_total})`
    : '';
  if (j.matched_animation) {
    const m = j.matched_animation;
    setNotice(`Saved as ${name} → matches ${m.name} (now ${m.variant_count} variants)${motionsInfo}`, 'matched');
  } else {
    setNotice(`Saved as ${name} → new animation${motionsInfo}`, 'ok');
  }
```

- [ ] **Step 3: Add shell-test coverage**

Read `/home/frank/lepro/tests/test_cockpit_shell.py` first. Add a test for the
Motions page following the exact pattern that file uses for the other tabs (e.g. if
it has `test_animations_page_renders`, clone it):

```python
async def test_motions_page_renders():
    resp = await workshop.index_motions(None)
    text = resp.text if isinstance(resp.text, str) else resp.body.decode()
    assert "motion-grid" in text
    assert 'class="tabs"' in text
```

(Adapt names/assertions to the file's existing conventions — the test must verify
the Motions page renders the shell + panel and the nav contains all six tabs.)

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all PASS.

- [ ] **Step 5: Manual smoke test against the running workshop**

The workshop server may be running with old code. Restart it first (check with the
user if unsure whether anything is using the lamp), then:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8081/motions          # expect 200
curl -s http://127.0.0.1:8081/api/motions | python3 -c "
import sys, json; d = json.load(sys.stdin)
print('motions:', d['total'], '| named:', d['named'])"
# Play motion-001 in red/white and confirm the lamp changes + active mode:
curl -s -X POST http://127.0.0.1:8081/api/motions/motion-001/play \
  -H 'Content-Type: application/json' -d '{"palette": ["FF0000", "FFFFFF"]}'
curl -s http://127.0.0.1:8081/api/cockpit/active
# expect: {"mode": "motion", "label": "🎨 Motion — motion-001"}
curl -s -X POST http://127.0.0.1:8081/api/stop
```

- [ ] **Step 6: Commit**

```bash
git add web/server.py tests/test_cockpit_shell.py
git commit -m "feat(motions): Motions tab UI — palette picker, play, rename, next-unnamed flow"
```

---

### Task 7: README + final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the README**

In `/home/frank/lepro/README.md`, after the "### Lamp TUI" section (added by the TUI
project), add:

````markdown

### Motion library

Every unique animation the Lepro app can produce, cataloged by palette-independent
signature and playable in **any** palette — open the workshop's **Motions** tab.

```bash
.venv/bin/python -m web.motions     # (re)build motions.json from presets/
```

The catalog (`motions.json`) is a database: motion names you assign in the UI are
never overwritten by rebuilds. Capturing new animations from the Animations tab
automatically merges new motions into the catalog.
````

- [ ] **Step 2: Run the complete test suite one final time**

Run: `.venv/bin/python -m pytest -q`
Expected: all PASS. Record the final count.

- [ ] **Step 3: Verify the catalog state**

```bash
.venv/bin/python -c "
import json
c = json.load(open('motions.json'))
print('catalog:', len(c['motions']), 'motions')
print('recolorable:', sum(1 for m in c['motions'] if m['recolorable']))
print('named:', sum(1 for m in c['motions'] if m['name']))"
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README section for the motion library"
```

---

## Self-review notes

- **Spec coverage:** Section 1 (engine → Tasks 1-2, catalog model → Task 3);
  Section 2 (rebuild/CLI → Task 3, endpoints/play/capture-hook → Task 4);
  Section 3 (Motions tab UI + Animations counter → Task 6); Section 4 (ground-truth
  + corpus tests → Tasks 1-3, API tests → Task 4); reconciliation item 2
  (_recolor_preset bug → Task 5).
- **Type consistency:** `motions.merge_preset` returns `{"new", "known", "total"}`
  (Task 3) and Task 4's capture hook + Task 6's JS read exactly those keys ✓.
  `motion_signature` returns `(strict, loose)` tuples; merge stores
  `loose_sig`/`strict_variants` ✓. `find_palette_blocks` → `PaletteBlock(start,
  count, colors)` used by remap/recolor ✓.
- **Known risks:** (1) `tests/test_cockpit_shell.py` internals weren't inspected —
  Tasks 4/6 tell the implementer to read it and follow its conventions, and flag
  the tab-list assertion as an expected change. (2) The exact backfill motion count
  is unknown until Task 3 Step 5 runs — the commit message gets the real number.
  (3) `api_preview`'s `apply_color_map` is unrelated to `_recolor_preset` and is
  deliberately NOT touched (different feature, out of scope).
