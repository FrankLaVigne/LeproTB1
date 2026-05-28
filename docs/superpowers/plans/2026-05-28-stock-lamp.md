# Stock-Lamp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone Python script that polls a single stock ticker via yfinance and turns the TB1 green on uptick / red on downtick (live-tick semantics, compared to previous poll).

**Architecture:** One process, one ticker, one persistent `LeproClient`, one polling loop. Pure `decide_color` is the future hook for animations. Sync `fetch_price` is wrapped with `asyncio.to_thread` inside the async loop.

**Tech Stack:** Python 3.12, `yfinance` 1.4.0, existing `lepro.LeproClient`, `pytest` + `pytest-asyncio` (already in repo).

---

## File Structure

- `stock_lamp.py` (create) — script: `fetch_price`, `decide_color`, `run`, `_interval`, `main`.
- `tests/test_stock_lamp.py` (create) — unit tests for `decide_color`, `_interval`, and `run` with fakes.
- `requirements.txt` (modify) — add `yfinance>=0.2,<1.5`.
- `README.md` (modify) — add `## Stock tracker` section.

All logic lives in `stock_lamp.py`; no shared modules introduced. The script is a peer to `play_preset.py` in the repo root.

---

### Task 1: Add yfinance dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Append the new dependency**

The current `requirements.txt` is:
```
aiohttp>=3.9
aiomqtt>=2.0
mcp>=1.27,<2
uvicorn>=0.30
```

Add one line so it reads exactly:
```
aiohttp>=3.9
aiomqtt>=2.0
mcp>=1.27,<2
uvicorn>=0.30
yfinance>=0.2,<1.5
```

- [ ] **Step 2: Install and verify**

Run: `.venv/bin/pip install -r requirements.txt && .venv/bin/python -c "import yfinance; print('yfinance', yfinance.__version__)"`
Expected: prints `yfinance 1.4.0` (or similar 1.4.x). No errors.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "deps: add yfinance for the stock-lamp tracker"
```

---

### Task 2: `decide_color` pure function (TDD)

**Files:**
- Create: `stock_lamp.py`
- Create: `tests/test_stock_lamp.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stock_lamp.py` with EXACTLY:
```python
"""Tests for stock_lamp."""

import pytest

import stock_lamp


def test_decide_color_first_sample_returns_none():
    assert stock_lamp.decide_color(None, 100.0) is None


def test_decide_color_uptick_returns_green():
    assert stock_lamp.decide_color(100.0, 100.5) == (0, 255, 0)


def test_decide_color_downtick_returns_red():
    assert stock_lamp.decide_color(100.0, 99.9) == (255, 0, 0)


def test_decide_color_flat_returns_none():
    assert stock_lamp.decide_color(100.0, 100.0) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stock_lamp.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stock_lamp'`

- [ ] **Step 3: Create `stock_lamp.py` with the function**

```python
#!/usr/bin/env python3
"""Track a stock ticker and color the lamp green on uptick / red on downtick."""

from __future__ import annotations


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

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_stock_lamp.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add stock_lamp.py tests/test_stock_lamp.py
git commit -m "feat: add decide_color (live-tick semantics)"
```

---

### Task 3: `fetch_price` wrapper

**Files:**
- Modify: `stock_lamp.py` (append below `decide_color`)

`fetch_price` is the only network-touching code; per the spec, no unit test (would require mocking yfinance — overkill for this iteration). Just add the function and verify it imports.

- [ ] **Step 1: Add the function to `stock_lamp.py`**

Add `import yfinance as yf` to the imports at the top (alongside the `from __future__` line, in a new import block), and append this function below `decide_color`:

```python
import yfinance as yf


def fetch_price(symbol: str) -> float | None:
    """Return the latest known price for `symbol`, or None on any error.

    Synchronous; the async loop wraps this in `asyncio.to_thread`.
    """
    try:
        price = yf.Ticker(symbol).fast_info["last_price"]
        return float(price) if price is not None else None
    except Exception:  # noqa: BLE001  — any yfinance / network error -> None
        return None
```

