# Lepro MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing `LeproClient` as a networked, bearer-authenticated MCP server (streamable-HTTP) so OpenClaw and other MCP clients can control the TB1 — including built-in effects, per-segment color, and server-side choreographed animations.

**Architecture:** A new `mcp_server.py` builds a `FastMCP` server, registers tools that delegate to a single long-lived `LeproClient`, wraps the Starlette ASGI app with a bearer-token middleware, and runs under uvicorn. Animation/effect payload builders are added to `lepro.py` as pure functions (unit-testable without hardware), plus an `AnimationPlayer` for choreographed sequences.

**Tech Stack:** Python 3.12, `mcp` 1.27.1 (FastMCP), `uvicorn`, `aiomqtt`/`aiohttp` (existing), `pytest` + `pytest-asyncio` for tests.

---

## File Structure

- `lepro.py` (modify) — add effect/segment payload builders (`_speed_to_hex`, `_effect_tail`, `_build_d50`, `_build_effect_payload`, `EFFECTS`), `set_effect`/`set_segments` methods, `_frame_to_payload`, and the `AnimationPlayer` class.
- `mcp_server.py` (create) — FastMCP server, tool definitions, bearer middleware, lifespan managing the `LeproClient`, uvicorn runner.
- `tests/test_payloads.py` (create) — unit tests for builders.
- `tests/test_animation.py` (create) — frame validation + player with a fake client.
- `tests/test_auth.py` (create) — bearer middleware.
- `pytest.ini` (create) — `asyncio_mode = auto`.
- `requirements.txt` (modify) — add `mcp`, `uvicorn`; `requirements-dev.txt` (create) for pytest.
- `config.json.example` / `README.md` (modify) — document MCP token, host/port, second-account recommendation.
- `cli.py` (modify) — add a `capture` subcommand to record TB1 effect payloads.

All new client constants/functions are module-level in `lepro.py` so tests import them directly.

---

### Task 1: Test scaffolding

**Files:**
- Create: `pytest.ini`
- Create: `requirements-dev.txt`
- Modify: `requirements.txt`

- [ ] **Step 1: Create `pytest.ini`**

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 2: Create `requirements-dev.txt`**

```
-r requirements.txt
pytest>=8
pytest-asyncio>=0.23
```

- [ ] **Step 3: Add runtime deps to `requirements.txt`**

Append these two lines so the file reads:

```
aiohttp>=3.9
aiomqtt>=2.0
mcp>=1.27
uvicorn>=0.30
```

- [ ] **Step 4: Install and verify**

Run: `.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -c "import mcp, uvicorn, pytest; print('ok')"`
Expected: prints `ok`

- [ ] **Step 5: Commit**

```bash
git add pytest.ini requirements.txt requirements-dev.txt
git commit -m "test: add pytest scaffolding and MCP runtime deps"
```

---

### Task 2: Speed encoder + effect catalog

**Files:**
- Modify: `lepro.py` (add module-level constants + functions near the other helpers, after `_is_b_series`)
- Test: `tests/test_payloads.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_payloads.py
import lepro


def test_speed_to_hex_zero_is_1000():
    assert lepro._speed_to_hex(0) == "1000"


def test_speed_to_hex_is_four_hex_chars():
    for s in (1, 25, 50, 100):
        h = lepro._speed_to_hex(s)
        assert len(h) == 4
        int(h, 16)  # parses as hex


def test_speed_to_hex_clamps_over_100():
    assert lepro._speed_to_hex(500) == lepro._speed_to_hex(100)


def test_effects_catalog_contains_known_names():
    for name in ("solid", "breath", "gradient", "clockwise", "flash", "wave_1", "laser_4"):
        assert name in lepro.EFFECTS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_payloads.py -v`
Expected: FAIL with `AttributeError: module 'lepro' has no attribute '_speed_to_hex'`

- [ ] **Step 3: Add the implementation to `lepro.py`**

