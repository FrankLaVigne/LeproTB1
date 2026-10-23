"""Scheduler runner (web/scheduler.py) against a fake lamp driver and a fake clock."""

import json
import random
from datetime import datetime, timedelta

import pytest

from web import schedule as sch
from web.market_calendar import ET
from web.scheduler import Scheduler


def at(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=ET)


class FakeDriver:
    def __init__(self, ids=("m1", "m2", "m3")):
        self.calls = []
        self.ids = list(ids)
        self.owned = True
        self.busy = False
        self.ticker_failures = 0
        self.change = 1.0
        self.last_color = None

    async def start_ticker(self, symbol, interval):
        self.calls.append(("start_ticker", symbol, interval))
        if self.ticker_failures:
            self.ticker_failures -= 1
            raise RuntimeError("could not fetch first price")
        self.owned = True
        return ("ticker", symbol)

    async def play_motion(self, motion_id):
        self.calls.append(("play_motion", motion_id))
        self.owned = True
        return ("motion", motion_id)

    async def solid(self, color):
        self.calls.append(("solid", color))

    async def power_off(self):
        self.calls.append(("power_off",))

    def owns(self, handle):
        return handle is not None and self.owned

    def capture_busy(self):
        return self.busy

    def motion_ids(self):
        return list(self.ids)

    def motion_name(self, motion_id):
        return f"name-{motion_id}"

    async def day_change(self, symbol):
        return self.change

    def last_ticker_color(self):
        return self.last_color

    def names(self):
        return [c[0] for c in self.calls]


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def make(tmp_path):
    def _make(start, driver=None, **settings):
        clock = Clock(start)
        driver = driver or FakeDriver()
        path = tmp_path / "schedule.json"
        if settings:
            sch.save_settings(path, {**sch.DEFAULTS, **settings})
        s = Scheduler(driver, path, {}, now=clock, rng=random.Random(1))
        return s, driver, clock
    return _make


async def run_for(s, clock, seconds):
    end = clock.t + timedelta(seconds=seconds)
    while clock.t < end:
        delay = await s.step()
        clock.t = min(clock.t + timedelta(seconds=delay), end)
    await s.step()


@pytest.mark.parametrize("start,first", [
    (at(2026, 10, 27, 2, 0), ("power_off",)),
    (at(2026, 10, 24, 12, 0), ("power_off",)),
    (at(2026, 10, 27, 10, 0), ("start_ticker", "IBM", 30)),
])
async def test_boot_applies_current_phase(make, start, first):
    s, d, _ = make(start)
    await s.step()
    assert d.calls == [first]


async def test_boot_in_evening_plays_motion(make):
    s, d, _ = make(at(2026, 10, 27, 18, 0))
    await s.step()
    assert d.names() == ["play_motion"]
    assert s.status()["current_motion"].startswith("name-")


async def test_opening_bell_then_ticker(make):
    s, d, clock = make(at(2026, 10, 27, 9, 29, 50), opening_motion="m2")
    await run_for(s, clock, 40)
    assert d.calls == [("power_off",), ("play_motion", "m2"), ("start_ticker", "IBM", 30)]


async def test_random_bell_uses_catalog(make):
    s, d, clock = make(at(2026, 10, 27, 9, 30, 1))
    await s.step()
    assert d.calls[0][0] == "play_motion" and d.calls[0][1] in d.ids


async def test_missing_bell_motion_falls_back_to_random(make):
    s, d, clock = make(at(2026, 10, 27, 9, 30, 1), opening_motion="gone")
    await s.step()
    assert d.calls[0][1] in d.ids


@pytest.mark.parametrize("change,last,color", [
    (1.5, None, "00FF00"), (-2.0, None, "FF0000"), (0.0, None, "FFFFFF"),
    (None, "FF0000", "FF0000"), (None, None, "FFFFFF"),
])
async def test_recap_colors(make, change, last, color):
    d = FakeDriver()
    d.change, d.last_color = change, last
    s, d, _ = make(at(2026, 10, 27, 16, 5), driver=d)
    await s.step()
    assert d.calls == [("solid", color)]


async def test_evening_rotates_without_repeat(make):
    s, d, clock = make(at(2026, 10, 27, 18, 0))
    await run_for(s, clock, 35 * 60)
    plays = [c[1] for c in d.calls if c[0] == "play_motion"]
    assert len(plays) == 4
    assert all(a != b for a, b in zip(plays, plays[1:]))


async def test_evening_restricted_to_selected_motions(make):
    s, d, clock = make(at(2026, 10, 27, 18, 0), evening_motions=["m3"])
    await run_for(s, clock, 25 * 60)
    assert {c[1] for c in d.calls if c[0] == "play_motion"} == {"m3"}


