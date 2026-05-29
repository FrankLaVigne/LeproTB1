# Stock Ticker Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/ticker` page to `workshop.py` that runs a multi-symbol stock-tracking session, with up to 3 Yahoo tickers (one per ring) driving per-ring solid colors and a 5-second whole-lamp Breathe flash on every tick.

**Architecture:** A background `asyncio.Task` lives in the workshop process and shares the existing `LeproClient` MQTT slot. Pure helpers (`decide_ring_color`, `build_ticker_d50`) live in a new `ticker.py` module; the polling loop and state live in a `TickerSession` class there too. `workshop.py` adds three new POST routes, one new GET page route, an inline `_PAGE_TICKER` HTML constant, and a mutex helper that 409s `/api/diy/paint` and `/api/preview` while a session is running.

**Tech Stack:** Python 3.12, existing `aiohttp` + `lepro.LeproClient` + `yfinance` (already pinned at 1.4.0). No new deps. Vanilla HTML/CSS/JS for the page.

---

## File Structure

- **`ticker.py`** (new) — Pure helpers (`fetch_price`, `decide_ring_color`, `build_ticker_d50`) + `TickerSession` class encapsulating the background polling task. Imports `workshop.build_d50_from_leds` to avoid re-deriving the d50 format.
- **`workshop.py`** (modify) — Add module-level `_ticker_session`, `_check_ticker_mutex` helper, four route handlers (`api_ticker_start`, `api_ticker_stop`, `api_ticker_state`, `index_ticker`), inline `_PAGE_TICKER` constant, third tab in both existing page constants, mutex calls in `api_diy_paint` and `api_preview`. Net ~350 lines added.
- **`tests/test_ticker.py`** (new) — ~10 unit tests for the two pure functions and `TickerSession.snapshot()`.
- **`README.md`** (modify) — Extend the existing Preset workshop section with a Ticker paragraph.

Nine tasks below; pure helpers first (TDD), then the session class, then the routes, then the page, then the docs.

---

### Task 1: `decide_ring_color` pure function

**Files:**
- Create: `ticker.py`
- Create: `tests/test_ticker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ticker.py` with this content:

```python
"""Unit tests for ticker.py — pure-function and snapshot-shape coverage."""

import pytest

import ticker


# --- decide_ring_color tests --------------------------------------------------


def test_decide_ring_color_first_sample_baseline_white():
    # No prior price -> white baseline, marked as ticked so it shows up live.
    color, ticked = ticker.decide_ring_color(None, 100.0, None)
    assert (color, ticked) == ("FFFFFF", True)


def test_decide_ring_color_up_from_baseline_white():
    color, ticked = ticker.decide_ring_color(100.0, 110.0, "FFFFFF")
    assert (color, ticked) == ("00FF00", True)


def test_decide_ring_color_down_from_green():
    color, ticked = ticker.decide_ring_color(110.0, 105.0, "00FF00")
    assert (color, ticked) == ("FF0000", True)


def test_decide_ring_color_flat_keeps_prior_color():
    color, ticked = ticker.decide_ring_color(100.0, 100.0, "00FF00")
    assert (color, ticked) == ("00FF00", False)


def test_decide_ring_color_fetch_fail_yellow():
    color, ticked = ticker.decide_ring_color(100.0, None, "00FF00")
    assert (color, ticked) == ("FFFF00", True)


def test_decide_ring_color_recover_from_yellow_with_uptick():
    color, ticked = ticker.decide_ring_color(100.0, 110.0, "FFFF00")
    assert (color, ticked) == ("00FF00", True)


def test_decide_ring_color_repeated_uptick_still_green_but_not_ticked():
    # If the prior color is already green and the move is again upward,
    # the color doesn't change so ticked must be False (no flash).
    color, ticked = ticker.decide_ring_color(100.0, 110.0, "00FF00")
    assert (color, ticked) == ("00FF00", False)


def test_decide_ring_color_fetch_fail_when_already_yellow_no_tick():
    color, ticked = ticker.decide_ring_color(100.0, None, "FFFF00")
    assert (color, ticked) == ("FFFF00", False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ticker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ticker'`.

- [ ] **Step 3: Create `ticker.py` with the minimal implementation**

Create `/home/frank/lepro/ticker.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ticker.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Verify the full repo suite still passes**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add ticker.py tests/test_ticker.py
git commit -m "feat: ticker.decide_ring_color (pure function)"
```

---

### Task 2: `build_ticker_d50` pure function

**Files:**
- Modify: `ticker.py` (append below `decide_ring_color`)
- Modify: `tests/test_ticker.py` (append)

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_ticker.py`:

```python


# --- build_ticker_d50 tests ---------------------------------------------------


def _rings(outer="000000", middle="000000", inner="000000"):
    """Build a minimal rings dict matching the TickerSession.snapshot() shape."""
    return {
        "outer":  {"color": outer},
        "middle": {"color": middle},
        "inner":  {"color": inner},
    }


def test_build_ticker_d50_three_rings_steady_no_flash():
    # Outer green, middle red, inner yellow. No flash -> Steady tail,
    # per-ring multi-color palette.
    rings = _rings(outer="00FF00", middle="FF0000", inner="FFFF00")
    d50 = ticker.build_ticker_d50(rings, flash_color=None)
    assert d50 == ("N01:P1000300FF00FF0000FFFF00"
                   "F2100030058003E002EU3V3000640000E1;")