Add `import math` to the imports block, then add after `_is_b_series`:

```python
# d50-family effects (firmware runs them); names map to a payload "tail".
_D50_EFFECTS = ("solid", "breath", "gradient", "clockwise", "counterclockwise", "circular")
# d60 "special" effects; each maps to a 7-char prefix.
_D60_SPECIAL = {
    "flash": "2000064",
    "wave_1": "2010064", "wave_2": "2020064", "wave_3": "2030064", "wave_4": "2040064",
    "laser_1": "2050064", "laser_2": "2060064", "laser_3": "2070064", "laser_4": "2080064",
}
EFFECTS = list(_D50_EFFECTS) + list(_D60_SPECIAL)


def _speed_to_hex(speed: int) -> str:
    """Encode a 0-100 speed into the 4-hex-char form the d50 effects expect."""
    s = max(0, min(100, int(speed)))
    if s <= 0:
        return "1000"
    raw = int(round(-117.41 * math.log(s + 1) + 597.75))
    return f"0{raw:03X}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_payloads.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add lepro.py tests/test_payloads.py
git commit -m "feat: add effect catalog and speed encoder"
```

---

### Task 3: d50 builder + effect payload builder

**Files:**
- Modify: `lepro.py` (add after the Task 2 functions)
- Test: `tests/test_payloads.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_payloads.py

def test_build_d50_single_color_25_segments():
    d50 = lepro._build_d50([(255, 0, 0)] * 25, "solid")
    # one group, 25 segments (0x0019), red FF0000, solid tail
    assert d50 == "N01:P10001FF0000F2100010019U3V3000640000E1;"


def test_build_d50_two_groups():
    colors = [(255, 0, 0)] * 10 + [(0, 0, 255)] * 15
    d50 = lepro._build_d50(colors, "solid")
    assert d50.startswith("N01:P10002FF00000000FF")  # 2 groups, red then blue
    assert "F21000200" in d50  # 2 groups in the lengths block


def test_build_effect_payload_special_uses_d60():
    d = lepro._build_effect_payload("flash", speed=50)
    assert d["d1"] == 1 and d["d2"] == 3
    assert d["d60"].startswith("2000064")
    assert len(d["d60"]) == 13  # 7 prefix + 2 sens + 4 zeros


def test_build_effect_payload_d50_effect():
    d = lepro._build_effect_payload("breath", speed=50, color=(0, 255, 0))
    assert d["d1"] == 1 and d["d2"] == 2
    assert d["d50"].startswith("N01:P10001" + "00FF00")
    assert "E4" in d["d50"]  # breath marker


def test_build_effect_payload_brightness_optional():
    assert "d52" not in lepro._build_effect_payload("flash")
    assert lepro._build_effect_payload("flash", pct=50)["d52"] == 500


def test_build_effect_payload_unknown_raises():
    import pytest
    with pytest.raises(ValueError):
        lepro._build_effect_payload("nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_payloads.py -k build_ -v`
Expected: FAIL with `AttributeError: module 'lepro' has no attribute '_build_d50'`

- [ ] **Step 3: Add the implementation to `lepro.py`**

