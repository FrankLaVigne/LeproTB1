"""Tests for GET /api/lamp/leds + the _active_mode() refactor."""

import json

import pytest

from web import server as workshop


SOLID_ORANGE = "N01:P10001FFAA00F21000100C4U3V3000640000E1;"
N02_APP_ANIM = "N02:P10002FF0000008000U510F2100010f01V3001640396;"


class _FakeClient:
    def __init__(self, fields):
        self.state = {"dev1": fields}


def _body(resp):
    return json.loads(resp.body.decode() if isinstance(resp.body, bytes) else resp.body)


@pytest.fixture
def quiet_sessions(monkeypatch):
    """No background sessions running — active mode falls through to idle/off."""
    monkeypatch.setattr(workshop, "_capture_session", None)
    monkeypatch.setattr(workshop, "_clock_session", None)
    monkeypatch.setattr(workshop, "_ticker_session", None)
    monkeypatch.setattr(workshop, "_preview_task", None)


@pytest.mark.asyncio
async def test_lamp_leds_segmented_n01(quiet_sessions, monkeypatch):
    fields = {"d1": 1, "d2": 2, "d52": 800, "d50": SOLID_ORANGE}
    monkeypatch.setattr(workshop, "_client", _FakeClient(fields))
    body = _body(await workshop.api_lamp_leds(None))
    assert body["power"] is True
    assert body["brightness_pct"] == 80
    assert body["lamp_mode"] == "segmented"
    assert body["active"] == {"mode": "idle", "label": "✨ Idle"}
    assert body["leds"] == ["FFAA00"] * 196
    assert body["fields"]["d52"] == 800
    assert body["polled_at"]


@pytest.mark.asyncio
async def test_lamp_leds_rgb_mode(quiet_sessions, monkeypatch):
    fields = {"d1": 1, "d2": 1, "d5": "000003E803E8"}
    monkeypatch.setattr(workshop, "_client", _FakeClient(fields))
    body = _body(await workshop.api_lamp_leds(None))
    assert body["lamp_mode"] == "rgb"
    assert body["leds"] == ["FF0000"] * 196
    assert body["brightness_pct"] is None    # no d52 reported


@pytest.mark.asyncio
async def test_lamp_leds_white_mode(quiet_sessions, monkeypatch):
    fields = {"d1": 1, "d2": 0, "d4": 1000}
    monkeypatch.setattr(workshop, "_client", _FakeClient(fields))
    body = _body(await workshop.api_lamp_leds(None))
    assert body["lamp_mode"] == "white"
    assert body["leds"] == ["EBF2FF"] * 196


@pytest.mark.asyncio
async def test_lamp_leds_undecodable_app_animation(quiet_sessions, monkeypatch):
    fields = {"d1": 1, "d2": 2, "d50": N02_APP_ANIM}
    monkeypatch.setattr(workshop, "_client", _FakeClient(fields))
    body = _body(await workshop.api_lamp_leds(None))
    assert body["leds"] is None
    assert body["lamp_mode"] == "segmented"


@pytest.mark.asyncio
async def test_lamp_leds_no_client(quiet_sessions, monkeypatch):
    monkeypatch.setattr(workshop, "_client", None)
    body = _body(await workshop.api_lamp_leds(None))
    assert body["power"] is False
    assert body["leds"] is None
    assert body["brightness_pct"] is None
    assert body["lamp_mode"] is None


@pytest.mark.asyncio
async def test_lamp_leds_power_off(quiet_sessions, monkeypatch):
    fields = {"d1": 0, "d2": 2, "d50": SOLID_ORANGE}
    monkeypatch.setattr(workshop, "_client", _FakeClient(fields))
    body = _body(await workshop.api_lamp_leds(None))
    assert body["power"] is False
    assert body["active"]["mode"] == "off"
    # LEDs still decoded — the TUI dims them rather than dropping them.
    assert body["leds"] == ["FFAA00"] * 196


@pytest.mark.asyncio
async def test_cockpit_active_agrees_with_lamp_leds(quiet_sessions, monkeypatch):
    """After the refactor both endpoints answer from the same _active_mode()."""
    monkeypatch.setattr(workshop, "_client", _FakeClient({"d1": 1}))
    active = _body(await workshop.api_cockpit_active(None))
    leds = _body(await workshop.api_lamp_leds(None))
    assert active == leds["active"] == {"mode": "idle", "label": "✨ Idle"}


@pytest.mark.asyncio
async def test_route_is_registered():
    app = workshop.build_app()
    routes = {(r.method, r.resource.canonical) for r in app.router.routes()
              if r.resource is not None}
    assert ("GET", "/api/lamp/leds") in routes
