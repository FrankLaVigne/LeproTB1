"""Tests for the per-ring color/brightness helpers and POST /api/rings."""

import json

import pytest

from web import server as workshop


# ---------------------------------------------------------------------------
# scale_hex
# ---------------------------------------------------------------------------

def test_scale_hex_100_is_unchanged():
    assert workshop.scale_hex("FF8000", 100) == "FF8000"


def test_scale_hex_0_is_black():
    assert workshop.scale_hex("FF8000", 0) == "000000"


def test_scale_hex_50_halves_each_channel():
    # FF=255 -> 128 (0x80, round(127.5)=128), 80=128 -> 64 (0x40), 00 -> 00
    assert workshop.scale_hex("FF8000", 50) == "804000"


def test_scale_hex_50_of_white():
    # 255 * 0.5 = 127.5 -> round -> 128 -> 0x80
    assert workshop.scale_hex("FFFFFF", 50) == "808080"


def test_scale_hex_strips_leading_hash():
    assert workshop.scale_hex("#FF8000", 100) == "FF8000"


def test_scale_hex_uppercases():
    assert workshop.scale_hex("ff8000", 100) == "FF8000"


def test_scale_hex_clamps_below_zero():
    assert workshop.scale_hex("FF8000", -20) == "000000"


def test_scale_hex_clamps_above_100():
    assert workshop.scale_hex("FF8000", 250) == "FF8000"


def test_scale_hex_bad_hex_raises():
    with pytest.raises(ValueError):
        workshop.scale_hex("ZZZZZZ", 100)


def test_scale_hex_short_hex_raises():
    with pytest.raises(ValueError):
        workshop.scale_hex("FFF", 100)


# ---------------------------------------------------------------------------
# build_ring_leds
# ---------------------------------------------------------------------------

def test_build_ring_leds_total_is_196():
    leds = workshop.build_ring_leds(
        {"color": "FF0000", "bright": 100},
        {"color": "00FF00", "bright": 100},
        {"color": "0000FF", "bright": 100},
    )
    assert len(leds) == 196


def test_build_ring_leds_segment_counts():
    leds = workshop.build_ring_leds(
        {"color": "FF0000", "bright": 100},
        {"color": "00FF00", "bright": 100},
        {"color": "0000FF", "bright": 100},
    )
    outer = leds[0:88]
    middle = leds[88:150]
    inner = leds[150:196]
    assert len(outer) == 88
    assert len(middle) == 62
    assert len(inner) == 46


def test_build_ring_leds_segments_hold_correct_colors():
    leds = workshop.build_ring_leds(
        {"color": "FF0000", "bright": 100},
        {"color": "00FF00", "bright": 100},
        {"color": "0000FF", "bright": 100},
    )
    assert all(c == "FF0000" for c in leds[0:88])
    assert all(c == "00FF00" for c in leds[88:150])
    assert all(c == "0000FF" for c in leds[150:196])


def test_build_ring_leds_applies_per_ring_brightness():
    leds = workshop.build_ring_leds(
        {"color": "FFFFFF", "bright": 100},
        {"color": "FFFFFF", "bright": 50},
        {"color": "FFFFFF", "bright": 0},
    )
    assert all(c == "FFFFFF" for c in leds[0:88])
    assert all(c == "808080" for c in leds[88:150])
    assert all(c == "000000" for c in leds[150:196])


# ---------------------------------------------------------------------------
# POST /api/rings
# ---------------------------------------------------------------------------

class _FakeReq:
    def __init__(self, body=None, **match_info):
        self._body = body if body is not None else {}
        self.match_info = match_info

    async def json(self):
        return self._body


def _body(resp):
    return json.loads(resp.body.decode() if isinstance(resp.body, bytes) else resp.body)


class _FakeClient:
    def __init__(self):
        self.sent = []

    async def send_raw(self, payload, did=None):
        self.sent.append((payload, did))