```python
def _effect_tail(effect: str, sp: str) -> str:
    """Return the d50 trailing segment encoding the named effect at speed-hex `sp`."""
    return {
        "solid": "000640000E1",
        "breath": f"000640000E4{sp}0000{sp}1664",
        "gradient": f"100640000E3{sp}C2O6{sp}",
        "clockwise": f"00164{sp}E1",
        "counterclockwise": f"00264{sp}E1",
        "circular": f"100640000E1C2O6{sp}",
    }[effect]


def _build_d50(segment_colors, effect: str = "solid", speed: int = 50) -> str:
    """Build the grouped d50 string from up to 25 RGB segments + an effect."""
    groups: list[list] = []
    for col in segment_colors:
        col = tuple(int(c) for c in col)
        if groups and groups[-1][0] == col:
            groups[-1][1] += 1
        else:
            groups.append([col, 1])
    total = sum(g[1] for g in groups)
    if total < 25:
        groups[-1][1] += 25 - total
    while sum(g[1] for g in groups) > 25:
        excess = sum(g[1] for g in groups) - 25
        if groups[-1][1] > excess:
            groups[-1][1] -= excess
        else:
            groups.pop()
    num = len(groups)
    colors_str = "".join(f"{r:02X}{g:02X}{b:02X}" for (r, g, b), _ in groups)
    lengths_str = "".join(f"{cnt:04X}" for _, cnt in groups)
    tail = _effect_tail(effect, _speed_to_hex(speed))
    return f"N01:P1000{num}{colors_str}F21000{num}{lengths_str}U3V3{tail};"


def _build_effect_payload(name: str, speed: int = 50, color=(255, 255, 255),
                          pct: int | None = None) -> dict:
    """Build the MQTT 'd' payload for a named effect (d50 family or d60 special)."""
    if name in _D60_SPECIAL:
        sens = max(0, min(0x63, round(max(0, min(100, speed)) * 0x63 / 100)))
        d = {"d1": 1, "d2": 3, "d60": f"{_D60_SPECIAL[name]}{sens:02X}0000"}
    elif name in _D50_EFFECTS:
        d = {"d1": 1, "d2": 2, "d50": _build_d50([color] * 25, name, speed)}
    else:
        raise ValueError(f"unknown effect {name!r}; see lepro.EFFECTS")
    if pct is not None:
        d["d52"] = max(0, min(1000, int(round(pct * 10))))
    return d
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_payloads.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add lepro.py tests/test_payloads.py
git commit -m "feat: add d50 and effect payload builders"
```

---

### Task 4: `set_effect` and `set_segments` client methods

**Files:**
- Modify: `lepro.py` (add methods to `LeproClient`, near `set_color`)
- Test: `tests/test_payloads.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_payloads.py
import pytest


class _FakeMQTT:
    def __init__(self):
        self.published = []

    async def publish(self, topic, payload):
        self.published.append((topic, payload))


def _client_with_fake_mqtt():
    c = lepro.LeproClient.__new__(lepro.LeproClient)  # bypass __init__/network
    c._mqtt = _FakeMQTT()
    c.devices = [lepro.Device(did="111", fid="f", name="lamp", series="TB1")]
    return c


@pytest.mark.asyncio
async def test_set_effect_publishes_to_set_topic():
    import json
    c = _client_with_fake_mqtt()
    await c.set_effect("flash", speed=70)
    topic, payload = c._mqtt.published[-1]
    assert topic == "le/111/prp/set"
    d = json.loads(payload)["d"]
    assert d["d2"] == 3 and d["d60"].startswith("2000064")


@pytest.mark.asyncio
async def test_set_segments_publishes_d50():
    import json
    c = _client_with_fake_mqtt()
    await c.set_segments([(255, 0, 0), (0, 0, 255)])
    d = json.loads(c._mqtt.published[-1][1])["d"]
    assert d["d2"] == 2 and d["d50"].startswith("N01:P10002")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_payloads.py -k "set_effect or set_segments" -v`
Expected: FAIL with `AttributeError: 'LeproClient' object has no attribute 'set_effect'`

- [ ] **Step 3: Add the methods to `LeproClient`** (after `set_color`)

```python
    async def set_effect(self, name: str, speed: int = 50, color=(255, 255, 255),
                         pct: int | None = None, did: str | None = None) -> None:
        """Run a named firmware effect (see lepro.EFFECTS)."""
        await self._publish(self._dev(did).did, _build_effect_payload(name, speed, color, pct))

    async def set_segments(self, colors, did: str | None = None) -> None:
        """Set up to 25 RGB segment-groups across the rings (solid, no motion)."""
        d = {"d1": 1, "d2": 2, "d50": _build_d50(colors, "solid")}
        await self._publish(self._dev(did).did, d)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_payloads.py -k "set_effect or set_segments" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lepro.py tests/test_payloads.py
git commit -m "feat: add set_effect and set_segments client methods"
```

