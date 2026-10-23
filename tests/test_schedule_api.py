"""/api/schedule endpoints, the server's LampDriver, and schedule-aware banners."""

import asyncio
import json
from datetime import datetime

import pytest

from web import schedule as sch
from web import server as workshop
from web.market_calendar import ET
from web.scheduler import Scheduler


class _FakeReq:
    def __init__(self, body=None):
        self._body = body if body is not None else {}
        self.match_info = {}

    async def json(self):
        return self._body


def _body(resp):
    return json.loads(resp.body.decode() if isinstance(resp.body, bytes) else resp.body)


class _NullDriver:
    async def start_ticker(self, symbol, interval): return object()
    async def play_motion(self, motion_id): return object()
    async def solid(self, color): pass
    async def power_off(self): pass
    def owns(self, handle): return False
    def capture_busy(self): return False
    def motion_ids(self): return []
    def motion_name(self, motion_id): return motion_id
    async def day_change(self, symbol): return None
    def last_ticker_color(self): return None


@pytest.fixture
def scheduler(tmp_path, monkeypatch):
    s = Scheduler(_NullDriver(), tmp_path / "schedule.json", {},
                  now=lambda: datetime(2026, 10, 27, 11, 0, tzinfo=ET))
    monkeypatch.setattr(workshop, "_scheduler", s)
    return s


async def test_get_schedule_shape(scheduler):
    body = _body(await workshop.api_schedule_get(_FakeReq()))
    assert body["settings"] == sch.DEFAULTS
    assert body["status"]["phase"] == "market"
    assert body["status"]["next_change_at"] == "2026-10-27T16:00:00-04:00"


async def test_put_schedule_updates_and_persists(scheduler, tmp_path):
    resp = await workshop.api_schedule_put(_FakeReq({"lights_out": "23:45"}))
    assert resp.status == 200
    assert _body(resp)["settings"]["lights_out"] == "23:45"
    assert json.loads((tmp_path / "schedule.json").read_text())["lights_out"] == "23:45"


async def test_put_schedule_invalid_400(scheduler):
    resp = await workshop.api_schedule_put(_FakeReq({"interval": 7}))
    assert resp.status == 400
    body = _body(resp)
    assert body["ok"] is False and body["error"].startswith("interval")


async def test_get_schedule_without_scheduler_503(monkeypatch):
    monkeypatch.setattr(workshop, "_scheduler", None)
    assert (await workshop.api_schedule_get(_FakeReq())).status == 503


class _StubScheduler:
    def __init__(self, driving):
        self._driving = driving

    def driving(self):
        return self._driving

    def status(self):
        return {"phase": "evening", "next_phase": "night",
                "next_change_at": "2026-10-27T22:30:00-04:00"}


@pytest.fixture
def idle_server(monkeypatch):
    for name in ("_client", "_capture_session", "_clock_session",
                 "_ticker_session", "_preview_task"):
        monkeypatch.setattr(workshop, name, None)


def test_active_mode_prefix_when_scheduler_driving(idle_server, monkeypatch):
    monkeypatch.setattr(workshop, "_scheduler", _StubScheduler(True))
    assert workshop._active_mode()["label"].startswith("📅 ")
    monkeypatch.setattr(workshop, "_scheduler", _StubScheduler(False))
    assert not workshop._active_mode()["label"].startswith("📅")


async def test_lamp_leds_includes_schedule(idle_server, monkeypatch):
    monkeypatch.setattr(workshop, "_scheduler", _StubScheduler(True))
    body = _body(await workshop.api_lamp_leds(_FakeReq()))
    assert body["schedule"] == {"phase": "evening", "next_phase": "night",
                                "next_change_at": "2026-10-27T22:30:00-04:00"}
    monkeypatch.setattr(workshop, "_scheduler", None)
    assert _body(await workshop.api_lamp_leds(_FakeReq()))["schedule"] is None


# --- _ServerDriver --------------------------------------------------------

class _FakeSession:
    def __init__(self, running=True, color="FF0000", frame_count=0):
        self.running = running
        self.stopped = False
        self._color = color
        self.frame_count = frame_count

    async def stop(self):
        self.running = False
        self.stopped = True

    def snapshot(self):
        return {"rings": {"outer": {"color": self._color}}}


def test_driver_owns_ticker_and_preview(idle_server, monkeypatch):
    drv = workshop._ServerDriver()
    sess = _FakeSession()
    monkeypatch.setattr(workshop, "_ticker_session", sess)
    assert drv.owns(sess) and not drv.owns(_FakeSession())
    sess.running = False
    assert not drv.owns(sess)
    assert not drv.owns(None)