- [ ] **Step 2: Verify import + the existing tests still pass**

Run: `.venv/bin/python -c "import stock_lamp; print('ok'); print(stock_lamp.fetch_price.__doc__.splitlines()[0])"`
Expected: prints `ok` and the one-line docstring `Return the latest known price for \`symbol\`, or None on any error.`

Run: `.venv/bin/python -m pytest tests/test_stock_lamp.py -v`
Expected: PASS (still 4)

- [ ] **Step 3: Commit**

```bash
git add stock_lamp.py
git commit -m "feat: add fetch_price wrapper around yfinance"
```

---

### Task 4: `run` async loop (TDD with fakes)

**Files:**
- Modify: `stock_lamp.py` (append below `fetch_price`)
- Modify: `tests/test_stock_lamp.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_stock_lamp.py`:
```python
import asyncio
from itertools import chain, repeat


class _FakeClient:
    """LeproClient stand-in that records set_color calls."""

    def __init__(self):
        self.calls: list[tuple[int, int, int]] = []

    async def set_color(self, r: int, g: int, b: int, pct=None, did=None):
        self.calls.append((r, g, b))


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

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stock_lamp.py -k run -v`
Expected: FAIL with `AttributeError: module 'stock_lamp' has no attribute 'run'`

- [ ] **Step 3: Implement `run` in `stock_lamp.py`**

Add `import asyncio` and `from datetime import datetime` to the top of `stock_lamp.py` (in the existing import block alongside `import yfinance as yf`), then append:

```python
def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_stock_lamp.py -v`
Expected: PASS (6 tests total)

- [ ] **Step 5: Commit**

```bash
git add stock_lamp.py tests/test_stock_lamp.py
git commit -m "feat: add async run loop with fake-client tests"
```

---

### Task 5: CLI (`_interval` validator + `main`)

**Files:**
- Modify: `stock_lamp.py` (append below `run`)
- Modify: `tests/test_stock_lamp.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stock_lamp.py`:
```python
import argparse


def test_interval_accepts_floor():
    assert stock_lamp._interval("5") == 5


def test_interval_accepts_larger():
    assert stock_lamp._interval("30") == 30


def test_interval_rejects_below_floor():
    with pytest.raises(argparse.ArgumentTypeError):
        stock_lamp._interval("4")


def test_interval_rejects_non_integer():
    with pytest.raises(argparse.ArgumentTypeError):
        stock_lamp._interval("nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stock_lamp.py -k interval -v`
Expected: FAIL with `AttributeError: module 'stock_lamp' has no attribute '_interval'`

- [ ] **Step 3: Implement `_interval` and `main`**

Add `import argparse` and `import sys` to the top of `stock_lamp.py` (in the same import block as `asyncio`/`datetime`/`yfinance`), then append:

```python
from lepro import LeproClient, load_config


def _interval(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"interval must be an integer, got {value!r}")
    if n < 5:
        raise argparse.ArgumentTypeError(f"interval must be >= 5 (got {n})")
    return n


async def _run_main(symbol: str, interval: int) -> int:
    cfg = load_config()
    if not cfg["account"] or not cfg["password"]:
        print("Missing credentials. Create config.json or set LEPRO_ACCOUNT / LEPRO_PASSWORD.",
              file=sys.stderr)
        return 2

    # First sample up front so a bad symbol exits cleanly before we open MQTT.
    first = await asyncio.to_thread(fetch_price, symbol)
    if first is None:
        print(f"error: could not fetch price for {symbol!r} on first try", file=sys.stderr)
        return 1

    client = LeproClient(cfg["account"], cfg["password"], cfg["region"])
    await client.login()
    await client.connect_mqtt()
    try:
        await run(symbol, interval, client)
    finally:
        await client.close()
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Color the lamp green on uptick / red on downtick.")
    p.add_argument("symbol", help="Yahoo ticker, e.g. IBM, 7203.T, BBVA.MC")
    p.add_argument("--interval", type=_interval, default=30,
                   help="seconds between polls (minimum 5; default 30)")
    args = p.parse_args()
    try:
        sys.exit(asyncio.run(_run_main(args.symbol, args.interval)))
    except KeyboardInterrupt:
        print()  # newline after the ^C
        sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the full file's tests**

Run: `.venv/bin/python -m pytest tests/test_stock_lamp.py -v`
Expected: PASS (10 tests total)

- [ ] **Step 5: Smoke-check the CLI**

Run: `.venv/bin/python stock_lamp.py --help`
Expected: prints argparse help with `symbol` positional and `--interval` option.

Run: `.venv/bin/python stock_lamp.py X --interval 3 2>&1 | tail -3`
Expected: argparse error mentioning `interval must be >= 5`, exit code 2.

- [ ] **Step 6: Confirm the full repo test suite still passes**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (the 35 prior project tests + 10 new = 45 total).

- [ ] **Step 7: Commit**

```bash
git add stock_lamp.py tests/test_stock_lamp.py
git commit -m "feat: add stock_lamp CLI (interval validator, main)"
```

---

### Task 6: README documentation

**Files:**
- Modify: `README.md` (add a `## Stock tracker` section)

