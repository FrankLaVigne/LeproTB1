# Capture UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a UI-based capture flow to the Animations tab: user clicks "🎥 Capture", triggers ONE animation in the Lepro phone app, server records distinct d50 echoes from MQTT, auto-stops on idle/cap, user names + saves the result as a new preset in `presets/`.

**Architecture:** Pure helpers + `CaptureSession` class in a new `web/captures.py` (mirrors the `TickerSession` / `ClockSession` pattern — own polling task, snapshot for state, mutex with other lamp drivers). `web/server.py` adds 4 routes (`/api/captures/start`, `/save`, `/cancel`, `/state`), extends `api_cockpit_active` with a `"capturing"` mode, integrates teardown into the existing `_stop_preview` / power-off / mutex paths. `_PANEL_ANIMATIONS` HTML/JS grows a top-of-page capture bar with live frame counter, save form, and dedup notice on success.

**Tech Stack:** Python 3.12, aiohttp, vanilla HTML/CSS/JS. No new deps.

---

## Task 0 (validation, do BEFORE Task 1): verify MQTT capture path works

**Files:**
- Read only

The spec flags a known risk: while the workshop holds the MQTT session slot, can the Lepro phone app still trigger animations the workshop will observe? Verify before writing any code. **If this fails, escalate as BLOCKED and pivot to the second-Lepro-account migration.**

- [ ] **Step 1: Start a fresh workshop**

```bash
pkill -f "web.server"    2>/dev/null
pkill -f "mcphost.server" 2>/dev/null
sleep 2
nohup .venv/bin/python -u -m web.server > /tmp/capture-validate.log 2>&1 &
disown
sleep 6
ss -tlnp 2>/dev/null | grep 8081 && echo "workshop up"
```

Expected: `workshop up` printed.

- [ ] **Step 2: Watch the d50 field while triggering an animation**

While the workshop is running, ask the user to open the Lepro phone app and trigger ANY animation. Then read the lamp's current d50:

```bash
curl -s http://127.0.0.1:8081/api/lamp/state | python3 -c "
import sys, json
d = json.load(sys.stdin)['devices']
for did, f in d.items():
    print(did, 'd1', f.get('d1'), 'd50[:60]', (f.get('d50') or '')[:60])
"
```

Wait 5-10s after the user triggers, then re-run the curl. If the `d50` string changes between the two runs, the workshop is receiving state echoes from MQTT while the user drives the lamp from the phone app — **the capture path works**. Proceed to Task 1.

If the `d50` does NOT change after triggering an animation, the phone app is being blocked at the MQTT layer and the capture-from-UI feature is blocked on the second-account migration. **Report BLOCKED** with this evidence and stop — do not proceed to Task 1.

---

### Task 1: `dedup_consecutive` pure function

**Files:**
- Create: `web/captures.py`
- Create: `tests/test_captures.py`

- [ ] **Step 1: Create the failing tests**

Create `/home/frank/lepro/tests/test_captures.py`:

```python
"""Tests for web.captures — the capture-from-UI flow's pure helpers + session."""

import json
from datetime import datetime

import pytest

from web import captures


# --- dedup_consecutive ------------------------------------------------------


def test_dedup_consecutive_drops_adjacent_duplicates():
    # "A A B" -> "A B" — the second A is dropped because it's adjacent.
    assert captures.dedup_consecutive(["A", "A", "B"]) == ["A", "B"]


def test_dedup_consecutive_keeps_non_adjacent_duplicates():
    # "A B A" -> "A B A" — the third entry is the SAME as the first, but
    # B sits between them, so it's NOT a consecutive duplicate.
    # This matters because the Lepro AI cycles through frames in a multi-
    # frame preset and may revisit the same frame later in the sequence.
    assert captures.dedup_consecutive(["A", "B", "A"]) == ["A", "B", "A"]


def test_dedup_consecutive_empty():
    assert captures.dedup_consecutive([]) == []


def test_dedup_consecutive_single_entry():
    assert captures.dedup_consecutive(["X"]) == ["X"]


def test_dedup_consecutive_all_same():
    # "A A A A" -> "A".
    assert captures.dedup_consecutive(["A", "A", "A", "A"]) == ["A"]
```

- [ ] **Step 2: Run the tests — confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_captures.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'web.captures'`.

- [ ] **Step 3: Create `web/captures.py` with the function**

Create `/home/frank/lepro/web/captures.py`:

```python
"""Capture-from-UI flow for the Animations tab.

A free-form, one-click-per-capture model: user clicks Capture in the UI,
server starts polling the lamp's d50 field via MQTT (the listen task that
already populates ``_client.state[did]`` is running on the workshop server),
collecting distinct d50 values. Auto-stops on idle gap or hard cap.

See ``docs/superpowers/specs/2026-06-01-capture-ui-design.md`` for the
working model, the known MQTT-session-fight risk, and the rationale for
the timing constants.
"""

from __future__ import annotations


def dedup_consecutive(frames: list) -> list:
    """Drop consecutive duplicates from a frame list.

    Non-adjacent duplicates are preserved — the Lepro AI cycles through
    frames in multi-frame presets and may revisit the same frame later
    in the sequence. We only collapse a frame that is identical to the
    one IMMEDIATELY before it.
    """
    out: list = []
    for frame in frames:
        if out and out[-1] == frame:
            continue
        out.append(frame)
    return out
```

- [ ] **Step 4: Run the tests — confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_captures.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures (218 + 5 = 223 passing).

- [ ] **Step 6: Commit**

```bash
git add web/captures.py tests/test_captures.py
git commit -m "feat(captures): dedup_consecutive (pure: collapse adjacent same-frame)"
```

---

### Task 2: `auto_capture_name` pure function

**Files:**
- Modify: `web/captures.py` (append)
- Modify: `tests/test_captures.py` (append)

Name format: `capture-YYYY-MM-DD-HHMM-N` where N is the next free sequence number for that minute. Reads from `existing_names` (list of preset stems already on disk).

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_captures.py`:

```python


# --- auto_capture_name ------------------------------------------------------


def test_auto_capture_name_first_of_minute():
    now = datetime(2026, 6, 1, 14, 32, 7)
    assert captures.auto_capture_name(now, existing_names=[]) == "capture-2026-06-01-1432-1"


def test_auto_capture_name_collides_increments():
    now = datetime(2026, 6, 1, 14, 32, 7)
    existing = ["capture-2026-06-01-1432-1"]
    assert captures.auto_capture_name(now, existing_names=existing) == "capture-2026-06-01-1432-2"


def test_auto_capture_name_skips_to_next_free():
    now = datetime(2026, 6, 1, 14, 32, 7)
    existing = ["capture-2026-06-01-1432-1", "capture-2026-06-01-1432-2"]
    assert captures.auto_capture_name(now, existing_names=existing) == "capture-2026-06-01-1432-3"


def test_auto_capture_name_ignores_other_minutes():
    # An existing name from a different minute doesn't bump our sequence.
    now = datetime(2026, 6, 1, 14, 32, 7)
    existing = ["capture-2026-06-01-1430-1", "capture-2026-06-01-1500-1"]
    assert captures.auto_capture_name(now, existing_names=existing) == "capture-2026-06-01-1432-1"


def test_auto_capture_name_pads_zero_month_day_hour_minute():
    now = datetime(2026, 1, 5, 4, 7, 0)
    assert captures.auto_capture_name(now, existing_names=[]) == "capture-2026-01-05-0407-1"
```

- [ ] **Step 2: Run the tests — confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_captures.py -k auto_capture_name -v`
Expected: FAIL with `AttributeError: module 'web.captures' has no attribute 'auto_capture_name'`.

- [ ] **Step 3: Append the implementation**

Append to `web/captures.py`:

```python
from datetime import datetime


def auto_capture_name(now: datetime, existing_names: list) -> str:
    """Return a unique preset name like ``capture-YYYY-MM-DD-HHMM-N``.

    N starts at 1 for that minute and increments past any existing names
    that already use the same minute stamp.
    """
    stamp = now.strftime("capture-%Y-%m-%d-%H%M-")
    existing = set(existing_names)
    n = 1
    while f"{stamp}{n}" in existing:
        n += 1
    return f"{stamp}{n}"
```

- [ ] **Step 4: Run the tests — confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_captures.py -k auto_capture_name -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add web/captures.py tests/test_captures.py
git commit -m "feat(captures): auto_capture_name (timestamp + sequence)"
```

---

### Task 3: `build_capture_preset` pure function

**Files:**
- Modify: `web/captures.py` (append)
- Modify: `tests/test_captures.py` (append)

Assembles the preset JSON shape that mirrors today's saved-from-DIY format. Single-frame captures use `payload`; multi-frame use `frames`. Each frame's `d1` defaults to 1 and `d2` defaults to 2 (segmented mode); the captured d50 is the only payload field that varies.

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_captures.py`:

```python


# --- build_capture_preset ---------------------------------------------------


def test_build_capture_preset_single_frame_uses_payload_shape():
    frames = ["N01:P10001FFFFFFF21000100C4U3V3000640000E1;"]
    preset = captures.build_capture_preset(frames, name="my-capture")
    assert preset["name"] == "my-capture"
    assert "payload" in preset
    assert preset["payload"]["d50"] == frames[0]
    assert preset["payload"]["d1"] == 1
    assert preset["payload"]["d2"] == 2
    assert "frames" not in preset


def test_build_capture_preset_multi_frame_uses_frames_shape():
    frames = [
        "N01:P10001FFFFFFF21000100C4U3V3000640000E1;",
        "N01:P10001FF0000F21000100C4U3V3000640000E1;",
        "N01:P10001"+"00FF00"+"F21000100C4U3V3000640000E1;",
    ]
    preset = captures.build_capture_preset(frames, name="my-capture")
    assert preset["name"] == "my-capture"
    assert "frames" in preset
    assert len(preset["frames"]) == 3
    for i, f in enumerate(preset["frames"]):
        assert f["d50"] == frames[i]
        assert f["d1"] == 1
        assert f["d2"] == 2
    assert "payload" not in preset


def test_build_capture_preset_includes_captured_date_and_prompt():
    frames = ["N01:P10001FFFFFFF21000100C4U3V3000640000E1;"]
    preset = captures.build_capture_preset(frames, name="my-capture")
    assert "captured" in preset
    # ISO YYYY-MM-DD shape
    assert len(preset["captured"]) == 10 and preset["captured"][4] == "-"
    assert preset["prompt"] == "captured via UI"
    assert preset["description"].startswith("Captured")


def test_build_capture_preset_empty_frames_raises():
    with pytest.raises(ValueError):
        captures.build_capture_preset([], name="anything")
```

- [ ] **Step 2: Run the tests — confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_captures.py -k build_capture_preset -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Append the implementation**

Append to `web/captures.py`:

```python
from datetime import date


def build_capture_preset(frames: list, name: str) -> dict:
    """Assemble the preset JSON for a UI-captured animation.

    Single-frame captures get a ``payload`` key matching the existing
    DIY-save shape. Multi-frame captures get a ``frames`` list matching
    the existing Lepro-AI-capture shape. Both shapes are consumed by
    ``web/animations.py`` and the Presets tab without special-casing.
    """
    if not frames:
        raise ValueError("cannot build preset from zero frames")

    common = {
        "name": name,
        "description": f"Captured via the Animations tab UI on {date.today().isoformat()}.",
        "captured": date.today().isoformat(),
        "prompt": "captured via UI",
    }
    if len(frames) == 1:
        return {**common, "payload": {"d1": 1, "d2": 2, "d50": frames[0]}}
    return {**common, "frames": [{"d1": 1, "d2": 2, "d50": d} for d in frames]}
```

- [ ] **Step 4: Run the tests — confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_captures.py -k build_capture_preset -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add web/captures.py tests/test_captures.py
git commit -m "feat(captures): build_capture_preset (single/multi-frame JSON shapes)"
```

---

### Task 4: `CaptureSession` state + snapshot (no async loop yet)

**Files:**
- Modify: `web/captures.py` (append)
- Modify: `tests/test_captures.py` (append)

