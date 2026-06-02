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
import re
from dataclasses import dataclass

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