- [ ] **Step 1: Add the section to `README.md`**

Insert this new section between the existing `## MCP server` section and the `## Protocol notes` section:

```markdown
## Stock tracker

Color the lamp green on every uptick and red on every downtick of a single
stock, polled live:

```bash
.venv/bin/python stock_lamp.py IBM
.venv/bin/python stock_lamp.py 7203.T --interval 10   # Toyota on Tokyo
.venv/bin/python stock_lamp.py BBVA.MC --interval 60  # BBVA on Madrid
```

The ticker uses Yahoo Finance's suffix convention (no suffix = US listings;
`.T` = Tokyo; `.MC` = Madrid; etc.). `--interval` is in seconds, minimum 5,
default 30. Ctrl-C to stop.

First sample establishes the baseline (no color change). After that, each
poll is compared to the previous poll:

- price went up → lamp turns **green**
- price went down → lamp turns **red**
- price unchanged → no command sent
```

- [ ] **Step 2: Verify it lands cleanly between the sections**

Run: `grep -n "^## " README.md`
Expected: shows `## Stock tracker` between `## MCP server` and `## Protocol notes`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document the stock_lamp tracker"
```

---

## Self-Review

**Spec coverage:**
- yfinance dep → Task 1 ✓
- `decide_color` pure function + tests → Task 2 ✓
- `fetch_price` sync wrapper → Task 3 ✓
- `run` async loop with `asyncio.to_thread`, prev tracking, status line per poll, lamp-publish error tolerance → Task 4 ✓
- `_interval` floor of 5 → Task 5 ✓
- `main` with argparse, first-sample exit-1 on failure, KeyboardInterrupt → Task 5 ✓
- README docs → Task 6 ✓
- 4 `decide_color` tests + 2 `run` tests + 4 `_interval` tests = 10 tests, matching spec's "Tests" section (which has 4 explicit; we add the 2 `run` tests for the loop integration + 4 `_interval` tests for the CLI floor — these are cheap and guard real behavior) ✓
- All public surface (`fetch_price`, `decide_color`, `run`, `main`) and signatures match the spec ✓

**Placeholder scan:** none — every step has full code or full command + expected output.

**Type consistency:** `decide_color(prev, now) -> tuple[int,int,int] | None`, `fetch_price(symbol) -> float | None`, `run(symbol, interval, client, fetch_fn=fetch_price) -> None`, `_interval(value: str) -> int`. All used consistently across the plan; the test's `_FakeClient.set_color` signature matches `LeproClient.set_color`'s real signature (`r, g, b, pct=None, did=None`).

**Note for implementer:** The async `run` test uses real timing (`asyncio.sleep(0.4)` with `interval=0.02`) and `asyncio.to_thread`. If the test is flaky on a very slow machine, bump the outer sleep to `0.6`; do not change the assertion logic.
