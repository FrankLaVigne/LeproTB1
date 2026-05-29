# Preset Workshop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A web UI ("the workshop") for browsing the captured `presets/` library, recoloring a chosen preset via a color-combo picker, naming the variant, previewing it on the lamp, and saving it as a new `presets/*.json`.

**Architecture:** One `workshop.py` script — sibling to `app.py`. Single aiohttp server with one persistent `LeproClient`, one mutable `_preview_task: asyncio.Task | None`, and inlined HTML/CSS/JS. Pure functions for palette extraction and color substitution (unit tested, no hardware needed); the playback loop is exercised against a fake client.

**Tech Stack:** Python 3.12, existing `aiohttp`, existing `lepro.LeproClient`. No new dependencies.

---

## File Structure

- `workshop.py` (create) — script: imports, pure helpers (`extract_palette`, `apply_color_map`, `_sanitize_name`), aiohttp routes (`/`, `/api/presets`, `/api/presets/{name}`, `/api/preview`, `/api/stop`, `/api/save`), `_run_preview` loop, app factory + `main`. ~400 lines including inlined HTML/CSS/JS.
- `tests/test_workshop.py` (create) — unit tests for the pure helpers and the `_run_preview` loop with a fake client.
- `README.md` (modify) — add `## Preset workshop` section between `## Stock tracker` and `## Protocol notes`.

Eight tasks below; the file builds up incrementally with TDD where the function is pure.

---

### Task 1: Pure helper — `extract_palette`

**Files:**
- Create: `workshop.py`
- Create: `tests/test_workshop.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_workshop.py` with:
```python
"""Tests for workshop."""

import pytest

import workshop


def test_extract_palette_single_frame_one_color():
    preset = {"payload": {"d50": "N01:P10001FF0000F2100010019U3V3000640000E1;"}}
    assert workshop.extract_palette(preset) == ["FF0000"]


def test_extract_palette_single_frame_multi_color():
    preset = {"payload": {"d50": "N02:P10003FF000000FF0000FF59U510...;P600...;"}}
    assert workshop.extract_palette(preset) == ["FF0000", "00FF00", "0000FF"]


def test_extract_palette_multi_frame_dedups_and_orders_by_first_occurrence():
    preset = {"frames": [
        {"d50": "N01:P10002AAA000BBB111;"},
        {"d50": "N01:P10002BBB111CCC222;"},  # BBB111 already seen, CCC222 is new
    ]}
    assert workshop.extract_palette(preset) == ["AAA000", "BBB111", "CCC222"]


def test_extract_palette_normalizes_to_uppercase():
    preset = {"payload": {"d50": "N01:P40005e500e500e500e500e500U504F2...;"}}
    assert workshop.extract_palette(preset) == ["E500E5"]


def test_extract_palette_handles_per_ring_format():
    preset = {"payload": {"d50":
        "#V:0358c4000000003ec4000000002ec400000000;"
        "#I00:N01:P100038000FFFFC0CB8000FFU701...;"
        "#I01:N01:P100038000FFFFC0CB8000FFU701...;"
        "#I02:N01:P100038000FFFFC0CB8000FFU701...;"
    }}
    # Same palette repeated per ring → dedup keeps first-occurrence order
    assert workshop.extract_palette(preset) == ["8000FF", "FFC0CB"]


def test_extract_palette_empty_preset_returns_empty_list():
    assert workshop.extract_palette({"frames": []}) == []
    assert workshop.extract_palette({"payload": {"d50": ""}}) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_workshop.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'workshop'`

- [ ] **Step 3: Create `workshop.py` with the function**

