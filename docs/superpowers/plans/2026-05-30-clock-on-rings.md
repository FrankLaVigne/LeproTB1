# Clock on the Rings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/clock` page and background session that turns the Lepro lamp into a three-handed analog clock: outer ring = seconds, middle = minutes, inner = hours. Dot mode, per-ring configurable colors, 12h default with 24h toggle, 1-second cadence.

**Architecture:** New `clock.py` with two pure helpers (`compute_positions`, `build_clock_leds`) plus a `ClockSession` class modelled exactly on `TickerSession`. `workshop.py` adds four routes (`/api/clock/{start,stop,state}` + `/clock`), wires the 5th nav tab into all existing pages, and adds a mutex against `/api/diy/paint` and `/api/preview` (mirrors the ticker mutex). `static/lamp-utils.js` gets a tiny `computeClockPositions` helper so the page can render the visualizer client-side at 1 Hz without polling the server.

**Tech Stack:** Python 3.12, existing `aiohttp` + `lepro.LeproClient`. No new deps. Vanilla HTML/CSS/JS.

---

## File Structure

- **`clock.py`** (new) — `compute_positions(now, mode) -> dict`, `build_clock_leds(positions, colors) -> list`, `ClockSession` class (init/start/stop/snapshot/_tick_once/_run). Re-uses `workshop.apply_lamp_rotation` and `workshop.build_d50_from_leds` via a local import (mirrors `ticker.build_ticker_d50`'s pattern).
- **`workshop.py`** (modify) — add `_clock_session` module global, `_check_clock_mutex()`, four route handlers, `_PAGE_CLOCK` inline HTML, 5th tab on all 4 existing pages, mutex calls in `api_diy_paint` + `api_preview`, integrate clock-stop into `api_power(off)` and `api_stop`, register routes.
- **`static/lamp-utils.js`** (modify) — append `computeClockPositions(now, mode)` JS helper for the page-side visualizer.
- **`tests/test_clock.py`** (new) — pure-function + async tests (~10).
- **`README.md`** (modify) — append Clock section to the Preset workshop block.

Eight tasks below — pure helpers first (TDD), then the session, then routes, then page, then docs.

---

### Task 1: `compute_positions` pure function

**Files:**
- Create: `clock.py`
- Create: `tests/test_clock.py`

- [ ] **Step 1: Create the failing tests**

Create `tests/test_clock.py`:

```python
"""Unit tests for clock.py — pure-function and snapshot-shape coverage."""

from datetime import datetime

import pytest

import clock


# --- compute_positions tests --------------------------------------------------


def test_compute_positions_midnight_12h_all_zero():
    # 2026-01-01 00:00:00 -> seconds=0, minutes=0, hour=0 -> all positions 0.
    now = datetime(2026, 1, 1, 0, 0, 0)
    pos = clock.compute_positions(now, mode="12h")
    assert pos == {"outer": 0, "middle": 0, "inner": 0}


def test_compute_positions_noon_12h_inner_at_zero():
    # Noon: hour=12, 12 % 12 == 0 -> inner dot at 0 (top of inner ring).
    now = datetime(2026, 1, 1, 12, 0, 0)
    pos = clock.compute_positions(now, mode="12h")
    assert pos == {"outer": 0, "middle": 0, "inner": 0}


def test_compute_positions_noon_24h_inner_halfway():
    # 24h mode at 12:00 -> inner is halfway around 46 = 23.
    now = datetime(2026, 1, 1, 12, 0, 0)
    pos = clock.compute_positions(now, mode="24h")
    assert pos == {"outer": 0, "middle": 0, "inner": 23}


def test_compute_positions_seconds_advance():
    # 30 seconds -> outer at half of 88 = 44.
    now = datetime(2026, 1, 1, 0, 0, 30)
    pos = clock.compute_positions(now, mode="12h")
    assert pos["outer"] == 44


def test_compute_positions_minute_drifts_with_seconds():
    # minute=5 second=30 -> middle is between minute-5 and minute-6.
    # minute-5 alone: round(5 * 62/60) = round(5.166) = 5.
    # minute-5 + half-second drift: round(5.5 * 62/60) = round(5.683) = 6.
    now = datetime(2026, 1, 1, 0, 5, 30)
    pos = clock.compute_positions(now, mode="12h")
    assert pos["middle"] == 6


def test_compute_positions_hour_drifts_with_minutes():
    # 8:30 in 12h -> inner is between hour-8 and hour-9.
    # hour-8 alone: round(8 * 46/12) = round(30.666) = 31.
    # hour-8 + half-hour drift: round(8.5 * 46/12) = round(32.583) = 33.
    now = datetime(2026, 1, 1, 8, 30, 0)
    pos = clock.compute_positions(now, mode="12h")
    assert pos["inner"] == 33


def test_compute_positions_rejects_unknown_mode():
    with pytest.raises(ValueError):
        clock.compute_positions(datetime(2026, 1, 1, 0, 0, 0), mode="invalid")


def test_compute_positions_wraps_mod_ring_size():
    # The "% ring_size" guard catches edge cases where rounding lands at the
    # ring size (e.g., second=59 with fractional carry could round up to 88).
    now = datetime(2026, 1, 1, 0, 0, 59, 999_000)
    pos = clock.compute_positions(now, mode="12h")
    assert 0 <= pos["outer"] < 88
    assert 0 <= pos["middle"] < 62
    assert 0 <= pos["inner"] < 46
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_clock.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'clock'`.

- [ ] **Step 3: Create `clock.py` with the function**

Create `/home/frank/lepro/clock.py`:

```python
"""Lepro clock-on-rings session — pure helpers + ClockSession class.

Turns the lamp into a three-handed analog clock: outer = seconds (88 LEDs),
middle = minutes (62 LEDs), inner = hours (46 LEDs). Dot mode (one bright
LED per ring), per-ring configurable colors, 12h default with 24h toggle,
1-second cadence.
"""

from __future__ import annotations

from datetime import datetime


_VALID_MODES = ("12h", "24h")


def compute_positions(now: datetime, mode: str = "12h") -> dict:
    """Return per-ring page-space LED indices for the clock dots at ``now``.

    ``mode`` is "12h" (default) or "24h". Adds the fractional carry from
    the next-finer time unit so the minute and hour hands drift slowly
    between marks like an analog clock. Each position is `% ring_size`
    so an edge-case round-up at second 59.999... wraps to 0 cleanly.
    """
    if mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of {_VALID_MODES}; got {mode!r}")

    sec_frac = now.second + now.microsecond / 1_000_000
    outer = round(sec_frac * 88 / 60) % 88

    min_frac = now.minute + sec_frac / 60
    middle = round(min_frac * 62 / 60) % 62

    if mode == "12h":
        hour_unit = (now.hour % 12) + now.minute / 60
        hours_per_cycle = 12
    else:
        hour_unit = now.hour + now.minute / 60
        hours_per_cycle = 24
    inner = round(hour_unit * 46 / hours_per_cycle) % 46

    return {"outer": outer, "middle": middle, "inner": inner}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_clock.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Verify the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add clock.py tests/test_clock.py
git commit -m "feat: clock.compute_positions (pure: datetime -> per-ring LED indices)"
```

---

### Task 2: `build_clock_leds` pure function

**Files:**
- Modify: `clock.py` (append below `compute_positions`)
- Modify: `tests/test_clock.py` (append)

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_clock.py`:

```python


# --- build_clock_leds tests ---------------------------------------------------


def _colors(outer="FF0000", middle="00FF00", inner="0000FF"):
    return {"outer": outer, "middle": middle, "inner": inner}


def test_build_clock_leds_paints_exactly_three_lit_leds():
    positions = {"outer": 0, "middle": 0, "inner": 0}
    leds = clock.build_clock_leds(positions, _colors())
    assert len(leds) == 196
    lit = [(i, c) for i, c in enumerate(leds) if c is not None]
    assert len(lit) == 3


def test_build_clock_leds_uses_correct_colors_at_correct_indices():
    positions = {"outer": 5, "middle": 7, "inner": 9}
    leds = clock.build_clock_leds(positions, _colors())
    # Outer ring covers indices 0..87, middle 88..149, inner 150..195.
    assert leds[5] == "FF0000"
    assert leds[88 + 7] == "00FF00"
    assert leds[150 + 9] == "0000FF"
    # And everything else is None (off).
    other = [c for i, c in enumerate(leds)
             if i not in (5, 88 + 7, 150 + 9)]
    assert all(c is None for c in other)


def test_build_clock_leds_supports_zero_positions():
    positions = {"outer": 0, "middle": 0, "inner": 0}
    leds = clock.build_clock_leds(positions, _colors())
    assert leds[0] == "FF0000"
    assert leds[88] == "00FF00"
    assert leds[150] == "0000FF"


def test_build_clock_leds_supports_last_positions():
    # outer max = 87, middle max = 61, inner max = 45
    positions = {"outer": 87, "middle": 61, "inner": 45}
    leds = clock.build_clock_leds(positions, _colors())
    assert leds[87] == "FF0000"
    assert leds[149] == "00FF00"
    assert leds[195] == "0000FF"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_clock.py -k build_clock_leds -v`
Expected: FAIL with `AttributeError: module 'clock' has no attribute 'build_clock_leds'`.

- [ ] **Step 3: Implement** — append to `clock.py`:

```python
def build_clock_leds(positions: dict, colors: dict) -> list:
    """Return a 196-entry page-space LED array with one dot per ring.

    Positions are 0-indexed within each ring (outer 0..87, middle 0..61,
    inner 0..45). The caller is responsible for applying the lamp
    rotation and encoding via ``workshop.build_d50_from_leds`` before
    sending. Returns a fresh list (callers may mutate).
    """
    leds = [None] * 196
    leds[positions["outer"]] = colors["outer"]
    leds[88 + positions["middle"]] = colors["middle"]
    leds[150 + positions["inner"]] = colors["inner"]
    return leds
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_clock.py -k build_clock_leds -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Verify the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add clock.py tests/test_clock.py
git commit -m "feat: clock.build_clock_leds (page-space 196-LED array, one dot per ring)"
```

---

### Task 3: `ClockSession` state + snapshot

**Files:**
- Modify: `clock.py` (append below `build_clock_leds`)
- Modify: `tests/test_clock.py` (append)

This task adds the data model and `snapshot()`. The async loop comes in Task 4.

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_clock.py`:

```python


# --- ClockSession state tests -------------------------------------------------


def test_clock_session_initial_snapshot_not_running():
    sess = clock.ClockSession(client=None, colors=_colors(), mode="12h")
    snap = sess.snapshot()
    assert snap["running"] is False
    assert snap["since"] is None
    assert snap["mode"] == "12h"
    assert snap["colors"] == _colors()
    assert snap["now_displayed"] is None


def test_clock_session_defaults_to_12h_with_red_green_blue():
    # Constructing with no colors at all -> defaults.
    sess = clock.ClockSession(client=None)
    snap = sess.snapshot()
    assert snap["mode"] == "12h"
    assert snap["colors"] == {"outer": "FF0000", "middle": "00FF00", "inner": "0000FF"}


def test_clock_session_rejects_bad_color():
    with pytest.raises(ValueError):
        clock.ClockSession(client=None,
                            colors={"outer": "ZZZ", "middle": "00FF00", "inner": "0000FF"})


def test_clock_session_rejects_bad_mode():
    with pytest.raises(ValueError):
        clock.ClockSession(client=None, mode="13h")


def test_clock_session_snapshot_json_serialisable():
    import json
    sess = clock.ClockSession(client=None, colors=_colors(), mode="24h")
    assert json.dumps(sess.snapshot())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_clock.py -k clock_session -v`
Expected: FAIL with `AttributeError: module 'clock' has no attribute 'ClockSession'`.

- [ ] **Step 3: Implement** — append to `clock.py`:

```python
import re
from typing import Optional


_HEX6 = re.compile(r"^[0-9A-Fa-f]{6}$")

_DEFAULT_COLORS = {
    "outer": "FF0000",
    "middle": "00FF00",
    "inner": "0000FF",
}


class ClockSession:
    """Holds the clock's per-ring colors + mode + the polling task.

    The async loop lives in ``start()`` / ``_run()`` (added Task 4).
    Snapshot is JSON-serialisable for the /api/clock/state endpoint.
    """

    def __init__(self, client, colors: Optional[dict] = None, mode: str = "12h"):
        if mode not in _VALID_MODES:
            raise ValueError(f"mode must be one of {_VALID_MODES}; got {mode!r}")
        merged = dict(_DEFAULT_COLORS)
        if colors:
            merged.update(colors)
        for ring, value in merged.items():
            if not _HEX6.match(value):
                raise ValueError(f"colors[{ring!r}] = {value!r} is not a 6-hex string")
        self._client = client
        self._colors = merged
        self._mode = mode
        self._since: Optional[str] = None
        self._task = None
        self._last_displayed: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def snapshot(self) -> dict:
        return {
            "running": self.running,
            "since": self._since,
            "mode": self._mode,
            "colors": dict(self._colors),
            "now_displayed": self._last_displayed,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_clock.py -k clock_session -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Verify the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add clock.py tests/test_clock.py
git commit -m "feat: ClockSession state + snapshot (no async loop yet)"
```

---

### Task 4: `ClockSession.start` / `.stop` / `._tick_once` — async loop

**Files:**
- Modify: `clock.py` (extend the class with async methods)
- Modify: `tests/test_clock.py` (append)

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_clock.py`:

```python


# --- ClockSession async tests -------------------------------------------------


class _FakeClient:
    def __init__(self):
        self.sent = []

    async def send_raw(self, payload):
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_session_tick_once_sends_d50_with_three_lit_leds():
    client = _FakeClient()
    sess = clock.ClockSession(client=client, colors=_colors(), mode="12h")
    await sess._tick_once()
    assert len(client.sent) == 1
    payload = client.sent[0]
    assert payload["d1"] == 1
    assert payload["d2"] == 2
    # The d50 should contain the three colors (rotated + RLE-encoded, but
    # the colors themselves appear in the palette either way).
    d50 = payload["d50"]
    assert "FF0000" in d50
    assert "00FF00" in d50
    assert "0000FF" in d50


@pytest.mark.asyncio
async def test_session_tick_once_updates_now_displayed():
    client = _FakeClient()
    sess = clock.ClockSession(client=client)
    assert sess.snapshot()["now_displayed"] is None
    await sess._tick_once()
    assert sess.snapshot()["now_displayed"] is not None


@pytest.mark.asyncio
async def test_session_start_then_stop_lifecycle():
    client = _FakeClient()
    sess = clock.ClockSession(client=client)
    assert sess.running is False
    await sess.start()
    assert sess.running is True
    await sess.stop()
    assert sess.running is False


@pytest.mark.asyncio
async def test_session_stop_leaves_lamp_state_unchanged():
    # Unlike the ticker, the clock's stop does NOT send d1:0 — it leaves the
    # last frame on the lamp.
    client = _FakeClient()
    sess = clock.ClockSession(client=client)
    await sess.start()
    await sess.stop()
    # No "{'d1': 0}" payload should appear in client.sent.
    assert {"d1": 0} not in client.sent
    for p in client.sent:
        # Every sent payload has d1=1 (clock keeps the lamp on).
        assert p.get("d1") != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_clock.py -v`
Expected: FAIL with attribute errors on `_tick_once`, `start`, `stop`.

- [ ] **Step 3: Extend `ClockSession`** — add these methods inside the class in `clock.py`:

```python
    async def _tick_once(self) -> None:
        """Compute the current clock face and send it to the lamp.

        Imports workshop locally to avoid a circular import — workshop loads
        clock, but the rotation + d50 helpers we need live at workshop's
        module scope.
        """
        from workshop import apply_lamp_rotation, build_d50_from_leds  # noqa: PLC0415

        now = datetime.now()
        positions = compute_positions(now, mode=self._mode)
        page_leds = build_clock_leds(positions, self._colors)
        physical_leds = apply_lamp_rotation(page_leds)
        d50 = build_d50_from_leds(physical_leds, "Steady", 50)
        self._last_displayed = now.isoformat(timespec="seconds")
        try:
            await self._client.send_raw({"d1": 1, "d2": 2, "d50": d50})
        except Exception:  # noqa: BLE001 — log and retry next tick
            pass

    async def start(self) -> None:
        """Spawn the background polling loop."""
        import asyncio
        if self.running:
            return
        self._since = datetime.now().isoformat(timespec="seconds")
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run())

    async def _run(self) -> None:
        """The loop body. Cancelled by stop()."""
        import asyncio
        try:
            while True:
                await self._tick_once()
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """Cancel the polling loop. Leaves the lamp in its current state."""
        import asyncio
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self._since = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_clock.py -v`
Expected: PASS (all clock tests including 4 new async ones).

- [ ] **Step 5: Verify the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add clock.py tests/test_clock.py
git commit -m "feat: ClockSession.start/stop/_tick_once (1Hz async loop, leaves frame on stop)"
```

