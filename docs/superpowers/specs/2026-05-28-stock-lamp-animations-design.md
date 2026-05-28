# Stock-Lamp Animations Design Spec

**Date:** 2026-05-28
**Status:** Approved (pending implementation plan)
**Repo:** git@github.com:FrankLaVigne/LeproTB1.git
**Builds on:** `2026-05-28-stock-lamp-design.md`

## Goal

Add **pulsing animations on ticks** to the stock-lamp script: on every uptick
the lamp pulses green, on every downtick it pulses red, and on the **first flat
poll after a tick** it calms down to solid (same color, no motion). Subsequent
identical commands are deduplicated so the pulse animation doesn't restart with
each consecutive same-direction tick. Additionally, **on fetch failure the lamp
goes solid yellow** — a clear "I don't know what's happening" signal — and
recovers automatically on the next successful poll.

The previous spec (`stock-lamp-design.md`) called out `decide_color` as the
clean swap-point for richer effects later — this spec realizes that swap.

## Non-goals

- No new CLI flags. (No `--effect`, `--breath-speed`, etc. YAGNI.)
- No animation other than `breath`. The reference integration has more effect
  names (gradient, flash, wave_*, laser_*), but only `breath` is empirically
  confirmed to animate on the TB1. Exploring the others is a separate iteration.
- No multi-color animations or captured presets — those are scene-specific and
  don't fit a single-color up/down indicator.
- No animation speed tuning. Default `breath` speed = 50.
- No "after-hours dimming", no idle/neutral state, no time-of-day awareness.

## Architecture

Refactor `stock_lamp.py` only — same file, same dependencies, same CLI surface,
same lifecycle. The change is internal: swap the color-tuple return for a
richer command tuple, route through a small dispatcher.

```
                  poll → fetch_price → now (float or None on failure)
                              │
                              ▼
   decide_command(prev_price, now, last_command)  →  Command | None
                              │
                              ▼
              cmd is not None?  →  apply_command(client, cmd)
                              │           │
                              │           │  ("animate", c) → set_effect("breath", color=c)
                              │           │  ("solid",  c) → set_color(*c)
                              ▼           ▼
        update prev_price (on success only) + last_command (on publish only)
```

`now is None` (fetch failure) is passed straight into `decide_command`, which
returns `("solid", YELLOW)` — no special handling needed in `run`.

## Components (changes to `stock_lamp.py`)

### Removed

- `decide_color(prev, now) -> tuple[int,int,int] | None`. The command form
  replaces it.

### Added

#### `Command` type alias and color constants
Module-level:
```python
Command = tuple[str, tuple[int, int, int]]  # ("animate" | "solid", (r, g, b))

GREEN = (0, 255, 0)
RED = (255, 0, 0)
YELLOW = (255, 255, 0)  # fetch failure
```

#### `decide_command(prev_price, now, last_command) -> Command | None`
Pure function. `now` may be `None` to signal a fetch failure. State table:

| `prev_price` | `now` | last_command | Result |
|---|---|---|---|
| any | `None` (fetch failed) | `("solid", YELLOW)` | `None` (dedup; already yellow) |
| any | `None` (fetch failed) | anything else | `("solid", YELLOW)` |
| `None` | float | any | `None` (baseline, first sample) |
| float | `now > prev` | `("animate", GREEN)` | `None` (dedup) |
| float | `now > prev` | anything else | `("animate", GREEN)` |
| float | `now < prev` | `("animate", RED)` | `None` (dedup) |
| float | `now < prev` | anything else | `("animate", RED)` |
| float | `now == prev` | `("animate", c)` | `("solid", c)` (calm down) |
| float | `now == prev` | `("solid", c)` | `None` (already solid) |
| float | `now == prev` | `None` | `None` (first flat from cold) |