```python
#!/usr/bin/env python3
"""Workshop — web UI for browsing, recoloring, previewing, and saving presets.

Sibling to app.py. Defaults to 0.0.0.0:8081.
"""

from __future__ import annotations

import re

# Match P1000<count><colors>. Count is a single decimal digit (1-9 verified;
# REVERSE_ENGINEERING.md caps at 9). Colors are run-length-encoded 6-hex tuples.
_PALETTE_RE = re.compile(r"P1000(\d)((?:[0-9A-Fa-f]{6})+)")


def _iter_d50s(preset: dict):
    """Yield every d50 string in a preset, single-frame or multi-frame."""
    if "frames" in preset:
        for frame in preset["frames"]:
            d50 = frame.get("d50")
            if d50:
                yield d50
    payload = preset.get("payload")
    if payload and payload.get("d50"):
        yield payload["d50"]


def extract_palette(preset: dict) -> list[str]:
    """Return distinct palette colors (uppercase hex) in first-occurrence order."""
    seen: dict[str, None] = {}  # dict preserves insertion order, acts as ordered set
    for d50 in _iter_d50s(preset):
        for m in _PALETTE_RE.finditer(d50):
            count = int(m.group(1))
            hex_run = m.group(2)
            for i in range(count):
                color = hex_run[i * 6:(i + 1) * 6].upper()
                if color not in seen:
                    seen[color] = None
    return list(seen.keys())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_workshop.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add workshop.py tests/test_workshop.py
git commit -m "feat: add extract_palette (pure, first-occurrence order, uppercase)"
```

---

### Task 2: Pure helper — `apply_color_map`

**Files:**
- Modify: `workshop.py` (append below `extract_palette`)
- Modify: `tests/test_workshop.py` (append)

- [ ] **Step 1: Append the failing tests**

```python


# --- apply_color_map tests ----------------------------------------------------


def test_apply_color_map_single_frame():
    preset = {"name": "x", "payload": {"d1": 1, "d2": 2,
                                       "d50": "N01:P10001FF0000F2100010019U3V3;"}}
    out = workshop.apply_color_map(preset, {"FF0000": "00FF00"})
    assert out["payload"]["d50"] == "N01:P1000100FF00F2100010019U3V3;"
    # Original untouched (deep copy)
    assert preset["payload"]["d50"] == "N01:P10001FF0000F2100010019U3V3;"


def test_apply_color_map_multi_frame():
    preset = {"name": "x", "frames": [
        {"d2": 2, "d50": "N01:P10001FF0000;"},
        {"d2": 2, "d50": "N01:P10002FF0000FFC0CB;"},
    ]}
    out = workshop.apply_color_map(preset, {"FF0000": "00FF00", "FFC0CB": "0000FF"})
    assert out["frames"][0]["d50"] == "N01:P1000100FF00;"
    assert out["frames"][1]["d50"] == "N01:P1000200FF000000FF;"


def test_apply_color_map_case_insensitive_input_uppercase_output():
    preset = {"payload": {"d50": "N01:P10001ff0000;"}}
    out = workshop.apply_color_map(preset, {"ff0000": "ff00ff"})
    assert out["payload"]["d50"] == "N01:P10001FF00FF;"


def test_apply_color_map_unknown_old_color_is_noop():
    preset = {"payload": {"d50": "N01:P10001FF0000;"}}
    out = workshop.apply_color_map(preset, {"BADBAD": "00FF00"})
    assert out["payload"]["d50"] == "N01:P10001FF0000;"


def test_apply_color_map_empty_map_is_deep_copy():
    preset = {"name": "x", "payload": {"d50": "N01:P10001FF0000;"}}
    out = workshop.apply_color_map(preset, {})
    assert out == preset
    assert out is not preset
    assert out["payload"] is not preset["payload"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_workshop.py -k apply_color_map -v`
Expected: FAIL with `AttributeError: module 'workshop' has no attribute 'apply_color_map'`

- [ ] **Step 3: Append the implementation to `workshop.py`**

Add `import copy` to the imports at the top (alongside `import re`), then append:
```python
def apply_color_map(preset: dict, color_map: dict[str, str]) -> dict:
    """Return a deep copy of `preset` with every d50 hex-substituted.

    Input keys and values are normalized to uppercase; substitution emits
    uppercase hex (matching the format the Lepro app emits in captures).
    Substring collisions are avoided because palette colors are always exactly
    6 hex characters, and (per the white-blue-tour experiment) palette colors
    live ONLY inside P1000{N}{colors} blocks.
    """
    norm = {k.upper(): v.upper() for k, v in color_map.items()}
    out = copy.deepcopy(preset)
    for d50_holder in _holders(out):
        d50 = d50_holder["d50"]
        # Use case-insensitive substitution by walking the lowercase version
        # of d50 and rebuilding. Simplest: replace per key, both cases.
        for old, new in norm.items():
            # Replace uppercase form
            d50 = d50.replace(old, new).replace(old.lower(), new)
        d50_holder["d50"] = d50
    return out


def _holders(preset: dict):
    """Yield the dicts whose 'd50' key we should rewrite in place."""
    if "frames" in preset:
        for frame in preset["frames"]:
            if "d50" in frame:
                yield frame
    payload = preset.get("payload")
    if payload and "d50" in payload:
        yield payload
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_workshop.py -v`
Expected: PASS (11 tests total — 6 from Task 1 + 5 new)