---

### Task 5: Backend routes — `/api/clock/{start,stop,state}` + mutex

**Files:**
- Modify: `workshop.py` — add four route handlers + mutex helper + integrate into power/stop, register routes.

- [ ] **Step 1: Add the handlers above the existing `_PAGE = """..."""` line**

Place this block immediately after `api_lamp_state` (the existing clock-neighbouring endpoint) and BEFORE `index`:

```python
# --- Clock endpoints ---------------------------------------------------------

import clock as _clock_mod

_clock_session = None  # type: ignore[assignment]


def _check_clock_mutex():
    """Raise web.HTTPConflict if the clock is running."""
    global _clock_session
    if _clock_session is not None and _clock_session.running:
        raise web.HTTPConflict(
            text='{"ok": false, "error": "clock is running; stop it first"}',
            content_type="application/json",
        )


async def api_clock_start(req):
    global _clock_session
    try:
        body = await req.json()
        colors = body.get("colors") or {}
        mode = body.get("mode", "12h")
        if _clock_session is not None and _clock_session.running:
            return web.json_response(
                {"ok": False, "error": "clock already running"}, status=409)
        # Ticker mutex too — only one lamp-driving session at a time.
        if _ticker_session is not None and _ticker_session.running:
            return web.json_response(
                {"ok": False, "error": "stock ticker is running; stop it first"},
                status=409)
        sess = _clock_mod.ClockSession(_client, colors=colors, mode=mode)
        await sess.start()
        _clock_session = sess
        snap = sess.snapshot()
        return web.json_response({"ok": True, "since": snap["since"], "mode": snap["mode"]})
    except (ValueError, KeyError, TypeError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def api_clock_stop(_req):
    global _clock_session
    if _clock_session is None:
        return web.json_response({"ok": True})
    await _clock_session.stop()
    _clock_session = None
    return web.json_response({"ok": True})


async def api_clock_state(_req):
    if _clock_session is None:
        return web.json_response({
            "running": False, "since": None, "mode": None,
            "colors": None, "now_displayed": None,
        })
    return web.json_response(_clock_session.snapshot())


async def index_clock(_req):
    return web.Response(text=_PAGE_CLOCK, content_type="text/html")


# Real clock UI inlined in Task 7.
_PAGE_CLOCK = "<!doctype html><title>clock</title><body>clock loading...</body>"
```

