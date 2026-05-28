# Stock-Lamp Animations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace stock_lamp's solid-color reaction with **pulsing animations on ticks, solid color on the first flat after a tick, and yellow on fetch failure**, while preserving the live-tick semantics.

**Architecture:** One pure decision function (`decide_command`) that takes `(prev_price, now, last_command)` and returns a `Command` tuple `("animate"|"solid", (r,g,b))` or `None`. A tiny async dispatcher (`apply_command`) maps commands to the existing `LeproClient.set_effect("breath", ...)` and `set_color(...)` calls. The `run` loop tracks two state variables (`prev_price`, `last_command`) and prints status via a pure `_status_line`.

**Tech Stack:** Python 3.12, existing `lepro.LeproClient`, `pytest` + `pytest-asyncio` (already in repo).

---

## File Structure

- `stock_lamp.py` (modify) — remove `decide_color`; add `Command` type alias + `GREEN`/`RED`/`YELLOW` constants + `decide_command` + `apply_command` + `_status_line`; modify `run` to track `last_command` and use the new dispatcher.
- `tests/test_stock_lamp.py` (modify) — replace 4 `decide_color` tests with 16 `decide_command` tests; update `_FakeClient` to capture both `set_color` and `set_effect`; update the 2 `run` tests; add 6 `_status_line` tests. Final count: **28 tests** in this file.
- `README.md` (modify) — update the existing `## Stock tracker` section's behavior bullets.

All work is in one Python module plus its tests plus the README. Six tasks below.

---

### Task 1: Constants + `Command` type alias

**Files:**
- Modify: `stock_lamp.py` (add module-level constants near the top, after the imports)

- [ ] **Step 1: Add constants and type alias to `stock_lamp.py`**

After the existing imports at the top of `stock_lamp.py` (which currently end with `from lepro import LeproClient, load_config`), add a blank line, then this block:

```python
# --- types & constants ---------------------------------------------------------

Command = tuple[str, tuple[int, int, int]]  # ("animate" | "solid", (r, g, b))

GREEN = (0, 255, 0)
RED = (255, 0, 0)
YELLOW = (255, 255, 0)  # solid yellow = fetch failure / "I don't know"
```

- [ ] **Step 2: Verify import still works and existing tests pass**

Run: `.venv/bin/python -c "import stock_lamp; print(stock_lamp.GREEN, stock_lamp.RED, stock_lamp.YELLOW); print(stock_lamp.Command)"`
Expected: prints `(0, 255, 0) (255, 0, 0) (255, 255, 0)` and `tuple[str, tuple[int, int, int]]` (the alias)

Run: `.venv/bin/python -m pytest tests/test_stock_lamp.py -q`
Expected: 10 passed (the existing tests untouched).

- [ ] **Step 3: Commit**

```bash
git add stock_lamp.py
git commit -m "feat: add Command type alias and GREEN/RED/YELLOW constants"
```

---

### Task 2: `decide_command` pure function (TDD; replaces `decide_color`)

**Files:**
- Modify: `stock_lamp.py` (replace the `decide_color` function with `decide_command`)
- Modify: `tests/test_stock_lamp.py` (replace the 4 `decide_color` tests with 16 `decide_command` tests)

- [ ] **Step 1: Replace the `decide_color` tests with the `decide_command` tests in `tests/test_stock_lamp.py`**

Find the existing 4 tests in `tests/test_stock_lamp.py`:
```python
def test_decide_color_first_sample_returns_none():
    assert stock_lamp.decide_color(None, 100.0) is None


def test_decide_color_uptick_returns_green():
    assert stock_lamp.decide_color(100.0, 100.5) == (0, 255, 0)


def test_decide_color_downtick_returns_red():
    assert stock_lamp.decide_color(100.0, 99.9) == (255, 0, 0)


def test_decide_color_flat_returns_none():
    assert stock_lamp.decide_color(100.0, 100.0) is None
```