def test_build_ticker_d50_all_off_steady():
    rings = _rings()  # all black
    d50 = ticker.build_ticker_d50(rings, flash_color=None)
    # All three rings off -> single-color palette, single group of 196.
    assert d50 == "N01:P10001000000F21000100C4U3V3000640000E1;"


def test_build_ticker_d50_flash_color_overrides_per_ring():
    # During a flash the per-ring colors are hidden: single-color palette,
    # whole-lamp Breathe tail.
    import workshop
    rings = _rings(outer="00FF00", middle="FF0000", inner="FFFF00")
    d50 = ticker.build_ticker_d50(rings, flash_color="00FF00")
    expected = ("N01:P10001"
                + "00FF00"
                + "F21000100C4U3V3"
                + workshop.effect_tail("Breathe", 50)
                + ";")
    assert d50 == expected


def test_build_ticker_d50_one_ring_off_others_lit():
    # Outer green, middle off, inner red. Middle's black is a third palette entry.
    rings = _rings(outer="00FF00", middle="000000", inner="FF0000")
    d50 = ticker.build_ticker_d50(rings, flash_color=None)
    assert d50 == ("N01:P1000300FF00000000FF0000"
                   "F2100030058003E002EU3V3000640000E1;")


def test_build_ticker_d50_all_three_rings_same_color_compresses_to_one_group():
    # If somehow all three rings ended up green, RLE compression should fold
    # them into a single 196-LED group.
    rings = _rings(outer="00FF00", middle="00FF00", inner="00FF00")
    d50 = ticker.build_ticker_d50(rings, flash_color=None)
    assert d50 == "N01:P1000100FF00F21000100C4U3V3000640000E1;"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ticker.py -k build_ticker_d50 -v`
Expected: FAIL with `AttributeError: module 'ticker' has no attribute 'build_ticker_d50'`.

- [ ] **Step 3: Add the implementation to `ticker.py`**

Append below `decide_ring_color` in `ticker.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ticker.py -k build_ticker_d50 -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Verify the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add ticker.py tests/test_ticker.py
git commit -m "feat: ticker.build_ticker_d50 (per-ring steady + whole-lamp Breathe flash)"
```

---

### Task 3: `fetch_price` helper (mirrors stock_lamp.py)

**Files:**
- Modify: `ticker.py` (append below `build_ticker_d50`)
- Modify: `tests/test_ticker.py` (append)

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_ticker.py`:

```python


# --- fetch_price tests --------------------------------------------------------


def test_fetch_price_unknown_symbol_returns_None(monkeypatch):
    # Patch yfinance.Ticker to raise.
    class _Boom:
        def __init__(self, symbol):
            raise RuntimeError("simulated network error")
    monkeypatch.setattr("yfinance.Ticker", _Boom)
    assert ticker.fetch_price("NOPE") is None


def test_fetch_price_returns_last_price_as_float(monkeypatch):
    class _Fake:
        def __init__(self, symbol):
            self.fast_info = {"last_price": 176.42}
    monkeypatch.setattr("yfinance.Ticker", _Fake)
    assert ticker.fetch_price("AAPL") == 176.42


def test_fetch_price_returns_None_when_last_price_missing(monkeypatch):
    class _Fake:
        def __init__(self, symbol):
            self.fast_info = {"last_price": None}
    monkeypatch.setattr("yfinance.Ticker", _Fake)
    assert ticker.fetch_price("AAPL") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ticker.py -k fetch_price -v`
Expected: FAIL with `AttributeError: module 'ticker' has no attribute 'fetch_price'`.

- [ ] **Step 3: Implement `fetch_price`** — append to `ticker.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ticker.py -k fetch_price -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Verify the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add ticker.py tests/test_ticker.py
git commit -m "feat: ticker.fetch_price (mirrors stock_lamp pattern, yfinance lazy)"
```

---

### Task 4: `TickerSession` class — state + snapshot

**Files:**
- Modify: `ticker.py` (append below `fetch_price`)
- Modify: `tests/test_ticker.py` (append)

This task adds the data model and the read-only `snapshot()` method. The async polling loop comes in Task 5.

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_ticker.py`:

```python


# --- TickerSession tests ------------------------------------------------------


def test_ticker_session_initial_snapshot_not_running():
    sess = ticker.TickerSession(client=None,
                                 symbols={"outer": "AAPL"},
                                 interval=30)
    snap = sess.snapshot()
    assert snap["running"] is False
    assert snap["since"] is None
    assert snap["interval"] == 30
    assert snap["flash_until"] is None
    # Only outer is configured; middle and inner are explicitly None.
    assert snap["rings"]["outer"]["symbol"] == "AAPL"
    assert snap["rings"]["outer"]["color"] == "000000"
    assert snap["rings"]["middle"] is None
    assert snap["rings"]["inner"] is None


def test_ticker_session_snapshot_after_baseline_set():
    sess = ticker.TickerSession(client=None,
                                 symbols={"outer": "AAPL", "inner": "SPY"},
                                 interval=10)
    sess.set_baseline("outer", 175.10)
    sess.set_baseline("inner", 420.00)
    snap = sess.snapshot()
    # Baselines don't make the session "running" — only start() does.
    assert snap["running"] is False
    assert snap["rings"]["outer"]["prev_price"] == 175.10
    assert snap["rings"]["outer"]["color"] == "FFFFFF"
    assert snap["rings"]["middle"] is None
    assert snap["rings"]["inner"]["prev_price"] == 420.00
    assert snap["rings"]["inner"]["color"] == "FFFFFF"