- [ ] **Step 2: Wire `_check_clock_mutex()` into `api_diy_paint` and `api_preview`**

In `workshop.py`, find `api_diy_paint`. It currently has:

```python
async def api_diy_paint(req):
    try:
        body = await req.json()
        _check_ticker_mutex()
        ...
```

Add `_check_clock_mutex()` immediately after `_check_ticker_mutex()`:

```python
async def api_diy_paint(req):
    try:
        body = await req.json()
        _check_ticker_mutex()
        _check_clock_mutex()
        ...
```

Do the same edit for `api_preview` — find its `_check_ticker_mutex()` call and add `_check_clock_mutex()` right after.

- [ ] **Step 3: Wire clock-stop into `api_power` (off path) and `api_stop`**

Find `api_power` in `workshop.py`. It already stops the ticker when `on=False`:

```python
async def api_power(req):
    # ... existing logic ...
    if not on:
        if _ticker_session is not None and _ticker_session.running:
            await _ticker_session.stop()
            _ticker_session = None
    # ... existing power-off send ...
```

Add a parallel `_clock_session` teardown right after the ticker one:

```python
async def api_power(req):
    # ... existing logic ...
    if not on:
        global _ticker_session, _clock_session
        if _ticker_session is not None and _ticker_session.running:
            await _ticker_session.stop()
            _ticker_session = None
        if _clock_session is not None and _clock_session.running:
            await _clock_session.stop()
            _clock_session = None
    # ... existing power-off send ...
```