Replace those 4 tests (delete them and insert this block in the same location):
```python
# --- decide_command tests ------------------------------------------------------

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

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stock_lamp.py -k decide_command -v`
Expected: FAIL with `AttributeError: module 'stock_lamp' has no attribute 'decide_command'`

- [ ] **Step 3: Replace `decide_color` with `decide_command` in `stock_lamp.py`**

Find the existing `decide_color` function in `stock_lamp.py`:
```python
def decide_color(prev: float | None, now: float) -> tuple[int, int, int] | None:
    """Return the color the lamp should display, or None if no change should be sent.

    - prev is None  -> None (first sample, just establish baseline)
    - now > prev    -> (0, 255, 0)  green
    - now < prev    -> (255, 0, 0)  red
    - now == prev   -> None (no publish)
    """
    if prev is None or now == prev:
        return None
    return (0, 255, 0) if now > prev else (255, 0, 0)
```

Replace it with EXACTLY this:
```python
def decide_command(prev_price: float | None,
                   now: float | None,
                   last_command: Command | None) -> Command | None:
    """Return the next lamp Command, or None if nothing should be published.

    - `now is None` (fetch failed) -> solid yellow (deduped if already yellow)
    - `prev_price is None`         -> None (baseline, first successful sample)
    - `now > prev_price`           -> animate green (deduped if already)
    - `now < prev_price`           -> animate red   (deduped if already)
    - `now == prev_price`, last was animate -> solid in that color (calm down)
    - `now == prev_price`, last was solid or None -> None
    """
    if now is None:
        desired: Command = ("solid", YELLOW)
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

- [ ] **Step 4: Run tests to verify they pass (and the old run/interval tests still pass)**

Run: `.venv/bin/python -m pytest tests/test_stock_lamp.py -v`

Expected: 16 `decide_command` tests pass. The 2 `run` tests will FAIL now (they assert against the old behavior — we'll update them in Task 4). The 4 `_interval` tests still pass.

Concretely you should see: 16 PASS for `decide_command`, 2 FAIL for `test_run_*`, 4 PASS for `test_interval_*`. That is the expected interim state — proceed to Step 5.

- [ ] **Step 5: Commit**

```bash
git add stock_lamp.py tests/test_stock_lamp.py
git commit -m "feat: replace decide_color with decide_command (animate/solid/yellow)"
```

---

### Task 3: `apply_command` async dispatcher

**Files:**
- Modify: `stock_lamp.py` (add `apply_command` immediately after `decide_command`)

Per spec, `apply_command` is the single place that talks to `LeproClient`. No unit test for it directly — it's exercised by the `run` tests in Task 4 via a `_FakeClient` that records both `set_color` and `set_effect` calls.

- [ ] **Step 1: Add `apply_command` to `stock_lamp.py`**

Find the end of `decide_command` you just added. After it (preserving a blank line gap), add:
```python
async def apply_command(client, cmd: Command) -> None:
    """Dispatch a Command to the lamp: animate -> breath effect, solid -> set_color."""
    kind, color = cmd
    if kind == "animate":
        await client.set_effect("breath", speed=50, color=color)
    else:  # "solid"
        await client.set_color(*color)
```

- [ ] **Step 2: Verify import**

Run: `.venv/bin/python -c "import stock_lamp; import inspect; print('apply_command is coro:', inspect.iscoroutinefunction(stock_lamp.apply_command))"`
Expected: prints `apply_command is coro: True`

- [ ] **Step 3: Run the existing passing tests**

Run: `.venv/bin/python -m pytest tests/test_stock_lamp.py -k "decide_command or interval" -v`
Expected: 20 passed (16 decide_command + 4 interval). The 2 run tests are still failing — that's fine.

- [ ] **Step 4: Commit**

```bash
git add stock_lamp.py
git commit -m "feat: add apply_command dispatcher (breath for animate, set_color for solid)"
```

---

### Task 4: Update `run` loop to track `last_command` (and update `run` tests)

**Files:**
- Modify: `stock_lamp.py` — rewrite the body of `run` to use `decide_command` + `apply_command` + a new `_status_line` (created in Task 5; for now the run body inlines simple status prints, then Task 5 extracts and tests `_status_line`).
- Modify: `tests/test_stock_lamp.py` — update `_FakeClient` and the two existing `run` tests.

NOTE: To keep tasks bite-sized, this task gets `run` working with **inline status prints** (not perfect text, but functionally correct). Task 5 extracts the status text into `_status_line` and locks it in with its own tests.

- [ ] **Step 1: Update `_FakeClient` and the two `run` tests in `tests/test_stock_lamp.py`**

Find the existing `_FakeClient` class:
```python
class _FakeClient:
    """LeproClient stand-in that records set_color calls."""

    def __init__(self):
        self.calls: list[tuple[int, int, int]] = []

    async def set_color(self, r: int, g: int, b: int, pct=None, did=None):
        self.calls.append((r, g, b))