- [ ] **Step 5: Commit**

```bash
git add workshop.py tests/test_workshop.py
git commit -m "feat: add apply_color_map (deep-copy, case-insensitive, uppercase out)"
```

---

### Task 3: Sanitize / validate variant names

**Files:**
- Modify: `workshop.py` (append below `_holders`)
- Modify: `tests/test_workshop.py` (append)

- [ ] **Step 1: Append the failing tests**

```python


# --- _sanitize_name tests -----------------------------------------------------


def test_sanitize_name_passes_simple_kebab():
    assert workshop._sanitize_name("cyberpunk-warm") == "cyberpunk-warm"


def test_sanitize_name_rejects_empty():
    with pytest.raises(ValueError):
        workshop._sanitize_name("")


def test_sanitize_name_rejects_uppercase():
    with pytest.raises(ValueError):
        workshop._sanitize_name("CyberpunkWarm")


def test_sanitize_name_rejects_spaces():
    with pytest.raises(ValueError):
        workshop._sanitize_name("cyber punk")


def test_sanitize_name_rejects_path_traversal():
    with pytest.raises(ValueError):
        workshop._sanitize_name("../escape")
    with pytest.raises(ValueError):
        workshop._sanitize_name("a/b")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_workshop.py -k sanitize -v`
Expected: FAIL with `AttributeError: module 'workshop' has no attribute '_sanitize_name'`

- [ ] **Step 3: Append to `workshop.py`**

```python
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _sanitize_name(name: str) -> str:
    """Validate a kebab-case preset name. Raises ValueError on invalid input."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(
            f"preset name {name!r} must be kebab-case "
            "([a-z0-9][a-z0-9-]*) — no spaces, uppercase, or path separators"
        )
    return name
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_workshop.py -v`
Expected: PASS (16 tests total)

- [ ] **Step 5: Commit**

```bash
git add workshop.py tests/test_workshop.py
git commit -m "feat: add _sanitize_name (kebab-case validator, path-traversal safe)"
```

---

### Task 4: `_run_preview` async loop (TDD with fake client)

**Files:**
- Modify: `workshop.py` (append)
- Modify: `tests/test_workshop.py` (append)

- [ ] **Step 1: Append the failing tests**

```python


# --- _run_preview tests -------------------------------------------------------

import asyncio


class _FakeClient:
    """Records every send_raw call as a list of (did, payload-dict) tuples."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def send_raw(self, d: dict, did: str | None = None):
        self.calls.append((did, d))


@pytest.mark.asyncio
async def test_run_preview_single_frame_publishes_once_then_idles():
    client = _FakeClient()
    preset = {"payload": {"d1": 1, "d2": 2, "d50": "N01:P10001FF0000;"}}
    task = asyncio.create_task(workshop._run_preview(preset, "abc", client))
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert len(client.calls) == 1
    did, payload = client.calls[0]
    assert did == "abc"
    assert payload["d50"] == "N01:P10001FF0000;"
    assert payload["d1"] == 1


@pytest.mark.asyncio
async def test_run_preview_multi_frame_cycles_with_duration():
    client = _FakeClient()
    preset = {
        "frame_duration_ms": 50,
        "frames": [
            {"d2": 2, "d50": "N01:P10001FF0000;"},
            {"d2": 2, "d50": "N01:P1000100FF00;"},
        ],
    }
    task = asyncio.create_task(workshop._run_preview(preset, "abc", client))
    await asyncio.sleep(0.25)  # ~5 frames at 50ms each
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    # Frames cycle: should have published at least both d50 strings
    d50s = {payload["d50"] for _, payload in client.calls}
    assert d50s == {"N01:P10001FF0000;", "N01:P1000100FF00;"}
    assert len(client.calls) >= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_workshop.py -k run_preview -v`
Expected: FAIL with `AttributeError: module 'workshop' has no attribute '_run_preview'`

- [ ] **Step 3: Append to `workshop.py`**