(`api_power` already has a `global _ticker_session` — extend it to include `_clock_session`. The exact placement may differ from the snippet above; the rule is: anywhere `_ticker_session` is checked and stopped, also check and stop `_clock_session`.)

Same edit for `api_stop`: find the existing ticker-stop block at the top and add a clock-stop block in the same place:

```python
async def api_stop(_req):
    global _preview_task, _ticker_session, _clock_session
    if _ticker_session is not None and _ticker_session.running:
        await _ticker_session.stop()
        _ticker_session = None
    if _clock_session is not None and _clock_session.running:
        await _clock_session.stop()
        _clock_session = None
    # ... existing preview-task cancellation logic ...
```

- [ ] **Step 4: Register the four new routes in `build_app`**

In `build_app`'s `app.add_routes([...])`, append FOUR entries after the existing lamp-state route:

```python
        web.get("/api/lamp/state", api_lamp_state),
        web.get("/clock", index_clock),
        web.post("/api/clock/start", api_clock_start),
        web.post("/api/clock/stop", api_clock_stop),
        web.get("/api/clock/state", api_clock_state),
    ])
    # Static assets — currently just lamp-utils.js shared by /diy and /state.
    app.router.add_static("/static", _HERE / "static")
```

- [ ] **Step 5: Smoke-test routes**

