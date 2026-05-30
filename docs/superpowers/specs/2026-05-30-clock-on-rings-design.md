# Clock on the Rings — Design Spec

**Date:** 2026-05-30
**Status:** Approved by user via brainstorm (dot mode + per-ring configurable colors + 1s cadence + 12h default + ticker-style mutex + stop leaves last frame).

## Goal

Add a `/clock` page that turns the lamp into a three-handed analog clock: a
single bright LED on each ring marks the current seconds (outer 88),
minutes (middle 62), and hours (inner 46), updated every second.

## Approach

Same architecture as the stock ticker (`ticker.py` + `TickerSession`):
- A new `clock.py` module with pure helpers and a `ClockSession` class.
- Background `asyncio.Task` lives in the workshop process, repaints at 1 Hz.
- Three routes (`/api/clock/start`, `/api/clock/stop`, `/api/clock/state`).
- One page route (`/clock`) with the 5th nav tab (`⏰ Clock`).
- Mutex against `/api/diy/paint` and `/api/preview` while running.

The clock is deterministic — it just needs the local clock and per-ring
color preferences. No external data feed, no first-fetch validation,
no network polling. Simpler than the ticker.

## Lamp behaviour

### Dot rendering

Exactly **one LED per ring** lit at any moment. All other LEDs off.