```python
_DEFAULT_FRAME_DURATION_MS = 2500


async def _run_preview(preset: dict, did: str, client) -> None:
    """Cycle the preset's frames on the lamp until cancelled.

    Single-frame presets publish once and then idle (we still loop with the
    default duration, but the publish is the same payload so it's harmless and
    keeps the loop shape uniform).
    """
    if "frames" in preset:
        frames = preset["frames"]
        dur_ms = preset.get("frame_duration_ms", _DEFAULT_FRAME_DURATION_MS)
    else:
        frames = [preset["payload"]]
        dur_ms = _DEFAULT_FRAME_DURATION_MS
    try:
        while True:
            for frame in frames:
                payload = {"d1": 1}
                payload.update({k: v for k, v in frame.items() if k != "duration_ms"})
                await client.send_raw(payload, did)
                await asyncio.sleep(dur_ms / 1000)
    except asyncio.CancelledError:
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_workshop.py -v`
Expected: PASS (18 tests total — 16 prior + 2 new). May add a few seconds of runtime due to the timing tests.

- [ ] **Step 5: Commit**

```bash
git add workshop.py tests/test_workshop.py
git commit -m "feat: add _run_preview async loop with fake-client tests"
```

---

### Task 5: Aiohttp app skeleton + GET routes

**Files:**
- Modify: `workshop.py` (append)

- [ ] **Step 1: Add the app, GET routes, and a minimal `main`**

Add to the imports at the top of `workshop.py` (alphabetically, after `import copy`):
```python
import json
import logging
import os
from pathlib import Path

import asyncio
from aiohttp import web

from lepro import LeproClient, LeproError, load_config
```

Replace the existing `import re` placement so the final import block is:
```python
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
from pathlib import Path

from aiohttp import web

from lepro import LeproClient, LeproError, load_config
```

Then append at the bottom of `workshop.py` (after `_run_preview`):
```python
logging.basicConfig(level=logging.INFO)
_LOG = logging.getLogger("workshop")

_HERE = Path(__file__).resolve().parent
_PRESETS_DIR = _HERE / "presets"

# Module-level singletons set during lifespan startup.
_client: LeproClient | None = None
_preview_task: asyncio.Task | None = None


def _load_preset(name: str) -> dict:
    """Load presets/<name>.json or raise FileNotFoundError."""
    _sanitize_name(name)
    path = _PRESETS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"preset {name!r} not found")
    return json.loads(path.read_text())


def _list_preset_names() -> list[str]:
    return sorted(p.stem for p in _PRESETS_DIR.glob("*.json"))


async def index(_req):
    return web.Response(text=_PAGE, content_type="text/html")


async def api_presets(_req):
    """Return [{name, frame_count, palette}] for every preset."""
    out = []
    for name in _list_preset_names():
        try:
            p = _load_preset(name)
        except Exception:  # noqa: BLE001
            continue
        frame_count = len(p["frames"]) if "frames" in p else 1
        out.append({"name": name,
                    "frame_count": frame_count,
                    "palette": extract_palette(p)})
    return web.json_response({"ok": True, "presets": out})


async def api_preset(req):
    name = req.match_info["name"]
    try:
        return web.json_response({"ok": True, "preset": _load_preset(name)})
    except (FileNotFoundError, ValueError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


# Tiny placeholder page so smoke tests in Task 7 don't 500. Real UI inlined in
# Task 8.
_PAGE = "<!doctype html><title>workshop</title><body>workshop loading...</body>"
```

- [ ] **Step 2: Add the app factory and main**

