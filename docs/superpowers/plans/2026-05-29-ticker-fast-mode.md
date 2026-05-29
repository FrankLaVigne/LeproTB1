# Ticker "Fast Mode" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect "fast moving" rings from recent_ticks velocity (last 3 ticks, same direction, ≥ 0.5%) and switch the whole lamp from Steady to Breathe whenever any ring is fast. Per-ring colors are preserved in the multi-color palette during sustained breathe.

**Architecture:** Pure `is_ring_fast(recent_ticks)` lives in `ticker.py`; `TickerSession._tick_once` sets `r["is_fast"]` after computing the new color; `build_ticker_d50` gains an `effect` parameter so the caller picks Steady / multi-color Breathe / single-color Breathe; the page UI shows a "fast" badge per ring.

**Tech Stack:** No new deps.

---

## File Structure

- `ticker.py` — add `FAST_THRESHOLD` constant + `is_ring_fast` pure function; expand `build_ticker_d50` signature with an `effect` parameter; `_tick_once` updates per-ring `is_fast` and picks the global effect; `_run` keeps the Steady-revert behavior but reverts to the right base effect (Breathe if anyone fast, Steady otherwise).
- `tests/test_ticker.py` — ~5 new tests.
- `workshop.py` — `_PAGE_TICKER` JS renders the fast badge.

Five tasks below.

---

### Task 1: `is_ring_fast` pure function

**Files:**
- Modify: `ticker.py` (append below `decide_ring_color`)
- Modify: `tests/test_ticker.py` (append)

- [ ] **Step 1: Append the failing tests**

```python


# --- is_ring_fast tests -------------------------------------------------------


def test_is_ring_fast_needs_at_least_3_ticks():
    # Fewer than 3 ticks -> never fast (insufficient signal).
    assert ticker.is_ring_fast([]) is False
    assert ticker.is_ring_fast([{"price": 100.0, "direction": "up"}]) is False
    assert ticker.is_ring_fast([
        {"price": 110.0, "direction": "up"},
        {"price": 100.0, "direction": "up"},
    ]) is False


def test_is_ring_fast_all_up_with_big_enough_jump():
    # 3 ticks, all up, 1% jump newest vs oldest -> fast.
    ticks = [
        {"price": 101.0, "direction": "up"},
        {"price": 100.5, "direction": "up"},
        {"price": 100.0, "direction": "up"},
    ]
    assert ticker.is_ring_fast(ticks) is True


def test_is_ring_fast_all_down_with_big_enough_jump():
    ticks = [
        {"price": 99.0, "direction": "down"},
        {"price": 99.5, "direction": "down"},
        {"price": 100.0, "direction": "down"},
    ]
    assert ticker.is_ring_fast(ticks) is True


def test_is_ring_fast_mixed_directions_never_fast():
    # Even with large absolute move, mixed direction = not "fast".
    ticks = [
        {"price": 110.0, "direction": "up"},
        {"price": 95.0,  "direction": "down"},
        {"price": 100.0, "direction": "up"},
    ]
    assert ticker.is_ring_fast(ticks) is False


def test_is_ring_fast_below_threshold_not_fast():
    # All up, but only 0.1% over 3 ticks -> not fast.
    ticks = [
        {"price": 100.1, "direction": "up"},
        {"price": 100.05, "direction": "up"},
        {"price": 100.0, "direction": "up"},
    ]
    assert ticker.is_ring_fast(ticks) is False


def test_is_ring_fast_ignores_baseline_and_error_ticks():
    # Direction "baseline" or "error" doesn't count toward "all same direction".
    ticks = [
        {"price": 110.0, "direction": "up"},
        {"price": 105.0, "direction": "up"},
        {"price": 100.0, "direction": "baseline"},
    ]
    assert ticker.is_ring_fast(ticks) is False
```

- [ ] **Step 2: Run tests to verify they fail**

`.venv/bin/python -m pytest tests/test_ticker.py -k is_ring_fast -v` → FAIL (`AttributeError: module 'ticker' has no attribute 'is_ring_fast'`).

- [ ] **Step 3: Implement in `ticker.py`** — append BELOW `decide_ring_color`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

`.venv/bin/python -m pytest tests/test_ticker.py -k is_ring_fast -v` → PASS (6 tests).

- [ ] **Step 5: Full suite**

