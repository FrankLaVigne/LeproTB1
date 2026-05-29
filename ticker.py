"""Lepro stock-ticker session — pure helpers + TickerSession class.

Drives up to 3 Yahoo Finance symbols against the lamp's three concentric rings.
Each ring shows its symbol's most-recent direction as a solid color; every
tick triggers a 5-second whole-lamp Breathe flash in the new color.
"""

from __future__ import annotations

# Per-ring color codes (also re-used by build_ticker_d50 and the page).
COLOR_OFF = "000000"
COLOR_WHITE = "FFFFFF"   # baseline / first sample
COLOR_GREEN = "00FF00"
COLOR_RED = "FF0000"
COLOR_YELLOW = "FFFF00"  # fetch failed


def decide_ring_color(prev_price, now_price, prev_color):
    """Return ``(new_color, ticked)`` for one ring on one poll.

    ``ticked`` is True iff the color CHANGED (so the caller should start a
    flash). Flat moves return ``ticked=False`` and keep the prior color.
    Fetch failure (``now_price is None``) -> yellow.
    """
    if now_price is None:
        new_color = COLOR_YELLOW
    elif prev_price is None:
        new_color = COLOR_WHITE
    elif now_price > prev_price:
        new_color = COLOR_GREEN
    elif now_price < prev_price:
        new_color = COLOR_RED
    else:
        # Flat: keep the prior color, or fall back to white if there's nothing.
        new_color = prev_color if prev_color is not None else COLOR_WHITE
    ticked = new_color != prev_color
    return new_color, ticked