Append:
```python
async def _on_startup(app):
    global _client
    cfg = load_config()
    if not cfg["account"] or not cfg["password"]:
        raise SystemExit("Missing credentials: create config.json or set LEPRO_ACCOUNT/LEPRO_PASSWORD.")
    _client = LeproClient(cfg["account"], cfg["password"], cfg["region"])
    await _client.login()
    await _client.connect_mqtt()
    app["listener"] = asyncio.create_task(_client.listen_forever())
    host = os.environ.get("LEPRO_WORKSHOP_HOST", "0.0.0.0")
    port = int(os.environ.get("LEPRO_WORKSHOP_PORT", "8081"))
    _LOG.info("workshop ready on http://%s:%s (LAN-only; no auth)", host, port)


async def _on_cleanup(app):
    global _preview_task, _client
    if _preview_task and not _preview_task.done():
        _preview_task.cancel()
        try:
            await _preview_task
        except asyncio.CancelledError:
            pass
    app["listener"].cancel()
    if _client is not None:
        await _client.close()
    _client = None
    _preview_task = None


def build_app() -> web.Application:
    app = web.Application()
    app.add_routes([
        web.get("/", index),
        web.get("/api/presets", api_presets),
        web.get(r"/api/presets/{name}", api_preset),
    ])
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


def main() -> None:
    host = os.environ.get("LEPRO_WORKSHOP_HOST", "0.0.0.0")
    port = int(os.environ.get("LEPRO_WORKSHOP_PORT", "8081"))
    web.run_app(build_app(), host=host, port=port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify the app builds and lists presets**

Run: `.venv/bin/python -c "
import workshop
app = workshop.build_app()
print('routes:', sorted(r.method + ' ' + str(r.resource.canonical) for r in app.router.routes()))
print('presets:', workshop._list_preset_names())
print('palette of cyberpunk:', workshop.extract_palette(workshop._load_preset('cyberpunk'))[:5])
"`
Expected: prints the route list (`GET /`, `GET /api/presets`, `GET /api/presets/{name}`), a list including at least `christmas`, `cyberpunk`, `hulk`, `mars_colors`, `purple-pink-tour`, `white-blue-tour`, and ~5 hex strings.

- [ ] **Step 4: Verify full repo suite still passes**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 5: Commit**

```bash
git add workshop.py
git commit -m "feat: workshop app factory + GET /api/presets routes"
```

---

### Task 6: POST `/api/preview`, `/api/stop`, `/api/save`

**Files:**
- Modify: `workshop.py` (add three POST handlers + wire them up)

- [ ] **Step 1: Add the POST handlers**

Append to `workshop.py` (above `_PAGE`):
```python
async def api_preview(req):
    global _preview_task
    try:
        body = await req.json()
        base_name = body["base_name"]
        color_map = body.get("color_map") or {}
        preset = _load_preset(base_name)
        recolored = apply_color_map(preset, color_map)
        did = _client._dev(None).did
        # Cancel any in-progress preview before starting the new one.
        if _preview_task and not _preview_task.done():
            _preview_task.cancel()
            try:
                await _preview_task
            except asyncio.CancelledError:
                pass
        _preview_task = asyncio.create_task(_run_preview(recolored, did, _client))
        return web.json_response({"ok": True})
    except (LeproError, ValueError, KeyError, FileNotFoundError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def api_stop(_req):
    global _preview_task
    if _preview_task and not _preview_task.done():
        _preview_task.cancel()
        try:
            await _preview_task
        except asyncio.CancelledError:
            pass
    _preview_task = None
    return web.json_response({"ok": True})


async def api_save(req):
    try:
        body = await req.json()
        new_name = _sanitize_name(body["new_name"])
        base_name = body["base_name"]
        color_map = body.get("color_map") or {}
        path = _PRESETS_DIR / f"{new_name}.json"
        if path.exists():
            return web.json_response(
                {"ok": False,
                 "error": f"preset {new_name!r} already exists; pick a unique name"},
                status=400)
        base = _load_preset(base_name)
        recolored = apply_color_map(base, color_map)
        recolored["name"] = new_name
        recolored["prompt"] = f"{base_name} recolored via workshop"
        from datetime import date
        recolored["captured"] = date.today().isoformat()
        path.write_text(json.dumps(recolored, indent=2) + "\n")
        return web.json_response({"ok": True, "path": str(path.relative_to(_HERE))})
    except (ValueError, KeyError, FileNotFoundError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
```

- [ ] **Step 2: Wire them into `build_app`**

In `build_app`, replace the three-route `add_routes` call with:
```python
    app.add_routes([
        web.get("/", index),
        web.get("/api/presets", api_presets),
        web.get(r"/api/presets/{name}", api_preset),
        web.post("/api/preview", api_preview),
        web.post("/api/stop", api_stop),
        web.post("/api/save", api_save),
    ])
```

- [ ] **Step 3: Smoke-test the app structure (no network)**

