# Stock-Lamp Design Spec

**Date:** 2026-05-28
**Status:** Approved (pending implementation plan)
**Repo:** git@github.com:FrankLaVigne/LeproTB1.git

## Goal

A standalone script that **tracks a single stock ticker and turns the TB1
green or red on every uptick / downtick** — using live-tick semantics
(compare each poll to the previous poll). Ticker is a single CLI argument
that encodes both symbol and exchange via Yahoo's standard suffix convention
(`IBM`, `7203.T`, `BBVA.MC`, etc.). Polling interval is configurable.

Animations are out of scope for this iteration — `decide_color` is structured
as the clean swap-point where richer effects can replace solid colors later.

## Non-goals

- No multi-ticker tracking (start with one; add later if useful).
- No baseline modes other than "previous poll" (vs day-open / vs prev-close
  are intentionally deferred).
- No purchase-price / P&L tracking.
- No market-hours handling (after-hours: prices stop ticking, lamp holds last
  state — that's fine).
- No animations / effects on tick (deferred; `decide_color` is the hook).
- No CLI-subcommand integration into `cli.py` (deferred; cli.py is one-shot
  by design and this loops).
- No MCP tool wrapping (deferred; YAGNI without an agent caller).

## Architecture

One process, one ticker, one persistent `LeproClient`, one polling loop.

```
                ┌───────────────────────────────┐
                │  yfinance.Ticker(symbol)      │
                │      .fast_info["last_price"] │
                └──────────────┬────────────────┘
                               │ float | None
                               ▼
              ┌─────────────────────────────────────┐
              │  fetch_price(symbol)                │
              │  (network errors → None)            │
              └──────────────┬──────────────────────┘
                             │
                             ▼
              ┌─────────────────────────────────────┐
              │  decide_color(prev, now)            │
              │   uptick   → (0, 255, 0)            │
              │   downtick → (255, 0, 0)            │
              │   flat     → None                   │
              │   prev None→ None (first sample)    │
              └──────────────┬──────────────────────┘
                             │ (r,g,b) | None
                             ▼
              ┌─────────────────────────────────────┐
              │  await client.set_color(r, g, b)    │
              │  (skipped if None)                  │
              └─────────────────────────────────────┘
                             │
                             ▼
                  asyncio.sleep(interval)
                  (loop)
```

Reuses:
- `lepro.LeproClient` — login (cached session) + MQTT + `set_color`.
- `lepro.load_config()` — credentials from `config.json` / `LEPRO_*` env.

## Components (`stock_lamp.py`)

### `fetch_price(symbol: str) -> float | None`
Synchronous wrapper around `yfinance.Ticker(symbol).fast_info["last_price"]`.
Returns the float price on success, `None` on any exception (network,
yfinance internal errors, missing field). The loop calls it via
`asyncio.to_thread` so it doesn't block the event loop.

### `decide_color(prev: float | None, now: float) -> tuple[int,int,int] | None`
Pure function. The only place tick semantics live.
- `prev is None` → `None` (first sample, just establish baseline)
- `now > prev`  → `(0, 255, 0)`  (green)
- `now < prev`  → `(255, 0, 0)`  (red)
- `now == prev` → `None` (no publish)

### `async run(symbol, interval, client) -> None`
The loop. Holds `prev_price: float | None = None`. Each iteration:
1. `now = await asyncio.to_thread(fetch_price, symbol)`
2. If `now is None`: print a `"warn: fetch failed"` line, `await asyncio.sleep(interval)`, continue.
3. `color = decide_color(prev_price, now)`
4. Print a status line (see Output below).
5. If `color is not None`: `await client.set_color(*color)` (wrapped in try/except — log and continue on lamp errors).
6. `prev_price = now`
7. `await asyncio.sleep(interval)`

### `main() -> None`
- argparse: `symbol` (positional), `--interval` (int, default `30`).
- Validates `interval >= 5` (lower bound so we don't hammer Yahoo).
- Builds `LeproClient` from `load_config()`.
- On first sample only: if `fetch_price` returns `None`, print a clear error
  and exit `1`. Otherwise establish baseline and proceed.
- Calls `await client.login() / await client.connect_mqtt()` once.
- Runs `run(...)` inside a `try/finally` that calls `await client.close()`.
- `KeyboardInterrupt` exits gracefully with no traceback.

## CLI

```bash
python stock_lamp.py IBM
python stock_lamp.py 7203.T --interval 10
python stock_lamp.py BBVA.MC --interval 60
```

Exit codes:
- `0` — clean Ctrl-C exit
- `1` — first sample failed (bad symbol / network down at startup)
- `2` — bad arguments (argparse default)

## Output

One status line per poll (timestamp ISO, symbol, price, decision):

```
2026-05-28 02:45:12  IBM  $258.41  (first sample, baseline set)
2026-05-28 02:45:42  IBM  $258.48  ↑ GREEN
2026-05-28 02:46:12  IBM  $258.45  ↓ RED
2026-05-28 02:46:42  IBM  $258.45  · (no change)
2026-05-28 02:47:12  IBM  warn: fetch failed
```

## Error handling

| Source | Behavior |
|--------|----------|
| `fetch_price` raises | caught → returns `None` → loop warns & continues |
| First-sample `None` | clean error message, exit `1` |
| Mid-run `None` | warn line, no publish, next tick retries |
| `LeproClient.set_color` raises | log warning, continue (next tick re-tries) |
| Ctrl-C | finally block awaits `client.close()`, exit `0` |
| `interval < 5` | argparse error, exit `2` |

No automatic reconnect logic beyond what `LeproClient.listen_forever` and the
existing session caching already provide. If the cached session expires
mid-run, `set_color` will raise `AuthError` and we surface a warning; the user
can restart.

## Future hook for animations

`decide_color` returns a `tuple[int,int,int] | None` today. When we add
animations, the swap is:

```python
def decide_effect(prev: float | None, now: float) -> Effect | None: ...

# in run():
effect = decide_effect(prev_price, now)
if effect is not None:
    await effect.apply(client)
```

`Effect` would be a small protocol with an `async apply(client)` method. Solid
colors become `SolidColorEffect((0,255,0))`; an "upbeat" animation could
become `RainbowChaseEffect()`; etc. **Not designing it now** — just noting
the boundary so we keep it clean.

## Dependencies

Add to `requirements.txt`:
```
yfinance>=0.2
```

`yfinance` has no API key requirement and supports global exchanges via
ticker suffixes. It's unofficial but stable and widely used. Acceptable
trade-off for a "fun" tracker.

## Tests

Two pure unit tests, no network / no hardware:

```python
def test_decide_color_first_sample_returns_none():
    assert decide_color(None, 100.0) is None

def test_decide_color_uptick_green():
    assert decide_color(100.0, 100.5) == (0, 255, 0)

def test_decide_color_downtick_red():
    assert decide_color(100.0, 99.9) == (255, 0, 0)

def test_decide_color_flat_returns_none():
    assert decide_color(100.0, 100.0) is None
```

No `fetch_price` test (network-dependent; would require mocking yfinance —
overkill for this iteration). No integration test against the real lamp (manual
verification with `python stock_lamp.py IBM` during market hours suffices).

## File plan

- `stock_lamp.py` (create) — the script: imports, `fetch_price`, `decide_color`,
  `run`, `main`.
- `requirements.txt` (modify) — add `yfinance>=0.2`.
- `tests/test_stock_lamp.py` (create) — the four `decide_color` tests.
- `README.md` (modify) — add a `## Stock tracker` section with a one-liner
  example and a note that ticker uses Yahoo's suffix convention.

## Open questions

None — every design decision above is explicit. Anything genuinely
unspecified is intentionally deferred to a future iteration (animations,
multi-ticker, baseline modes other than live-tick).
