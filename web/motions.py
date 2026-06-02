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
    return isinstance(d50, str) and bool(_P4_RE.search(d50))


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
    if not isinstance(d50, str):
        d50 = ""
    return (_sha1(_masked(d50, mask_counts=False)),
            _sha1(_masked(d50, mask_counts=True)))


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
