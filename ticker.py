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


def build_ticker_d50(rings, flash_color):
    """Compose the d50 string for the lamp.

    ``rings`` is a dict shaped like::
        {"outer": {"color": "00FF00"}, "middle": {...}, "inner": {...}}

    When ``flash_color`` is None: emits a per-ring multi-color Steady d50.
    When ``flash_color`` is non-None: emits a single-color (flash_color)
    full-lamp Breathe d50 — the per-ring colors are intentionally hidden
    during the 5-second flash window. (Imported here to avoid a circular
    import: workshop.build_d50_from_leds + workshop.effect_tail are the
    canonical d50 encoders.)
    """
    # Local import — ticker is loaded by workshop, but the d50 helpers we
    # need are defined at module scope in workshop, so import them here.
    from workshop import build_d50_from_leds  # noqa: PLC0415

    if flash_color is not None:
        leds = [flash_color] * 196
        return build_d50_from_leds(leds, "Breathe", 50)

    outer = rings["outer"]["color"]
    middle = rings["middle"]["color"]
    inner = rings["inner"]["color"]
    leds = [outer] * 88 + [middle] * 62 + [inner] * 46
    return build_d50_from_leds(leds, "Steady", 50)
