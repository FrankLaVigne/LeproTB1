#!/usr/bin/env python3
"""Workshop — web UI for browsing, recoloring, previewing, and saving presets.

Sibling to app.py. Defaults to 0.0.0.0:8081.
"""

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

# Match P1000<count><colors>. Count is a single decimal digit (1-9 verified;
# REVERSE_ENGINEERING.md caps at 9). Colors are distinct 6-hex RGB tuples.
_P1000_RE = re.compile(r"P1000(\d)((?:[0-9A-Fa-f]{6})+)")

# Match P4000<count><hex>. D50_FORMAT.md: "fixed-pattern shortcut where the
# palette is one color used N times." One 6-hex color repeated count times.
_P4000_RE = re.compile(r"P4000(\d)((?:[0-9A-Fa-f]{6})+)")


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
        for m in _P1000_RE.finditer(d50):
            count = int(m.group(1))
            hex_run = m.group(2)
            for i in range(count):
                color = hex_run[i * 6:(i + 1) * 6].upper()
                if color not in seen:
                    seen[color] = None
        for m in _P4000_RE.finditer(d50):
            # P4000 encodes one color repeated N times; extract only the first.
            color = m.group(2)[:6].upper()
            if color not in seen:
                seen[color] = None
    return list(seen.keys())


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
    for holder in _holders(out):
        d50 = holder["d50"]
        for old, new in norm.items():
            # Replace BOTH the uppercase and the lowercase form, emitting uppercase
            d50 = d50.replace(old, new).replace(old.lower(), new)
        holder["d50"] = d50
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


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _sanitize_name(name: str) -> str:
    """Validate a kebab-case preset name. Raises ValueError on invalid input."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(
            f"preset name {name!r} must be kebab-case "
            "([a-z0-9][a-z0-9-]*) — no spaces, uppercase, or path separators"
        )
    return name


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


# Tiny placeholder page so smoke tests don't 500. Real UI inlined in Task 7.
_PAGE = "<!doctype html><title>workshop</title><body>workshop loading...</body>"


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
        web.post("/api/preview", api_preview),
        web.post("/api/stop", api_stop),
        web.post("/api/save", api_save),
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