The fetch-failure row goes FIRST so a yellow lamp doesn't depend on the
previous price. On recovery (first successful poll), the table falls through to
the normal price-comparison rules using the unchanged `prev_price` — so if the
price moved during the outage, you'll see the appropriate uptick/downtick on
the very first successful poll. If the recovery price equals `prev_price`, the
flat-after-animate or flat-after-solid rules apply normally — which means after
yellow, a flat recovery returns `None` (lamp stays yellow until a real tick
happens).

Pseudocode:
```python
def decide_command(prev_price, now, last_command):
    if now is None:
        desired = ("solid", YELLOW)
        return None if desired == last_command else desired
    if prev_price is None:
        return None
    if now > prev_price:
        desired = ("animate", GREEN)
    elif now < prev_price:
        desired = ("animate", RED)
    elif last_command is not None and last_command[0] == "animate":
        desired = ("solid", last_command[1])
    else:
        return None
    return None if desired == last_command else desired
```

#### `apply_command(client, cmd) -> None` (async)
Small dispatcher:
```python
async def apply_command(client, cmd):
    kind, color = cmd
    if kind == "animate":
        await client.set_effect("breath", speed=50, color=color)
    else:  # "solid"
        await client.set_color(*color)
```

`speed=50` is the default mid-pace pulse. `set_effect`/`set_color` are existing
`LeproClient` methods.

### Modified

#### `run(symbol, interval, client, fetch_fn=fetch_price) -> None`
- Replaces `prev: float | None = None` with TWO state variables:
  ```python
  prev: float | None = None
  last_command: Command | None = None
  ```
- Replaces the existing `decide_color → set_color` block with:
  ```python
  now = await asyncio.to_thread(fetch_fn, symbol)
  cmd = decide_command(prev, now, last_command)
  status = _status_line(prev, now, cmd, last_command)
  print(f"{_ts()}  {symbol}  {status}")
  if cmd is not None:
      try:
          await apply_command(client, cmd)
          last_command = cmd
      except Exception as e:  # noqa: BLE001
          print(f"warn: lamp publish failed: {e}")
  if now is not None:
      prev = now
  ```
- **Notes:**
  - `last_command` is only updated when `apply_command` succeeds. If the
    publish raises, we keep `last_command` unchanged so the next tick still
    reflects the lamp's actual state from our point of view.
  - `prev_price` is **not** touched when `now is None` (fetch failure). The
    next successful poll compares to the same baseline, so a real tick during
    the outage is detected on recovery.
  - The price is no longer printed inline by `run`; `_status_line` is now
    responsible for the whole suffix including the price when available (since
    "warn: fetch failed" has no price to show).

#### `_status_line(prev, now, cmd, last_command) -> str` (new helper, pure)
Generates the entire status suffix shown after the timestamp + symbol. Pure
for testability. Includes the price (or "warn: fetch failed" when `now is None`):

| Situation | Output |
|---|---|
| `now is None`, publishing yellow | `warn: fetch failed (lamp → yellow)` |
| `now is None`, already yellow (dedup) | `warn: fetch failed (already yellow)` |
| `prev is None` (baseline) | `$X.XX  (first sample, baseline set)` |
| `cmd == ("animate", GREEN)` | `$X.XX  ↑ pulsing green` |
| `cmd == ("animate", RED)` | `$X.XX  ↓ pulsing red` |
| `cmd == ("solid", GREEN)` | `$X.XX  · holding green` |
| `cmd == ("solid", RED)` | `$X.XX  · holding red` |
| Flat dedup (price unchanged, no cmd) | `$X.XX  · (no change)` |
| Same-direction tick deduped (uptick, last was animate green) | `$X.XX  ↑ (already pulsing green)` |
| Same-direction tick deduped (downtick, last was animate red) | `$X.XX  ↓ (already pulsing red)` |

(The "already pulsing" lines come from `cmd is None` but `now != prev`.)

## CLI / dependencies

Unchanged from the previous spec.
- No new flags.
- No new dependencies (`set_effect` already exists in `lepro.py`).