Run:
```bash
.venv/bin/python -c "
import workshop
app = workshop.build_app()
got = sorted(r.method + ' ' + str(r.resource.canonical) for r in app.router.routes())
must_have = ['POST /api/clock/start', 'POST /api/clock/stop',
             'GET /api/clock/state', 'GET /clock', 'HEAD /clock',
             'HEAD /api/clock/state']
for path in must_have:
    assert path in got, f'missing {path!r}'
print('clock routes registered; total routes:', len(got))
"
```
Expected: prints something like `clock routes registered; total routes: 31`.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 7: Commit**

```bash
git add workshop.py
git commit -m "feat: clock backend (start/stop/state) + mutex on /diy/paint & /preview"
```

---

### Task 6: `computeClockPositions` helper in `static/lamp-utils.js`

**Files:**
- Modify: `static/lamp-utils.js` — append the JS-side position computer.

- [ ] **Step 1: Append to `static/lamp-utils.js`**

Append at the end of `/home/frank/lepro/static/lamp-utils.js`:

```javascript

// Client-side clock position math (mirrors clock.compute_positions in
// Python). Used by /clock's visualizer to render at 1 Hz without
// polling the server. `now` is a JS Date; `mode` is "12h" or "24h".
// Returns {outer, middle, inner} per-ring page-space LED indices.
export function computeClockPositions(now, mode) {
  if (mode !== "12h" && mode !== "24h") {
    throw new Error(`mode must be "12h" or "24h", got ${mode}`);
  }
  const secFrac = now.getSeconds() + now.getMilliseconds() / 1000;
  const outer = Math.round(secFrac * 88 / 60) % 88;

  const minFrac = now.getMinutes() + secFrac / 60;
  const middle = Math.round(minFrac * 62 / 60) % 62;

  let hourUnit, cycle;
  if (mode === "12h") {
    hourUnit = (now.getHours() % 12) + now.getMinutes() / 60;
    cycle = 12;
  } else {
    hourUnit = now.getHours() + now.getMinutes() / 60;
    cycle = 24;
  }
  const inner = Math.round(hourUnit * 46 / cycle) % 46;

  return { outer, middle, inner };
}
```

- [ ] **Step 2: Smoke-test the helper is importable**

Run:
```bash
.venv/bin/python -c "
content = open('static/lamp-utils.js').read()
assert 'computeClockPositions' in content, 'helper not present'
assert 'export function computeClockPositions' in content, 'helper not exported'
print('lamp-utils.js updated with computeClockPositions')
"
```
Expected: prints `lamp-utils.js updated with computeClockPositions`.

- [ ] **Step 3: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 4: Commit**

```bash
git add static/lamp-utils.js
git commit -m "feat(lamp-utils): computeClockPositions for client-side visualizer"
```

---

### Task 7: The Clock page (HTML/CSS/JS)

**Files:**
- Modify: `workshop.py` — replace the `_PAGE_CLOCK` placeholder string + add the 5th tab to `_PAGE`, `_PAGE_DIY`, `_PAGE_TICKER`, `_PAGE_STATE`.

- [ ] **Step 1: Replace `_PAGE_CLOCK`**

Find the line:
```python
_PAGE_CLOCK = "<!doctype html><title>clock</title><body>clock loading...</body>"
```

Replace with:

```python
_PAGE_CLOCK = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lepro Clock</title>
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
  .lamp-canvas { display: flex; justify-content: center; padding: 6px 0; }
  h2 { font-size: 12px; margin: 0 0 8px; color: #aaa;
       text-transform: uppercase; letter-spacing: 0.08em; }
  .color-row { display: grid; grid-template-columns: 90px 50px 1fr;
               align-items: center; gap: 10px; margin: 8px 0; }
  .color-row label { font-size: 13px; color: #ccc; }
  .color-row input[type=color] { width: 44px; height: 32px;
                                  border: 2px solid #333; border-radius: 8px;
                                  cursor: pointer; background: none; padding: 0; }
  .color-row .hex { font: 12px ui-monospace, monospace; color: #888; }
  .mode-toggle { display: flex; gap: 4px; background: #2a2a30;
                 padding: 4px; border-radius: 8px; max-width: 180px; }
  .mode-toggle button { flex: 1; padding: 6px 12px; border: 0;
                        border-radius: 6px; background: transparent;
                        color: #eee; cursor: pointer; font: inherit; }
  .mode-toggle button.active { background: #5fd9d9; color: #111; font-weight: 700; }
  .controls { display: flex; gap: 8px; margin-top: 8px; }
  .controls button { flex: 1; padding: 12px; border: 0; border-radius: 10px;
                     background: #2a2a30; color: #eee; cursor: pointer;
                     font: inherit; font-weight: 700; }
  .controls button.primary { background: #2c8f4f; color: #fff; }
  .controls button.danger { background: #8f2c2c; color: #fff; }
  .controls button:disabled { opacity: 0.4; cursor: not-allowed; }
  #status { font-size: 12px; color: #777; margin-top: 10px; min-height: 1.2em; }
  .clock-readout { font: 600 28px ui-monospace, monospace;
                   text-align: center; color: #eee; margin: 4px 0 10px; }
</style></head>
<body><div class="wrap">
  <div class="header">
    <div class="tabs">
      <a href="/">&#x1F3A8; Presets</a>
      <a href="/diy">&#x270F;&#xFE0F; DIY</a>
      <a href="/ticker">&#x1F4C8; Ticker</a>
      <a href="/state">&#x1F4CA; State</a>
      <a href="/clock" class="active">&#x23F0; Clock</a>
    </div>
    <div class="power-btns">
      <button class="on" id="pwr-on">On</button>
      <button class="off" id="pwr-off">Off</button>
    </div>
  </div>

  <div class="card">
    <div class="clock-readout" id="readout">--:--:--</div>
    <div class="lamp-canvas">
      <svg id="lamp" width="380" height="380" viewBox="-200 -200 400 400"></svg>
    </div>
  </div>

  <div class="card">
    <h2>Colors</h2>
    <div class="color-row">
      <label>Outer (seconds)</label>
      <input type="color" id="color-outer" value="#FF0000">
      <div class="hex" id="hex-outer">FF0000</div>
    </div>
    <div class="color-row">
      <label>Middle (minutes)</label>
      <input type="color" id="color-middle" value="#00FF00">
      <div class="hex" id="hex-middle">00FF00</div>
    </div>
    <div class="color-row">
      <label>Inner (hours)</label>
      <input type="color" id="color-inner" value="#0000FF">
      <div class="hex" id="hex-inner">0000FF</div>
    </div>
  </div>

  <div class="card">
    <h2>Hour format</h2>
    <div class="mode-toggle" id="mode-toggle">
      <button data-mode="12h" class="active">12h</button>
      <button data-mode="24h">24h</button>
    </div>
    <div class="controls">
      <button class="primary" id="start-btn">Start</button>
      <button class="danger" id="stop-btn" disabled>Stop</button>
    </div>
    <div id="status">not running</div>
  </div>
</div>

<script type="module">
import { computeClockPositions } from '/static/lamp-utils.js';

const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));

const OUTER = Array.from({length:22}, (_,i) => [i*4, i*4+4]);
const MIDDLE = [
  ...Array.from({length:13}, (_,i) => [88+i*4, 88+i*4+4]),
  [140,145], [145,150],
];
const INNER = [
  ...Array.from({length:9}, (_,i) => [150+i*4, 150+i*4+4]),
  [186,191], [191,196],
];
const RING_GEOMETRY = {
  outer:  {r0: 130, r1: 180},
  middle: {r0: 90,  r1: 125},
  inner:  {r0: 50,  r1: 85},
};

const state = {
  colors: {outer: 'FF0000', middle: '00FF00', inner: '0000FF'},
  mode: '12h',
  running: false,
};

function arcPath(r0, r1, a0, a1) {
  const toXY = (r, a) => [r*Math.cos(a), r*Math.sin(a)];
  const [x0a, y0a] = toXY(r0, a0);
  const [x1a, y1a] = toXY(r1, a0);
  const [x1b, y1b] = toXY(r1, a1);
  const [x0b, y0b] = toXY(r0, a1);
  const large = (a1 - a0) > Math.PI ? 1 : 0;
  return `M${x0a},${y0a} L${x1a},${y1a} A${r1},${r1} 0 ${large} 1 ${x1b},${y1b}`
       + ` L${x0b},${y0b} A${r0},${r0} 0 ${large} 0 ${x0a},${y0a} Z`;
}

function segmentsContaining(ring, ledIdx) {
  // For 48-mode rendering: find which segment contains the given LED.
  const segs = ring === 'outer' ? OUTER : ring === 'middle' ? MIDDLE : INNER;
  const base = ring === 'outer' ? 0 : ring === 'middle' ? 88 : 150;
  const absIdx = base + ledIdx;
  for (let i = 0; i < segs.length; i++) {
    if (absIdx >= segs[i][0] && absIdx < segs[i][1]) return i;
  }
  return null;
}

function drawClock(positions) {
  // Always render in 48-mode for the clock visualizer (faithful to how
  // the lamp itself displays the dot, accounting for diffuser blur).
  const svg = $('#lamp');
  svg.innerHTML = '';
  for (const [name, segs] of [['outer', OUTER], ['middle', MIDDLE], ['inner', INNER]]) {
    const g = RING_GEOMETRY[name];
    const total = segs.length;
    const litSegmentIdx = segmentsContaining(name, positions[name]);
    for (let i = 0; i < total; i++) {
      const a0 = (i / total) * 2 * Math.PI - Math.PI / 2;
      const a1 = ((i + 1) / total) * 2 * Math.PI - Math.PI / 2;
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', arcPath(g.r0, g.r1, a0, a1));
      const lit = i === litSegmentIdx;
      const color = lit ? '#' + state.colors[name] : '#000';
      path.setAttribute('fill', color);
      path.setAttribute('stroke', '#1c1c1f');
      path.setAttribute('stroke-width', '1');
      svg.appendChild(path);
    }
  }
}

function updateReadout(now) {
  const hh = String(now.getHours()).padStart(2, '0');
  const mm = String(now.getMinutes()).padStart(2, '0');
  const ss = String(now.getSeconds()).padStart(2, '0');
  $('#readout').textContent = `${hh}:${mm}:${ss}`;
}

function tickVisualizer() {
  const now = new Date();
  const positions = computeClockPositions(now, state.mode);
  drawClock(positions);
  updateReadout(now);
}

async function postJSON(path, body) {
  const opts = {method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body || {})};
  const r = await fetch(path, opts);
  return r.json();
}

function syncColorPickers() {
  for (const ring of ['outer', 'middle', 'inner']) {
    $(`#color-${ring}`).value = '#' + state.colors[ring];
    $(`#hex-${ring}`).textContent = state.colors[ring];
  }
}