Run: `.venv/bin/python -c "
import workshop
app = workshop.build_app()
methods_paths = sorted(r.method + ' ' + str(r.resource.canonical) for r in app.router.routes())
expected = sorted([
    'GET /', 'HEAD /', 'GET /api/presets', 'HEAD /api/presets',
    'GET /api/presets/{name}', 'HEAD /api/presets/{name}',
    'POST /api/preview', 'POST /api/stop', 'POST /api/save',
])
assert methods_paths == expected, (methods_paths, expected)
print('all 6 routes registered correctly')
"`
Expected: prints `all 6 routes registered correctly`. If it raises an AssertionError, the new POSTs aren't wired up; re-check the `add_routes` call.

- [ ] **Step 4: Verify full repo suite still passes**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 5: Commit**

```bash
git add workshop.py
git commit -m "feat: workshop POST /api/preview, /api/stop, /api/save"
```

---

### Task 7: Inline HTML / CSS / JS — the frontend

**Files:**
- Modify: `workshop.py` (replace `_PAGE` with the real UI)

- [ ] **Step 1: Replace the placeholder `_PAGE` constant**

Find the existing line:
```python
_PAGE = "<!doctype html><title>workshop</title><body>workshop loading...</body>"
```

Replace with the full HTML page. **Note for the engineer:** this is a single long triple-quoted string. Take care with the f-string-style brace escaping — there is none here; this is a plain raw string, no `.format()` is called on it.