`.venv/bin/python -m pytest -q` — 0 failures.

- [ ] **Step 6: Commit**

```bash
git add ticker.py tests/test_ticker.py
git commit -m "feat: ticker.is_ring_fast (velocity detector, 3-tick window, 0.5% threshold)"
```

---

### Task 2: `build_ticker_d50` `effect` parameter — Steady / Breathe-multicolor

**Files:**
- Modify: `ticker.py` (extend `build_ticker_d50`)
- Modify: `tests/test_ticker.py` (append)

- [ ] **Step 1: Append the failing tests**

```python


# --- build_ticker_d50 effect=Breathe (multi-color) tests ----------------------


def test_build_ticker_d50_breathe_multicolor_keeps_per_ring_palette():
    # New: effect="Breathe" with flash_color=None keeps the per-ring palette
    # but uses the Breathe tail (so the lamp pulses while showing per-ring
    # directional colors).
    import workshop
    rings = _rings(outer="00FF00", middle="FF0000", inner="FFFF00")
    d50 = ticker.build_ticker_d50(rings, flash_color=None, effect="Breathe")
    expected = ("N01:P1000300FF00FF0000FFFF00"
                "F2100030058003E002EU3V3"
                + workshop.effect_tail("Breathe", 50)
                + ";")
    assert d50 == expected


def test_build_ticker_d50_steady_is_default_when_effect_omitted():
    # Calling without the effect kwarg must still produce Steady (backwards
    # compatible with the existing call sites).
    rings = _rings(outer="00FF00", middle="FF0000", inner="FFFF00")
    d50_default = ticker.build_ticker_d50(rings, flash_color=None)
    d50_explicit = ticker.build_ticker_d50(rings, flash_color=None, effect="Steady")
    assert d50_default == d50_explicit


def test_build_ticker_d50_flash_color_takes_precedence_over_effect():
    # When flash_color is set, the function ALWAYS does single-color Breathe
    # regardless of the effect parameter.
    rings = _rings(outer="00FF00", middle="FF0000", inner="FFFF00")
    a = ticker.build_ticker_d50(rings, flash_color="00FF00", effect="Steady")
    b = ticker.build_ticker_d50(rings, flash_color="00FF00", effect="Breathe")
    assert a == b
```

- [ ] **Step 2: Run tests to verify they fail**

`.venv/bin/python -m pytest tests/test_ticker.py -k breathe_multicolor -v` → FAIL.

- [ ] **Step 3: Extend `build_ticker_d50`** in `ticker.py`. Replace the existing function body with:

```python
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
    from workshop import build_d50_from_leds  # noqa: PLC0415

    if flash_color is not None:
        leds = [flash_color] * 196
        return build_d50_from_leds(leds, "Breathe", 50)

    outer = rings["outer"]["color"]
    middle = rings["middle"]["color"]
    inner = rings["inner"]["color"]
    leds = [outer] * 88 + [middle] * 62 + [inner] * 46
    return build_d50_from_leds(leds, effect, 50)
```

- [ ] **Step 4: Run tests to verify they pass**

`.venv/bin/python -m pytest tests/test_ticker.py -k build_ticker_d50 -v` → PASS (8 tests including the 5 existing + 3 new).

- [ ] **Step 5: Full suite**

`.venv/bin/python -m pytest -q` — 0 failures.

- [ ] **Step 6: Commit**

```bash
git add ticker.py tests/test_ticker.py
git commit -m "feat: build_ticker_d50 effect parameter (Steady default + multi-color Breathe)"
```

---

### Task 3: Wire `is_fast` into `_tick_once` + `_run`; expose in snapshot

**Files:**
- Modify: `ticker.py` (extend `_tick_once`, `_run`, ring init)
- Modify: `tests/test_ticker.py` (append)

- [ ] **Step 1: Append the failing tests**

