"""Pure helpers that turn the lamp's reported d-fields into a 196-color LED view.

No I/O, no client, no aiohttp — everything here is a pure function so it can be
unit-tested without a lamp. Mirrors the front-end logic in
web/static/lamp-utils.js (parseD50_N01 / unrotateToPage); keep the two in sync.

Used by the GET /api/lamp/leds endpoint (web/server.py), which feeds the TUI
(cli/tui.py).
"""

from __future__ import annotations

import colorsys
import re

TOTAL_LEDS = 196

# Page→physical ring rotation. MUST mirror server.py's _OUTER_ROTATION,
# _MIDDLE_ROTATION, _INNER_ROTATION (and lamp-utils.js). See docs/CALIBRATION.md.
OUTER_ROT = 31
MIDDLE_ROT = 22
INNER_ROT = 4

# The N01 d50 format we generate from DIY paints + ticker/clock frames.
# N and G are single digits in our format (max 9 groups) — mirrors
# lamp-utils.js parseD50_N01. Official-app captures use N02/N03/#V: prefixes
# this regex deliberately does not match.
_N01_RE = re.compile(
    r"^N01:P1000([0-9])([0-9A-Fa-f]+)F21000([0-9])([0-9A-Fa-f]+)U3V3")


def parse_d50_n01(d50) -> list[str] | None:
    """Parse an N01 d50 string into a 196-entry physical-space color list.

    Returns None for anything that isn't the N01 format we generate
    (N02/N03/#V:/per-ring captures from the official app), or whose group
    lengths don't sum to exactly 196.
    """
    if not d50 or not isinstance(d50, str):
        return None
    m = _N01_RE.match(d50)
    if not m:
        return None
    n = int(m.group(1))
    colors_hex = m.group(2)
    if len(colors_hex) < n * 6:
        return None
    g = int(m.group(3))
    lengths_hex = m.group(4)
    if len(lengths_hex) < g * 4:
        return None
    palette = [colors_hex[i * 6:i * 6 + 6].upper() for i in range(n)]
    physical: list[str] = []
    for k in range(g):
        length = int(lengths_hex[k * 4:k * 4 + 4], 16)
        physical.extend([palette[k % n]] * length)
    return physical if len(physical) == TOTAL_LEDS else None


def unrotate_to_page(physical: list[str]) -> list[str]:
    """Invert apply_lamp_rotation(): physical-space colors → page-space.

    Page space is the orientation the DIY editor paints in, with each ring's
    index 0 at 12 o'clock. Symmetric to unrotateToPage() in lamp-utils.js.
    """
    page = ["000000"] * TOTAL_LEDS
    for i in range(88):
        page[i] = physical[(i + OUTER_ROT) % 88]
    for k in range(62):
        page[88 + k] = physical[88 + (k + MIDDLE_ROT) % 62]
    for k in range(46):
        page[150 + k] = physical[150 + (k + INNER_ROT) % 46]
    return page


def hsv_hex_to_rgb(d5) -> str | None:
    """Convert a d5 HSV hex string (HHHH SSSS VVVV) to a 6-hex RGB color.

    d5 encodes hue 0..360, sat 0..1000, val 0..1000 as three big-endian
    4-hex-digit values (see lepro/client.py set_color). Returns None on
    malformed input.
    """
    if not isinstance(d5, str) or len(d5) < 12:
        return None
    try:
        hue = int(d5[0:4], 16)
        sat = int(d5[4:8], 16)
        val = int(d5[8:12], 16)
    except ValueError:
        return None
    r, g, b = colorsys.hsv_to_rgb(
        (hue % 360) / 360, min(sat, 1000) / 1000, min(val, 1000) / 1000)
    # int(x + 0.5) instead of round() — predictable half-up rounding.
    return (f"{int(r * 255 + 0.5):02X}"
            f"{int(g * 255 + 0.5):02X}"
            f"{int(b * 255 + 0.5):02X}")


# Warm/cool white endpoints for the d4 → RGB approximation.
_WARM = (255, 197, 143)   # ~2700K
_COOL = (235, 242, 255)   # ~6500K


def cct_to_rgb(d4) -> str:
    """Approximate a d4 color temperature (0=2700K warm .. 1000=6500K cool) as RGB.

    Linear blend between a warm white and a cool white. Good enough for a
    visualizer; not colorimetrically accurate.
    """
    t = max(0, min(1000, int(d4))) / 1000
    r = int(_WARM[0] + (_COOL[0] - _WARM[0]) * t + 0.5)
    g = int(_WARM[1] + (_COOL[1] - _WARM[1]) * t + 0.5)
    b = int(_WARM[2] + (_COOL[2] - _WARM[2]) * t + 0.5)
    return f"{r:02X}{g:02X}{b:02X}"


def fields_to_leds(fields) -> list[str] | None:
    """Turn a lamp's reported d-fields into a 196-entry page-space color list.

    Dispatches on d2 (mode):
      2 (segmented) → parse d50 if it's our N01 format, unrotate to page space
      1 (RGB)       → all 196 LEDs the d5 color
      0 (white/CCT) → all 196 LEDs a warm/cool approximation of d4
      3 / unknown / unparseable → None (visualizer shows dark rings)
    """
    if not fields:
        return None
    d2 = fields.get("d2")
    if d2 == 2:
        physical = parse_d50_n01(fields.get("d50"))
        if physical is None:
            return None
        return unrotate_to_page(physical)
    if d2 == 1:
        rgb = hsv_hex_to_rgb(fields.get("d5"))
        return [rgb] * TOTAL_LEDS if rgb else None
    if d2 == 0:
        d4 = fields.get("d4")
        if d4 is None:
            return None
        return [cct_to_rgb(d4)] * TOTAL_LEDS
    return None
