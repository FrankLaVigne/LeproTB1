# Stock Ticker Page — Design Spec

**Date:** 2026-05-29
**Status:** Approved (pending user review)

## Goal

Add a `/ticker` page to `workshop.py` that lets the user run a multi-symbol stock-tracking session against the lamp from the web, replacing the `stock_lamp.py` CLI for casual use. Up to three Yahoo-Finance tickers run in parallel, one per concentric ring (outer / middle / inner), each ring showing its symbol's most recent direction as a solid color, with a brief whole-lamp breathe flash on every tick.

## Approach

A background `asyncio.Task` lives in the workshop process — the same process that already owns the `LeproClient` MQTT slot. This is the only viable architecture: a subprocess wrapping `stock_lamp.py` would open its own MQTT session and conflict with the workshop's (the cloud enforces ~one session per account), and a browser-driven poll loop is blocked by Yahoo's CORS posture.

Pure helpers (`decide_ring_color`, `build_ticker_d50`) live in a new `ticker.py` module with their own unit tests. `workshop.py` holds a module-level `_ticker_session: TickerSession | None` and three thin route handlers.

## Lamp behaviour

### Per-ring solid color (steady base state)

| Ring condition | Color (hex) |
|---|---|
| No symbol assigned | `000000` (off) |
| First sample taken, no direction yet | `FFFFFF` (white — baseline) |
| Most recent direction ↑ | `00FF00` (green) |
| Most recent direction ↓ | `FF0000` (red) |
| Most recent fetch failed | `FFFF00` (yellow) |
| Most recent direction flat | (ring keeps its prior color) |

The 196-LED array is composed from the three ring colors: indices 0–87 = outer, 88–149 = middle, 150–195 = inner.

### Tick flash

When `decide_ring_color` reports `ticked=True` for any ring on a poll, the server records `flash_color = <that ring's new color>` and `flash_until = now + 5s`. During the 5-second flash window the lamp shows a **single-color full-lamp Breathe** in the flash color — the per-ring colors are temporarily hidden — using the d50 effect tail produced by `effect_tail("Breathe", speed=50)`. When `now >= flash_until`, the next payload reverts to the per-ring multi-color Steady d50 and the per-ring colors reappear.

When two rings tick on the same poll, evaluation order is outer → middle → inner, so the inner ring's color "wins" the flash. Deterministic and good enough; we don't need a richer policy for three concurrent streams.

### Brightness

Brightness (d52) stays orthogonal to the ticker — `POST /api/brightness` remains available while running.

## Routes

### `POST /api/ticker/start`

Request body:
```json
{
  "outer": "AAPL",          // optional
  "middle": "IBM",          // optional
  "inner": "SPY",           // optional
  "interval": 30            // seconds; must be one of {10, 30, 60, 300}
}
```

Behaviour:
1. Reject (400) if all three symbol fields are empty/missing.
2. Reject (400) if `interval` is not in the allowed set.
3. Reject (409) if `_ticker_session is not None and _ticker_session.running`.
4. Run a first-sample `fetch_price` for each non-empty symbol via `asyncio.to_thread`. If ANY symbol's first fetch returns `None`, abort with 400 `{"ok": false, "error": "could not fetch first price for <symbols>"}` and do NOT start the loop. (Mirrors `stock_lamp.py`'s `_run_main` behavior.)
5. Create a `TickerSession` with the validated symbols + baselines, start its background task, store it as `_ticker_session`.

Response (200):
```json
{
  "ok": true,
  "since": "2026-05-29T14:25:03",
  "baselines": {"outer": 176.42, "middle": 138.25}
}
```

### `POST /api/ticker/stop`

Behaviour:
1. If `_ticker_session is None` or not running, return 200 `{"ok": true}` (idempotent).
2. Cancel the background task and await its completion.
3. Send `{"d1": 0}` to power the lamp off.
4. Clear `_ticker_session`.

Response: `{"ok": true}`.

### `GET /api/ticker/state`

Always available (no 409, no auth). Returns:

```json
{
  "running": true,
  "since": "2026-05-29T14:25:03",
  "interval": 30,
  "flash_until": "2026-05-29T14:32:12",
  "rings": {
    "outer": {
      "symbol": "AAPL",
      "prev_price": 175.10,
      "current_price": 176.42,
      "color": "00FF00",
      "ticked_at": "2026-05-29T14:32:07",
      "last_fetch_at": "2026-05-29T14:32:07",
      "last_fetch_ok": true,
      "recent_ticks": [
        {"at": "2026-05-29T14:32:07", "price": 176.42, "direction": "up"},
        {"at": "2026-05-29T14:31:37", "price": 175.10, "direction": "baseline"}
      ]
    },
    "middle": { ... },
    "inner":  null
  }
}
```