---

### Task 5: AnimationPlayer (frame conversion + sequencer)

**Files:**
- Modify: `lepro.py` (add `_MIN_FRAME_MS`, `_frame_to_payload`, `AnimationPlayer`)
- Test: `tests/test_animation.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_animation.py
import asyncio
import json
import pytest
import lepro


def test_frame_to_payload_color_b_series():
    d = lepro._frame_to_payload({"color": [255, 0, 0], "duration_ms": 100}, b_series=True)
    assert d["d2"] == 1 and "d5" in d  # B-series uses HSV d5


def test_frame_to_payload_segments():
    d = lepro._frame_to_payload({"segments": [[255, 0, 0]], "duration_ms": 100}, b_series=True)
    assert d["d2"] == 2 and d["d50"].startswith("N01:")


def test_frame_to_payload_brightness_only():
    d = lepro._frame_to_payload({"brightness": 50, "duration_ms": 100}, b_series=True)
    assert d["d3"] == 500  # b-series brightness field


def test_frame_to_payload_duration_floor():
    with pytest.raises(ValueError):
        lepro._frame_to_payload({"color": [1, 2, 3], "duration_ms": 10}, b_series=True)


def test_frame_to_payload_requires_content():
    with pytest.raises(ValueError):
        lepro._frame_to_payload({"duration_ms": 100}, b_series=True)


class _FakeMQTT:
    def __init__(self):
        self.published = []

    async def publish(self, topic, payload):
        self.published.append((topic, json.loads(payload)["d"]))


def _client():
    c = lepro.LeproClient.__new__(lepro.LeproClient)
    c._mqtt = _FakeMQTT()
    c.devices = [lepro.Device(did="111", fid="f", name="lamp", series="TB1")]
    return c


@pytest.mark.asyncio
async def test_animation_plays_frames_then_stops():
    c = _client()
    player = lepro.AnimationPlayer(c, c.devices[0])
    await player.play([
        {"color": [255, 0, 0], "duration_ms": 80},
        {"color": [0, 255, 0], "duration_ms": 80},
    ], repeat=False)
    await asyncio.sleep(0.3)  # let it run through both frames
    await player.stop()
    assert len(c._mqtt.published) >= 2


@pytest.mark.asyncio
async def test_animation_stop_cancels_repeat():
    c = _client()
    player = lepro.AnimationPlayer(c, c.devices[0])
    await player.play([{"color": [255, 0, 0], "duration_ms": 80}], repeat=True)
    await asyncio.sleep(0.25)
    await player.stop()
    count_after_stop = len(c._mqtt.published)
    await asyncio.sleep(0.25)
    assert len(c._mqtt.published) == count_after_stop  # no more frames after stop
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_animation.py -v`
Expected: FAIL with `AttributeError: module 'lepro' has no attribute '_frame_to_payload'`

- [ ] **Step 3: Add the implementation to `lepro.py`**

Add the constant near `_SESSION_TTL`:

```python
_MIN_FRAME_MS = 80  # animation frame floor, to avoid hammering MQTT
```

Add the pure function near `_build_effect_payload`:

```python
def _frame_to_payload(frame: dict, b_series: bool) -> dict:
    """Convert one animation frame to an MQTT 'd' payload. Raises ValueError if invalid."""
    dur = frame.get("duration_ms")
    if not isinstance(dur, int) or dur < _MIN_FRAME_MS:
        raise ValueError(f"frame duration_ms must be an int >= {_MIN_FRAME_MS}")
    d: dict = {"d1": 1}
    if "brightness" in frame:
        val = max(0, min(1000, int(round(frame["brightness"] * 10))))
        d["d3" if b_series else "d52"] = val
    if "segments" in frame:
        d["d2"] = 2
        d["d50"] = _build_d50(frame["segments"], "solid")
    elif "color" in frame:
        r, g, b = (int(max(0, min(255, c))) for c in frame["color"])
        if b_series:
            hue, _s, _v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            hue_deg = int(round((hue * 360) % 360))
            val = d.get("d3", 1000)
            d["d2"] = 1
            d["d5"] = f"{hue_deg:04X}{1000:04X}{val:04X}"
        else:
            d["d2"] = 2
            d["d50"] = _build_d50([(r, g, b)] * 25, "solid")
    if "color" not in frame and "segments" not in frame and "brightness" not in frame:
        raise ValueError("frame needs at least one of: color, segments, brightness")
    return d
```

Add the class near the bottom of `lepro.py` (module level, after `LeproClient`):

```python
class AnimationPlayer:
    """Plays a choreographed sequence of frames on one device via MQTT."""

    def __init__(self, client: "LeproClient", device: Device):
        self._client = client
        self._device = device
        self._task: asyncio.Task | None = None

    async def play(self, frames: list[dict], repeat: bool | int = False) -> None:
        if not frames:
            raise ValueError("frames must be non-empty")
        if len(frames) > 500:
            raise ValueError("too many frames (max 500)")
        payloads = [(_frame_to_payload(f, self._device.is_b_series), f["duration_ms"])
                    for f in frames]  # validates all frames up front
        await self.stop()
        self._task = asyncio.create_task(self._run(payloads, repeat))

    async def _run(self, payloads, repeat) -> None:
        loops = (1 << 30) if repeat is True else (1 if repeat is False else int(repeat))
        try:
            for _ in range(loops):
                for d, dur in payloads:
                    await self._client._publish(self._device.did, d)
                    await asyncio.sleep(dur / 1000)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_animation.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add lepro.py tests/test_animation.py
git commit -m "feat: add AnimationPlayer and frame-to-payload conversion"
```

---

### Task 6: Bearer-auth ASGI middleware

**Files:**
- Create: `mcp_server.py` (start the file with just the middleware + helper)
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_auth.py
import pytest
from mcp_server import BearerAuthMiddleware


class _SpyApp:
    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _scope(auth=None):
    headers = [(b"authorization", auth.encode())] if auth else []
    return {"type": "http", "headers": headers}


async def _drain():
    sent = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request"}

    return sent, send, receive


@pytest.mark.asyncio
async def test_valid_token_passes_through():
    app = _SpyApp()
    mw = BearerAuthMiddleware(app, token="secret")
    sent, send, receive = await _drain()
    await mw(_scope("Bearer secret"), receive, send)
    assert app.called is True


@pytest.mark.asyncio
async def test_missing_token_returns_401():
    app = _SpyApp()
    mw = BearerAuthMiddleware(app, token="secret")
    sent, send, receive = await _drain()
    await mw(_scope(None), receive, send)
    assert app.called is False
    assert sent[0]["status"] == 401


@pytest.mark.asyncio
async def test_wrong_token_returns_401():
    app = _SpyApp()
    mw = BearerAuthMiddleware(app, token="secret")
    sent, send, receive = await _drain()
    await mw(_scope("Bearer nope"), receive, send)
    assert sent[0]["status"] == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server'`