@pytest.fixture
def quiet_lamp(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(workshop, "_client", client)
    monkeypatch.setattr(workshop, "_capture_session", None)
    monkeypatch.setattr(workshop, "_clock_session", None)
    monkeypatch.setattr(workshop, "_ticker_session", None)
    monkeypatch.setattr(workshop, "_preview_task", None)
    monkeypatch.setattr(workshop, "_preview_name", None)
    return client


async def test_api_rings_valid_sends_expected_d50(quiet_lamp):
    body = {
        "outer": {"color": "FF0000", "bright": 100},
        "middle": {"color": "00FF00", "bright": 100},
        "inner": {"color": "0000FF", "bright": 100},
    }
    resp = await workshop.api_rings(_FakeReq(body))
    assert _body(resp)["ok"] is True
    assert len(quiet_lamp.sent) == 1
    payload, did = quiet_lamp.sent[0]
    leds = workshop.build_ring_leds(
        body["outer"], body["middle"], body["inner"])
    expected = workshop.build_d50_from_leds(leds, "Steady", 50)
    assert payload == {"d1": 1, "d2": 2, "d50": expected}
    assert "FF0000" in payload["d50"]
    assert "00FF00" in payload["d50"]
    assert "0000FF" in payload["d50"]


async def test_api_rings_missing_ring_400(quiet_lamp):
    body = {
        "outer": {"color": "FF0000", "bright": 100},
        "middle": {"color": "00FF00", "bright": 100},
    }
    resp = await workshop.api_rings(_FakeReq(body))
    assert resp.status == 400
    assert quiet_lamp.sent == []


async def test_api_rings_bad_color_400(quiet_lamp):
    body = {
        "outer": {"color": "ZZZZZZ", "bright": 100},
        "middle": {"color": "00FF00", "bright": 100},
        "inner": {"color": "0000FF", "bright": 100},
    }
    resp = await workshop.api_rings(_FakeReq(body))
    assert resp.status == 400
    assert "6-hex" in _body(resp)["error"]
    assert quiet_lamp.sent == []


async def test_api_rings_bad_bright_400(quiet_lamp):
    body = {
        "outer": {"color": "FF0000", "bright": 150},
        "middle": {"color": "00FF00", "bright": 100},
        "inner": {"color": "0000FF", "bright": 100},
    }
    resp = await workshop.api_rings(_FakeReq(body))
    assert resp.status == 400
    assert quiet_lamp.sent == []


def _ok_body():
    return {
        "outer": {"color": "FF0000", "bright": 100},
        "middle": {"color": "00FF00", "bright": 100},
        "inner": {"color": "0000FF", "bright": 100},
    }


class _Running:
    running = True


async def test_api_rings_blocked_by_ticker(quiet_lamp, monkeypatch):
    monkeypatch.setattr(workshop, "_ticker_session", _Running())
    from aiohttp import web as aioweb

    with pytest.raises(aioweb.HTTPConflict):
        await workshop.api_rings(_FakeReq(_ok_body()))
    assert quiet_lamp.sent == []


async def test_api_rings_blocked_by_clock(quiet_lamp, monkeypatch):
    monkeypatch.setattr(workshop, "_clock_session", _Running())
    from aiohttp import web as aioweb

    with pytest.raises(aioweb.HTTPConflict):
        await workshop.api_rings(_FakeReq(_ok_body()))
    assert quiet_lamp.sent == []


async def test_api_rings_blocked_by_capture(quiet_lamp, monkeypatch):
    monkeypatch.setattr(workshop, "_capture_session", _Running())
    from aiohttp import web as aioweb

    with pytest.raises(aioweb.HTTPConflict):
        await workshop.api_rings(_FakeReq(_ok_body()))
    assert quiet_lamp.sent == []


async def test_api_rings_bool_bright_400(quiet_lamp):
    """bright must be a real int, not a bool (True == 1 would otherwise slip through)."""
    body = _ok_body()
    body["outer"]["bright"] = True
    resp = await workshop.api_rings(_FakeReq(body))
    assert resp.status == 400
    assert "bright" in _body(resp)["error"]
    assert quiet_lamp.sent == []


async def test_route_registration():
    app = workshop.build_app()
    routes = {(r.method, r.resource.canonical) for r in app.router.routes()
              if r.resource is not None}
    assert ("POST", "/api/rings") in routes
    assert ("GET", "/rings") in routes