The data model: holds the lamp client reference, the baseline d50 (so we don't record frames that ALREADY existed on the lamp when capture started), the collected frames list, timing fields. The async loop arrives in Task 5.

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_captures.py`:

```python


# --- CaptureSession state ---------------------------------------------------


def test_capture_session_initial_snapshot_not_running():
    sess = captures.CaptureSession(client=None, baseline_d50="N01:base;")
    snap = sess.snapshot()
    assert snap["running"] is False
    assert snap["started_at"] is None
    assert snap["frame_count"] == 0
    assert snap["auto_stop_at"] is None
    assert snap["default_name"] is None


def test_capture_session_frame_count_reflects_record_frame():
    sess = captures.CaptureSession(client=None, baseline_d50="X")
    sess.record_frame("first")
    sess.record_frame("second")
    assert sess.frame_count == 2
    assert sess.frames == ["first", "second"]


def test_capture_session_record_frame_dedups_adjacent():
    sess = captures.CaptureSession(client=None, baseline_d50="X")
    sess.record_frame("A")
    sess.record_frame("A")  # adjacent duplicate, dropped
    sess.record_frame("B")
    sess.record_frame("A")  # non-adjacent, kept
    assert sess.frames == ["A", "B", "A"]


def test_capture_session_record_frame_ignores_baseline():
    # If the lamp echoes the baseline d50 (because nothing has changed yet),
    # don't record it as a frame.
    sess = captures.CaptureSession(client=None, baseline_d50="BASE")
    sess.record_frame("BASE")
    sess.record_frame("BASE")
    assert sess.frames == []


def test_capture_session_record_frame_ignores_none_and_empty():
    sess = captures.CaptureSession(client=None, baseline_d50="X")
    sess.record_frame(None)
    sess.record_frame("")
    assert sess.frames == []


def test_capture_session_running_reflects_task_state():
    sess = captures.CaptureSession(client=None, baseline_d50=None)
    assert sess.running is False
```

- [ ] **Step 2: Run the tests — confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_captures.py -k "capture_session" -v`
Expected: FAIL with `AttributeError: module 'web.captures' has no attribute 'CaptureSession'`.

- [ ] **Step 3: Append the class**

Append to `web/captures.py`:

```python
from typing import Optional


_IDLE_TIMEOUT_S = 6.0   # auto-stop after this many seconds without a new frame
_HARD_CAP_S = 90.0      # absolute cap regardless of activity


class CaptureSession:
    """One in-flight capture window: polls the lamp's d50 over MQTT,
    collects distinct frames, auto-stops on idle gap or hard cap.

    The polling loop lives in ``start()`` / ``_run()`` (added in Task 5).
    State + snapshot here are exercised by the routes synchronously.
    """

    def __init__(self, client, baseline_d50: Optional[str],
                 idle_timeout: float = _IDLE_TIMEOUT_S,
                 hard_cap: float = _HARD_CAP_S):
        self._client = client
        self._baseline_d50 = baseline_d50
        self._idle_timeout = idle_timeout
        self._hard_cap = hard_cap
        self._frames: list = []
        self._started_at: Optional[datetime] = None
        self._last_frame_at: Optional[datetime] = None
        self._task = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    @property
    def frames(self) -> list:
        return list(self._frames)

    def record_frame(self, d50: Optional[str]) -> None:
        """Record one polled d50. Drops baseline matches, adjacent dups,
        empty / None values. Updates the last-frame timestamp on accept."""
        if not d50:
            return
        if d50 == self._baseline_d50:
            return
        if self._frames and self._frames[-1] == d50:
            return
        self._frames.append(d50)
        self._last_frame_at = datetime.now()

    def snapshot(self) -> dict:
        """Return JSON-serialisable state for /api/captures/state."""
        auto_stop = None
        default_name = None
        if self._started_at is not None:
            hard_cap_at = self._started_at.timestamp() + self._hard_cap
            if self._last_frame_at is not None:
                idle_at = self._last_frame_at.timestamp() + self._idle_timeout
                auto_stop_ts = min(hard_cap_at, idle_at)
            else:
                auto_stop_ts = hard_cap_at
            auto_stop = datetime.fromtimestamp(auto_stop_ts).isoformat(timespec="seconds")
            default_name = auto_capture_name(self._started_at, existing_names=[])
        return {
            "running": self.running,
            "started_at": self._started_at.isoformat(timespec="seconds") if self._started_at else None,
            "frame_count": self.frame_count,
            "auto_stop_at": auto_stop,
            "default_name": default_name,
        }
```

- [ ] **Step 4: Run the tests — confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_captures.py -k "capture_session" -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add web/captures.py tests/test_captures.py
git commit -m "feat(captures): CaptureSession state + snapshot (no loop yet)"
```

---

### Task 5: `CaptureSession.start` / `.stop` / `._tick_once` — async polling loop

**Files:**
- Modify: `web/captures.py` (extend the class)
- Modify: `tests/test_captures.py` (append)

The poll loop runs at 200ms cadence: reads `self._client.state[<first did>].get("d50")`, calls `self.record_frame(d50)`, then checks auto-stop conditions. Stops itself when idle-timeout or hard-cap fires.

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_captures.py`:

```python


# --- CaptureSession async loop ----------------------------------------------


import asyncio


class _FakeClient:
    """Lets us prime the lamp's reported state for capture tests."""

    def __init__(self, did: str = "abc"):
        self.state = {did: {"d50": None}}
        self.did = did

    def set_d50(self, d50):
        self.state[self.did]["d50"] = d50


@pytest.mark.asyncio
async def test_tick_once_records_new_d50():
    client = _FakeClient()
    sess = captures.CaptureSession(client=client, baseline_d50=None)
    client.set_d50("frame-a")
    sess._tick_once()
    assert sess.frames == ["frame-a"]


@pytest.mark.asyncio
async def test_tick_once_ignores_baseline():
    client = _FakeClient()
    sess = captures.CaptureSession(client=client, baseline_d50="base")
    client.set_d50("base")
    sess._tick_once()
    assert sess.frames == []


@pytest.mark.asyncio
async def test_tick_once_appends_only_distinct_values():
    client = _FakeClient()
    sess = captures.CaptureSession(client=client, baseline_d50=None)
    for d50 in ["a", "a", "b", "b", "c"]:
        client.set_d50(d50)
        sess._tick_once()
    assert sess.frames == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_start_then_stop_lifecycle():
    client = _FakeClient()
    sess = captures.CaptureSession(client=client, baseline_d50=None)
    assert sess.running is False
    await sess.start()
    assert sess.running is True
    await sess.stop()
    assert sess.running is False


@pytest.mark.asyncio
async def test_idle_timeout_fires_when_no_frames():
    # With a tiny idle_timeout we can verify auto-stop without sleeping forever.
    client = _FakeClient()
    sess = captures.CaptureSession(client=client, baseline_d50=None,
                                    idle_timeout=0.3, hard_cap=10.0)
    await sess.start()
    # Don't change client.set_d50 — no frames will be recorded.
    await asyncio.sleep(0.7)
    assert sess.running is False
    assert sess.frames == []


@pytest.mark.asyncio
async def test_hard_cap_fires_even_with_steady_frames():
    # Tiny hard cap + a frame stream that keeps the idle timer fresh.
    client = _FakeClient()
    sess = captures.CaptureSession(client=client, baseline_d50=None,
                                    idle_timeout=10.0, hard_cap=0.5)
    await sess.start()
    # Pump distinct frames so the idle-timeout WOULD never fire.
    for i in range(10):
        client.set_d50(f"frame-{i}")
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.3)
    assert sess.running is False
```

- [ ] **Step 2: Run the tests — confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_captures.py -k "tick_once or start_then_stop or timeout_fires or hard_cap_fires" -v`
Expected: FAIL with `AttributeError: 'CaptureSession' object has no attribute '_tick_once'`.

- [ ] **Step 3: Extend the class — add async methods inside `CaptureSession`**

Append these methods inside the `CaptureSession` class in `web/captures.py`:

```python
    def _tick_once(self) -> None:
        """One poll iteration: read the lamp's current d50, maybe record it."""
        if self._client is None:
            return
        state = getattr(self._client, "state", {}) or {}
        if not state:
            return
        # Use the first device; the workshop only ever has one lamp anyway.
        first = next(iter(state.values()), None)
        if not first:
            return
        self.record_frame(first.get("d50"))

    def _should_auto_stop(self) -> bool:
        if self._started_at is None:
            return False
        now = datetime.now()
        if (now - self._started_at).total_seconds() >= self._hard_cap:
            return True
        if self._last_frame_at is not None:
            if (now - self._last_frame_at).total_seconds() >= self._idle_timeout:
                return True
        return False

    async def start(self) -> None:
        """Begin the polling loop. No-op if already running."""
        import asyncio
        if self.running:
            return
        self._started_at = datetime.now()
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run())

    async def _run(self) -> None:
        """The 200ms poll loop, until auto-stop or cancelled."""
        import asyncio
        try:
            while True:
                self._tick_once()
                if self._should_auto_stop():
                    return
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            return

    async def stop(self) -> None:
        """Stop the loop. Preserves the collected frames so the caller
        can still build a preset from them."""
        import asyncio
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
```

- [ ] **Step 4: Run the tests — confirm they pass**

Run: `.venv/bin/python -m pytest tests/test_captures.py -k "tick_once or start_then_stop or timeout_fires or hard_cap_fires" -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add web/captures.py tests/test_captures.py
git commit -m "feat(captures): CaptureSession start/stop/_tick_once + idle/cap auto-stop"
```

---

### Task 6: Backend routes — `/api/captures/{start,save,cancel,state}` + mutex + active-mode

**Files:**
- Modify: `web/server.py`

Adds 4 routes, module-level `_capture_session`, mutex integration with ticker/clock/preview start/preview/diy-paint, extends `api_cockpit_active` with a `capturing` mode branch, registers the routes in `build_app`.

- [ ] **Step 1: Add the module-level state + imports**

Find the existing line in `web/server.py`:

```python
from web import animations as _animations_mod
```

Right AFTER it, add:

```python
from web import captures as _captures_mod