```python
_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lepro Preset Workshop</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { font: 15px/1.4 system-ui, sans-serif; margin: 0;
         background: #111; color: #eee; min-height: 100vh; }
  .wrap { display: grid; grid-template-columns: 320px 1fr; gap: 24px;
          padding: 20px; max-width: 1100px; margin: 0 auto; }
  @media (max-width: 760px) { .wrap { grid-template-columns: 1fr; } }
  h1 { font-size: 18px; margin: 0 0 16px; color: #5fd9d9; }
  h2 { font-size: 14px; margin: 16px 0 8px; color: #aaa;
       text-transform: uppercase; letter-spacing: 0.08em; }
  .card { background: #1c1c1f; padding: 16px; border-radius: 14px;
          box-shadow: 0 4px 16px rgba(0,0,0,.4); }
  .preset-row { display: flex; align-items: center; gap: 8px;
                padding: 10px 12px; border-radius: 10px; cursor: pointer;
                border: 1px solid transparent; }
  .preset-row:hover { background: #232328; }
  .preset-row.selected { border-color: #5fd9d9; background: #1f2a2a; }
  .preset-name { flex: 1; font-weight: 600; }
  .preset-meta { font-size: 12px; color: #888; }
  .palette-row { display: flex; gap: 4px; margin-top: 4px; }
  .palette-dot { width: 14px; height: 14px; border-radius: 50%;
                 border: 1px solid #333; }
  .swatch-row { display: flex; flex-wrap: wrap; gap: 12px; margin: 8px 0 16px; }
  .swatch { display: flex; flex-direction: column; align-items: center; gap: 4px; }
  .swatch input[type=color] { width: 48px; height: 48px; border: 2px solid #444;
                              border-radius: 50%; cursor: pointer; background: none; }
  .swatch-orig { font-size: 11px; color: #888; font-family: ui-monospace, monospace; }
  label { display: block; font-size: 12px; color: #aaa; margin: 12px 0 4px;
          text-transform: uppercase; letter-spacing: 0.08em; }
  input[type=text] { width: 100%; padding: 10px 12px; border-radius: 8px;
                     background: #2a2a30; color: #eee; border: 1px solid #333;
                     font: inherit; }
  .slider-row { display: flex; align-items: center; gap: 10px; margin: 6px 0; }
  .slider-row span { width: 88px; font-size: 12px; color: #aaa; }
  input[type=range] { flex: 1; }
  input[type=range]:disabled { opacity: .35; cursor: not-allowed; }
  .soon { font-size: 11px; color: #888; }
  .btns { display: flex; gap: 8px; margin-top: 16px; flex-wrap: wrap; }
  button { padding: 10px 16px; border-radius: 10px; border: 0;
           font: inherit; font-weight: 600; cursor: pointer;
           background: #2a2a30; color: #eee; }
  button.primary { background: #5fd9d9; color: #111; }
  button:disabled { opacity: .4; cursor: not-allowed; }
  #status { font-size: 12px; color: #777; margin-top: 12px; min-height: 1.2em; }
  .empty { color: #888; font-style: italic; padding: 20px; text-align: center; }
</style></head>
<body><div class="wrap">
  <div class="card">
    <h1>← Workshop</h1>
    <h2>Preset library</h2>
    <div id="preset-list"></div>
  </div>
  <div class="card" id="editor">
    <div class="empty">Pick an animation on the left to start.</div>
  </div>
</div>
<script type="module">
const $ = s => document.querySelector(s);
const list = $('#preset-list');
const editor = $('#editor');
let state = { presets: [], selected: null, base: null, palette: [], colorMap: {} };

async function api(path, body) {
  const r = await fetch(path, body ? {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  } : {});
  return r.json();
}

function dot(hex) { return `<span class="palette-dot" style="background:#${hex}"></span>`; }

async function loadPresets() {
  const j = await api('/api/presets');
  state.presets = j.presets || [];
  list.innerHTML = state.presets.map(p => `
    <div class="preset-row" data-name="${p.name}">
      <div>
        <div class="preset-name">${p.name}</div>
        <div class="preset-meta">${p.frame_count} frame${p.frame_count===1?'':'s'}</div>
        <div class="palette-row">${p.palette.slice(0,8).map(dot).join('')}</div>
      </div>
    </div>
  `).join('');
  for (const row of list.querySelectorAll('.preset-row')) {
    row.onclick = () => selectPreset(row.dataset.name);
  }
  // After save, re-select the new preset if it's now in the list
  if (state.selected && state.presets.find(p => p.name === state.selected)) {
    list.querySelector(`[data-name="${state.selected}"]`)?.classList.add('selected');
  }
}

async function selectPreset(name) {
  state.selected = name;
  for (const row of list.querySelectorAll('.preset-row')) {
    row.classList.toggle('selected', row.dataset.name === name);
  }
  const j = await api(`/api/presets/${name}`);
  if (!j.ok) { editor.innerHTML = `<div class="empty">error: ${j.error}</div>`; return; }
  state.base = j.preset;
  state.palette = state.presets.find(p => p.name === name).palette;
  state.colorMap = Object.fromEntries(state.palette.map(c => [c, c]));
  renderEditor();
}

function renderEditor() {
  const frames = state.base.frames ? state.base.frames.length : 1;
  editor.innerHTML = `
    <h1>${state.selected}</h1>
    <div class="preset-meta">${frames} frame${frames===1?'':'s'}</div>
    <label>Variant name</label>
    <input type="text" id="vname" value="${state.selected}-recolored">
    <label>Color combo (${state.palette.length})</label>
    <div class="swatch-row">
      ${state.palette.map((orig,i)=>`
        <div class="swatch">
          <input type="color" data-orig="${orig}" value="#${orig}">
          <span class="swatch-orig">#${orig}</span>
        </div>
      `).join('')}
    </div>
    <label>Speed</label>
    <div class="slider-row">
      <input type="range" disabled value="50">
      <span class="soon">🚫 decode pending</span>
    </div>
    <label>Brightness</label>
    <div class="slider-row">
      <input type="range" disabled value="100">
      <span class="soon">🚫 decode pending</span>
    </div>
    <div class="btns">
      <button class="primary" id="preview">▶ Preview</button>
      <button id="stop">■ Stop</button>
      <button id="save">💾 Save</button>
    </div>
    <div id="status"></div>
  `;
  for (const inp of editor.querySelectorAll('input[type=color]')) {
    inp.oninput = () => {
      state.colorMap[inp.dataset.orig] = inp.value.replace('#','').toUpperCase();
    };
  }
  $('#preview').onclick = doPreview;
  $('#stop').onclick = doStop;
  $('#save').onclick = doSave;
}

async function doPreview() {
  const j = await api('/api/preview', {base_name: state.selected, color_map: state.colorMap});
  $('#status').textContent = j.ok ? 'preview running…' : 'error: ' + j.error;
}
async function doStop() {
  const j = await api('/api/stop', {});
  $('#status').textContent = j.ok ? 'stopped' : 'error: ' + j.error;
}
async function doSave() {
  const name = $('#vname').value.trim();
  if (!name) { $('#status').textContent = 'name required'; return; }
  const j = await api('/api/save', {new_name: name, base_name: state.selected, color_map: state.colorMap});
  if (!j.ok) { $('#status').textContent = 'error: ' + j.error; return; }
  $('#status').textContent = 'saved → ' + j.path;
  state.selected = name;
  await loadPresets();
  await selectPreset(name);
}

loadPresets();
</script></body></html>"""
```

