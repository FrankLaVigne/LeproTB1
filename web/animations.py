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


import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path


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
