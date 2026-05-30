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


# Fast-mover detection: the % change between the oldest and newest of the
# last FAST_WINDOW ticks must exceed FAST_THRESHOLD AND all FAST_WINDOW
# ticks must be in the same direction ("up" or "down"). Calibrated for
# 30-second polls: 0.5% over 3 polls = ~20%/hour pace.
FAST_WINDOW = 3
FAST_THRESHOLD = 0.005   # 0.5%


def is_ring_fast(recent_ticks):
    """Return True if the ring is in a sustained directional move.

    ``recent_ticks`` is the newest-first list from TickerSession.snapshot();
    each entry has ``price`` (float) and ``direction`` ("up"/"down"/...).
    """
    if len(recent_ticks) < FAST_WINDOW:
        return False
    window = recent_ticks[:FAST_WINDOW]
    directions = {t["direction"] for t in window}
    if directions != {"up"} and directions != {"down"}:
        return False
    newest = window[0]["price"]
    oldest = window[-1]["price"]
    if oldest == 0:
        return False
    return abs((newest - oldest) / oldest) >= FAST_THRESHOLD


def build_ticker_d50(rings, flash_color, effect="Steady"):
    """Compose the d50 string for the lamp.

    ``rings`` is a dict shaped like::
        {"outer": {"color": "00FF00"}, "middle": {...}, "inner": {...}}

    When ``flash_color`` is non-None: emits a single-color (flash_color)
    full-lamp Breathe d50 — used for the 5-second tick flash. The ``effect``
    parameter is ignored in this mode.

    When ``flash_color`` is None: emits a per-ring multi-color d50 with the
    requested ``effect`` tail. ``effect="Steady"`` (default) is the normal
    state; ``effect="Breathe"`` is the sustained "fast mode" — the per-ring
    colors are still visible but the whole lamp pulses.
    """
    from web.server import build_d50_from_leds  # noqa: PLC0415

    if flash_color is not None:
        leds = [flash_color] * 196
        return build_d50_from_leds(leds, "Breathe", 50)

    outer = rings["outer"]["color"]
    middle = rings["middle"]["color"]
    inner = rings["inner"]["color"]
    leds = [outer] * 88 + [middle] * 62 + [inner] * 46
    return build_d50_from_leds(leds, effect, 50)


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
                "is_fast": False,
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

    async def _fetch_all(self):
        """Fetch the latest price for every configured ring, in parallel."""
        import asyncio
        active = [(ring, r["symbol"]) for ring, r in self._rings.items() if r is not None]
        results = await asyncio.gather(
            *[asyncio.to_thread(fetch_price, sym) for _, sym in active]
        )
        return {ring: price for (ring, _), price in zip(active, results)}

    async def _tick_once(self):
        """Run one poll iteration: fetch -> decide -> compose -> send."""
        results = await self._fetch_all()
        flash_color = None
        for ring in self._VALID_RINGS:
            if ring not in results:
                continue
            r = self._rings[ring]
            now = results[ring]
            old_price = r["prev_price"]
            new_color, ticked = decide_ring_color(old_price, now, r["color"])
            r["color"] = new_color
            r["current_price"] = now
            r["last_fetch_at"] = datetime.now().isoformat(timespec="seconds")
            r["last_fetch_ok"] = now is not None
            if now is not None:
                r["prev_price"] = now
            if ticked:
                direction = (
                    "baseline" if new_color == COLOR_WHITE
                    else "up" if new_color == COLOR_GREEN
                    else "down" if new_color == COLOR_RED
                    else "error"
                )
                self.record_tick(ring, now if now is not None else 0.0, direction)
                flash_color = new_color  # outer->middle->inner; last writer wins
                r["ticked_at"] = datetime.now().isoformat(timespec="seconds")
            elif now is not None and old_price is not None:
                # No color change, but price still moved — record a directional
                # tick so is_ring_fast can see sustained moves even when the
                # ring stays the same color.
                if now > old_price:
                    self.record_tick(ring, now, "up")
                elif now < old_price:
                    self.record_tick(ring, now, "down")

        if flash_color is not None:
            from datetime import timedelta
            self._flash_until = (datetime.now() + timedelta(seconds=5)).isoformat(timespec="seconds")

        # After all rings have been updated this poll, recompute is_fast.
        for ring in self._VALID_RINGS:
            r = self._rings[ring]
            if r is None:
                continue
            r["is_fast"] = is_ring_fast(r["recent_ticks"])

        # Pick effect: if anyone is fast and we're not in a flash, sustained Breathe.
        any_fast = any(
            r is not None and r["is_fast"]
            for r in self._rings.values()
        )
        flashing = self._is_flashing()
        send_flash = flash_color if flashing else None
        base_effect = "Breathe" if any_fast else "Steady"
        d50 = build_ticker_d50(self._snapshot_rings_for_d50(), send_flash, effect=base_effect)

        try:
            await self._client.send_raw({"d1": 1, "d2": 2, "d50": d50})
        except Exception:  # noqa: BLE001 — log and continue (matches stock_lamp.py)
            pass

    def _snapshot_rings_for_d50(self):
        """build_ticker_d50 expects every ring slot present; substitute black."""
        return {
            ring: (self._rings[ring] if self._rings[ring] is not None
                   else {"color": COLOR_OFF})
            for ring in self._VALID_RINGS
        }

    def _is_flashing(self):
        if self._flash_until is None:
            return False
        return datetime.fromisoformat(self._flash_until) > datetime.now()

    async def start(self):
        """Spawn the background polling loop."""
        import asyncio
        if self.running:
            return
        self._since = datetime.now().isoformat(timespec="seconds")
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run())

    async def _run(self):
        """The loop body — driven by start(), cancellable by stop().

        After a tick that triggers a Breathe flash, hold the flash for 5 seconds
        then send a Steady-revert payload so the lamp doesn't stay in Breathe
        mode for the full poll interval (which can be up to 5 minutes).
        """
        import asyncio
        try:
            while True:
                await self._tick_once()
                if self._is_flashing() and self._interval > 5:
                    # Hold the Breathe flash for 5 s, then revert to the
                    # appropriate base effect (Breathe if any ring is fast,
                    # Steady otherwise).
                    await asyncio.sleep(5)
                    any_fast = any(
                        r is not None and r["is_fast"]
                        for r in self._rings.values()
                    )
                    revert_effect = "Breathe" if any_fast else "Steady"
                    revert_d50 = build_ticker_d50(
                        self._snapshot_rings_for_d50(), None, effect=revert_effect)
                    try:
                        await self._client.send_raw({"d1": 1, "d2": 2, "d50": revert_d50})
                    except Exception:  # noqa: BLE001
                        pass
                    # Clear the flash window now that we've reverted.
                    self._flash_until = None
                    remaining = max(0, self._interval - 5)
                    await asyncio.sleep(remaining)
                else:
                    await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        """Cancel the polling loop and power the lamp off."""
        import asyncio
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        try:
            await self._client.send_raw({"d1": 0})
        except Exception:  # noqa: BLE001
            pass
        self._since = None
        self._flash_until = None