When `running: false`, `since`, `interval`, `flash_until`, and `rings` are all `null`.

## Mutex with other lamp endpoints

While the ticker is running, these existing endpoints **return 409** `{"ok": false, "error": "stock ticker is running; stop it first"}`:

- `POST /api/diy/paint`
- `POST /api/preview`

These remain available unchanged:

- `POST /api/power` — power-off implicitly stops the ticker (calls the same shutdown path as `/api/ticker/stop`); power-on resumes from current state.
- `POST /api/brightness` — d52 only; no conflict.
- `POST /api/stop` — already a "stop everything" gesture; it stops the ticker too.
- `POST /api/save`, `POST /api/diy/save` — file writes only, never touch the lamp.
- All GETs.

## Module structure

### New file: `ticker.py`

```python
RingState = TypedDict("RingState", {
    "symbol": str,
    "prev_price": float | None,
    "current_price": float | None,
    "color": str,                  # 6-hex
    "ticked_at": datetime | None,
    "last_fetch_at": datetime | None,
    "last_fetch_ok": bool,
    "recent_ticks": list[dict],    # capped at 10 entries
})

def fetch_price(symbol: str) -> float | None: ...
def decide_ring_color(prev_price, now_price, prev_color) -> tuple[str, bool]: ...
def build_ticker_d50(rings: dict, flash_color: str | None) -> str: ...

class TickerSession:
    def __init__(self, client, symbols: dict[str, str], interval: int): ...
    @property
    def running(self) -> bool: ...
    def snapshot(self) -> dict: ...   # serializable for GET /state
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

`build_ticker_d50` re-uses `workshop.build_d50_from_leds` to avoid drift in the d50 format. (Import direction: `ticker.py` imports from `workshop`. We could split the d50 helpers into a third module to keep `ticker` independent of `workshop`, but YAGNI — `workshop.build_d50_from_leds` is small, pure, and unlikely to move.)

### Modified file: `workshop.py`

Add:
- Module-level `_ticker_session: TickerSession | None = None`.
- `_check_ticker_mutex()` helper that raises a 409 if the session is running. Called from `api_diy_paint` and `api_preview`.
- Three new route handlers: `api_ticker_start`, `api_ticker_stop`, `api_ticker_state`.
- A new `GET /ticker` handler `index_ticker` + inline `_PAGE_TICKER` constant.
- Register all four new routes in `build_app`.
- Update the tab strip in `_PAGE` and `_PAGE_DIY` to include the Ticker tab.

### New file: `tests/test_ticker.py`

Unit tests for the two pure functions and the `TickerSession.snapshot()` shape. ~10 tests.

## Page layout

Three stacked ring cards + an interval picker + Start/Stop + status line:

```
┌─────────────────────────────────────────┐
│ 🎨 Workshop  ✏️ DIY  📈 Ticker (active)  │  ⏻ On  ⏻ Off
├─────────────────────────────────────────┤
│ OUTER                            🟢      │
│ Symbol: [AAPL_______]                    │
│ $176.42  ↑ green                         │
│ prev: $175.10 · updated 14:32:07 (5s)    │
│ history: ↑$176.42 14:32 · ↓$175.10 14:31 │
├─────────────────────────────────────────┤
│ MIDDLE                           🔴      │
│ Symbol: [IBM________]                    │
│ ...                                      │
├─────────────────────────────────────────┤
│ INNER                            ⚫      │
│ Symbol: [____________]                   │
│ (no symbol — ring off)                   │
├─────────────────────────────────────────┤
│ Poll every: [ 10s | 30s ✓ | 60s | 5m ]   │
│ [ ▶ Start ]  [ ⏹ Stop ]                  │
│ status: running since 14:25 · next 22s   │
└─────────────────────────────────────────┘
```

### Interaction details

- Symbol text inputs are editable when stopped, `readonly` when running.
- Interval radios are editable when stopped, disabled when running.
- Start button is disabled if all three symbol fields are blank.
- Stop button is disabled when not running.
- Per-card color dot reflects the current ring color (off / white / green / red / yellow).
- "history" line shows the last 5 entries from `recent_ticks` (newest first), compact format: `↑$176.42 14:32`.
- Page polls `GET /api/ticker/state` every **5 seconds** when the tab is foregrounded. (No backoff / visibility tricks; YAGNI.)
- "next poll in Xs" countdown is computed client-side from `since + interval × n`.

### Tab nav

Add a third tab (📈 Ticker) to the `_PAGE` and `_PAGE_DIY` headers. Existing inline-style pattern from Task 7 of the DIY plan reused verbatim.

## Data flow per tick

1. Background task wakes up, calls `await asyncio.gather(*[asyncio.to_thread(fetch_price, sym) for sym in active_symbols])`.
2. For each active ring, call `decide_ring_color(ring.prev_price, now, ring.color)` → `(new_color, ticked)`.
3. Update ring state in-place: `prev_price ← now if now is not None else prev_price`, `current_price ← now`, `color ← new_color`, `last_fetch_at ← datetime.now()`, `last_fetch_ok ← (now is not None)`.
4. If any ring returned `ticked=True`, set `flash_color ← that ring's new_color` (outer→middle→inner order) and `flash_until ← now + 5s`. Push an entry onto `ring.recent_ticks` (capped at 10).
5. Compose effective flash: `flashing = datetime.now() < flash_until`. If flashing, pass `flash_color`; else pass `None`.
6. Call `build_ticker_d50(rings, flash_color if flashing else None)`. When `flash_color is None`, returns a per-ring multi-color steady d50. When `flash_color` is given, returns a single-color (the flash color) full-lamp Breathe d50 — the per-ring colors are intentionally hidden during the 5-second flash and snap back when it ends.
7. `await _client.send_raw({"d1": 1, "d2": 2, "d50": d50})`. On `LeproError`, log and continue (next tick will retry).
8. `await asyncio.sleep(interval)`. Cancellation point.

