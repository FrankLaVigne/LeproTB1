"""Tests for cli.tui_api.LampApi against a real (in-process) aiohttp server."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from cli.tui_api import LampApi


@pytest.fixture
async def stub_server():
    """An aiohttp TestServer that records every request the LampApi makes."""
    received = {}

    async def leds(_req):
        return web.json_response({"power": True, "brightness_pct": 80, "leds": None})

    async def power(req):
        received["power"] = await req.json()
        return web.json_response({"ok": True})

    async def brightness(req):
        received["brightness"] = await req.json()
        return web.json_response({"ok": True})

    async def stop(_req):
        received["stop"] = True
        return web.json_response({"ok": True})

    async def paint(req):
        received["paint"] = await req.json()
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/api/lamp/leds", leds)
    app.router.add_post("/api/power", power)
    app.router.add_post("/api/brightness", brightness)
    app.router.add_post("/api/stop", stop)
    app.router.add_post("/api/diy/paint", paint)
    server = TestServer(app)
    await server.start_server()
    yield server, received
    await server.close()


@pytest.mark.asyncio
async def test_get_leds(stub_server):
    server, _received = stub_server
    api = LampApi(str(server.make_url("")))
    try:
        data = await api.get_leds()
        assert data["power"] is True
        assert data["brightness_pct"] == 80
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_set_power(stub_server):
    server, received = stub_server
    api = LampApi(str(server.make_url("")))
    try:
        result = await api.set_power(False)
        assert result["ok"] is True
        assert received["power"] == {"on": False}
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_set_brightness_converts_pct_to_value(stub_server):
    server, received = stub_server
    api = LampApi(str(server.make_url("")))
    try:
        await api.set_brightness(80)
        # The web API takes 0..1000; LampApi converts from percent.
        assert received["brightness"] == {"value": 800}
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_stop_all(stub_server):
    server, received = stub_server
    api = LampApi(str(server.make_url("")))
    try:
        await api.stop_all()
        assert received["stop"] is True
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_fill_sends_196_leds_steady(stub_server):
    server, received = stub_server
    api = LampApi(str(server.make_url("")))
    try:
        await api.fill("FF0000")
        body = received["paint"]
        assert body["leds"] == ["FF0000"] * 196
        assert body["effect"] == "Steady"
        assert body["speed"] == 50
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_base_url_trailing_slash_is_normalized(stub_server):
    server, _received = stub_server
    api = LampApi(str(server.make_url("")) + "///")
    try:
        data = await api.get_leds()
        assert data["power"] is True
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_post_returns_error_dict_on_http_error_status():
    """A 409 (mutex conflict) surfaces as a dict, not an exception."""
    async def conflict(_req):
        return web.json_response({"ok": False, "error": "clock is running"}, status=409)

    app = web.Application()
    app.router.add_post("/api/stop", conflict)
    server = TestServer(app)
    await server.start_server()
    api = LampApi(str(server.make_url("")))
    try:
        result = await api.stop_all()
        assert result["ok"] is False
        assert "clock" in result["error"]
    finally:
        await api.close()
        await server.close()