_capture_session = None  # type: ignore[assignment]
```

- [ ] **Step 2: Add a `_stop_capture()` helper next to `_stop_preview()`**

Find `async def _stop_preview()` in `web/server.py`. Immediately AFTER its body, add:

```python
async def _stop_capture() -> None:
    """Cancel and clear any in-flight capture session. Safe to call when
    no capture is active. Used by power-off and by the mutex paths so the
    capture doesn't keep polling after another lamp-driver takes over."""
    global _capture_session
    sess = _capture_session
    if sess is not None and sess.running:
        await sess.stop()
    _capture_session = None
```

- [ ] **Step 3: Add the 4 route handlers above the existing `_PANEL_ANIMATIONS` constant**

Find `_PANEL_ANIMATIONS` in `web/server.py`. Insert these handlers IMMEDIATELY ABOVE the `_PANEL_ANIMATIONS = ...` line:

```python
async def api_captures_start(_req):
    """Start a new capture session. 409 if any other lamp-driver is running."""
    global _capture_session
    # Mutex with ticker / clock / preview / existing capture.
    if _ticker_session is not None and _ticker_session.running:
        return web.json_response(
            {"ok": False, "error": "stock ticker is running; stop it first"},
            status=409)
    if _clock_session is not None and _clock_session.running:
        return web.json_response(
            {"ok": False, "error": "clock is running; stop it first"},
            status=409)
    if _preview_task is not None and not _preview_task.done():
        return web.json_response(
            {"ok": False, "error": "preset preview is running; stop it first"},
            status=409)
    if _capture_session is not None and _capture_session.running:
        return web.json_response(
            {"ok": False, "error": "a capture is already in progress"},
            status=409)
    # Baseline: whatever the lamp's d50 is right now. Anything that DIFFERS
    # from this baseline becomes a recorded frame.
    baseline = None
    if _client is not None:
        for fields in _client.state.values():
            baseline = fields.get("d50")
            break
    sess = _captures_mod.CaptureSession(_client, baseline_d50=baseline)
    await sess.start()
    _capture_session = sess
    return web.json_response({"ok": True, "started_at": sess.snapshot()["started_at"]})


async def api_captures_state(_req):
    """Return the active capture's snapshot, or a 'not running' shape."""
    if _capture_session is None:
        return web.json_response({
            "running": False, "started_at": None, "frame_count": 0,
            "auto_stop_at": None, "default_name": None,
        })
    return web.json_response(_capture_session.snapshot())


async def api_captures_cancel(_req):
    """Stop the capture without saving. Idempotent."""
    await _stop_capture()
    return web.json_response({"ok": True})