function syncModeToggle() {
  for (const b of $$('#mode-toggle button')) {
    b.classList.toggle('active', b.dataset.mode === state.mode);
  }
}

function setInputsDisabled(disabled) {
  for (const ring of ['outer', 'middle', 'inner']) {
    $(`#color-${ring}`).disabled = disabled;
  }
  for (const b of $$('#mode-toggle button')) b.disabled = disabled;
  $('#start-btn').disabled = disabled;
  $('#stop-btn').disabled = !disabled;
}

for (const ring of ['outer', 'middle', 'inner']) {
  $(`#color-${ring}`).oninput = e => {
    if (state.running) return;  // editing locked while running
    state.colors[ring] = e.target.value.replace('#', '').toUpperCase();
    $(`#hex-${ring}`).textContent = state.colors[ring];
  };
}
for (const b of $$('#mode-toggle button')) {
  b.onclick = () => {
    if (state.running) return;
    state.mode = b.dataset.mode;
    syncModeToggle();
  };
}

$('#start-btn').onclick = async () => {
  const j = await postJSON('/api/clock/start', {colors: state.colors, mode: state.mode});
  if (!j.ok) { $('#status').textContent = 'error: ' + j.error; return; }
  state.running = true;
  setInputsDisabled(true);
  $('#status').textContent = 'running since ' + (j.since ? j.since.slice(11, 16) : '?');
};
$('#stop-btn').onclick = async () => {
  await postJSON('/api/clock/stop', {});
  state.running = false;
  setInputsDisabled(false);
  $('#status').textContent = 'stopped (last frame left on lamp)';
};
$('#pwr-on').onclick = () => postJSON('/api/power', {on: true});
$('#pwr-off').onclick = () => postJSON('/api/power', {on: false});

async function refreshFromServer() {
  try {
    const j = await fetch('/api/clock/state').then(r => r.json());
    if (j.running) {
      state.running = true;
      if (j.colors) state.colors = j.colors;
      if (j.mode) state.mode = j.mode;
      syncColorPickers();
      syncModeToggle();
      setInputsDisabled(true);
      $('#status').textContent = 'running since ' + (j.since ? j.since.slice(11, 16) : '?');
    } else {
      state.running = false;
      setInputsDisabled(false);
      if ($('#status').textContent === '' || $('#status').textContent === 'not running') {
        $('#status').textContent = 'not running';
      }
    }
  } catch (e) { /* silent */ }
}