## Error handling

| Scenario | Behaviour |
|---|---|
| Individual symbol fetch returns `None` | Ring color → yellow. Other rings unaffected. Logged. |
| `_client.send_raw` raises `LeproError` | Log, swallow, continue. Next tick retries. |
| First-fetch validation on `/start` fails for any symbol | Abort start, return 400 with the list of failing symbols. No background task created. |
| Start when already running | 409 `{"ok": false, "error": "stock ticker already running"}`. |
| Stop when not running | 200 `{"ok": true}` (idempotent). |
| `yfinance` import fails | `ticker.py` itself wraps the import in a try/except so the workshop process can still serve the other pages. `fetch_price` returns `None` permanently; the first-fetch validation will fail and the user sees a clear "yfinance not installed" message. |
| Workshop process restart with ticker running | Ticker is gone (no persistence). User clicks Start again. Acceptable for v1. |

## Testing

### `tests/test_ticker.py`

Pure-function tests (no I/O, no mocks beyond a synthetic `fetch_fn`):

- `test_decide_ring_color_first_sample_baseline_white` — prev=None, now=any → (`FFFFFF`, ticked=True)
- `test_decide_ring_color_up_from_baseline` — prev=100, now=110, prev_color=white → (green, True)
- `test_decide_ring_color_down_from_green` — prev=110, now=105, prev_color=green → (red, True)
- `test_decide_ring_color_flat_keeps_color` — prev=100, now=100, prev_color=green → (green, False)
- `test_decide_ring_color_fetch_fail_yellow` — prev=100, now=None, prev_color=green → (yellow, True)
- `test_decide_ring_color_recover_from_yellow` — prev=100, now=110, prev_color=yellow → (green, True)
- `test_build_ticker_d50_three_rings_steady` — assert the 196-LED layout splits correctly at 88/150, Steady tail.
- `test_build_ticker_d50_off_ring_is_black` — unassigned ring stays `000000`.
- `test_build_ticker_d50_breathe_flash_color` — when `flash_color` is non-None, the d50 emits a single-color palette (the flash color, applied to all 196 LEDs) with the Breathe tail. Per-ring colors are NOT preserved during a flash; they reappear when `flash_color` returns to None.
- `test_TickerSession_snapshot_shape` — round-trip a fake session, verify `snapshot()` returns the documented keys.

### Smoke tests (manual, not automated)

- `.venv/bin/python -c "import workshop; print(len(list(workshop.build_app().router.routes())))"` → 20 (was 15, +2 for GET /ticker and its implicit HEAD, +3 POSTs).
- Hit `/ticker` in a browser, fill AAPL/IBM, Start, observe colors change over a minute.

## Deliberately deferred (YAGNI)

- Watchlist / favorites persistence — drop, user can keep symbols in browser autocomplete.
- Session-aware fetch (pre-market / post-market / regular) — drop for v1; the captured-symbol behavior matches `stock_lamp.py` today.
- Per-symbol intervals — drop, one interval governs all three.
- Multi-flash coordination (overlapping tick windows) — drop, inner-wins is good enough.
- Persisting ticker state across server restarts — drop, restart loses state.
- Mobile push on tick — drop, the lamp IS the push.
- "Set as Today's tickers" remembering — drop, type them in.

## File-change summary

| File | Change | Lines (est.) |
|---|---|---|
| `ticker.py` (new) | Pure helpers + `TickerSession` | ~180 |
| `workshop.py` | 3 new POST + 1 new GET routes, mutex helper, page constant | ~350 |
| `tests/test_ticker.py` (new) | Unit tests | ~150 |
| `README.md` | Append Ticker section under Preset workshop | ~10 |

Total: ~690 lines added.
