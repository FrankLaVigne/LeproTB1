"""Pure render math for the lamp TUI visualizer (cli/tui.py).

No Textual or Rich imports — everything here is data-in/data-out so it can be
unit-tested without a terminal. Two views:

  rings_grid()  — per-pixel inverse mapping into 3 concentric ring bands; the
                  inverse of how the web cockpit draws SVG arcs (cockpit.js)
  strips_rows() — each ring unrolled into a horizontal run of LED colors

Page-space conventions match lamp-utils.js: each ring's index 0 at 12 o'clock,
increasing clockwise. Ring layout: outer 0-87, middle 88-149, inner 150-195.
"""

from __future__ import annotations

import math

# (name, first page-space index, LED count)
RINGS = (
    ("outer", 0, 88),
    ("middle", 88, 62),
    ("inner", 150, 46),
)

# Ring band radii as fractions of the canvas radius. Mirrors the web SVG
# geometry in cockpit.js RING_GEOMETRY (outer 130-180, middle 90-125,
# inner 50-85), normalized by the outer radius 180.
RING_BANDS = {
    "outer": (130 / 180, 1.0),
    "middle": (90 / 180, 125 / 180),
    "inner": (50 / 180, 85 / 180),
}

# Unlit-LED / no-data pixel color (dim navy — matches the web's #0a0a14 vibe).
DARK = (16, 16, 26)


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    """'FFAA00' -> (255, 170, 0)."""
    return (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))


def _led_color(leds, idx: int, dim: float):
    """Resolve page-space LED ``idx`` to an RGB tuple, honoring missing data."""
    if leds is None:
        return DARK
    color = leds[idx]
    if not color or color == "000000":
        return DARK
    r, g, b = hex_to_rgb(color)
    return (round(r * dim), round(g * dim), round(b * dim))


def led_index_at(px: float, py: float, size: int) -> int | None:
    """Map a pixel on a size×size canvas to a page-space LED index (or None).

    None means the pixel falls outside all three ring bands (center hole,
    gaps between rings, corners).
    """
    c = (size - 1) / 2
    dx, dy = px - c, py - c
    r = math.hypot(dx, dy) / (size / 2)
    # Angle 0 at 12 o'clock, increasing clockwise, normalized to [0, 1).
    # Screen y grows downward, hence -dy.
    angle = math.atan2(dx, -dy) / (2 * math.pi) % 1.0
    for _name, start, count in RINGS:
        r0, r1 = RING_BANDS[_name]
        if r0 <= r <= r1:
            return start + min(int(angle * count), count - 1)
    return None


def rings_grid(leds, size: int, dim: float = 1.0):
    """Build a size×size grid of RGB tuples (None = background pixel).

    leds: 196-entry page-space color list, or None (no decodable state —
    every in-band pixel renders DARK so the ring shapes stay visible).
    dim: 0..1 multiplier, used to dim the whole lamp when power is off.
    """
    grid = []
    for y in range(size):
        row = []
        for x in range(size):
            idx = led_index_at(x, y, size)
            row.append(None if idx is None else _led_color(leds, idx, dim))
        grid.append(row)
    return grid


def strips_rows(leds, dim: float = 1.0):
    """Unrolled view: [(ring_name, [rgb, ...]), ...] in outer/middle/inner order."""
    return [
        (name, [_led_color(leds, start + i, dim) for i in range(count)])
        for name, start, count in RINGS
    ]