async def test_evening_without_motions_is_warm_white(make):
    s, d, _ = make(at(2026, 10, 27, 18, 0), driver=FakeDriver(ids=()))
    await s.step()
    assert d.calls == [("solid", "FFB46B")]


async def test_takeover_stops_rotation(make):
    s, d, clock = make(at(2026, 10, 27, 22, 0))
    await s.step()
    d.owned = False  # user painted DIY
    await run_for(s, clock, 31 * 60)
    assert d.names() == ["play_motion", "power_off"]


async def test_manual_change_survives_until_next_phase(make):
    s, d, clock = make(at(2026, 10, 27, 15, 0))
    await s.step()
    d.owned = False
    await run_for(s, clock, 60 * 60 + 5)
    assert d.names() == ["start_ticker", "play_motion"]  # closing bell at 16:00


async def test_market_retries_ticker_start(make):
    d = FakeDriver()
    d.ticker_failures = 2
    s, d, clock = make(at(2026, 10, 27, 10, 0), driver=d)
    await run_for(s, clock, 120)
    assert d.names() == ["start_ticker"] * 3
    assert s.status()["phase"] == "market"


async def test_capture_blocks_then_applies(make):
    d = FakeDriver()
    d.busy = True
    s, d, clock = make(at(2026, 10, 27, 10, 0), driver=d)
    await run_for(s, clock, 120)
    assert d.calls == []
    d.busy = False
    await run_for(s, clock, 60)
    assert d.names() == ["start_ticker"]


async def test_update_saves_and_reapplies(make, tmp_path):
    s, d, clock = make(at(2026, 10, 27, 21, 0))
    await s.step()
    status = await s.update({"lights_out": "20:30"})
    assert status["settings"]["lights_out"] == "20:30"
    await s.step()
    assert d.names() == ["play_motion", "power_off"]
    assert json.loads((tmp_path / "schedule.json").read_text())["lights_out"] == "20:30"


async def test_update_invalid_leaves_file_alone(make, tmp_path):
    s, d, clock = make(at(2026, 10, 27, 21, 0), lights_out="23:00")
    with pytest.raises(ValueError, match="^interval"):
        await s.update({"interval": 7})
    assert json.loads((tmp_path / "schedule.json").read_text())["lights_out"] == "23:00"


async def test_symbol_change_restarts_ticker(make):
    s, d, clock = make(at(2026, 10, 27, 11, 0))
    await s.step()
    await s.update({"symbol": "aapl"})
    await s.step()
    assert d.calls == [("start_ticker", "IBM", 30), ("start_ticker", "AAPL", 30)]


async def test_symbol_change_after_takeover_does_not_restart(make):
    s, d, clock = make(at(2026, 10, 27, 11, 0))
    await s.step()
    d.owned = False
    await s.update({"symbol": "AAPL"})
    await s.step()
    assert d.names() == ["start_ticker"]


async def test_disable_leaves_lamp_enable_applies(make):
    s, d, clock = make(at(2026, 10, 27, 11, 0))
    await s.update({"enabled": False})
    await run_for(s, clock, 6 * 3600)
    assert d.calls == []
    await s.update({"enabled": True})
    await s.step()
    assert d.names() == ["play_motion"]  # 17:00 → evening


async def test_driving_reflects_ownership(make):
    s, d, _ = make(at(2026, 10, 27, 11, 0))
    await s.step()
    assert s.driving() is True
    d.owned = False
    assert s.driving() is False


async def test_status_shape(make):
    s, d, _ = make(at(2026, 10, 27, 11, 0))
    await s.step()
    st = s.status()
    assert st["phase"] == "market" and st["next_phase"] == "closing_bell"
    assert st["next_change_at"] == "2026-10-27T16:00:00-04:00"
    assert st["enabled"] is True and st["trading_day"] is True


async def test_run_loop_applies_and_wakes_on_update(tmp_path):
    import asyncio

    clock = Clock(at(2026, 10, 27, 21, 0))
    d = FakeDriver()

    async def long_sleep(_delay):
        await asyncio.sleep(3600)  # only an update() wake can end this

    s = Scheduler(d, tmp_path / "schedule.json", {}, now=clock, sleep=long_sleep)
    task = asyncio.create_task(s.run())
    try:
        for _ in range(50):
            await asyncio.sleep(0)
        assert d.names() == ["play_motion"]
        await s.update({"lights_out": "20:30"})
        for _ in range(50):
            await asyncio.sleep(0)
        assert d.names() == ["play_motion", "power_off"]
    finally:
        task.cancel()