The LED index for each ring (in page-space, 0 = 12 o'clock):

```
outer  = round((seconds + microseconds/1e6) * 88 / 60) % 88
middle = round((minutes + seconds/60) * 62 / 60) % 62
inner  = round((hour_unit + minutes/60) * 46 / hours_per_cycle) % 46
```

where for 12-hour mode `hour_unit = now.hour % 12` and `hours_per_cycle =
12`; for 24-hour mode `hour_unit = now.hour` and `hours_per_cycle = 24`.

Adding the fractional carry from the next-finer unit (seconds drift the
minute hand, minutes drift the hour hand) gives the analog clock's slow
between-tick motion — a nice touch that costs nothing.

### Page-space → physical rotation

`build_clock_leds(positions, colors)` returns a 196-entry **page-space**
array. `ClockSession._tick` applies `workshop.apply_lamp_rotation` and
then `workshop.build_d50_from_leds` to produce the physical-space d50
that's sent to the lamp. Reuses the same calibration the DIY page uses.

### Cadence

`_tick` runs every 1 second. ~60 MQTT writes per minute — within the
lamp's tolerance (we ran the ticker at 6 writes/minute and stock_lamp
historically at 2 writes/minute with no issues; 60/min is fine for the
local LAN MQTT broker we're connected to).

### Stop behaviour

Cancels the background task and **leaves the last frame on the lamp**.
No `d1:0` send. User hits the existing power button if they want it
dark. (Different from the ticker, which sends `d1:0` on stop — the clock
is meant to be a persistent display; abrupt power-off would feel wrong.)

## Routes

### `POST /api/clock/start`

Request body:
```json
{
  "colors": {
    "outer":  "FF0000",   // optional, defaults to red
    "middle": "00FF00",   // optional, defaults to green
    "inner":  "0000FF"    // optional, defaults to blue
  },
  "mode": "12h"   // optional, "12h" or "24h"; defaults to "12h"
}
```

Behaviour:
- Reject (400) if any color isn't a 6-hex string.
- Reject (400) if `mode` isn't `"12h"` or `"24h"`.
- Reject (409) if `_clock_session is not None and _clock_session.running`.
- Construct a `ClockSession(client, colors, mode)`, start the background
  task, store it as `_clock_session`.

Response (200):
```json
{"ok": true, "since": "2026-05-30T08:15:03", "mode": "12h"}
```

### `POST /api/clock/stop`

Behaviour:
- If not running, return 200 `{"ok": true}` (idempotent).
- Cancel the task and await it.
- Leave the lamp in its current state (no `d1:0` send).
- Clear `_clock_session`.

### `GET /api/clock/state`

Always available. Returns:

```json
{
  "running": true,
  "since": "2026-05-30T08:15:03",
  "mode": "12h",
  "colors": {"outer": "FF0000", "middle": "00FF00", "inner": "0000FF"},
  "now_displayed": "2026-05-30T08:15:42"
}
```

When `running: false`, all detail fields are `null`.

## Mutex

Identical to the ticker. While `_clock_session is not None and
.running`:

- `POST /api/diy/paint` → 409 `{"ok": false, "error": "clock is running; stop it first"}`
- `POST /api/preview` → 409 (same message)
- `POST /api/power {on: false}` → stops the clock first, then powers off
- `POST /api/stop` → stops the clock too
- Everything else (brightness, saves, GETs, ticker routes) → unchanged

If both the ticker AND the clock try to run, the second one to start
returns 409. They can't coexist — they'd fight over the lamp's d50.

## Module structure

### New file: `clock.py`

```python
def compute_positions(now: datetime, mode: str = "12h") -> dict:
    """Return {outer, middle, inner} per-ring page-space LED indices."""

def build_clock_leds(positions: dict, colors: dict) -> list:
    """Return a 196-entry page-space LED array with one dot per ring."""

class ClockSession:
    def __init__(self, client, colors: dict, mode: str = "12h"): ...
    @property
    def running(self) -> bool: ...
    def snapshot(self) -> dict: ...   # serializable for /state
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

Pure functions importable for testing. `build_clock_leds` does NOT call
the d50 encoder or apply rotation — it returns a page-space LED array.
The session class does the rotation + encoding + send in `_tick`.

### Modified file: `workshop.py`

- Module-level `_clock_session: ClockSession | None = None`.
- `_check_clock_mutex()` helper (mirrors `_check_ticker_mutex`).
- Four new route handlers: `api_clock_start`, `api_clock_stop`,
  `api_clock_state`, `index_clock`.
- New `_PAGE_CLOCK` inline HTML constant (5th tab strip, color pickers,
  12/24h toggle, start/stop, status line, live SVG visualizer).
- Update tab strips on all 4 existing pages (`_PAGE`, `_PAGE_DIY`,
  `_PAGE_TICKER`, `_PAGE_STATE`) to include the Clock tab.
- Add `_check_clock_mutex()` calls into `api_diy_paint` and `api_preview`.
- Update `api_power` and `api_stop` to also tear down `_clock_session`.
- Register the four new routes in `build_app`.

### Modified file: `static/lamp-utils.js`

Add a small client-side `computeClockPositions(now, mode)` helper so the
page can render the visualizer at 1 Hz without polling the server.
Identical math to the server's `compute_positions`. (Discrepancy concern:
client and server might drift apart if the user's browser clock is wrong,
but for a personal LAN tool this is acceptable — the lamp shows server
time, the page shows browser time, usually identical.)

### New file: `tests/test_clock.py`

~10 tests for the two pure functions plus `ClockSession.snapshot()` shape.

## Page layout

```
┌──────────────────────────────────────────────────┐
│ 🎨 Presets ✏️ DIY 📈 Ticker 📊 State ⏰ Clock    │  ⏻ On ⏻ Off
├──────────────────────────────────────────────────┤
│                                                  │
│         (live SVG clock face visualizer,         │
│         same 3-ring SVG as DIY/State pages)      │
│                                                  │
├──────────────────────────────────────────────────┤
│ COLORS                                           │
│ Outer (seconds)  [color picker red]              │
│ Middle (minutes) [color picker green]            │
│ Inner (hours)    [color picker blue]             │
├──────────────────────────────────────────────────┤
│ HOUR FORMAT                                      │
│ [12h ✓] [24h]                                    │
├──────────────────────────────────────────────────┤
│ [ ▶ Start ]  [ ⏹ Stop ]                          │
│ status: not running                              │
└──────────────────────────────────────────────────┘
```

Interaction:
- Color pickers + mode toggle editable any time; if running, a change
  POSTs an update OR shows "stop first to change". MVP choice: show
  "stop first" (avoid the complexity of live re-config on the server).
- Visualizer renders client-side at 1 Hz using `computeClockPositions`.
  When stopped, it shows what the lamp WOULD show — a preview, not the
  actual lamp state.
- Page polls `/api/clock/state` every 5s for the running flag and the
  server's `now_displayed` (purely for the status line).

## Error handling

| Scenario | Behaviour |
|---|---|
| Color not 6-hex on start | 400, no session created |
| Mode not 12h/24h | 400, no session created |
| Start while already running | 409 |
| Start while ticker running | 409 (ticker mutex catches it first) |
| Stop when not running | 200, idempotent |
| `_client.send_raw` raises | Log, continue. Next tick retries. |
| Workshop process restart | Clock is gone (no persistence). User restarts via Start. |
| Lamp time vs clock displayed | Server uses `datetime.now()` (local time on the workshop VM). |

## Testing

`tests/test_clock.py`:

- `test_compute_positions_midnight_12h_all_at_zero` — 00:00:00 → all positions 0.
- `test_compute_positions_noon_12h_hour_at_zero` — 12:00:00 → inner 0 (since noon = 12 = hour 12 % 12 = 0).
- `test_compute_positions_noon_24h_hour_at_halfway` — 24h mode at 12:00:00 → inner ≈ 23 (halfway around 46).
- `test_compute_positions_fractional_minute_drifts_hour_hand` — 8:30 → inner is between hour-8's slot and hour-9's slot.
- `test_compute_positions_fractional_second_drifts_minute_hand` — minute=5 second=30 → middle is between minute-5's slot and minute-6's slot.
- `test_compute_positions_rejects_unknown_mode` — `mode="invalid"` raises.
- `test_build_clock_leds_paints_one_per_ring` — returns array with exactly 3 non-None entries.
- `test_build_clock_leds_uses_correct_colors_at_correct_indices` — outer color at outer position, etc.
- `test_clock_session_snapshot_shape` — round-trip via json.dumps.
- `test_clock_session_initial_not_running` — newly constructed session has running=False.

Plus 2-3 async tests using `_FakeClient` from the ticker tests:
- `test_clock_session_tick_sends_d50` — one `_tick_once` call publishes a payload to the lamp.
- `test_clock_session_start_stop_lifecycle` — `start()` makes it running, `stop()` makes it not.

## Deliberately deferred (YAGNI)

- Live-reconfigure colors / mode without stopping
- Per-ring display mode (some dot, some sweep)
- Color shift across the day
- Multiple time zones across the rings
- AM/PM indicator
- Date display
- Persistence of config across workshop restart (defer to v2 — easy
  addition via `clock_config.json` later)

## File-change summary

| File | Change | Lines (est.) |
|---|---|---|
| `clock.py` (new) | Pure helpers + `ClockSession` | ~160 |
| `workshop.py` | 4 routes, mutex helper, page constant, 5th tab × 4 pages, power/stop integration | ~350 |
| `static/lamp-utils.js` | + `computeClockPositions` helper | ~20 |
| `tests/test_clock.py` (new) | Unit + async tests | ~180 |
| `README.md` | Append Clock section | ~10 |

Total: ~720 lines added.