async def api_captures_save(req):
    """Stop the capture (if still running), build the preset, write it
    to presets/, return path + matched_animation info (or null)."""
    global _capture_session
    try:
        body = await req.json()
        raw_name = body.get("name")
        if raw_name is None:
            return web.json_response({"ok": False, "error": "name required"}, status=400)
        name = _sanitize_name(str(raw_name))
        sess = _capture_session
        if sess is None:
            return web.json_response({"ok": False, "error": "no capture in progress"}, status=400)
        # Stop the loop first so no more frames sneak in mid-save.
        if sess.running:
            await sess.stop()
        frames = sess.frames
        if not frames:
            _capture_session = None
            return web.json_response({"ok": False, "error": "no frames captured; nothing to save"}, status=400)
        out_path = _PRESETS_DIR / f"{name}.json"
        if out_path.exists():
            return web.json_response(
                {"ok": False, "error": f"preset {name!r} already exists; pick another name"},
                status=400)
        preset = _captures_mod.build_capture_preset(frames, name)
        out_path.write_text(json.dumps(preset, indent=2) + "\n")
        _capture_session = None
        # Look up whether the new preset matches an existing animation group.
        matched = None
        for anim in _grouped_animations():
            if any(m.name == name for m in anim.members) and len(anim.members) > 1:
                matched = {
                    "id": anim.id,
                    "name": anim.name,
                    "variant_count": len(anim.members),
                }
                break
        return web.json_response({
            "ok": True,
            "path": str(out_path.relative_to(_PROJECT_ROOT)),
            "matched_animation": matched,
        })
    except (KeyError, ValueError, TypeError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
```

- [ ] **Step 4: Extend `api_cockpit_active` with the capturing branch**

Find `api_cockpit_active` in `web/server.py`. The existing function has an ordered set of checks. Add a NEW top-priority check (above the `d1 == 0` off check) so capture mode wins over everything else (since the user is actively interacting with it):

Find the existing function body and locate:

```python
    # 1. Power off wins.
    if _client is not None:
        for fields in _client.state.values():
            if fields.get("d1") == 0:
                return web.json_response({"mode": "off", "label": "⏻ Off"})
```

IMMEDIATELY BEFORE this block (so it becomes the new check #0), insert:

```python
    # 0. Capture in progress wins over everything — user is actively driving this.
    if _capture_session is not None and _capture_session.running:
        n = _capture_session.frame_count
        return web.json_response({
            "mode": "capturing",
            "label": f"🎥 Capturing — {n} frame{'s' if n != 1 else ''}",
        })
```

- [ ] **Step 5: Add `_stop_capture()` to the power-off teardown**

Find `async def api_power` (in `web/server.py`). The off-branch already calls `_stop_preview()`, `_ticker_session.stop()`, `_clock_session.stop()`. Add `await _stop_capture()` to the same teardown sequence. Locate the section that looks roughly like:

```python
        if not on:
            await _stop_preview()
            if _ticker_session is not None and _ticker_session.running:
                await _ticker_session.stop()
            ...
```

Insert `await _stop_capture()` next to the other stop calls — order doesn't matter functionally, but for readability place it AFTER `_stop_preview()` so all four (preview / ticker / clock / capture) sit together.

- [ ] **Step 6: Add capture mutex to `api_ticker_start`, `api_clock_start`, `api_diy_paint`, `api_preview`**

These four endpoints currently `await _stop_preview()` to clear the preview loop. Add a parallel mutex against an in-flight capture — but instead of auto-stopping the capture, REFUSE the new operation (the user is actively capturing; we don't want to silently destroy their in-flight work).

In each of `api_ticker_start`, `api_clock_start`, `api_diy_paint`, `api_preview`, find the existing `_check_*_mutex()` calls (or the first `await _stop_preview()`). IMMEDIATELY BEFORE any mutex-style call, add:

```python
    if _capture_session is not None and _capture_session.running:
        raise web.HTTPConflict(
            text='{"ok": false, "error": "a capture is in progress; save or cancel it first"}',
            content_type="application/json",
        )
```

Note: for `api_diy_paint` and `api_preview` which already use `_check_ticker_mutex()` / `_check_clock_mutex()` helpers, define a `_check_capture_mutex()` helper next to those (matches the existing pattern). For `api_ticker_start` / `api_clock_start` which currently do inline checks, the inline form is fine — just add the conditional.

Suggested helper (place next to `_check_ticker_mutex`):

```python
def _check_capture_mutex():
    """Raise HTTPConflict if a capture is in progress."""
    global _capture_session
    if _capture_session is not None and _capture_session.running:
        raise web.HTTPConflict(
            text='{"ok": false, "error": "a capture is in progress; save or cancel it first"}',
            content_type="application/json",
        )
```

Then in `api_diy_paint` and `api_preview`, add `_check_capture_mutex()` right after the existing `_check_clock_mutex()` call.

- [ ] **Step 7: Register the 4 routes in `build_app`**

In `build_app`'s `app.add_routes([...])` list, find the existing `web.post(r"/api/animations/{id}/save", api_animation_save),` line. Add these 4 entries AFTER it:

```python
        web.post("/api/captures/start", api_captures_start),
        web.get("/api/captures/state", api_captures_state),
        web.post("/api/captures/cancel", api_captures_cancel),
        web.post("/api/captures/save", api_captures_save),
```

- [ ] **Step 8: Smoke-test all routes are registered**

```bash
.venv/bin/python -c "
from web import server
app = server.build_app()
routes = sorted(r.method + ' ' + str(r.resource.canonical) for r in app.router.routes())
need = ['POST /api/captures/start', 'GET /api/captures/state',
        'POST /api/captures/cancel', 'POST /api/captures/save']
for n in need:
    assert n in routes, f'missing: {n}'
print('all 4 capture routes registered; total:', len(routes))
"
```

Expected: prints `all 4 capture routes registered; total: <N+5>` (4 explicit + 1 implicit HEAD on the GET).

- [ ] **Step 9: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 10: Commit**

```bash
git add web/server.py
git commit -m "feat(captures): backend routes + active-mode branch + mutex integration"
```

---

### Task 7: HTTP smoke tests for capture routes

**Files:**
- Modify: `tests/test_captures.py` (append)

3 async tests exercising the route handlers via monkeypatch (no real MQTT or aiohttp app needed — direct handler calls with fake requests).

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_captures.py`:

```python


# --- HTTP layer -------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_captures_save_with_no_frames_returns_400(tmp_path, monkeypatch):
    """If a capture exists but has zero frames, save returns 400."""
    from web import server as workshop

    monkeypatch.setattr(workshop, "_PRESETS_DIR", tmp_path)
    monkeypatch.setattr(workshop, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(workshop, "_ANIMATIONS_OVERRIDES_PATH", tmp_path / "animations.json")

    sess = captures.CaptureSession(client=None, baseline_d50=None)
    # Don't start the loop — directly inject the session as if mid-capture.
    workshop._capture_session = sess

    class _Req:
        async def json(self): return {"name": "x"}

    try:
        resp = await workshop.api_captures_save(_Req())
        body = json.loads(resp.body.decode() if isinstance(resp.body, bytes) else resp.body)
        assert body["ok"] is False
        assert "no frames" in body["error"]
    finally:
        workshop._capture_session = None


@pytest.mark.asyncio
async def test_api_captures_save_writes_preset_and_reports_matched_animation(tmp_path, monkeypatch):
    """A capture with frames writes a file and surfaces matched_animation
    when the new preset's fingerprint matches an existing group."""
    from web import server as workshop

    monkeypatch.setattr(workshop, "_PRESETS_DIR", tmp_path)
    monkeypatch.setattr(workshop, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(workshop, "_ANIMATIONS_OVERRIDES_PATH", tmp_path / "animations.json")

    # Pre-populate an existing preset with a known d50 so the new save
    # fingerprints as a match.
    existing = {"name": "existing",
                "payload": {"d50": "N01:P10001FF0000F21000100C4U3V3000640000E1;"}}
    (tmp_path / "existing.json").write_text(json.dumps(existing))

    # Build a CaptureSession that has the same d50 in its frames.
    sess = captures.CaptureSession(client=None, baseline_d50=None)
    sess.record_frame("N01:P10001FF0000F21000100C4U3V3000640000E1;")
    workshop._capture_session = sess

    class _Req:
        async def json(self): return {"name": "newone"}

    try:
        resp = await workshop.api_captures_save(_Req())
        body = json.loads(resp.body.decode() if isinstance(resp.body, bytes) else resp.body)
        assert body["ok"] is True, body
        assert (tmp_path / "newone.json").exists()
        assert body["matched_animation"] is not None
        assert body["matched_animation"]["variant_count"] == 2
    finally:
        workshop._capture_session = None


@pytest.mark.asyncio
async def test_api_captures_cancel_is_idempotent(tmp_path, monkeypatch):
    """Cancel always returns ok, even with no active session."""
    from web import server as workshop
    workshop._capture_session = None  # ensure clean state
    resp = await workshop.api_captures_cancel(None)
    body = json.loads(resp.body.decode() if isinstance(resp.body, bytes) else resp.body)
    assert body["ok"] is True
```

- [ ] **Step 2: Run the new tests**

Run: `.venv/bin/python -m pytest tests/test_captures.py -k "api_captures" -v`
Expected: PASS (3 tests).

- [ ] **Step 3: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 4: Commit**

```bash
git add tests/test_captures.py
git commit -m "test(captures): HTTP smoke tests for the new routes"
```

---

### Task 8: `_PANEL_ANIMATIONS` UI — capture bar + counter + save form

**Files:**
- Modify: `web/server.py` (extend the `_PANEL_ANIMATIONS` constant)
- Modify: `web/static/cockpit.css` (append `.capture-*` styles)

Adds the visible UI: a capture bar at the top of the Animations panel with the progress counter, the Capture button (which morphs into "Capturing... N frames" + Save / Cancel), and the post-capture save form with name input + dedup notice.

- [ ] **Step 1: Append `.capture-*` styles to cockpit.css**

Append to `/home/frank/lepro/web/static/cockpit.css`:

```css
/* === Animations: capture bar ============================================= */

.capture-bar {
  display: flex;
  align-items: center;
  gap: var(--gap);
  padding: var(--gap);
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--r);
  margin-bottom: var(--gap);
}
.capture-counter {
  flex: 1;
  font-size: 13px;
  color: var(--text-dim);
}
.capture-counter strong { color: var(--text); font-weight: 700; }

#capture-btn,
.capture-action {
  padding: 8px 14px;
  border: 1px solid var(--border);
  background: var(--panel-hi);
  color: var(--text);
  border-radius: var(--r-sm);
  font-size: 12px;
  font-weight: 600;
}
#capture-btn:hover,
.capture-action:hover { background: var(--accent-soft); color: var(--accent); }
#capture-btn.active {
  background: var(--accent-soft);
  color: var(--accent);
  border-color: rgba(0, 221, 255, 0.4);
}

.capture-saveform {
  display: flex;
  gap: var(--gap-sm);
  align-items: center;
  margin-top: var(--gap-sm);
  padding-top: var(--gap-sm);
  border-top: 1px solid var(--border);
}
.capture-saveform input[type=text] {
  flex: 1;
  padding: 6px 10px;
  border: 1px solid var(--border);
  background: var(--panel-hi);
  color: var(--text);
  border-radius: var(--r-sm);
  font: inherit;
}
.capture-notice {
  font-size: 12px;
  color: var(--text-dim);
  margin-top: var(--gap-sm);
  min-height: 1.2em;
}
.capture-notice.ok { color: var(--ok); }
.capture-notice.matched { color: var(--accent); }
.capture-notice.err { color: var(--danger); }
```

- [ ] **Step 2: Update `_PANEL_ANIMATIONS` in `web/server.py`**

Find the existing `_PANEL_ANIMATIONS = """..."""` constant. We're extending it: adding a `<div class="capture-bar">` immediately above `<div id="anim-list">` and a `<script>` section that drives it.

REPLACE the existing `_PANEL_ANIMATIONS = """..."""` constant with this updated version. The structural diff: we add the `.capture-bar` at the top, two extra script functions (`startCapture`, `pollCaptureState`, `submitSave`, `cancelCapture`), and call `pollCaptureState()` once on load to recover any in-flight capture across page reloads.

```python
_PANEL_ANIMATIONS = """
<div class="capture-bar">
  <div class="capture-counter">
    <strong id="capture-count">—</strong> unique animations / ~72 target
  </div>
  <button id="capture-btn" class="capture-action">🎥 Capture</button>
</div>
<div id="capture-saveform" class="capture-saveform" style="display:none">
  <input type="text" id="capture-name" placeholder="capture-...">
  <button class="capture-action" id="capture-save">💾 Save</button>
  <button class="capture-action" id="capture-discard">Discard</button>
</div>
<div id="capture-notice" class="capture-notice"></div>

<div id="anim-list"></div>
<div id="anim-empty" style="display:none">
  No animations yet. Use the Capture button above (or
  <code>python -m cli.main capture --seconds 90</code>) and they'll appear
  here grouped by motion pattern.
</div>
<div id="anim-status"></div>

<script type="module">
const $ = s => document.querySelector(s);
const list = $('#anim-list');
const empty = $('#anim-empty');
const status = $('#anim-status');
const captureBtn = $('#capture-btn');
const captureCount = $('#capture-count');
const saveForm = $('#capture-saveform');
const nameInput = $('#capture-name');
const notice = $('#capture-notice');

const TARGET_TOTAL = 72;
let pollTimer = null;

function setStatus(msg, isError) {
  status.textContent = msg || '';
  status.style.color = isError ? 'var(--danger)' : '';
}

function setNotice(msg, cls) {
  notice.className = 'capture-notice' + (cls ? ' ' + cls : '');
  notice.textContent = msg || '';
}

async function postJSON(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

function paletteSwatches(palette) {
  return palette.map(c => `<span class="swatch" style="background:#${c}"></span>`).join('');
}

function buildRow(anim) {
  const variantCount = anim.members.length;
  const firstStats = (anim.members[0] || {}).frame_stats || {total: 0, unique: 0};
  const stats = firstStats.total > 1
    ? `${firstStats.total} frames (${firstStats.unique} unique)`
    : `${firstStats.total} frame`;

  const pickerInputs = anim.default_palette.map((c, i) =>
    `<input type="color" value="#${c}" data-idx="${i}">`).join('');
  const variantPills = anim.members.map(m =>
    `<span class="v">${m.name}</span>`).join('');

  const row = document.createElement('div');
  row.className = 'anim-row';
  row.dataset.id = anim.id;
  row.innerHTML = `
    <div class="anim-title" data-action="toggle">${anim.name}</div>
    <div class="anim-actions">
      <button data-action="play">▶ Play</button>
      <button data-action="toggle">✎ Edit</button>
    </div>
    <div class="anim-meta">
      <div class="anim-palette">${paletteSwatches(anim.default_palette)}</div>
      <span>${stats}</span>
      <span>·</span>
      <span>${variantCount} variant${variantCount === 1 ? '' : 's'}</span>
    </div>
    <div class="anim-expanded">
      <div>
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:4px">RENAME</div>
        <div class="anim-saverow">
          <input type="text" data-role="rename" value="${anim.name}">
          <button data-action="rename">Save name</button>
        </div>
      </div>
      <div>
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:4px">RECOLOR PALETTE</div>
        <div class="anim-pickers">${pickerInputs}</div>
      </div>
      <div>
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:4px">SAVE AS NEW PRESET</div>
        <div class="anim-saverow">
          <input type="text" data-role="save-name" placeholder="my-variant">
          <button data-action="save">💾 Save</button>
        </div>
      </div>
      <div>
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:4px">VARIANTS</div>
        <div class="anim-variants">${variantPills}</div>
      </div>
    </div>
  `;
  return row;
}

function attachHandlers(row, anim) {
  for (const el of row.querySelectorAll('[data-action="toggle"]')) {
    el.addEventListener('click', () => row.classList.toggle('expanded'));
  }
  row.querySelector('[data-action="play"]').addEventListener('click', async (e) => {
    e.stopPropagation();
    const sourceName = anim.members[0].name;
    const j = await postJSON('/api/preview', {base_name: sourceName});
    setStatus(j.ok === false ? ('error: ' + j.error) : `playing ${sourceName}…`, j.ok === false);
  });
  row.querySelector('[data-action="rename"]').addEventListener('click', async () => {
    const name = row.querySelector('[data-role="rename"]').value.trim();
    if (!name) { setStatus('name required', true); return; }
    const j = await postJSON(`/api/animations/${anim.id}/rename`, {name});
    if (j.ok === false) { setStatus('error: ' + j.error, true); return; }
    setStatus(`renamed to ${name}`);
    await loadAnimations();
  });
  row.querySelector('[data-action="save"]').addEventListener('click', async () => {
    const newName = row.querySelector('[data-role="save-name"]').value.trim();
    if (!newName) { setStatus('save name required', true); return; }
    const palette = Array.from(row.querySelectorAll('.anim-pickers input[type=color]'))
      .sort((a, b) => parseInt(a.dataset.idx, 10) - parseInt(b.dataset.idx, 10))
      .map(input => input.value.replace('#', '').toUpperCase());
    const j = await postJSON(`/api/animations/${anim.id}/save`,
                              {name: newName, palette});
    if (j.ok === false) { setStatus('error: ' + j.error, true); return; }
    setStatus(`saved → ${j.path}`);
    await loadAnimations();
  });
}

async function loadAnimations() {
  try {
    const r = await fetch('/api/animations');
    const j = await r.json();
    list.innerHTML = '';
    const items = j.animations || [];
    captureCount.textContent = items.length;
    if (items.length === 0) {
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';
    for (const anim of items) {
      const row = buildRow(anim);
      attachHandlers(row, anim);
      list.appendChild(row);
    }
  } catch (e) {
    setStatus('failed to load animations: ' + e.message, true);
  }
}

// --- capture flow ----------------------------------------------------------

function setCaptureButtonRunning(running, frameCount) {
  if (running) {
    captureBtn.classList.add('active');
    captureBtn.textContent = `Capturing... ${frameCount} frame${frameCount === 1 ? '' : 's'}`;
  } else {
    captureBtn.classList.remove('active');
    captureBtn.textContent = '🎥 Capture';
  }
}

async function startCapture() {
  setNotice('');
  saveForm.style.display = 'none';
  const j = await postJSON('/api/captures/start', {});
  if (j.ok === false) { setNotice('error: ' + j.error, 'err'); return; }
  setCaptureButtonRunning(true, 0);
  pollCaptureState();
}

async function pollCaptureState() {
  try {
    const r = await fetch('/api/captures/state');
    const j = await r.json();
    if (j.running) {
      setCaptureButtonRunning(true, j.frame_count);
      pollTimer = setTimeout(pollCaptureState, 500);
    } else {
      setCaptureButtonRunning(false, 0);
      if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
      // If a previous capture finished and we have frames + a default
      // name, surface the save form. Server side: state-not-running with
      // frame_count == 0 means there's nothing to save.
      if (j.frame_count > 0 && j.default_name) {
        nameInput.value = j.default_name;
        saveForm.style.display = 'flex';
      }
    }
  } catch (e) {
    setNotice('lost connection to server while polling capture state', 'err');
  }
}

async function submitSave() {
  const name = nameInput.value.trim();
  if (!name) { setNotice('name required', 'err'); return; }
  const j = await postJSON('/api/captures/save', {name});
  if (j.ok === false) { setNotice('error: ' + j.error, 'err'); return; }
  saveForm.style.display = 'none';
  if (j.matched_animation) {
    const m = j.matched_animation;
    setNotice(`Saved as ${name} → matches ${m.name} (now ${m.variant_count} variants)`, 'matched');
  } else {
    setNotice(`Saved as ${name} → new animation`, 'ok');
  }
  await loadAnimations();
}

async function cancelCapture() {
  await postJSON('/api/captures/cancel', {});
  setCaptureButtonRunning(false, 0);
  saveForm.style.display = 'none';
  setNotice('capture discarded');
}

captureBtn.addEventListener('click', () => {
  if (captureBtn.classList.contains('active')) {
    // Already capturing — treat as Save Now: pretend the user clicked the
    // button while the loop's still alive. Stop polling, then call save
    // (the server will stop the loop itself before saving).
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    // Surface the save form right away with a placeholder name; the user
    // can finalise + click Save.
    fetch('/api/captures/state').then(r => r.json()).then(j => {
      if (j.frame_count > 0 && j.default_name) {
        nameInput.value = j.default_name;
        saveForm.style.display = 'flex';
        setCaptureButtonRunning(false, 0);
      }
    });
  } else {
    startCapture();
  }
});
$('#capture-save').addEventListener('click', submitSave);
$('#capture-discard').addEventListener('click', cancelCapture);

loadAnimations();
// On page mount, check if a capture is already in flight (so reloading
// the tab while capturing doesn't lose track of it).
pollCaptureState();
</script>
"""
```

- [ ] **Step 3: Smoke-test the page constant**

```bash
.venv/bin/python -c "
from web import server
p = server._PANEL_ANIMATIONS
for marker in ('capture-bar', 'capture-counter', 'capture-btn',
               'capture-saveform', '/api/captures/start',
               '/api/captures/state', '/api/captures/save',
               '/api/captures/cancel', 'TARGET_TOTAL', 'pollCaptureState'):
    assert marker in p, 'missing: ' + repr(marker)
print('page constant has all required markers')
"
```

Expected: prints `page constant has all required markers`.

- [ ] **Step 4: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 5: Restart workshop + smoke the live page**

```bash
pkill -f "web.server" 2>/dev/null; sleep 2
nohup .venv/bin/python -u -m web.server > /tmp/cap-smoke.log 2>&1 &
disown
sleep 6
ss -tlnp 2>/dev/null | grep 8081
curl -s -o /dev/null -m 3 -w "/animations -> %{http_code}\n" http://127.0.0.1:8081/animations
curl -s -o /dev/null -m 3 -w "/api/captures/state -> %{http_code}\n" http://127.0.0.1:8081/api/captures/state
curl -s -m 3 http://127.0.0.1:8081/api/captures/state
echo
```

Expected: both paths 200; the state endpoint returns `{"running": false, ...}`.

- [ ] **Step 6: Commit**

```bash
git add web/server.py web/static/cockpit.css
git commit -m "feat(captures): UI bar + counter + save form on the Animations tab"
```

---

### Task 9: README + final verify

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the Animations bullet in the cockpit section**

Find the `🎞 Animations` bullet under `## Web UI (cockpit)`. Currently ends with `Manual rename and merge available via animations.json (tracked in git, written by the tab's UI).` Add one sentence about capture:

REPLACE:

```markdown
  - **🎞 Animations** — the deduped catalog of motion patterns derived from
    your `presets/*.json` library. Click a row to pick new colors and save
    the result as a new preset. Useful when you've captured the same
    Lepro-AI prompt twice with different palettes and want to see they're
    the same motion underneath. Manual rename and merge available via
    `animations.json` (tracked in git, written by the tab's UI).
```

WITH:

```markdown
  - **🎞 Animations** — the deduped catalog of motion patterns derived from
    your `presets/*.json` library. Click a row to pick new colors and save
    the result as a new preset. Useful when you've captured the same
    Lepro-AI prompt twice with different palettes and want to see they're
    the same motion underneath. Manual rename and merge available via
    `animations.json` (tracked in git, written by the tab's UI). A **🎥
    Capture** button at the top lets you grow the library from the UI:
    click it, trigger one animation in the Lepro phone app, the server
    records the d50 frames over MQTT (auto-stops on 6 s idle or 90 s cap)
    and saves the result as a new preset. A counter shows your progress
    toward the Lepro app's ~72-animation catalog.
```

- [ ] **Step 2: Verify the bullet landed correctly**

```bash
grep -B1 -A2 "🎥 Capture" README.md | head -10
```

Expected: the new sentence sits inside the Animations bullet, NOT in another section.

- [ ] **Step 3: Final full-suite run**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 4: Final smoke across all routes**

```bash
pkill -f "web.server" 2>/dev/null; sleep 2
nohup .venv/bin/python -u -m web.server > /tmp/final-smoke.log 2>&1 &
disown
sleep 6
for path in / /diy /ticker /clock /animations /api/animations /api/captures/state /api/cockpit/active /static/cockpit.css /static/cockpit.js; do
  curl -s -o /dev/null -m 3 -w "  $path -> %{http_code}\n" "http://127.0.0.1:8081$path"
done
```

Expected: every path 200 (or 302 for /state).

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document the capture UI in the Animations tab section"
```

---

## Self-Review

**Spec coverage:**
- Pure `dedup_consecutive` → Task 1 ✓
- Pure `auto_capture_name` → Task 2 ✓
- Pure `build_capture_preset` → Task 3 ✓
- `CaptureSession` state + snapshot → Task 4 ✓
- `CaptureSession` async loop (200 ms poll + idle/cap auto-stop) → Task 5 ✓
- 4 routes (`/api/captures/{start,state,save,cancel}`) → Task 6 ✓
- `_stop_capture()` helper + power-off teardown → Task 6 ✓
- Mutex against ticker/clock/preview/diy-paint → Task 6 ✓
- Active-mode banner `"capturing"` branch → Task 6 ✓
- HTTP smoke tests → Task 7 ✓
- `_PANEL_ANIMATIONS` capture bar + counter + save form + dedup notice → Task 8 ✓
- `.capture-*` CSS → Task 8 ✓
- Resumability via page-mount poll → Task 8 (the `pollCaptureState()` call at the bottom of the script) ✓
- Progress counter "N unique / ~72 target" → Task 8 ✓
- README → Task 9 ✓
- Validation step before any code (Known Risk section in spec) → Task 0 ✓

**Placeholder scan:** no TBD/TODO/"similar to". Every step has complete code blocks. Task 0 is explicitly marked as gate-keeping (BLOCKED if the validation fails) rather than vague.

**Type consistency:**
- `CaptureSession(client, baseline_d50, idle_timeout=, hard_cap=)` — same constructor across Tasks 4, 5, 6, 7 ✓
- `record_frame(d50)`, `frame_count` (property), `frames` (property) — consistent across Tasks 4, 5 ✓
- `start()`, `stop()`, `_tick_once()`, `_run()`, `_should_auto_stop()`, `snapshot()` — same names everywhere ✓
- Snapshot keys (`running`, `started_at`, `frame_count`, `auto_stop_at`, `default_name`) — same in Task 4 (definition), Task 5 (tests), Task 6 (`api_captures_state`), Task 8 (JS reads) ✓
- Route paths `/api/captures/{start,state,save,cancel}` — same in Tasks 6, 7, 8 ✓
- `_capture_session` global name — same in Tasks 6, 7, 8 ✓
- Mutex helper `_check_capture_mutex()` — defined in Task 6, called in Task 6 step 6 ✓

**Notes for the implementer:**
- Task 0 is a real gate. If the validation fails, the rest of the plan can't ship — pivot to the second-Lepro-account migration instead.
- Task 5's tests use very short `idle_timeout` (0.3s) and `hard_cap` (0.5s) values to keep test runs fast. The real defaults (6.0 / 90.0) are baked into `_IDLE_TIMEOUT_S` / `_HARD_CAP_S` constants at module scope so they're easy to tune.
- The matched-animation lookup in `api_captures_save` (Task 6 step 3) compares preset names — it relies on the freshly-saved preset being one of the members in the group. If the user saves a preset with a name that fingerprints into an existing group, the response highlights the relationship. This works because `_grouped_animations()` always re-scans `presets/` fresh, so the new file IS in the scan.
- The page-mount `pollCaptureState()` call at the bottom of Task 8's script recovers any in-flight capture if the user navigates away from `/animations` and comes back. Same mechanism as the DIY page's lamp-state restore.
