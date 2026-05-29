"""Lepro stock-ticker session — pure helpers + TickerSession class.

Drives up to 3 Yahoo Finance symbols against the lamp's three concentric rings.
Each ring shows its symbol's most-recent direction as a solid color; every
tick triggers a 5-second whole-lamp Breathe flash in the new color.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

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


def fetch_price(symbol):
    """Return latest known price for ``symbol``, or None on any error.

    Synchronous; the polling loop wraps it in ``asyncio.to_thread``. Matches
    the shape used by ``stock_lamp.fetch_price`` so the two stay drop-in
    compatible.
    """
    try:
        import yfinance as yf  # local import — keeps workshop import time fast
        price = yf.Ticker(symbol).fast_info["last_price"]
        return float(price) if price is not None else None
    except Exception:  # noqa: BLE001  — any yfinance / network error -> None
        return None


class TickerSession:
    """Holds per-ring state for one polling session.

    The async polling loop lives in ``start()`` / ``_run()`` (added Task 5).
    All time fields are ISO strings to round-trip cleanly through JSON.
    """

    _VALID_RINGS = ("outer", "middle", "inner")

    def __init__(self, client, symbols, interval):
        if interval not in (10, 30, 60, 300):
            raise ValueError(f"interval must be 10, 30, 60, or 300; got {interval}")
        if not symbols:
            raise ValueError("at least one symbol required")
        for ring in symbols:
            if ring not in self._VALID_RINGS:
                raise ValueError(f"unknown ring {ring!r}")
        self._client = client
        self._interval = interval
        self._since: Optional[str] = None
        self._flash_until: Optional[str] = None
        self._task = None
        # Initialise per-ring state — None entries mean "no symbol assigned".
        self._rings = {ring: None for ring in self._VALID_RINGS}
        for ring, symbol in symbols.items():
            self._rings[ring] = {
                "symbol": symbol,
                "prev_price": None,
                "current_price": None,
                "color": COLOR_OFF,
                "ticked_at": None,
                "last_fetch_at": None,
                "last_fetch_ok": False,
                "recent_ticks": [],
            }

    @property
    def running(self):
        return self._task is not None and not self._task.done()

    def set_baseline(self, ring, price):
        """Seed a ring's prev_price with the first-sample value."""
        r = self._rings[ring]
        if r is None:
            raise ValueError(f"ring {ring!r} has no symbol; cannot set baseline")
        r["prev_price"] = price
        r["current_price"] = price
        r["color"] = COLOR_WHITE
        r["last_fetch_at"] = datetime.now().isoformat(timespec="seconds")
        r["last_fetch_ok"] = True

    def record_tick(self, ring, price, direction):
        """Push a tick event onto the ring's recent_ticks (capped at 10)."""
        r = self._rings[ring]
        if r is None:
            raise ValueError(f"ring {ring!r} has no symbol; cannot record tick")
        r["recent_ticks"].insert(0, {
            "at": datetime.now().isoformat(timespec="seconds"),
            "price": price,
            "direction": direction,
        })
        del r["recent_ticks"][10:]

    def snapshot(self):
        """Return a JSON-serialisable dict — exact shape documented in spec."""
        import copy
        return {
            "running": self.running,
            "since": self._since,
            "interval": self._interval,
            "flash_until": self._flash_until,
            "rings": {k: copy.deepcopy(v) for k, v in self._rings.items()},
        }