## Lifecycle & error handling

Unchanged. `apply_command` wraps both `set_effect` and `set_color`; either
exception is caught and logged in the existing try/except, the loop continues.

## Tests

Rewrite `tests/test_stock_lamp.py`'s `decide_color` block as `decide_command`
tests. **14 cases** covering every row of the state table plus the dedup gate
and the new fetch-failure behavior:

```python
G = (0, 255, 0)
R = (255, 0, 0)
Y = (255, 255, 0)
ANIMATE_G = ("animate", G)
ANIMATE_R = ("animate", R)
SOLID_G = ("solid", G)
SOLID_R = ("solid", R)
SOLID_Y = ("solid", Y)

def test_decide_command_baseline_returns_none():
    assert stock_lamp.decide_command(None, 100.0, None) is None

def test_decide_command_uptick_from_cold_animates_green():
    assert stock_lamp.decide_command(100.0, 100.5, None) == ANIMATE_G

def test_decide_command_uptick_dedup_when_already_animating_green():
    assert stock_lamp.decide_command(100.0, 100.5, ANIMATE_G) is None

def test_decide_command_downtick_from_cold_animates_red():
    assert stock_lamp.decide_command(100.0, 99.5, None) == ANIMATE_R

def test_decide_command_downtick_dedup_when_already_animating_red():
    assert stock_lamp.decide_command(100.0, 99.5, ANIMATE_R) is None

def test_decide_command_downtick_after_animate_green_animates_red():
    assert stock_lamp.decide_command(100.0, 99.5, ANIMATE_G) == ANIMATE_R

def test_decide_command_flat_after_animate_green_calms_to_solid_green():
    assert stock_lamp.decide_command(100.0, 100.0, ANIMATE_G) == SOLID_G

def test_decide_command_flat_after_animate_red_calms_to_solid_red():
    assert stock_lamp.decide_command(100.0, 100.0, ANIMATE_R) == SOLID_R

def test_decide_command_flat_after_solid_returns_none():
    assert stock_lamp.decide_command(100.0, 100.0, SOLID_G) is None

def test_decide_command_flat_from_cold_returns_none():
    assert stock_lamp.decide_command(100.0, 100.0, None) is None

def test_decide_command_uptick_after_solid_red_animates_green():
    assert stock_lamp.decide_command(100.0, 100.5, SOLID_R) == ANIMATE_G

def test_decide_command_fetch_failure_from_cold_goes_yellow():
    assert stock_lamp.decide_command(None, None, None) == SOLID_Y

def test_decide_command_fetch_failure_from_running_goes_yellow():
    assert stock_lamp.decide_command(100.0, None, ANIMATE_G) == SOLID_Y

def test_decide_command_fetch_failure_dedup_when_already_yellow():
    assert stock_lamp.decide_command(100.0, None, SOLID_Y) is None

def test_decide_command_recovery_with_uptick_after_yellow():
    # prev_price was 100.0, price moved during outage, first recovered poll is 100.5
    assert stock_lamp.decide_command(100.0, 100.5, SOLID_Y) == ANIMATE_G

def test_decide_command_recovery_flat_after_yellow_stays_silent():
    # prev was 100.0, came back at exactly 100.0 → no real tick, lamp stays yellow
    assert stock_lamp.decide_command(100.0, 100.0, SOLID_Y) is None
```

(16 tests — the baseline + 10 tick cases + 5 yellow/recovery cases.)

**Update the two existing `run` tests** so the `_FakeClient` also records
`set_effect` calls and the assertions match the new behavior:

```python
class _FakeClient:
    def __init__(self):
        self.calls: list[tuple] = []

    async def set_color(self, r, g, b, pct=None, did=None):
        self.calls.append(("solid", (r, g, b)))

    async def set_effect(self, name, speed=50, color=(255, 255, 255),
                         pct=None, did=None):
        self.calls.append(("animate", color))
```

