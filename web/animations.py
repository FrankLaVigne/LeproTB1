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