- [ ] **Step 3: Create `mcp_server.py` with the middleware**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_auth.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add mcp_server.py tests/test_auth.py
git commit -m "feat: add bearer-auth ASGI middleware"
```

---

### Task 7: MCP server assembly (tools, lifespan, runner)

**Files:**
- Modify: `mcp_server.py` (append server, tools, lifespan, runner below the middleware)
- Modify: `lepro.py` (extend `load_config` to surface MCP settings)

- [ ] **Step 1: Extend `load_config` in `lepro.py`**

Replace the `return {...}` at the end of `load_config` with:

```python
    return {
        "account": os.environ.get("LEPRO_ACCOUNT", cfg.get("account")),
        "password": os.environ.get("LEPRO_PASSWORD", cfg.get("password")),
        "region": os.environ.get("LEPRO_REGION", cfg.get("region", "na")),
        "mcp_token": os.environ.get("LEPRO_MCP_TOKEN", cfg.get("mcp_token")),
        "mcp_host": os.environ.get("LEPRO_MCP_HOST", cfg.get("mcp_host", "0.0.0.0")),
        "mcp_port": int(os.environ.get("LEPRO_MCP_PORT", cfg.get("mcp_port", 8765))),
    }
```

- [ ] **Step 2: Append the server to `mcp_server.py`**

```python
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
```

- [ ] **Step 3: Smoke-test imports and app construction**

Run: `.venv/bin/python -c "import mcp_server; app = mcp_server.mcp.streamable_http_app(); print('tools:', sorted(t.name for t in mcp_server.mcp._tool_manager.list_tools())); print('app ok')"`
Expected: prints the 11 tool names (`list_lights`, `list_effects`, `set_power`, `set_brightness`, `set_color`, `set_white`, `set_effect`, `set_segments`, `play_animation`, `stop_animation`, `get_state`, `send_raw`) and `app ok`

- [ ] **Step 4: Verify the full test suite still passes**

Run: `.venv/bin/python -m pytest -v`
Expected: PASS (all tests from Tasks 1-6)

- [ ] **Step 5: Commit**

```bash
git add mcp_server.py lepro.py
git commit -m "feat: assemble MCP server with tools, lifespan, and runner"
```

---

### Task 8: Live smoke test against the real lamp

**Files:** none (manual verification)

- [ ] **Step 1: Set a token in `config.json`**

Add `"mcp_token": "<pick-a-long-random-string>"` to `config.json` (already git-ignored).

- [ ] **Step 2: Start the server**

Run: `.venv/bin/python mcp_server.py`
Expected: logs `MCP ready: 1 device(s)` and uvicorn listening on `0.0.0.0:8765`.

- [ ] **Step 3: Reject unauthenticated requests**

In another shell, run:
`curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/mcp`
Expected: `401`

- [ ] **Step 4: Call a tool with the MCP client**

Create `tests/manual_smoke.py`:

```python
import asyncio, os, sys
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

TOKEN = sys.argv[1]
URL = "http://127.0.0.1:8765/mcp"


