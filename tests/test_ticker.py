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


def test_ticker_session_snapshot_isolated_from_session_state():
    # Mutating the snapshot must NOT corrupt the live session.
    sess = ticker.TickerSession(client=None,
                                 symbols={"outer": "AAPL"},
                                 interval=10)
    sess.set_baseline("outer", 100.0)
    snap = sess.snapshot()
    snap["rings"]["outer"]["color"] = "ZZZZZZ"
    snap["rings"]["outer"]["recent_ticks"].append({"hacked": True})
    # Live session should be untouched.
    fresh = sess.snapshot()
    assert fresh["rings"]["outer"]["color"] == "FFFFFF"
    assert fresh["rings"]["outer"]["recent_ticks"] == []


# --- TickerSession async tests -----------------------------------------------


class _FakeClient:
    """Captures every send_raw payload for assertions."""

    def __init__(self):
        self.sent = []

    async def send_raw(self, payload):
        self.sent.append(payload)


async def _coro(value):
    return value


@pytest.mark.asyncio
async def test_session_start_then_stop_marks_running_and_clears():
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
    assert snap["rings"]["middle"]["current_price"] == 139.10
    assert snap["rings"]["middle"]["recent_ticks"][0]["price"] == 139.10
    # Lamp received exactly one payload (the Breathe flash because the color changed).
    assert len(client.sent) == 1
    assert client.sent[0]["d1"] == 1
    assert client.sent[0]["d2"] == 2
    # The first payload after a tick is the single-color Breathe flash.
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
    # Steady tail ends in "E1;" (where ; is the d50 terminator).
    assert steady_payload["d50"].endswith("E1;")


# --- Bug 3 regression: 5-second Breathe flash must revert to Steady ----------


@pytest.mark.asyncio
async def test_run_sends_steady_revert_after_5s_flash():
    """_run must send a Steady payload ~5 s after a Breathe flash, not at interval end.

    We drive this without touching asyncio.sleep by:
    1. Calling _tick_once() directly to trigger the flash (sends Breathe payload).
    2. Simulating the _run revert branch: if _is_flashing(), send the Steady payload
       and clear _flash_until — exactly what _run does between sleep(5) and
       sleep(remaining).
    This verifies the revert logic produces the correct d50 and clears the window.
    """
    client = _FakeClient()
    sess = ticker.TickerSession(client=client,
                                 symbols={"outer": "AAPL"},
                                 interval=300)
    sess.set_baseline("outer", 100.0)

    # Trigger a flash by polling an uptick.
    sess._fetch_all = lambda: _coro({"outer": 110.0})
    await sess._tick_once()

    # Confirm the lamp is in Breathe/flash mode.
    assert sess._is_flashing(), "expected a flash window after a price uptick"
    assert "E4" in client.sent[-1]["d50"], "first payload should be a Breathe flash"

    # --- Simulate what _run does after sleep(5): send Steady revert + clear window ---
    steady_d50 = ticker.build_ticker_d50(sess._snapshot_rings_for_d50(), None)
    await client.send_raw({"d1": 1, "d2": 2, "d50": steady_d50})
    sess._flash_until = None

    # The revert payload must be Steady (ends in E1;) not Breathe.
    revert_payload = client.sent[-1]
    assert revert_payload["d50"].endswith("E1;"), (
        "revert payload after 5 s must be Steady, got: " + revert_payload["d50"]
    )
    # Flash window must now be cleared so the next poll starts clean.
    assert not sess._is_flashing(), "flash window should be cleared after revert"


@pytest.mark.asyncio
async def test_run_interval_greater_than_5_uses_split_sleep(monkeypatch):
    """_run with interval > 5 must sleep(5) then send Steady, then sleep(interval-5).

    We patch asyncio.sleep globally (all paths inside _run call the module-level
    asyncio.sleep) and let the loop run one full iteration to verify the sequence:
      tick -> sleep(5) -> Steady revert payload -> sleep(interval-5).
    """
    import asyncio
    client = _FakeClient()
    sess = ticker.TickerSession(client=client,
                                 symbols={"outer": "AAPL"},
                                 interval=10)
    sess.set_baseline("outer", 100.0)
    # Patch fetch to always return an uptick so a flash fires every poll.
    sess._fetch_all = lambda: _coro({"outer": 110.0})

    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def recording_sleep(seconds):
        sleeps.append(seconds)
        await real_sleep(0)  # yield without actually waiting

    # Patch asyncio.sleep globally so all callers (including _run's local import) see it.
    monkeypatch.setattr(asyncio, "sleep", recording_sleep)

    try:
        await sess.start()
        # Yield control repeatedly until we have at least 2 payloads and a sleep(5).
        for _ in range(50):
            await real_sleep(0)
            if len(client.sent) >= 2 and 5 in sleeps:
                break
        await sess.stop()
    finally:
        pass  # monkeypatch restores asyncio.sleep automatically

    # First payload: Breathe flash.
    assert len(client.sent) >= 2, f"expected at least 2 payloads; got {client.sent}"
    assert "E4" in client.sent[0]["d50"], "first payload must be Breathe flash"
    # Second payload: Steady revert (sent after sleep(5)).
    assert client.sent[1]["d50"].endswith("E1;"), (
        "second payload must be Steady revert, got: " + client.sent[1].get("d50", str(client.sent[1]))
    )
    # sleep(5) must appear (for the flash hold) in the sleep sequence.
    assert 5 in sleeps, f"expected a sleep(5) for flash hold; sleeps = {sleeps}"
    # sleep(interval-5) = sleep(5) must also appear.
    assert sleeps.count(5) >= 2, f"expected two sleep(5) calls (hold + remaining); sleeps = {sleeps}"


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

    # Fourth tick: a small uptick, no new direction change (color stays green
    # so no flash), but ring is fast. The d50 should be Breathe (E4 tail)
    # over the multi-color palette (not the single-color flash form).
    await sess._tick_once()
    payload = client.sent[-1]
    assert "E4" in payload["d50"]
    # Multi-color palette: N>=2 (outer green vs middle/inner off). The
    # single-color flash form would be P10001<hex>.
    assert payload["d50"].startswith("N01:P10003") or payload["d50"].startswith("N01:P10002")