```python


# --- TickerSession fast-mode tests --------------------------------------------


@pytest.mark.asyncio
async def test_tick_once_marks_ring_as_fast_after_sustained_uptrend():
    client = _FakeClient()
    sess = ticker.TickerSession(client=client,
                                 symbols={"outer": "AAPL"},
                                 interval=10)
    sess.set_baseline("outer", 100.0)
    # Three successive upticks of ~0.6% each.
    prices = iter([100.6, 101.2, 101.8])

    async def _one():
        return {"outer": next(prices)}
    sess._fetch_all = _one

    await sess._tick_once()
    await sess._tick_once()
    await sess._tick_once()

    snap = sess.snapshot()
    assert snap["rings"]["outer"]["is_fast"] is True


@pytest.mark.asyncio
async def test_tick_once_clears_is_fast_after_direction_change():
    client = _FakeClient()
    sess = ticker.TickerSession(client=client,
                                 symbols={"outer": "AAPL"},
                                 interval=10)
    sess.set_baseline("outer", 100.0)
    # Three upticks, then a downtick -> mixed window -> not fast.
    prices = iter([100.6, 101.2, 101.8, 101.0])

    async def _one():
        return {"outer": next(prices)}
    sess._fetch_all = _one

    for _ in range(4):
        await sess._tick_once()

    snap = sess.snapshot()
    assert snap["rings"]["outer"]["is_fast"] is False


@pytest.mark.asyncio
async def test_tick_once_sends_breathe_multicolor_d50_when_fast_and_no_flash():
    client = _FakeClient()
    sess = ticker.TickerSession(client=client,
                                 symbols={"outer": "AAPL"},
                                 interval=10)
    sess.set_baseline("outer", 100.0)
    prices = iter([100.6, 101.2, 101.8, 101.9])

    async def _one():
        return {"outer": next(prices)}
    sess._fetch_all = _one

    # Run 3 ticks to establish "fast"; force the flash window to expire each time.
    for _ in range(3):
        await sess._tick_once()
        sess._flash_until = None

    # Fourth tick: no NEW direction change, no flash, but ring is fast.
    # Decide_ring_color will see prev_color=green and new=green (still up),
    # so ticked=False; flash_color stays None. With fast=True the d50
    # should use Breathe (E4 in the tail) over the multi-color palette.
    await sess._tick_once()
    payload = client.sent[-1]
    # Multi-color palette = 3 distinct color slots before the lengths block.
    # The tail must be Breathe.
    assert "E4" in payload["d50"]
    # And the single-color flash form would be P10001<sixhex> — confirm we did
    # NOT emit that (palette N must be >1 because outer is green vs middle/inner off).
    assert payload["d50"].startswith("N01:P10003") or payload["d50"].startswith("N01:P10002")
```

- [ ] **Step 2: Run tests to verify they fail**

`.venv/bin/python -m pytest tests/test_ticker.py -k "fast or marks_ring" -v` → FAIL.

- [ ] **Step 3: Update `__init__`'s ring init in `ticker.py`**

In `TickerSession.__init__`, find the dict literal that initialises each ring:

```python
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
```

Add a trailing `"is_fast": False,` entry:

```python
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
```

- [ ] **Step 4: Update `_tick_once` in `ticker.py`**

After the per-ring loop (which sets color/current_price/ticks etc.) but BEFORE the `if flash_color is not None:` line that updates `_flash_until`, add a second pass that computes `is_fast` per ring:

```python
        # After all rings have been updated this poll, recompute is_fast.
        for ring in self._VALID_RINGS:
            r = self._rings[ring]
            if r is None:
                continue
            r["is_fast"] = is_ring_fast(r["recent_ticks"])
```

Then, where `_tick_once` calls `build_ticker_d50`, change the call to select the effect:

```python
        # Pick effect: if anyone is fast and we're not in a flash, sustained Breathe.
        any_fast = any(
            r is not None and r["is_fast"]
            for r in self._rings.values()
        )
        flashing = self._is_flashing()
        send_flash = flash_color if flashing else None
        base_effect = "Breathe" if any_fast else "Steady"
        d50 = build_ticker_d50(self._snapshot_rings_for_d50(), send_flash, effect=base_effect)
```

- [ ] **Step 5: Update `_run`'s post-flash revert in `ticker.py`**

In `_run`, the existing post-flash revert sends a Steady payload. Change it to send the correct base effect (Breathe if any ring is fast, Steady otherwise):

```python
                if self._is_flashing():
                    # Hold the Breathe flash for 5s, then revert to the
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
                    except Exception:
                        pass
                    self._flash_until = None
                    remaining = max(0, self._interval - 5)
                    await asyncio.sleep(remaining)
                else:
                    await asyncio.sleep(self._interval)
```

- [ ] **Step 6: Run tests to verify they pass**

