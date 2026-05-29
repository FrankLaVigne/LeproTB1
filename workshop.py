#!/usr/bin/env python3
"""Workshop — web UI for browsing, recoloring, previewing, and saving presets.

Sibling to app.py. Defaults to 0.0.0.0:8081.
"""

from __future__ import annotations

import copy
import re

# Match P1000<count><colors>. Count is a single decimal digit (1-9 verified;
# REVERSE_ENGINEERING.md caps at 9). Colors are distinct 6-hex RGB tuples.
_P1000_RE = re.compile(r"P1000(\d)((?:[0-9A-Fa-f]{6})+)")

# Match P4000<count><hex>. D50_FORMAT.md: "fixed-pattern shortcut where the
# palette is one color used N times." One 6-hex color repeated count times.
_P4000_RE = re.compile(r"P4000(\d)((?:[0-9A-Fa-f]{6})+)")


def _iter_d50s(preset: dict):
    """Yield every d50 string in a preset, single-frame or multi-frame."""
    if "frames" in preset:
        for frame in preset["frames"]:
            d50 = frame.get("d50")
            if d50:
                yield d50
    payload = preset.get("payload")
    if payload and payload.get("d50"):
        yield payload["d50"]


def extract_palette(preset: dict) -> list[str]:
    """Return distinct palette colors (uppercase hex) in first-occurrence order."""
    seen: dict[str, None] = {}  # dict preserves insertion order, acts as ordered set
    for d50 in _iter_d50s(preset):
        for m in _P1000_RE.finditer(d50):
            count = int(m.group(1))
            hex_run = m.group(2)
            for i in range(count):
                color = hex_run[i * 6:(i + 1) * 6].upper()
                if color not in seen:
                    seen[color] = None
        for m in _P4000_RE.finditer(d50):
            # P4000 encodes one color repeated N times; extract only the first.
            color = m.group(2)[:6].upper()
            if color not in seen:
                seen[color] = None
    return list(seen.keys())


def apply_color_map(preset: dict, color_map: dict[str, str]) -> dict:
    """Return a deep copy of `preset` with every d50 hex-substituted.

    Input keys and values are normalized to uppercase; substitution emits
    uppercase hex (matching the format the Lepro app emits in captures).
    Substring collisions are avoided because palette colors are always exactly
    6 hex characters, and (per the white-blue-tour experiment) palette colors
    live ONLY inside P1000{N}{colors} blocks.
    """
    norm = {k.upper(): v.upper() for k, v in color_map.items()}
    out = copy.deepcopy(preset)
    for holder in _holders(out):
        d50 = holder["d50"]
        for old, new in norm.items():
            # Replace BOTH the uppercase and the lowercase form, emitting uppercase
            d50 = d50.replace(old, new).replace(old.lower(), new)
        holder["d50"] = d50
    return out


def _holders(preset: dict):
    """Yield the dicts whose 'd50' key we should rewrite in place."""
    if "frames" in preset:
        for frame in preset["frames"]:
            if "d50" in frame:
                yield frame
    payload = preset.get("payload")
    if payload and "d50" in payload:
        yield payload


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _sanitize_name(name: str) -> str:
    """Validate a kebab-case preset name. Raises ValueError on invalid input."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(
            f"preset name {name!r} must be kebab-case "
            "([a-z0-9][a-z0-9-]*) — no spaces, uppercase, or path separators"
        )
    return name