def test_driver_capture_busy_includes_unsaved_frames(idle_server, monkeypatch):
    drv = workshop._ServerDriver()
    assert drv.capture_busy() is False
    monkeypatch.setattr(workshop, "_capture_session", _FakeSession(running=False, frame_count=3))
    assert drv.capture_busy() is True
    monkeypatch.setattr(workshop, "_capture_session", _FakeSession(running=True))
    assert drv.capture_busy() is True


def test_driver_empty_stopped_capture_does_not_block(idle_server, monkeypatch):
    monkeypatch.setattr(workshop, "_capture_session", _FakeSession(running=False, frame_count=0))
    assert workshop._ServerDriver().capture_busy() is False


def test_driver_last_ticker_color(idle_server, monkeypatch):
    drv = workshop._ServerDriver()
    assert drv.last_ticker_color() is None
    monkeypatch.setattr(workshop, "_ticker_session", _FakeSession(color="00FF00"))
    assert drv.last_ticker_color() == "00FF00"


async def test_driver_start_ticker_replaces_running_ticker(idle_server, monkeypatch):
    old = _FakeSession()
    monkeypatch.setattr(workshop, "_ticker_session", old)
    started = []

    async def fake_fetch(symbols):
        return {"outer": 190.0}

    async def fake_start(symbols, interval, whole_lamp, baselines=None):
        started.append((symbols, interval, whole_lamp, baselines))
        workshop._ticker_session = _FakeSession()
        return baselines

    monkeypatch.setattr(workshop, "_fetch_ticker_baselines", fake_fetch)
    monkeypatch.setattr(workshop, "_start_ticker", fake_start)
    handle = await workshop._ServerDriver().start_ticker("AAPL", 60)
    assert old.stopped
    assert started == [({"outer": "AAPL"}, 60, True, {"outer": 190.0})]
    assert handle is workshop._ticker_session


async def test_driver_start_ticker_raises_on_failure(idle_server, monkeypatch):
    async def failing(symbols):
        raise workshop._TickerStartError("could not fetch first price for: IBM")

    monkeypatch.setattr(workshop, "_fetch_ticker_baselines", failing)
    with pytest.raises(workshop._TickerStartError):
        await workshop._ServerDriver().start_ticker("IBM", 30)


# --- ticker.fetch_day_change ----------------------------------------------

def test_fetch_day_change(monkeypatch):
    from web import ticker

    class _Fake:
        def __init__(self, symbol):
            self.fast_info = {"last_price": 253.0, "previous_close": 250.0}

    monkeypatch.setattr("yfinance.Ticker", _Fake)
    assert ticker.fetch_day_change("IBM") == pytest.approx(3.0)

    class _Boom:
        def __init__(self, symbol):
            raise RuntimeError("network")

    monkeypatch.setattr("yfinance.Ticker", _Boom)
    assert ticker.fetch_day_change("IBM") is None


class _LiveTask:
    def done(self):
        return False


async def test_driver_failed_ticker_fetch_leaves_other_drivers_alone(idle_server, monkeypatch):
    preview = _LiveTask()
    monkeypatch.setattr(workshop, "_preview_task", preview)
    monkeypatch.setattr(workshop._ticker_mod, "fetch_price", lambda sym: None)
    with pytest.raises(workshop._TickerStartError):
        await workshop._ServerDriver().start_ticker("IBMM", 30)
    assert workshop._preview_task is preview  # the user's preview survived


def test_driver_owns_false_while_lamp_reports_off(idle_server, monkeypatch):
    class _Client:
        state = {"dev": {"d1": 0}}

    preview = _LiveTask()
    monkeypatch.setattr(workshop, "_preview_task", preview)
    monkeypatch.setattr(workshop, "_client", _Client())
    assert workshop._ServerDriver().owns(preview) is False
    _Client.state = {"dev": {"d1": 1}}
    assert workshop._ServerDriver().owns(preview) is True


async def test_put_schedule_save_error_is_json_500(scheduler, monkeypatch):
    def boom(path, settings):
        raise PermissionError("read-only file system")

    monkeypatch.setattr(sch, "save_settings", boom)
    resp = await workshop.api_schedule_put(_FakeReq({"lights_out": "23:00"}))
    assert resp.status == 500
    assert "read-only" in _body(resp)["error"]


async def test_schedule_panel_escapes_motion_name_and_sends_changes_only():
    resp = await workshop.index_schedule(None)
    text = resp.text if isinstance(resp.text, str) else resp.body.decode()
    assert "esc(st.current_motion)" in text
    assert "changedFields(" in text