async def main():
    async with streamablehttp_client(URL, headers={"Authorization": f"Bearer {TOKEN}"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            print("tools:", [t.name for t in (await s.list_tools()).tools])
            print("lights:", (await s.call_tool("list_lights", {})).content)
            print("on:", (await s.call_tool("set_power", {"on": True})).content)
            await s.call_tool("set_color", {"r": 0, "g": 0, "b": 255})


asyncio.run(main())
```

Run: `.venv/bin/python tests/manual_smoke.py <your-token>`
Expected: prints the tool list and lights; the lamp turns on and goes blue.

- [ ] **Step 5: Commit the smoke helper**

```bash
git add tests/manual_smoke.py
git commit -m "test: add manual MCP smoke-test helper"
```

---

### Task 9: Effect-capture helper + docs

**Files:**
- Modify: `cli.py` (add a `capture` subcommand)
- Modify: `README.md`, `config.json.example`

- [ ] **Step 1: Add the `capture` subcommand parser to `cli.py`**

In the subparser block, add:

```python
    sp_cap = sub.add_parser("capture", help="log raw MQTT reports while you trigger effects in the app")
    sp_cap.add_argument("--seconds", type=int, default=120)
```

- [ ] **Step 2: Add the capture handler in `cli.py`** (in the command dispatch, after `state`)

```python
        if args.cmd == "capture":
            did = args.did or client.devices[0].did
            await client.request_state(did)
            print(f"Listening {args.seconds}s on le/{did}/prp/# — trigger effects in the app now…")

            async def on_update(d, fields):
                interesting = {k: fields[k] for k in ("d2", "d50", "d60", "d5") if k in fields}
                if interesting:
                    print(f"[{d}] {interesting}")

            try:
                await asyncio.wait_for(client.listen(on_update=on_update), timeout=args.seconds)
            except asyncio.TimeoutError:
                pass
            return 0
```

- [ ] **Step 3: Verify capture parses**

Run: `.venv/bin/python cli.py capture --help`
Expected: shows the capture help with `--seconds`.

- [ ] **Step 4: Document in `README.md`**

Add a `## MCP server` section:

```markdown
## MCP server

Expose the lamp to AI agents (OpenClaw, Claude Desktop/Code, any MCP client) over
the network:

```bash
# add "mcp_token": "<random>" to config.json, then:
.venv/bin/python mcp_server.py        # streamable-HTTP on 0.0.0.0:8765
```

Clients connect to `http://<vm-ip>:8765/mcp` with header
`Authorization: Bearer <mcp_token>`. Tools: `list_lights`, `list_effects`,
`set_power`, `set_brightness`, `set_color`, `set_white`, `set_effect`,
`set_segments`, `play_animation`, `stop_animation`, `get_state`, `send_raw`.

Override bind with `LEPRO_MCP_HOST` / `LEPRO_MCP_PORT`. Without a token the server
refuses to bind a non-loopback address.

**Recommended:** run the server under a *dedicated second Lepro account* shared
into the lamp's Home, so it doesn't fight your phone for the single session.

### Effects on the TB1
Effect/segment payloads were reverse-engineered from Lepro bulbs/strips. To
confirm what the TB1 actually uses, run `cli.py capture`, trigger each effect in
the app, and adjust the catalog to the logged `d50`/`d60` values.
```

- [ ] **Step 5: Document MCP keys in `config.json.example`**

```json
{
  "account": "you@example.com",
  "password": "your-lepro-password",
  "region": "na",
  "mcp_token": "change-me-to-a-long-random-string",
  "mcp_host": "0.0.0.0",
  "mcp_port": 8765
}
```

- [ ] **Step 6: Commit**

```bash
git add cli.py README.md config.json.example
git commit -m "feat: add effect-capture helper and document the MCP server"
```

---

## Self-Review

**Spec coverage:**
- Architecture / streamable-HTTP / uvicorn → Task 7 ✓
- Bearer middleware + fail-safe bind → Task 6, Task 7 `main()` ✓
- Tools (all 12 incl. send_raw) → Task 7 ✓
- Built-in effects (`set_effect`/`list_effects`) → Tasks 3, 4, 7 ✓
- Per-segment (`set_segments`) → Tasks 3, 4, 7 ✓
- Choreographed animation (`AnimationPlayer`, play/stop) → Tasks 5, 7 ✓
- Config extension → Task 7 Step 1 ✓
- Lifecycle (login, listen_forever, cleanup) → Task 7 `_lifespan` ✓
- TB1 effect verification (capture) → Task 9 ✓
- Tests: unit (payloads, animation, auth) → Tasks 2-6; MCP smoke → Task 8 ✓
- Docs + second-account note → Task 9 ✓

**Placeholder scan:** none — every code/test step contains full code.

**Type consistency:** `_build_d50`, `_build_effect_payload`, `_frame_to_payload`, `EFFECTS`, `AnimationPlayer(client, device)`, `set_effect`/`set_segments` signatures, and the `_client`/`_players` singletons are used consistently across tasks. `_dev`/`_publish`/`request_state`/`listen`/`listen_forever`/`power`/`set_color`/`set_brightness`/`set_white`/`send_raw` are all existing `LeproClient` members from the current `lepro.py`.

**Note for implementer:** `get_state` and the capture step may return little/nothing while the phone app holds the account's session — expected, not a failure (see spec "single-session reality").