The two existing run tests' assertions become:
- `test_run_publishes_green_on_uptick_red_on_downtick`:
  ```
  assert client.calls == [("animate", G), ("solid", G), ("animate", R), ("solid", R)]
  ```
  (baseline 100 → up to 100.5 = animate G → flat = solid G → down to 99 =
  animate R → flat = solid R.)
- `test_run_tolerates_fetch_failure_and_continues`:
  ```
  assert client.calls == [("animate", G)]
  ```
  (None first → 100 baseline → up to 100.5 = animate G → flat repeats =
  solid G after the first flat, but the test only sleeps long enough for the
  uptick; bump if needed.)

**Note on the `tolerates_fetch_failure` test:** depending on cycle timing it
may capture the calming `("solid", G)` too. If so, the assertion becomes:
`assert client.calls[:2] == [("animate", G), ("solid", G)]` to remain stable.

Add `_status_line` tests (6 cases — one per distinct branch including fetch
failure) so the status text is locked in:
```python
def test_status_line_baseline():
    assert stock_lamp._status_line(None, 100.0, None, None) == "$100.00  (first sample, baseline set)"

def test_status_line_uptick_publishing():
    assert stock_lamp._status_line(100.0, 100.5, ANIMATE_G, None) == "$100.50  ↑ pulsing green"

def test_status_line_uptick_deduped():
    assert stock_lamp._status_line(100.0, 100.5, None, ANIMATE_G) == "$100.50  ↑ (already pulsing green)"

def test_status_line_flat_no_change():
    assert stock_lamp._status_line(100.0, 100.0, None, SOLID_G) == "$100.00  · (no change)"

def test_status_line_fetch_failure_publishing_yellow():
    assert stock_lamp._status_line(100.0, None, SOLID_Y, ANIMATE_G) == "warn: fetch failed (lamp → yellow)"

def test_status_line_fetch_failure_already_yellow():
    assert stock_lamp._status_line(100.0, None, None, SOLID_Y) == "warn: fetch failed (already yellow)"
```

(6 status-line tests + 16 decide_command tests + 4 existing `_interval` tests +
2 updated run tests = **28 unit tests** total in `test_stock_lamp.py`.)

## README update

Modify the existing `## Stock tracker` section in `README.md` to describe the
new behavior. Replace this block:

```markdown
First sample establishes the baseline (no color change). After that, each
poll is compared to the previous poll:

- price went up → lamp turns **green**
- price went down → lamp turns **red**
- price unchanged → no command sent
```

With:

```markdown
First sample establishes the baseline (no color change). After that, each
poll is compared to the previous poll:

- price went up → lamp **pulses green** (breath animation)
- price went down → lamp **pulses red** (breath animation)
- price unchanged → on the *first* flat poll after a tick, the lamp calms
  down to a **solid color** (whichever direction it was last pulsing);
  subsequent flat polls publish nothing
- repeated same-direction ticks are deduplicated, so the pulse doesn't
  visibly restart on each one
- fetch failed → lamp goes **solid yellow** ("I don't know"); when the next
  poll succeeds, the lamp recovers automatically based on the price change
  since the last successful poll

In short: pulse means "something is moving"; solid green/red means "calm";
yellow means "I can't see the price right now"; the color tells you the
most recent direction (or the failure state).
```

## File plan

- `stock_lamp.py` (modify) — remove `decide_color`, add `Command`,
  `decide_command`, `apply_command`, `_status_line`; modify `run` to track
  `last_command` and print via `_status_line`.
- `tests/test_stock_lamp.py` (modify) — replace `decide_color` tests with
  `decide_command` tests; update `_FakeClient` + the two `run` tests; add
  `_status_line` tests.
- `README.md` (modify) — replace the 3-bullet behavior block in the existing
  `## Stock tracker` section.

## Open questions

None.
