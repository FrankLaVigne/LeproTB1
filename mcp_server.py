#!/usr/bin/env python3
"""Networked MCP server for Lepro lights (streamable-HTTP, bearer-token auth)."""

from __future__ import annotations

import hmac


class BearerAuthMiddleware:
    """ASGI middleware enforcing `Authorization: Bearer <token>` on HTTP requests."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode()
        expected = f"Bearer {self.token}"
        if not (auth and hmac.compare_digest(auth, expected)):
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"unauthorized"})
            return
        await self.app(scope, receive, send)


import asyncio
import contextlib
import logging
import sys

import uvicorn
from mcp.server.fastmcp import FastMCP

from lepro import AnimationPlayer, EFFECTS, LeproClient, LeproError, load_config

logging.basicConfig(level=logging.INFO)
_LOG = logging.getLogger("lepro.mcp")

# Module-level singletons set during lifespan startup.
_client: LeproClient | None = None
_players: dict[str, AnimationPlayer] = {}


def _player_for(did: str) -> AnimationPlayer:
    dev = _client._dev(did)  # raises LeproError on unknown id
    if dev.did not in _players:
        _players[dev.did] = AnimationPlayer(_client, dev)
    return _players[dev.did]


@contextlib.asynccontextmanager
async def _lifespan(_server: FastMCP):
    global _client
    cfg = load_config()
    if not cfg["account"] or not cfg["password"]:
        raise SystemExit("Missing credentials: set account/password in config.json or env.")
    _client = LeproClient(cfg["account"], cfg["password"], cfg["region"])
    await _client.login()
    await _client.connect_mqtt()
    listener = asyncio.create_task(_client.listen_forever())
    _LOG.info("MCP ready: %d device(s)", len(_client.devices))
    try:
        yield
    finally:
        listener.cancel()
        for p in _players.values():
            await p.stop()
        await _client.close()


mcp = FastMCP("lepro", lifespan=_lifespan)


def _ok(did: str | None, **extra) -> dict:
    return {"ok": True, "did": (_client._dev(did).did if _client else did), **extra}


def _guard(fn):
    """Wrap a tool coroutine so LeproError/ValueError return structured errors."""
    async def wrapped(**kwargs):
        try:
            return await fn(**kwargs)
        except (LeproError, ValueError, KeyError) as e:
            return {"ok": False, "error": str(e)}
    wrapped.__name__ = fn.__name__
    return wrapped


@mcp.tool()
async def list_lights() -> dict:
    """List the lights on the account."""
    return {"ok": True, "lights": [
        {"did": d.did, "name": d.name, "series": d.series} for d in _client.devices]}


@mcp.tool()
async def list_effects() -> dict:
    """List the built-in effect names and the speed range."""
    return {"ok": True, "effects": EFFECTS, "speed_range": [0, 100]}


@mcp.tool()
@_guard
async def set_power(on: bool, did: str | None = None) -> dict:
    """Turn a light on or off."""
    await _client.power(on, did)
    return _ok(did, applied={"on": on})


@mcp.tool()
@_guard
async def set_brightness(pct: int, did: str | None = None) -> dict:
    """Set brightness as a percentage (0-100)."""
    await _client.set_brightness(pct, did)
    return _ok(did, applied={"pct": pct})


@mcp.tool()
@_guard
async def set_color(r: int, g: int, b: int, pct: int | None = None, did: str | None = None) -> dict:
    """Set an RGB color (0-255 each), optional brightness percent."""
    await _client.set_color(r, g, b, pct, did)
    return _ok(did, applied={"rgb": [r, g, b], "pct": pct})


@mcp.tool()
@_guard
async def set_white(kelvin: int, pct: int | None = None, did: str | None = None) -> dict:
    """Set tunable white (2700-6500 K), optional brightness percent."""
    await _client.set_white(kelvin, pct, did)
    return _ok(did, applied={"kelvin": kelvin, "pct": pct})


@mcp.tool()
@_guard
async def set_effect(name: str, speed: int = 50, did: str | None = None) -> dict:
    """Run a built-in effect (see list_effects) at speed 0-100."""
    await _client.set_effect(name, speed=speed, did=did)
    return _ok(did, applied={"effect": name, "speed": speed})


@mcp.tool()
@_guard
async def set_segments(colors: list[list[int]], did: str | None = None) -> dict:
    """Set up to 25 RGB segment-groups across the rings, e.g. [[255,0,0],[0,0,255]]."""
    await _client.set_segments(colors, did)
    return _ok(did, applied={"segments": len(colors)})


@mcp.tool()
@_guard
async def play_animation(frames: list[dict], repeat: bool = False, did: str | None = None) -> dict:
    """Play a choreographed animation. Each frame: {color|segments|brightness, duration_ms>=80}."""
    await _player_for(_client._dev(did).did).play(frames, repeat)
    return _ok(did, frames=len(frames), repeat=repeat)


@mcp.tool()
@_guard
async def stop_animation(did: str | None = None) -> dict:
    """Stop any running animation on the light."""
    await _player_for(_client._dev(did).did).stop()
    return _ok(did)


@mcp.tool()
@_guard
async def get_state(did: str | None = None) -> dict:
    """Best-effort live device state (may be partial due to the single-session limit)."""
    d = _client._dev(did)
    await _client.request_state(d.did)
    await asyncio.sleep(1.5)
    return _ok(did, state=_client.state.get(d.did, {}))


@mcp.tool()
@_guard
async def send_raw(d: dict, did: str | None = None) -> dict:
    """Escape hatch: send an arbitrary 'd' payload to the device."""
    await _client.send_raw(d, did)
    return _ok(did, sent=d)


def main() -> None:
    cfg = load_config()
    host, port, token = cfg["mcp_host"], cfg["mcp_port"], cfg["mcp_token"]
    is_loopback = host in ("127.0.0.1", "localhost", "::1")
    if not token and not is_loopback:
        sys.exit("Refusing to bind a non-loopback address without LEPRO_MCP_TOKEN set.")
    app = mcp.streamable_http_app()
    if token:
        app = BearerAuthMiddleware(app, token)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