def test_ticker_session_record_tick_caps_history_at_10():
    sess = ticker.TickerSession(client=None,
                                 symbols={"outer": "AAPL"},
                                 interval=10)
    # Push 12 ticks; only the most recent 10 should be retained.
    for i in range(12):
        sess.record_tick("outer", price=float(i), direction="up")
    snap = sess.snapshot()
    history = snap["rings"]["outer"]["recent_ticks"]
    assert len(history) == 10
    # Newest first: prices 11, 10, 9, ..., 2.
    assert [t["price"] for t in history] == list(range(11, 1, -1))


def test_ticker_session_snapshot_serializable_via_json():
    # The snapshot must survive aiohttp.web.json_response — i.e., it can't
    # leak datetime objects or other non-JSON types.
    import json
    sess = ticker.TickerSession(client=None,
                                 symbols={"middle": "IBM"},
                                 interval=30)
    sess.set_baseline("middle", 138.25)
    sess.record_tick("middle", price=139.10, direction="up")
    # Should not raise.
    assert json.dumps(sess.snapshot())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ticker.py -k ticker_session -v`
Expected: FAIL with `AttributeError: module 'ticker' has no attribute 'TickerSession'`.

- [ ] **Step 3: Implement `TickerSession` (state + snapshot only)** — append to `ticker.py`:

```python
from datetime import datetime
from typing import Optional


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
        return {
            "running": self.running,
            "since": self._since,
            "interval": self._interval,
            "flash_until": self._flash_until,
            "rings": dict(self._rings),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ticker.py -k ticker_session -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Verify the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add ticker.py tests/test_ticker.py
git commit -m "feat: TickerSession state model + snapshot (no async loop yet)"
```

---

### Task 5: `TickerSession.start` / `.stop` — the async polling loop

**Files:**
- Modify: `ticker.py` (extend the class)
- Modify: `tests/test_ticker.py` (append)

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_ticker.py`:

```python


# --- TickerSession async tests -----------------------------------------------


class _FakeClient:
    """Captures every send_raw payload for assertions."""

    def __init__(self):
        self.sent = []

    async def send_raw(self, payload):
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_session_start_then_stop_marks_running_and_clears():
    import asyncio
    client = _FakeClient()
    sess = ticker.TickerSession(client=client,
                                 symbols={"outer": "AAPL"},
                                 interval=10)
    sess.set_baseline("outer", 100.0)
    # Patch fetch to a deterministic source.
    fetched = [101.0, 102.0]
    async def _fake_one_poll():
        return {"outer": fetched.pop(0) if fetched else 102.0}
    sess._fetch_all = _fake_one_poll  # type: ignore[assignment]

    await sess.start()
    assert sess.running is True
    # Give the loop one tick (interval=10 means it sleeps; we don't wait
    # for the sleep — we cancel right away to confirm the start/stop wiring).
    await sess.stop()
    assert sess.running is False
    snap = sess.snapshot()
    assert snap["running"] is False


@pytest.mark.asyncio
async def test_session_one_poll_sends_payload_and_records_history():
    import asyncio
    client = _FakeClient()
    sess = ticker.TickerSession(client=client,
                                 symbols={"middle": "IBM"},
                                 interval=10)
    sess.set_baseline("middle", 138.25)

    async def _one_poll():
        # IBM ticked up.
        return {"middle": 139.10}
    sess._fetch_all = _one_poll  # type: ignore[assignment]

    # Drive a single iteration without waiting on asyncio.sleep.
    await sess._tick_once()

    snap = sess.snapshot()
    assert snap["rings"]["middle"]["color"] == "00FF00"
    assert snap["rings"]["middle"]["prev_price"] == 138.25  # baseline preserved across the snapshot's "prev"
    assert snap["rings"]["middle"]["current_price"] == 139.10
    assert snap["rings"]["middle"]["recent_ticks"][0]["price"] == 139.10
    # Lamp received exactly one payload (the Breathe flash because the color changed).
    assert len(client.sent) == 1
    assert client.sent[0]["d1"] == 1
    assert client.sent[0]["d2"] == 2
    # The first payload after a tick is the single-color Breathe flash.
    assert "FFFF" not in client.sent[0]["d50"]  # no yellow / no per-ring palette
    assert "E4" in client.sent[0]["d50"]  # Breathe effect tail marker


@pytest.mark.asyncio
async def test_session_steady_payload_when_no_flash_active():
    client = _FakeClient()
    sess = ticker.TickerSession(client=client,
                                 symbols={"outer": "AAPL"},
                                 interval=10)
    sess.set_baseline("outer", 100.0)

    # First poll: uptick triggers flash.
    sess._fetch_all = lambda: _coro({"outer": 110.0})
    await sess._tick_once()
    flash_payload = client.sent[-1]

    # Force the flash window to expire.
    sess._flash_until = None
    # Second poll: same price (flat) — no tick, Steady tail.
    sess._fetch_all = lambda: _coro({"outer": 110.0})
    await sess._tick_once()
    steady_payload = client.sent[-1]

    assert "E4" in flash_payload["d50"]      # Breathe
    assert "E1" in steady_payload["d50"]     # Steady tail (last char before ;)


async def _coro(value):
    return value
```

Also at the top of `tests/test_ticker.py`, add `import pytest` becomes `import pytest` + a conftest hook for asyncio. If your project doesn't have one, add this small `conftest.py`:

Create `tests/conftest.py` ONLY IF it does not already exist with these contents:

```python
import pytest


# Enable @pytest.mark.asyncio without needing pytest-asyncio's strict mode.
pytest_plugins = ["pytest_asyncio"]


def pytest_collection_modifyitems(config, items):
    # If asyncio_mode isn't already set, default async tests to auto mode.
    pass
```

Then verify `pytest-asyncio` is installed: `.venv/bin/python -c "import pytest_asyncio; print(pytest_asyncio.__version__)"`. If it errors, install it via `.venv/bin/pip install pytest-asyncio`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ticker.py -k session -v`
Expected: FAIL with attribute error on `_tick_once` / `start` / `stop`.

- [ ] **Step 3: Extend `TickerSession`** — append these methods inside the class in `ticker.py` (replace the closing of the class):

```python
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
        import asyncio
        results = await self._fetch_all()
        flash_color = None
        for ring in self._VALID_RINGS:
            if ring not in results:
                continue
            r = self._rings[ring]
            now = results[ring]
            new_color, ticked = decide_ring_color(r["prev_price"], now, r["color"])
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
                flash_color = new_color  # outer→middle→inner; last writer wins
                r["ticked_at"] = datetime.now().isoformat(timespec="seconds")

        if flash_color is not None:
            from datetime import timedelta
            self._flash_until = (datetime.now() + timedelta(seconds=5)).isoformat(timespec="seconds")

        # Decide which d50 to send: flash if we're still inside the window.
        flashing = self._is_flashing()
        send_flash = flash_color if flashing else None
        d50 = build_ticker_d50(self._snapshot_rings_for_d50(), send_flash)

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
        """The loop body — driven by start(), cancellable by stop()."""
        import asyncio
        try:
            while True:
                await self._tick_once()
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ticker.py -v`
Expected: PASS (all ticker tests).

- [ ] **Step 5: Verify the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add ticker.py tests/test_ticker.py tests/conftest.py
git commit -m "feat: TickerSession.start/stop/tick_once + asyncio polling loop"
```

---

### Task 6: Backend routes — `/api/ticker/start`, `/api/ticker/stop`, `/api/ticker/state`

**Files:**
- Modify: `workshop.py` — add three POST handlers + register them in `build_app`, add `_check_ticker_mutex()` calls into `api_diy_paint` and `api_preview`.

- [ ] **Step 1: Add the handlers above the existing `_PAGE = """..."""` line**

Place this block immediately after `api_brightness` (added by the DIY work) and BEFORE `index`:

```python
# --- Stock ticker endpoints ---------------------------------------------------

import ticker as _ticker_mod  # alias keeps namespace tidy

_ticker_session = None  # type: ignore[assignment]


def _check_ticker_mutex():
    """Raise web.HTTPConflict if the ticker session is running."""
    global _ticker_session
    if _ticker_session is not None and _ticker_session.running:
        raise web.HTTPConflict(
            text='{"ok": false, "error": "stock ticker is running; stop it first"}',
            content_type="application/json",
        )


async def api_ticker_start(req):
    global _ticker_session
    try:
        body = await req.json()
        interval = int(body.get("interval", 30))
        symbols = {}
        for ring in ("outer", "middle", "inner"):
            sym = body.get(ring)
            if sym is not None and str(sym).strip() != "":
                symbols[ring] = str(sym).strip().upper()
        if not symbols:
            return web.json_response(
                {"ok": False, "error": "at least one symbol required"},
                status=400,
            )
        if _ticker_session is not None and _ticker_session.running:
            return web.json_response(
                {"ok": False, "error": "stock ticker already running"},
                status=409,
            )
        import asyncio
        # First-sample fetch for every symbol; if any return None, abort.
        results = await asyncio.gather(
            *[asyncio.to_thread(_ticker_mod.fetch_price, s) for s in symbols.values()]
        )
        baselines = {}
        failed = []
        for (ring, sym), price in zip(symbols.items(), results):
            if price is None:
                failed.append(sym)
            else:
                baselines[ring] = price
        if failed:
            return web.json_response(
                {"ok": False, "error": f"could not fetch first price for: {', '.join(failed)}"},
                status=400,
            )
        sess = _ticker_mod.TickerSession(_client, symbols, interval)
        for ring, price in baselines.items():
            sess.set_baseline(ring, price)
        await sess.start()
        _ticker_session = sess
        snap = sess.snapshot()
        return web.json_response(
            {"ok": True, "since": snap["since"], "baselines": baselines}
        )
    except (ValueError, KeyError, TypeError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def api_ticker_stop(_req):
    global _ticker_session
    if _ticker_session is None:
        return web.json_response({"ok": True})
    await _ticker_session.stop()
    _ticker_session = None
    return web.json_response({"ok": True})


async def api_ticker_state(_req):
    if _ticker_session is None:
        return web.json_response({
            "running": False, "since": None, "interval": None,
            "flash_until": None, "rings": None,
        })
    return web.json_response(_ticker_session.snapshot())
```

- [ ] **Step 2: Wire the mutex into the two restricted endpoints**

In `workshop.py`, edit the body of `api_diy_paint`. After `body = await req.json()` add:

```python
        _check_ticker_mutex()
```

Same edit inside `api_preview` — find the line `body = await req.json()` (or equivalent) and add `_check_ticker_mutex()` right after parsing the body.

To match the existing error-envelope shape, also catch `web.HTTPConflict` in those handlers' `except` clauses:

```python
    except web.HTTPConflict:
        raise   # already a properly-formatted JSON 409
    except (LeproError, ValueError, KeyError, TypeError) as e:
        ...
```

Place the `except web.HTTPConflict: raise` BEFORE the generic `except` — Python checks excepts top-to-bottom.

- [ ] **Step 3: Register the three new POST routes in `build_app`**

In `build_app`'s `app.add_routes([...])`, append THREE entries after the existing DIY routes:

```python
        web.post("/api/ticker/start", api_ticker_start),
        web.post("/api/ticker/stop", api_ticker_stop),
        web.get("/api/ticker/state", api_ticker_state),
```

- [ ] **Step 4: Smoke-test routes count**

Run:
```bash
.venv/bin/python -c "
import workshop
app = workshop.build_app()
got = sorted(r.method + ' ' + str(r.resource.canonical) for r in app.router.routes())
expected = sorted([
    'GET /', 'HEAD /', 'GET /diy', 'HEAD /diy',
    'GET /api/presets', 'HEAD /api/presets',
    'GET /api/presets/{name}', 'HEAD /api/presets/{name}',
    'GET /api/ticker/state', 'HEAD /api/ticker/state',
    'POST /api/power', 'POST /api/preview', 'POST /api/stop', 'POST /api/save',
    'POST /api/diy/paint', 'POST /api/diy/save', 'POST /api/brightness',
    'POST /api/ticker/start', 'POST /api/ticker/stop',
])
assert got == expected, set(expected) ^ set(got)
print('all', len(got), 'routes registered')
"
```
Expected: prints `all 19 routes registered`.

(NB: 19, not 20 — the `GET /ticker` page route is added in Task 7 and contributes the implicit HEAD.)

- [ ] **Step 5: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add workshop.py
git commit -m "feat: ticker backend routes (start, stop, state) + mutex on /diy/paint & /preview"
```

---

### Task 7: GET /ticker placeholder route

**Files:**
- Modify: `workshop.py` — add `index_ticker` handler + register `GET /ticker` + placeholder `_PAGE_TICKER` constant (real UI in Task 8).

- [ ] **Step 1: Add the handler and placeholder above the existing `_PAGE = ...` line**

Place this block immediately after `index_diy` (added by the DIY work):

```python
async def index_ticker(_req):
    return web.Response(text=_PAGE_TICKER, content_type="text/html")


# Real ticker UI inlined in Task 8.
_PAGE_TICKER = "<!doctype html><title>ticker</title><body>ticker loading...</body>"
```

- [ ] **Step 2: Register the route**

In `build_app`'s `add_routes`, ADD `web.get("/ticker", index_ticker)` directly after `web.get("/diy", index_diy)`:

```python
        web.get("/", index),
        web.get("/diy", index_diy),
        web.get("/ticker", index_ticker),
```

- [ ] **Step 3: Smoke-test routes count**

Run:
```bash
.venv/bin/python -c "
import workshop
app = workshop.build_app()
got = sorted(r.method + ' ' + str(r.resource.canonical) for r in app.router.routes())
expected = sorted([
    'GET /', 'HEAD /', 'GET /diy', 'HEAD /diy',
    'GET /ticker', 'HEAD /ticker',
    'GET /api/presets', 'HEAD /api/presets',
    'GET /api/presets/{name}', 'HEAD /api/presets/{name}',
    'GET /api/ticker/state', 'HEAD /api/ticker/state',
    'POST /api/power', 'POST /api/preview', 'POST /api/stop', 'POST /api/save',
    'POST /api/diy/paint', 'POST /api/diy/save', 'POST /api/brightness',
    'POST /api/ticker/start', 'POST /api/ticker/stop',
])
assert got == expected, set(expected) ^ set(got)
print('all', len(got), 'routes including /ticker registered')
"
```
Expected: prints `all 21 routes including /ticker registered`.

- [ ] **Step 4: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 5: Commit**

```bash
git add workshop.py
git commit -m "feat: GET /ticker route + placeholder page"
```

---

### Task 8: The Ticker page (HTML/CSS/JS)

**Files:**
- Modify: `workshop.py` — replace the `_PAGE_TICKER` placeholder string + add the third tab to `_PAGE` and `_PAGE_DIY`.

- [ ] **Step 1: Replace `_PAGE_TICKER`**

Find the line:
```python
_PAGE_TICKER = "<!doctype html><title>ticker</title><body>ticker loading...</body>"
```

Replace with:

```python
_PAGE_TICKER = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lepro Ticker</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { font: 15px/1.4 system-ui, sans-serif; margin: 0;
         background: #111; color: #eee; min-height: 100vh; }
  .wrap { max-width: 540px; margin: 0 auto; padding: 16px; }
  .header { display: flex; align-items: center; justify-content: space-between;
            gap: 12px; margin-bottom: 12px; }
  .tabs a { color: #aaa; text-decoration: none; padding: 6px 12px;
            border-radius: 8px; font-weight: 600; }
  .tabs a.active { color: #5fd9d9; background: #1f2a2a; }
  .power-btns { display: flex; gap: 6px; }
  .power-btns button { padding: 6px 12px; font-size: 13px; border: 0;
                       border-radius: 8px; cursor: pointer; font-weight: 600; }
  .power-btns button.on { background: #2c8f4f; color: #fff; }
  .power-btns button.off { background: #8f2c2c; color: #fff; }
  .card { background: #1c1c1f; padding: 14px; border-radius: 14px;
          box-shadow: 0 4px 16px rgba(0,0,0,.4); margin-bottom: 14px; }
  .ring-head { display: flex; justify-content: space-between; align-items: center;
               margin-bottom: 8px; }
  .ring-head h2 { font-size: 12px; margin: 0; color: #aaa;
                  text-transform: uppercase; letter-spacing: 0.08em; }
  .dot { width: 14px; height: 14px; border-radius: 50%;
         background: #333; border: 1px solid #444; }
  .ring-card input[type=text] { width: 100%; padding: 10px 12px;
                                 border-radius: 8px; background: #2a2a30;
                                 color: #eee; border: 1px solid #333;
                                 font: inherit; text-transform: uppercase; }
  .ring-card input[type=text][readonly] { background: #1f1f23; color: #aaa; }
  .price { font: 600 22px ui-monospace, monospace; margin: 10px 0 2px; }
  .meta { font-size: 12px; color: #888; }
  .history { font: 12px ui-monospace, monospace; color: #999;
             margin-top: 6px; white-space: nowrap; overflow-x: auto; }
  .intervals { display: flex; gap: 4px; background: #2a2a30;
               padding: 4px; border-radius: 8px; margin-bottom: 12px; }
  .intervals button { flex: 1; padding: 6px 10px; border: 0;
                      border-radius: 6px; background: transparent;
                      color: #eee; cursor: pointer; font: inherit; }
  .intervals button.active { background: #5fd9d9; color: #111; font-weight: 700; }
  .intervals button:disabled { color: #555; cursor: not-allowed; }
  .controls { display: flex; gap: 8px; }
  .controls button { flex: 1; padding: 12px; border: 0; border-radius: 10px;
                     background: #2a2a30; color: #eee; cursor: pointer;
                     font: inherit; font-weight: 700; }
  .controls button.primary { background: #2c8f4f; color: #fff; }
  .controls button.danger { background: #8f2c2c; color: #fff; }
  .controls button:disabled { opacity: 0.4; cursor: not-allowed; }
  #status { font-size: 12px; color: #777; margin-top: 10px; min-height: 1.2em; }
</style></head>
<body><div class="wrap">
  <div class="header">
    <div class="tabs">
      <a href="/">&#x1F3A8; Workshop</a>
      <a href="/diy">&#x270F;&#xFE0F; DIY</a>
      <a href="/ticker" class="active">&#x1F4C8; Ticker</a>
    </div>
    <div class="power-btns">
      <button class="on" id="pwr-on">On</button>
      <button class="off" id="pwr-off">Off</button>
    </div>
  </div>

  <div class="card ring-card" data-ring="outer">
    <div class="ring-head"><h2>Outer</h2><div class="dot"></div></div>
    <input type="text" placeholder="AAPL" maxlength="12">
    <div class="price">&mdash;</div>
    <div class="meta">no symbol</div>
    <div class="history"></div>
  </div>

  <div class="card ring-card" data-ring="middle">
    <div class="ring-head"><h2>Middle</h2><div class="dot"></div></div>
    <input type="text" placeholder="IBM" maxlength="12">
    <div class="price">&mdash;</div>
    <div class="meta">no symbol</div>
    <div class="history"></div>
  </div>

  <div class="card ring-card" data-ring="inner">
    <div class="ring-head"><h2>Inner</h2><div class="dot"></div></div>
    <input type="text" placeholder="SPY" maxlength="12">
    <div class="price">&mdash;</div>
    <div class="meta">no symbol</div>
    <div class="history"></div>
  </div>

  <div class="card">
    <h2 style="margin:0 0 8px;font-size:12px;color:#aaa;text-transform:uppercase;letter-spacing:.08em">Poll every</h2>
    <div class="intervals" id="intervals">
      <button data-interval="10">10s</button>
      <button data-interval="30" class="active">30s</button>
      <button data-interval="60">60s</button>
      <button data-interval="300">5m</button>
    </div>
    <div class="controls">
      <button class="primary" id="start-btn">Start</button>
      <button class="danger" id="stop-btn" disabled>Stop</button>
    </div>
    <div id="status">not running</div>
  </div>
</div>

<script type="module">
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));

const state = { interval: 30, running: false };

function setActiveInterval(v) {
  state.interval = v;
  for (const b of $$('#intervals button')) {
    b.classList.toggle('active', parseInt(b.dataset.interval, 10) === v);
  }
}

for (const b of $$('#intervals button')) {
  b.onclick = () => { if (!state.running) setActiveInterval(parseInt(b.dataset.interval, 10)); };
}

function setInputsReadonly(ro) {
  for (const inp of $$('.ring-card input[type=text]')) inp.readOnly = ro;
  for (const b of $$('#intervals button')) b.disabled = ro;
}

async function api(path, body) {
  const opts = body
    ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}
    : {method: body === null ? 'POST' : 'GET'};
  if (path === '/api/ticker/start' || path === '/api/ticker/stop' || path === '/api/power') {
    opts.method = 'POST';
    if (body !== undefined && body !== null) opts.body = JSON.stringify(body);
    if (!opts.headers) opts.headers = {'Content-Type': 'application/json'};
  }
  const r = await fetch(path, opts);
  return r.json();
}

function dotColor(hex) {
  if (!hex || hex === '000000') return '#333';
  return '#' + hex;
}

function arrow(direction) {
  if (direction === 'up') return '↑';
  if (direction === 'down') return '↓';
  if (direction === 'error') return '!';
  return '·';
}

function timeAgo(iso) {
  if (!iso) return '';
  const sec = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (sec < 60) return sec + 's ago';
  return Math.floor(sec / 60) + 'm ago';
}

function renderRing(ring, data) {
  const card = $(`.ring-card[data-ring="${ring}"]`);
  const dot = card.querySelector('.dot');
  const price = card.querySelector('.price');
  const meta = card.querySelector('.meta');
  const history = card.querySelector('.history');
  const input = card.querySelector('input');

  if (!data) {
    dot.style.background = '#333';
    price.textContent = '—';
    meta.textContent = 'no symbol';
    history.textContent = '';
    return;
  }
  input.value = data.symbol;
  dot.style.background = dotColor(data.color);
  if (data.current_price !== null) {
    price.textContent = '$' + data.current_price.toFixed(2);
  } else {
    price.textContent = '—';
  }
  const dir = (data.recent_ticks[0] && data.recent_ticks[0].direction) || '';
  meta.textContent = `${arrow(dir)} ${data.color === '00FF00' ? 'green' : data.color === 'FF0000' ? 'red' : data.color === 'FFFF00' ? 'yellow' : data.color === 'FFFFFF' ? 'white' : 'off'} · updated ${timeAgo(data.last_fetch_at)}`;
  history.textContent = data.recent_ticks.slice(0, 5).map(t =>
    `${arrow(t.direction)}$${t.price.toFixed(2)} ${t.at.slice(11, 16)}`
  ).join(' · ');
}

function renderState(s) {
  state.running = s.running;
  setInputsReadonly(s.running);
  $('#start-btn').disabled = s.running;
  $('#stop-btn').disabled = !s.running;
  if (!s.running) {
    $('#status').textContent = 'not running';
    return;
  }
  if (s.interval) setActiveInterval(s.interval);
  for (const ring of ['outer', 'middle', 'inner']) {
    renderRing(ring, s.rings ? s.rings[ring] : null);
  }
  $('#status').textContent = `running since ${s.since ? s.since.slice(11, 16) : '?'}`;
}

async function refresh() {
  const j = await fetch('/api/ticker/state').then(r => r.json());
  renderState(j);
}

$('#start-btn').onclick = async () => {
  const body = {interval: state.interval};
  for (const card of $$('.ring-card')) {
    const sym = card.querySelector('input').value.trim();
    if (sym) body[card.dataset.ring] = sym;
  }
  if (!body.outer && !body.middle && !body.inner) {
    $('#status').textContent = 'enter at least one symbol';
    return;
  }
  $('#status').textContent = 'starting...';
  const j = await api('/api/ticker/start', body);
  if (!j.ok) { $('#status').textContent = 'error: ' + j.error; return; }
  await refresh();
};
$('#stop-btn').onclick = async () => {
  await api('/api/ticker/stop', null);
  await refresh();
};
$('#pwr-on').onclick = () => api('/api/power', {on: true});
$('#pwr-off').onclick = () => api('/api/power', {on: false});

// Initial render + 5-second poll.
refresh();
setInterval(refresh, 5000);
</script></body></html>"""
```

- [ ] **Step 2: Add the Ticker tab to `_PAGE`**

Locate this block inside the existing `_PAGE` (it was added in the DIY work as Task 7):

```html
      <div class="tabs">
        <a href="/" class="active" style="color:#5fd9d9;background:#1f2a2a;padding:6px 12px;border-radius:8px;text-decoration:none;font-weight:700">🎨 Workshop</a>
        <a href="/diy" style="color:#aaa;padding:6px 12px;border-radius:8px;text-decoration:none">✏️ DIY</a>
      </div>
```

Replace it with:

```html
      <div class="tabs">
        <a href="/" class="active" style="color:#5fd9d9;background:#1f2a2a;padding:6px 12px;border-radius:8px;text-decoration:none;font-weight:700">🎨 Workshop</a>
        <a href="/diy" style="color:#aaa;padding:6px 12px;border-radius:8px;text-decoration:none">✏️ DIY</a>
        <a href="/ticker" style="color:#aaa;padding:6px 12px;border-radius:8px;text-decoration:none">📈 Ticker</a>
      </div>
```

- [ ] **Step 3: Add the Ticker tab to `_PAGE_DIY`**

Locate this block inside `_PAGE_DIY`:

```html
    <div class="tabs">
      <a href="/">🎨 Workshop</a>
      <a href="/diy" class="active">✏️ DIY</a>
    </div>
```

(Note: the existing DIY page emits emojis as HTML numeric entities — `&#x1F3A8;` etc. — because of how the implementer chose to encode them in Task 6. Match WHATEVER convention the existing file uses; do not change the existing emoji encoding.)

Replace the `</div>` end with the new anchor inserted just before it:

```html
    <div class="tabs">
      <a href="/">🎨 Workshop</a>
      <a href="/diy" class="active">✏️ DIY</a>
      <a href="/ticker">📈 Ticker</a>
    </div>
```

(If the file uses HTML entities for the existing emojis, use HTML entities for the new emoji too: `&#x1F4C8;`. Mirror exactly whatever encoding is already present.)

- [ ] **Step 4: Smoke-test the page constant**

Run:
```bash
.venv/bin/python -c "
import workshop
p = workshop._PAGE_TICKER
for marker in ('Lepro Ticker', 'ring-card', 'data-ring=\"outer\"',
               'data-ring=\"middle\"', 'data-ring=\"inner\"',
               'data-interval=\"30\"', 'start-btn', 'stop-btn',
               'pwr-on', '/api/ticker/start', '/api/ticker/stop',
               '/api/ticker/state'):
    assert marker in p, 'missing ' + repr(marker)

# Tab nav reach: every page has the three tabs.
for name in ['_PAGE', '_PAGE_DIY', '_PAGE_TICKER']:
    page = getattr(workshop, name)
    assert 'href=\"/\"' in page, name + ' missing Workshop tab'
    assert 'href=\"/diy\"' in page, name + ' missing DIY tab'
    assert 'href=\"/ticker\"' in page, name + ' missing Ticker tab'
print('ticker page + tabs all good')
"
```
Expected: prints `ticker page + tabs all good`.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add workshop.py
git commit -m "feat: ticker page UI (3 ring cards + interval picker + start/stop) + tab nav"
```

---

### Task 9: README update + final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append the ticker paragraph to the existing `## Preset workshop` section**

Locate the existing `## Preset workshop` section. The DIY paragraph was added at the end of it by Task 8 of the DIY plan. Append THIS paragraph after the DIY paragraph (still inside the same section, before the next `##`):

```markdown

A **Stock Ticker** page is available at `http://<vm-ip>:8081/ticker` — assign up
to three Yahoo Finance symbols (one per concentric ring), pick a poll interval
(10s / 30s / 60s / 5m), and Start. Each ring shows its symbol's most recent
direction as a solid color (green ↑, red ↓, yellow on fetch failure, white
baseline, off if no symbol), and every tick triggers a 5-second whole-lamp
breathe flash in the new color. Stop powers the lamp off. While the ticker is
running, the DIY paint endpoint and the workshop preview endpoint return HTTP
409 — power, brightness, and saves stay available.
```

- [ ] **Step 2: Verify it landed in the right place**

Run: `grep -B1 -A2 "Stock Ticker" README.md`
Expected: shows the new paragraph nested inside `## Preset workshop`, not after `## Protocol notes`.

- [ ] **Step 3: Final full-suite run**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 4: Final app build smoke**

Run:
```bash
.venv/bin/python -c "
import workshop
app = workshop.build_app()
print('routes:', len(list(app.router.routes())))
print('build ok')
"
```
Expected: prints `routes: 21` and `build ok`.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document the stock ticker at /ticker"
```

---

## Self-Review

**Spec coverage (vs. spec):**
- Lamp behavior: per-ring solids + 5s whole-lamp Breathe flash → Tasks 1, 2, 5 ✓
- `decide_ring_color` pure → Task 1 ✓
- `build_ticker_d50` pure with single-color flash override → Task 2 ✓
- `fetch_price` mirroring stock_lamp → Task 3 ✓
- `TickerSession` state model + snapshot shape → Task 4 ✓
- Async polling loop with cancellation → Task 5 ✓
- POST /api/ticker/start with first-fetch validation + 409 if running → Task 6 ✓
- POST /api/ticker/stop powers lamp off → Task 6 (delegates to `TickerSession.stop`) ✓
- GET /api/ticker/state always available → Task 6 ✓
- Mutex 409 on /api/diy/paint + /api/preview → Task 6 ✓
- GET /ticker page route → Task 7 ✓
- Three ring cards + interval picker + Start/Stop + history → Task 8 ✓
- Tab nav across all three pages → Task 8 ✓
- README docs → Task 9 ✓

**Placeholder scan:** Task 2 deliberately walks the engineer through a verbose test then has them clean it up in Step 4 of the same task — this is intentional pedagogy, not a placeholder. Otherwise no TBD/TODO. The two `# noqa: BLE001` comments are intentional (mirrors stock_lamp.py's convention).

**Type consistency:**
- `TickerSession.__init__(client, symbols, interval)` matches the call site in `api_ticker_start` (Task 6) and the test setup (Tasks 4-5).
- `snapshot()` shape matches what `api_ticker_state` returns AND what the page JS in Task 8 reads (`rings.outer.color`, `recent_ticks`, etc.).
- Color constants `COLOR_OFF / COLOR_WHITE / COLOR_GREEN / COLOR_RED / COLOR_YELLOW` used identically across `decide_ring_color`, `build_ticker_d50`, `_tick_once`, and (as their hex literals) the page JS.
- Route registration in `build_app` matches the route handler function names.

**Notes for the implementer:**
- `_flash_until` is stored as a real ISO timestamp 5 seconds in the future. `_is_flashing()` compares it with `datetime.fromisoformat`. Keep the field as `None` when not flashing.
- Task 6's mutex helper raises `web.HTTPConflict` directly; the `except web.HTTPConflict: raise` clause must come BEFORE the generic `except (LeproError, ValueError, ...)` clause in both `api_diy_paint` and `api_preview`, or Python's MRO will eat the conflict first.
- `pytest-asyncio` may not be installed yet — Task 5 explicitly checks for it and installs it if missing.
- The page is single-poll (no foreground/background detection). 5-second poll = 12 reqs/min — fine for LAN, but if you ever expose this server publicly add a visibilitychange listener.