```

Replace it with:
```python
class _FakeClient:
    """LeproClient stand-in that records both set_color and set_effect calls."""

    def __init__(self):
        # Each entry is ("solid", (r, g, b)) or ("animate", (r, g, b)).
        self.calls: list[tuple[str, tuple[int, int, int]]] = []

    async def set_color(self, r: int, g: int, b: int, pct=None, did=None):
        self.calls.append(("solid", (r, g, b)))

    async def set_effect(self, name: str, speed: int = 50,
                         color: tuple[int, int, int] = (255, 255, 255),
                         pct=None, did=None):
        self.calls.append(("animate", color))
```

Find the existing `test_run_publishes_green_on_uptick_red_on_downtick`:
```python
@pytest.mark.asyncio
async def test_run_publishes_green_on_uptick_red_on_downtick():
    # Sequence: baseline 100, up to 100.5, flat, down to 99, flat. After that, repeat 99.
    prices = chain([100.0, 100.5, 100.5, 99.0, 99.0], repeat(99.0))

    def fake_fetch(_symbol):
        return next(prices)

    client = _FakeClient()
    task = asyncio.create_task(stock_lamp.run("X", 0.02, client, fake_fetch))
    await asyncio.sleep(0.4)  # ~20 cycles at 0.02s; plenty for 5 prices.
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # First sample → no call. Up → green. Flat → no call. Down → red. Flat → no call.
    assert client.calls == [(0, 255, 0), (255, 0, 0)]
```

Replace its assertion (only the assertion — keep the setup) so the full test now reads:
```python
@pytest.mark.asyncio
async def test_run_publishes_green_on_uptick_red_on_downtick():
    # Sequence: baseline 100, up to 100.5, flat, down to 99, flat. After that, repeat 99.
    prices = chain([100.0, 100.5, 100.5, 99.0, 99.0], repeat(99.0))

    def fake_fetch(_symbol):
        return next(prices)

    client = _FakeClient()
    task = asyncio.create_task(stock_lamp.run("X", 0.02, client, fake_fetch))
    await asyncio.sleep(0.4)  # ~20 cycles at 0.02s; plenty for 5 prices.
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Baseline 100, up→animate G, flat→solid G, down→animate R, flat→solid R.
    # After that prices stay at 99.0 → no further calls.
    G = (0, 255, 0)
    R = (255, 0, 0)
    assert client.calls == [("animate", G), ("solid", G), ("animate", R), ("solid", R)]
```

Find the existing `test_run_tolerates_fetch_failure_and_continues`:
```python
@pytest.mark.asyncio
async def test_run_tolerates_fetch_failure_and_continues():
    # First call fails (None), second call succeeds and becomes baseline, third is uptick.
    seq = chain([None, 100.0, 100.5], repeat(100.5))

    def fake_fetch(_):
        return next(seq)

    client = _FakeClient()
    task = asyncio.create_task(stock_lamp.run("X", 0.02, client, fake_fetch))
    await asyncio.sleep(0.3)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Failed first call → skipped. Baseline 100. Uptick to 100.5 → green. Flat → no call.
    assert client.calls == [(0, 255, 0)]