- [ ] **Step 2: Verify the page contains the expected structure**

Run: `.venv/bin/python -c "
import workshop
p = workshop._PAGE
for marker in ('Lepro Preset Workshop', 'preset-list', 'Variant name', 'Speed',
               'Brightness', '▶ Preview', '■ Stop', '💾 Save'):
    assert marker in p, 'missing ' + repr(marker)
print('page OK')
"`
Expected: prints `page OK`.

- [ ] **Step 3: Verify the suite still passes**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 4: Commit**

```bash
git add workshop.py
git commit -m "feat: workshop frontend (preset library + variant editor UI)"
```

---

### Task 8: README + final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the `## Preset workshop` section**

Read `README.md`, find the existing `## Stock tracker` section and the `## Protocol notes` section. Insert this new section between them:

```markdown
## Preset workshop

A web UI for browsing the captured preset library, recoloring a chosen preset
via a color-combo picker, naming the variant, previewing it on the lamp, and
saving the result as a new `presets/*.json`.

```bash
.venv/bin/python workshop.py        # serves on 0.0.0.0:8081
```

Open `http://<vm-ip>:8081` in a browser. Left column lists every preset with a
palette preview; click one to load it as the base. Right column has a Variant
name input, a Color Combo (N round swatches matching the base's distinct
palette colors — click each to pick a new hex), and disabled Speed / Brightness
sliders (decode pending — see `D50_FORMAT.md`). Preview pushes the recolored
animation to the lamp live; Save writes a new file under `presets/`.

LAN-only — no auth. Override the bind address with `LEPRO_WORKSHOP_HOST` /
`LEPRO_WORKSHOP_PORT`. Coexists with `app.py` (8080) and the MCP server (8765).
```

- [ ] **Step 2: Verify the section landed cleanly**

Run: `grep -n "^## " README.md`
Expected: shows `## Preset workshop` between `## Stock tracker` and `## Protocol notes`.

- [ ] **Step 3: Final full-suite run**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 4: Final app build smoke**

Run: `.venv/bin/python -c "import workshop; app = workshop.build_app(); print('routes:', len(list(app.router.routes()))); print('build ok')"`
Expected: prints a non-zero route count and `build ok`.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document the preset workshop"
```

---

## Self-Review

**Spec coverage:**
- `workshop.py` sibling script → Tasks 1-7 ✓
- Persistent `LeproClient` + `_preview_task` singleton → Task 5 + Task 6 ✓
- Default `0.0.0.0:8081`, env overrides → Task 5 ✓
- `GET /` (page), `GET /api/presets`, `GET /api/presets/{name}` → Task 5 ✓
- `POST /api/preview`, `POST /api/stop`, `POST /api/save` → Task 6 ✓
- Pure functions `extract_palette`, `apply_color_map`, `_sanitize_name` → Tasks 1-3 ✓
- `_run_preview` async loop with fake-client tests → Task 4 ✓
- Save flow: sanitize name, refuse overwrites, write JSON → Task 6 ✓
- Two-column responsive layout, color-combo picker, variant name, disabled sliders, Preview/Stop/Save buttons → Task 7 ✓
- README documentation → Task 8 ✓
- No new dependencies — uses existing aiohttp + LeproClient ✓
- LAN-only, no auth, documented at startup → Task 5 (banner) + Task 8 (README) ✓

**Placeholder scan:** no TBD / TODO / fill-in. Every step has the full code or the full command with expected output.

**Type consistency:** `extract_palette(preset) -> list[str]`, `apply_color_map(preset, color_map) -> dict`, `_sanitize_name(name) -> str`, `_load_preset(name) -> dict`, `_run_preview(preset, did, client) -> None`, `_preview_task: asyncio.Task | None`, `_client: LeproClient | None` — all used consistently across tasks. The `_FakeClient.send_raw(d, did=None)` signature matches the real `LeproClient.send_raw(d, did=None)`.

**Note for implementer:** Task 4's `test_run_preview_multi_frame_cycles_with_duration` uses real timing (`asyncio.sleep(0.25)` with `frame_duration_ms=50`). On a very slow machine this may flake. If so, bump the outer sleep to `0.5`; do not weaken the assertions. The single-frame test waits only `0.1`s and assertion is `len == 1`, which is robust.