`.venv/bin/python -m pytest tests/test_ticker.py -v` — all pass.

- [ ] **Step 7: Full suite**

`.venv/bin/python -m pytest -q` — 0 failures.

- [ ] **Step 8: Commit**

```bash
git add ticker.py tests/test_ticker.py
git commit -m "feat: ticker fast-mode (recompute is_fast each poll; Breathe when any ring fast)"
```

---

### Task 4: Page UI — fast badge per ring

**Files:**
- Modify: `workshop.py` — update `_PAGE_TICKER` JS to render `is_fast`.

- [ ] **Step 1: Locate `renderRing` in the `_PAGE_TICKER` constant**

The existing meta line in the page's `renderRing` JS reads:

```javascript
  meta.textContent = `${arrow(dir)} ${colorName(data.color)} · updated ${timeAgo(data.last_fetch_at)}`;
```

- [ ] **Step 2: Append a "fast" marker when the ring is fast**

Replace that line with:

```javascript
  const fast = data.is_fast ? ' • ⚡ FAST' : '';
  meta.textContent = `${arrow(dir)} ${colorName(data.color)} · updated ${timeAgo(data.last_fetch_at)}${fast}`;
```

(`⚡` = lightning bolt ⚡, `•` = bullet •. Keeping the existing JS-escape convention from Task 8 of the previous plan.)

- [ ] **Step 3: Smoke-test the page**

```bash
.venv/bin/python -c "
import workshop
p = workshop._PAGE_TICKER
assert 'is_fast' in p, 'page JS does not reference is_fast'
assert 'FAST' in p, 'page JS does not render the FAST badge'
print('fast badge wired into page')
"
```
Expected: prints `fast badge wired into page`.

- [ ] **Step 4: Full suite**

`.venv/bin/python -m pytest -q` — 0 failures.

- [ ] **Step 5: Commit**

```bash
git add workshop.py
git commit -m "feat(ticker-ui): show ⚡ FAST badge per ring when is_fast"
```

---

### Task 5: README + final verify

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Locate the existing Stock Ticker paragraph in README.md** (added by the previous plan, under `## Preset workshop`).

- [ ] **Step 2: Append a second sentence at the end of that paragraph**:

Find the last sentence of the Stock Ticker paragraph:

> Stop powers the lamp off. While the ticker is running, the DIY paint endpoint and the workshop preview endpoint return HTTP 409 — power, brightness, and saves stay available.

Append AFTER it (still inside the same paragraph or as a follow-on sentence):

```markdown

When any ring is in a sustained directional move (3 consecutive same-direction
ticks totalling ≥ 0.5%), it earns a **⚡ FAST** badge in the page and the whole
lamp switches from Steady to Breathe (per-ring colors still visible) until the
streak ends.
```

- [ ] **Step 3: Verify it landed in the right place**

`grep -B1 -A2 "FAST" README.md` → shows the new sentence inside the Stock Ticker context.

- [ ] **Step 4: Final full-suite run**

`.venv/bin/python -m pytest -q` → 0 failures.

- [ ] **Step 5: Final app build smoke**

`.venv/bin/python -c "import workshop; app = workshop.build_app(); print(len(list(app.router.routes())))"` → 21.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: document the ⚡ FAST mode in the ticker section"
```

---

## Self-Review

**Coverage:**
- velocity-aware `is_fast` from `recent_ticks` (Task 1) ✓
- `build_ticker_d50` multi-color Breathe mode (Task 2) ✓
- `_tick_once` recomputes is_fast per ring + picks base effect (Task 3) ✓
- `_run` post-flash revert uses the correct base effect (Task 3) ✓
- snapshot exposes is_fast per ring (Task 3 — added to ring init) ✓
- page UI badge (Task 4) ✓
- README docs (Task 5) ✓

**Placeholder scan:** none.

**Type consistency:**
- `is_ring_fast(recent_ticks)` signature consistent across Tasks 1, 3.
- `build_ticker_d50(rings, flash_color, effect="Steady")` consistent across Tasks 2, 3.
- Ring dict key `is_fast` consistent in init, tick_once, snapshot, page JS.

**Note for the implementer:** Task 3's `_tick_once` change has to happen in the right order: (1) update color/recent_ticks (existing), (2) recompute is_fast (new), (3) decide flash + base effect (modified), (4) compose + send. Don't reorder.