```

Replace the entire test with:
```python
@pytest.mark.asyncio
async def test_run_publishes_yellow_then_recovers():
    # First call fails -> yellow. Second call 100 -> baseline (last_command stays yellow).
    # Third call 100.5 -> animate green. Subsequent flat 100.5 -> solid green.
    seq = chain([None, 100.0, 100.5, 100.5, 100.5], repeat(100.5))

    def fake_fetch(_):
        return next(seq)

    client = _FakeClient()
    task = asyncio.create_task(stock_lamp.run("X", 0.02, client, fake_fetch))
    await asyncio.sleep(0.5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    G = (0, 255, 0)
    Y = (255, 255, 0)
    # First call -> yellow. Baseline poll (100.0) does NOT publish (prev was None,
    # we still update prev to 100.0 — see run logic). Uptick to 100.5 -> animate G.
    # Then flat -> solid G. Stop here; further flats publish nothing.
    assert client.calls[:3] == [("solid", Y), ("animate", G), ("solid", G)]
```

- [ ] **Step 2: Run those tests to verify they fail (against the OLD `run`)**

Run: `.venv/bin/python -m pytest tests/test_stock_lamp.py -k "test_run_" -v`
Expected: Both `test_run_*` tests FAIL — the old `run` still uses `decide_color` (which no longer exists). The exact error will be `AttributeError: module 'stock_lamp' has no attribute 'decide_color'` or similar; that confirms the run loop needs the rewrite.

- [ ] **Step 3: Rewrite the body of `run` in `stock_lamp.py`**

Find the existing `run` function:
```python
async def run(symbol: str, interval: float, client, fetch_fn=fetch_price) -> None:
    """Poll `symbol` every `interval` seconds; color the lamp on each tick."""
    prev: float | None = None
    while True:
        now = await asyncio.to_thread(fetch_fn, symbol)
        if now is None:
            print(f"{_ts()}  {symbol}  warn: fetch failed")
        else:
            color = decide_color(prev, now)
            if prev is None:
                print(f"{_ts()}  {symbol}  ${now:.2f}  (first sample, baseline set)")
            elif color is None:
                print(f"{_ts()}  {symbol}  ${now:.2f}  · (no change)")
            elif color == (0, 255, 0):
                print(f"{_ts()}  {symbol}  ${now:.2f}  ↑ GREEN")
            else:
                print(f"{_ts()}  {symbol}  ${now:.2f}  ↓ RED")

            if color is not None:
                try:
                    await client.set_color(*color)
                except Exception as e:  # noqa: BLE001  — log and retry next tick
                    print(f"warn: lamp publish failed: {e}")
            prev = now
        await asyncio.sleep(interval)
```

Replace it with EXACTLY this:
```python
async def run(symbol: str, interval: float, client, fetch_fn=fetch_price) -> None:
    """Poll `symbol` every `interval` seconds; drive the lamp via decide_command."""
    prev: float | None = None
    last_command: Command | None = None
    while True:
        now = await asyncio.to_thread(fetch_fn, symbol)
        cmd = decide_command(prev, now, last_command)

        # Status line: keep it simple here; Task 5 extracts/refines this.
        if now is None:
            suffix = "warn: fetch failed" + (
                " (lamp → yellow)" if cmd is not None else " (already yellow)"
            )
        elif prev is None:
            suffix = f"${now:.2f}  (first sample, baseline set)"
        elif cmd is not None:
            kind, color = cmd
            verb = "pulsing" if kind == "animate" else "holding"
            name = "green" if color == GREEN else ("red" if color == RED else "yellow")
            arrow = "↑" if color == GREEN else ("↓" if color == RED else "·")
            suffix = f"${now:.2f}  {arrow} {verb} {name}"
        elif now == prev:
            suffix = f"${now:.2f}  · (no change)"
        else:
            # Same-direction tick deduped.
            arrow = "↑" if now > prev else "↓"
            assert last_command is not None
            name = "green" if last_command[1] == GREEN else "red"
            suffix = f"${now:.2f}  {arrow} (already pulsing {name})"
        print(f"{_ts()}  {symbol}  {suffix}")

        if cmd is not None:
            try:
                await apply_command(client, cmd)
                last_command = cmd
            except Exception as e:  # noqa: BLE001  — log and retry next tick
                print(f"warn: lamp publish failed: {e}")

        if now is not None:
            prev = now
        await asyncio.sleep(interval)
```

- [ ] **Step 4: Run all tests to verify**

Run: `.venv/bin/python -m pytest tests/test_stock_lamp.py -v`
Expected: 22 passed (16 decide_command + 4 interval + 2 run).

If the run tests are flaky on slower hardware, bump the outer `asyncio.sleep` in the affected test from `0.4` / `0.5` to `0.6` / `0.7`. Do NOT change the assertions.

- [ ] **Step 5: Commit**

```bash
git add stock_lamp.py tests/test_stock_lamp.py
git commit -m "feat: run loop tracks last_command and dispatches via apply_command"
```

---

### Task 5: Extract `_status_line` and lock it in with tests

**Files:**
- Modify: `stock_lamp.py` — add `_status_line` (pure), call it from `run`.
- Modify: `tests/test_stock_lamp.py` — add 6 tests for `_status_line`.

- [ ] **Step 1: Append the `_status_line` tests to `tests/test_stock_lamp.py`**

After the existing tests (anywhere; the end of the file is fine), add:
```python
# --- _status_line tests --------------------------------------------------------


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

- [ ] **Step 2: Run to verify FAIL**

Run: `.venv/bin/python -m pytest tests/test_stock_lamp.py -k status_line -v`
Expected: FAIL with `AttributeError: module 'stock_lamp' has no attribute '_status_line'`

- [ ] **Step 3: Add `_status_line` and refactor `run` in `stock_lamp.py`**

Add this function in `stock_lamp.py` directly AFTER `apply_command` (so the file order is `decide_command`, `apply_command`, `_status_line`, then `_ts`, then `run`):
```python
def _status_line(prev: float | None,
                 now: float | None,
                 cmd: Command | None,
                 last_command: Command | None) -> str:
    """Render the human-readable suffix shown after the timestamp + symbol."""
    if now is None:
        return "warn: fetch failed" + (
            " (lamp → yellow)" if cmd is not None else " (already yellow)"
        )
    if prev is None:
        return f"${now:.2f}  (first sample, baseline set)"
    if cmd is not None:
        kind, color = cmd
        verb = "pulsing" if kind == "animate" else "holding"
        name = "green" if color == GREEN else ("red" if color == RED else "yellow")
        arrow = "↑" if color == GREEN else ("↓" if color == RED else "·")
        return f"${now:.2f}  {arrow} {verb} {name}"
    if now == prev:
        return f"${now:.2f}  · (no change)"
    # Same-direction tick deduped.
    arrow = "↑" if now > prev else "↓"
    name = "green" if last_command is not None and last_command[1] == GREEN else "red"
    return f"${now:.2f}  {arrow} (already pulsing {name})"
```

Find the inline status-suffix block inside `run` (the if/elif chain that builds `suffix`). Replace the whole chain with:
```python
        suffix = _status_line(prev, now, cmd, last_command)
        print(f"{_ts()}  {symbol}  {suffix}")
```

The final `run` body should now look like:
```python
async def run(symbol: str, interval: float, client, fetch_fn=fetch_price) -> None:
    """Poll `symbol` every `interval` seconds; drive the lamp via decide_command."""
    prev: float | None = None
    last_command: Command | None = None
    while True:
        now = await asyncio.to_thread(fetch_fn, symbol)
        cmd = decide_command(prev, now, last_command)
        suffix = _status_line(prev, now, cmd, last_command)
        print(f"{_ts()}  {symbol}  {suffix}")

        if cmd is not None:
            try:
                await apply_command(client, cmd)
                last_command = cmd
            except Exception as e:  # noqa: BLE001  — log and retry next tick
                print(f"warn: lamp publish failed: {e}")

        if now is not None:
            prev = now
        await asyncio.sleep(interval)
```

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/python -m pytest tests/test_stock_lamp.py -v`
Expected: 28 passed (16 decide_command + 4 interval + 2 run + 6 status_line).

Then run the FULL repo suite:
Run: `.venv/bin/python -m pytest -q`
Expected: all pass (the 35 prior repo tests minus the 4 retired `decide_color` tests, plus the 22 net-new tests added here = **63 total** if my arithmetic holds; if it differs by a few that's fine — what matters is **0 failures**).

- [ ] **Step 5: Commit**

```bash
git add stock_lamp.py tests/test_stock_lamp.py
git commit -m "refactor: extract _status_line as a pure function with its own tests"
```

---

### Task 6: README update

**Files:**
- Modify: `README.md` (update the `## Stock tracker` section's behavior bullets)

- [ ] **Step 1: Replace the behavior block in `README.md`**

Find this block inside the existing `## Stock tracker` section:
```markdown
First sample establishes the baseline (no color change). After that, each
poll is compared to the previous poll:

- price went up → lamp turns **green**
- price went down → lamp turns **red**
- price unchanged → no command sent
```

Replace it with EXACTLY:
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

- [ ] **Step 2: Verify the section is well-formed**

Run: `grep -n "^## \|pulses green\|pulses red\|solid yellow" README.md`
Expected: shows the `## Stock tracker` heading and the three new behavior lines mentioning "pulses green", "pulses red", and "solid yellow".

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: stock_lamp now pulses on tick, solid on calm, yellow on failure"
```

---

## Self-Review

**Spec coverage:**
- `Command` type alias + GREEN/RED/YELLOW constants → Task 1 ✓
- `decide_command` with 16-row state table including fetch-failure-first → Task 2 ✓
- `apply_command` → Task 3 ✓
- `run` tracks `last_command`, does not touch `prev` on `now is None`, only updates `last_command` on publish success → Task 4 ✓
- `_status_line` pure helper with all status branches → Task 5 ✓
- 16 `decide_command` tests, 6 `_status_line` tests, updated 2 `run` tests, kept 4 `_interval` tests = 28 in `tests/test_stock_lamp.py` ✓
- README update with the new behavior block including yellow → Task 6 ✓
- `decide_color` is fully removed (Task 2 replaces it; Task 4 makes `run` stop calling it) ✓

**Placeholder scan:** none — every step has the actual code or the actual command + expected output. The note "Task 5 extracts/refines this" in Task 4 is not a placeholder; it's an intentional staged refactor that's fully spelled out in Task 5.

**Type consistency:**
- `Command = tuple[str, tuple[int, int, int]]` defined Task 1, used in every subsequent task.
- `decide_command(prev_price: float|None, now: float|None, last_command: Command|None)` defined Task 2, called from `run` (Task 4) and `_status_line` (Task 5) with matching arg types.
- `apply_command(client, cmd: Command)` defined Task 3, awaited from `run` (Task 4) with matching signature.
- `_status_line(prev, now, cmd, last_command)` defined Task 5; tests in Step 1 of Task 5 match the signature.
- `_FakeClient` (Task 4 Step 1) records `("animate", color)` and `("solid", color)` — the run-test assertion in Task 4 and the spec's expectations both use the same shape.
- Module-level `GREEN`, `RED`, `YELLOW` constants are imported and used identically in `decide_command`, `_status_line`, and tests (via the local `G`/`R`/`Y` aliases).

**Note for implementer:** if the `test_run_publishes_yellow_then_recovers` test is flaky due to the inner `asyncio.sleep(0.5)` being too tight on a busy CI machine, the assertion uses `client.calls[:3]` so any extra calls beyond the first three (e.g. a subsequent dedup-suppressed flat or a late solid G) don't break the test. If the test STILL flakes, increase the outer sleep from `0.5` to `0.7`; do not change the assertion.
