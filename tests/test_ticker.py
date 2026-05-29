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