syncColorPickers();
syncModeToggle();
tickVisualizer();
setInterval(tickVisualizer, 1000);
refreshFromServer();
setInterval(refreshFromServer, 5000);
</script></body></html>"""
```

- [ ] **Step 2: Add the Clock tab to `_PAGE` (workshop / presets page)**

In `workshop.py`, find this block inside the existing `_PAGE`:

```html
        <a href="/diy" style="color:#aaa;padding:6px 12px;border-radius:8px;text-decoration:none">✏️ DIY</a>
        <a href="/ticker" style="color:#aaa;padding:6px 12px;border-radius:8px;text-decoration:none">📈 Ticker</a>
        <a href="/state" style="color:#aaa;padding:6px 12px;border-radius:8px;text-decoration:none">📊 State</a>
      </div>
```

Replace with:

```html
        <a href="/diy" style="color:#aaa;padding:6px 12px;border-radius:8px;text-decoration:none">✏️ DIY</a>
        <a href="/ticker" style="color:#aaa;padding:6px 12px;border-radius:8px;text-decoration:none">📈 Ticker</a>
        <a href="/state" style="color:#aaa;padding:6px 12px;border-radius:8px;text-decoration:none">📊 State</a>
        <a href="/clock" style="color:#aaa;padding:6px 12px;border-radius:8px;text-decoration:none">⏰ Clock</a>
      </div>
```

- [ ] **Step 3: Add the Clock tab to `_PAGE_DIY`**

In `workshop.py`, find this block inside `_PAGE_DIY` (it uses HTML numeric entities for the emojis):

```html
      <a href="/diy" class="active">&#x270F;&#xFE0F; DIY</a>
      <a href="/ticker">&#x1F4C8; Ticker</a>
      <a href="/state">&#x1F4CA; State</a>
    </div>
```

Replace with:

```html
      <a href="/diy" class="active">&#x270F;&#xFE0F; DIY</a>
      <a href="/ticker">&#x1F4C8; Ticker</a>
      <a href="/state">&#x1F4CA; State</a>
      <a href="/clock">&#x23F0; Clock</a>
    </div>
```

- [ ] **Step 4: Add the Clock tab to `_PAGE_TICKER`**

In `workshop.py`, find this block inside `_PAGE_TICKER`:

```html
      <a href="/diy">&#x270F;&#xFE0F; DIY</a>
      <a href="/ticker" class="active">&#x1F4C8; Ticker</a>
      <a href="/state">&#x1F4CA; State</a>
    </div>
```

Replace with:

```html
      <a href="/diy">&#x270F;&#xFE0F; DIY</a>
      <a href="/ticker" class="active">&#x1F4C8; Ticker</a>
      <a href="/state">&#x1F4CA; State</a>
      <a href="/clock">&#x23F0; Clock</a>
    </div>
```

- [ ] **Step 5: Add the Clock tab to `_PAGE_STATE`**

In `workshop.py`, find this block inside `_PAGE_STATE`:

```html
      <a href="/ticker">&#x1F4C8; Ticker</a>
      <a href="/state" class="active">&#x1F4CA; State</a>
    </div>
```

Replace with:

```html
      <a href="/ticker">&#x1F4C8; Ticker</a>
      <a href="/state" class="active">&#x1F4CA; State</a>
      <a href="/clock">&#x23F0; Clock</a>
    </div>
```

- [ ] **Step 6: Smoke-test the page + 5-tab nav on all five pages**

Run:
```bash
.venv/bin/python -c "
import workshop
p = workshop._PAGE_CLOCK
for marker in ('Lepro Clock', 'lamp-canvas', 'data-mode=\"12h\"',
               'data-mode=\"24h\"', 'color-outer', 'color-middle', 'color-inner',
               'start-btn', 'stop-btn', '/api/clock/start',
               '/api/clock/stop', '/api/clock/state', 'computeClockPositions'):
    assert marker in p, 'missing ' + repr(marker)

for name in ['_PAGE', '_PAGE_DIY', '_PAGE_TICKER', '_PAGE_STATE', '_PAGE_CLOCK']:
    page = getattr(workshop, name)
    for tab in ['href=\"/\"', 'href=\"/diy\"', 'href=\"/ticker\"',
                'href=\"/state\"', 'href=\"/clock\"']:
        assert tab in page, f'{name} missing {tab}'
print('clock page + 5-tab nav across all pages OK')
"
```
Expected: prints `clock page + 5-tab nav across all pages OK`.

- [ ] **Step 7: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 8: Commit**

```bash
git add workshop.py
git commit -m "feat: clock page UI (live SVG visualizer + color pickers + 12/24h toggle) + 5th tab"
```

---

### Task 8: README + final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Locate the existing Stock Ticker paragraph in `README.md`** (inside `## Preset workshop`).

- [ ] **Step 2: Append the clock paragraph after the ticker block**

After the existing `**⚡ FAST**` sentence (the last sentence of the Stock Ticker block, just before `## Protocol notes`), add:

```markdown

A **Clock** page is available at `http://<vm-ip>:8081/clock` — turns the lamp
into a three-handed analog clock with the outer ring showing seconds (88
LEDs), middle showing minutes (62), and inner showing hours (46). One bright
LED per ring marks the current position, drifting smoothly between marks as
the next-finer unit ticks. Per-ring colors are configurable from the page
(default: red seconds / green minutes / blue hours); the hour ring has a
12h / 24h toggle. Updates every second. Like the ticker, while the clock is
running the DIY paint and workshop preview endpoints return HTTP 409;
brightness and saves stay available. Stop leaves the last frame on the lamp
(use the power button to turn it off).
```

- [ ] **Step 3: Verify it landed in the right place**

Run: `grep -B1 -A2 "Clock.* page is available" README.md`
Expected: shows the new paragraph nested inside `## Preset workshop`.

- [ ] **Step 4: Final full-suite run**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 5: Final app build smoke**

Run:
```bash
.venv/bin/python -c "
import workshop
app = workshop.build_app()
print('routes:', len(list(app.router.routes())))
print('build ok')
"
```
Expected: prints `routes: 31` and `build ok`.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: document the clock-on-rings page"
```

---

## Self-Review

**Spec coverage:**
- Pure `compute_positions` with mode + fractional drift → Task 1 ✓
- Pure `build_clock_leds` → Task 2 ✓
- `ClockSession` state model + snapshot → Task 3 ✓
- `ClockSession` async loop + stop-leaves-frame → Task 4 ✓
- POST /api/clock/start with color + mode validation → Task 5 ✓
- POST /api/clock/stop idempotent + no power-off → Task 5 + Task 4 ✓
- GET /api/clock/state always-available → Task 5 ✓
- Mutex on /api/diy/paint + /api/preview → Task 5 (Step 2) ✓
- Power-off and /api/stop tear down clock too → Task 5 (Step 3) ✓
- Ticker-clock mutual exclusion (start while ticker running → 409) → Task 5 (Step 1, inside api_clock_start) ✓
- `computeClockPositions` JS helper → Task 6 ✓
- GET /clock page with live visualizer + color pickers + 12/24h + Start/Stop → Task 7 ✓
- 5th tab on all five pages → Task 7 (Steps 2-5) ✓
- README docs → Task 8 ✓

**Placeholder scan:** no TBD/TODO. Every step has actual code or actual command + expected output.

**Type consistency:**
- `compute_positions(now, mode)` signature matches across Tasks 1, 4 (`_tick_once`), 6 (JS mirror).
- `build_clock_leds(positions, colors)` signature matches across Tasks 2, 4.
- `ClockSession(client, colors, mode)` constructor matches across Tasks 3, 4, 5.
- Color dict keys (`outer`, `middle`, `inner`) consistent across Python, JS, and the page UI.
- Ring sizes (88/62/46) consistent across compute, build_clock_leds, and the visualizer's segment definitions.

**Notes for the implementer:**
- The `_VALID_MODES` constant defined in Task 1 is referenced by `ClockSession.__init__` in Task 3 — keep it at module scope, not inside a function.
- Task 5 step 3 modifies `api_power` and `api_stop`. The exact line numbers of those handlers will shift as the file grows — search by function name, not line number.
- The visualizer in Task 7 always renders at 48-mode (one lit segment per ring) because the lamp's diffuser blurs single LEDs into ~3-LED bands anyway; trying to show "the precise LED" at 196-mode would over-promise the lamp's actual resolution. The math (`computeClockPositions`) still returns LED indices; the visualizer maps each LED to the segment containing it via `segmentsContaining`.
