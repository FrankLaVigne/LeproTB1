#!/usr/bin/env python3
"""Workshop — web UI for browsing, recoloring, previewing, and saving presets.

Sibling to app.py. Defaults to 0.0.0.0:8081.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
import re
from pathlib import Path

from aiohttp import web

from lepro import LeproClient, LeproError, load_config
from web import lampview

# Match P1000<count><colors>. Count is a single decimal digit (1-9 verified;
# docs/REVERSE_ENGINEERING.md caps at 9). Colors are distinct 6-hex RGB tuples.
_P1000_RE = re.compile(r"P1000(\d)((?:[0-9A-Fa-f]{6})+)")

# Match P4000<count><hex>. docs/D50_FORMAT.md: "fixed-pattern shortcut where the
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


def _recolor_preset(preset: dict, old_palette: list, new_palette: list) -> dict:
    """Return a deep copy of ``preset`` with palette colors mapped old → new.

    Only the P1000<N><colors> section of each frame's d50 is modified;
    motion/length/effect fields are untouched. The mapping is positional:
    ``old_palette[i]`` → ``new_palette[i]``.
    """
    out = copy.deepcopy(preset)
    if len(old_palette) != len(new_palette):
        raise ValueError("palette length mismatch")
    mapping = {old.upper(): new.upper() for old, new in zip(old_palette, new_palette)}

    def remap_d50(d50: str) -> str:
        if not d50:
            return d50
        m = re.search(r"P1000(\d)([0-9A-Fa-f]+)", d50)
        if not m:
            return d50
        n = int(m.group(1))
        head = d50[:m.end(1)]   # everything up to and including the count digit
        original = m.group(2)
        block = original[:n * 6]
        tail = d50[m.end(1) + len(block):]
        # Map each 6-hex slot through the mapping.
        new_block = ""
        for i in range(n):
            slot = block[i * 6:i * 6 + 6].upper()
            new_block += mapping.get(slot, slot)
        return head + new_block + tail

    if "frames" in out:
        for f in out.get("frames") or []:
            f["d50"] = remap_d50(f.get("d50", ""))
    elif "payload" in out:
        out["payload"]["d50"] = remap_d50(out["payload"].get("d50", ""))
    return out


_DEFAULT_FRAME_DURATION_MS = 2500

# Segment → LED-range mapping for the DIY canvas's 48-mode display.
# Outer ring: 22 segments × 4 LEDs each = 88 LEDs.
# Middle ring: 13 segments × 4 LEDs + 2 segments × 5 LEDs = 62 LEDs.
# Inner ring:  9 segments × 4 LEDs + 2 segments × 5 LEDs = 46 LEDs.
# 5-LED segments are placed at the end of variable-count rings.
_OUTER_SEGMENTS = [(i * 4, i * 4 + 4) for i in range(22)]
_MIDDLE_SEGMENTS = (
    [(88 + i * 4, 88 + i * 4 + 4) for i in range(13)]
    + [(140, 145), (145, 150)]
)
_INNER_SEGMENTS = (
    [(150 + i * 4, 150 + i * 4 + 4) for i in range(9)]
    + [(186, 191), (191, 196)]
)
_RING_SEGMENTS = {
    "outer": _OUTER_SEGMENTS,
    "middle": _MIDDLE_SEGMENTS,
    "inner": _INNER_SEGMENTS,
}


# Page-to-physical rotation (see docs/CALIBRATION.md). The DIY canvas draws each
# ring's segment 0 at 12 o'clock, but the LED strip enters each ring at a
# different physical angle (outer/middle ~8 o'clock, inner ~11 o'clock).
# Applied at the page↔protocol boundary in api_diy_paint and api_diy_save.
# Tune these by ±1-3 if markers look off-center on the lamp.
_OUTER_ROTATION = 31
_MIDDLE_ROTATION = 22
_INNER_ROTATION = 4


def apply_lamp_rotation(page_leds: list) -> list:
    """Rotate a 196-LED page-space array into the lamp's physical orientation.

    The user paints colors into a page-model array where index 0 of each ring
    sits at the top of the on-screen canvas. The physical lamp wires its strip
    so each ring's LED 0 lands somewhere else (outer/middle ~8 o'clock, inner
    ~11). This rotates each ring independently so the painted top lands at the
    physical top. Raises ValueError if the input isn't exactly 196 entries.
    """
    if len(page_leds) != 196:
        raise ValueError(f"expected 196 LEDs, got {len(page_leds)}")
    physical = [None] * 196
    for i in range(88):
        physical[(i + _OUTER_ROTATION) % 88] = page_leds[i]
    for k in range(62):
        physical[88 + (k + _MIDDLE_ROTATION) % 62] = page_leds[88 + k]
    for k in range(46):
        physical[150 + (k + _INNER_ROTATION) % 46] = page_leds[150 + k]
    return physical


def segments_to_leds(ring: str, segment_idx: int) -> range:
    """Return the range of LED indices for a given (ring, segment) pair.

    Used by the DIY canvas in 48-mode to translate a clicked arc into the
    underlying LED indices to paint. Raises ValueError for unknown ring names
    and IndexError for out-of-range segment indices.
    """
    if ring not in _RING_SEGMENTS:
        raise ValueError(f"unknown ring {ring!r}; expected 'outer', 'middle', or 'inner'")
    segments = _RING_SEGMENTS[ring]
    if not 0 <= segment_idx < len(segments):
        raise IndexError(f"{ring} segment index {segment_idx} out of range (0..{len(segments) - 1})")
    start, stop = segments[segment_idx]
    return range(start, stop)


def _speed_to_hex(speed: int) -> str:
    """Encode a 0-100 speed into the 4-char hex slot the d50 effects expect.

    Re-uses the reference integration's log-scale formula
    (also lives in lepro.py as _speed_to_hex):
       raw = round(-117.41 * ln(speed + 1) + 597.75)
       returns "0XXX" (4 hex chars; high byte 0)
    Speed 0 is the special "1000" sentinel.
    """
    s = max(0, min(100, int(speed)))
    if s <= 0:
        return "1000"
    raw = int(round(-117.41 * math.log(s + 1) + 597.75))
    return f"0{raw:03X}"


def effect_tail(name: str, speed: int) -> str:
    """Compose the d50 effect tail for one of the six confirmed effects.

    Raises ValueError on unknown effect names.
    """
    sp = _speed_to_hex(speed)
    if name == "Steady":
        return "000640000E1"
    if name == "Breathe":
        return f"000640000E4{sp}0000{sp}1664"
    if name == "Gradient":
        return f"100640000E3{sp}C2O6{sp}"
    if name == "Leftward":
        return f"00164{sp}E1"
    if name == "Rightward":
        return f"00264{sp}E1"
    if name == "Circle":
        return f"100640000E1C2O6{sp}"
    raise ValueError(f"unknown effect {name!r}; expected one of "
                     "Steady, Breathe, Gradient, Leftward, Rightward, Circle")


def build_d50_from_leds(leds: list[str | None], effect: str, speed: int) -> str:
    """Compose a full d50 string from a 196-LED array + effect + speed.

    None values are treated as the color "000000" (off). Duplicate palette
    entries are emitted as-is (verified to work by experiment 2026-05-29 —
    see docs/D50_FORMAT.md). The output is uppercase normalized to match what the
    Lepro app emits.
    """
    if len(leds) != 196:
        raise ValueError(f"leds must have exactly 196 entries, got {len(leds)}")

    # 1. Normalize: None -> "000000", uppercase everything else.
    norm = ["000000" if c is None else c.upper() for c in leds]

    # 2. Compress consecutive same-color LEDs into (color, length) runs.
    runs: list[tuple[str, int]] = []
    for color in norm:
        if runs and runs[-1][0] == color:
            runs[-1] = (color, runs[-1][1] + 1)
        else:
            runs.append((color, 1))

    # 3. Build palette (in first-appearance order, duplicates allowed) +
    #    lengths string.
    colors = "".join(c for c, _ in runs)
    lengths = "".join(f"{n:04X}" for _, n in runs)
    n_groups = len(runs)

    # 4. Compose with effect tail.
    tail = effect_tail(effect, speed)
    return f"N01:P1000{n_groups}{colors}F21000{n_groups}{lengths}U3V3{tail};"


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


async def _stop_preview() -> None:
    """Cancel and clear the preset preview loop, if running. Safe to call
    when no preview is active. Used by power-off, ticker-start, clock-start,
    and DIY paint to prevent the preview's per-frame writes from clobbering
    the new state."""
    global _preview_task, _preview_name
    task = _preview_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _preview_task = None
    _preview_name = None


async def _stop_capture() -> None:
    """Cancel and clear any in-flight capture session. Safe to call when
    no capture is active. Used by power-off so the capture doesn't keep
    polling after the lamp is turned off."""
    global _capture_session
    sess = _capture_session
    if sess is not None and sess.running:
        await sess.stop()
    _capture_session = None


logging.basicConfig(level=logging.INFO)
_LOG = logging.getLogger("workshop")

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent  # repo root, parent of the web/ package
_PRESETS_DIR = _PROJECT_ROOT / "presets"

# --- Animations tab — see docs/superpowers/specs/2026-06-01-animations-tab-design.md
from web import animations as _animations_mod
from web import captures as _captures_mod

_capture_session = None  # type: ignore[assignment]

_ANIMATIONS_OVERRIDES_PATH = _PROJECT_ROOT / "animations.json"


def _read_animation_overrides() -> dict:
    """Read animations.json if it exists, return {} otherwise."""
    if not _ANIMATIONS_OVERRIDES_PATH.exists():
        return {}
    try:
        return json.loads(_ANIMATIONS_OVERRIDES_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_animation_overrides(d: dict) -> None:
    _ANIMATIONS_OVERRIDES_PATH.write_text(json.dumps(d, indent=2) + "\n")


def _grouped_animations() -> list:
    """Return the override-applied list of Animation groups."""
    groups = _animations_mod.group_presets(_PRESETS_DIR)
    return _animations_mod.apply_overrides(groups, _read_animation_overrides())


def _animation_to_json(anim) -> dict:
    return {
        "id": anim.id,
        "name": anim.name,
        "default_palette": anim.default_palette,
        "members": [
            {"name": m.name, "palette": m.palette, "frame_stats": m.frame_stats}
            for m in anim.members
        ],
    }


async def index_animations(_req):
    return web.Response(text=_render_shell("animations", _PANEL_ANIMATIONS, "Animations"),
                        content_type="text/html")


async def api_captures_start(_req):
    """Start a new capture session. 409 if any other lamp-driver is running."""
    global _capture_session
    # Mutex with ticker / clock / preview / existing capture.
    if _ticker_session is not None and _ticker_session.running:
        return web.json_response(
            {"ok": False, "error": "stock ticker is running; stop it first"},
            status=409)
    if _clock_session is not None and _clock_session.running:
        return web.json_response(
            {"ok": False, "error": "clock is running; stop it first"},
            status=409)
    if _preview_task is not None and not _preview_task.done():
        return web.json_response(
            {"ok": False, "error": "preset preview is running; stop it first"},
            status=409)
    if _capture_session is not None and _capture_session.running:
        return web.json_response(
            {"ok": False, "error": "a capture is already in progress"},
            status=409)
    # If the previous capture auto-stopped but the user hasn't saved or
    # cancelled it yet, refuse to overwrite the unsaved frames.
    if _capture_session is not None and not _capture_session.running \
            and _capture_session.frame_count > 0:
        return web.json_response(
            {"ok": False, "error": "previous capture has unsaved frames; save or discard it first"},
            status=409)
    # Baseline: whatever the lamp's d50 is right now. Anything that DIFFERS
    # from this baseline becomes a recorded frame.
    baseline = None
    if _client is not None:
        for fields in _client.state.values():
            baseline = fields.get("d50")
            break
    def _existing_preset_names():
        try:
            return {p.stem for p in _PRESETS_DIR.glob("*.json")}
        except OSError:
            return set()
    sess = _captures_mod.CaptureSession(_client, baseline_d50=baseline,
                                         existing_names_provider=_existing_preset_names)
    await sess.start()
    _capture_session = sess
    return web.json_response({"ok": True, "started_at": sess.snapshot()["started_at"]})


async def api_captures_state(_req):
    """Return the active capture's snapshot, or a 'not running' shape."""
    if _capture_session is None:
        return web.json_response({
            "running": False, "started_at": None, "frame_count": 0,
            "auto_stop_at": None, "default_name": None,
        })
    return web.json_response(_capture_session.snapshot())


async def api_captures_cancel(_req):
    """Stop the capture without saving. Idempotent."""
    await _stop_capture()
    return web.json_response({"ok": True})


async def api_captures_save(req):
    """Stop the capture (if still running), build the preset, write it
    to presets/, return path + matched_animation info (or null)."""
    global _capture_session
    try:
        body = await req.json()
        raw_name = body.get("name")
        if raw_name is None:
            return web.json_response({"ok": False, "error": "name required"}, status=400)
        name = _sanitize_name(str(raw_name))
        sess = _capture_session
        if sess is None:
            return web.json_response({"ok": False, "error": "no capture in progress"}, status=400)
        # Stop the loop first so no more frames sneak in mid-save.
        if sess.running:
            await sess.stop()
        frames = sess.frames
        if not frames:
            _capture_session = None
            return web.json_response({"ok": False, "error": "no frames captured; nothing to save"}, status=400)
        out_path = _PRESETS_DIR / f"{name}.json"
        if out_path.exists():
            return web.json_response(
                {"ok": False, "error": f"preset {name!r} already exists; pick another name"},
                status=400)
        preset = _captures_mod.build_capture_preset(frames, name)
        out_path.write_text(json.dumps(preset, indent=2) + "\n")
        _capture_session = None
        # Look up whether the new preset matches an existing animation group.
        matched = None
        for anim in _grouped_animations():
            if any(m.name == name for m in anim.members) and len(anim.members) > 1:
                matched = {
                    "id": anim.id,
                    "name": anim.name,
                    "variant_count": len(anim.members),
                }
                break
        return web.json_response({
            "ok": True,
            "path": str(out_path.relative_to(_PROJECT_ROOT)),
            "matched_animation": matched,
        })
    except (KeyError, ValueError, TypeError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


# Replaced by the real UI in Task 6.
_PANEL_ANIMATIONS = """
<div class="capture-bar">
  <div class="capture-counter">
    <strong id="capture-count">—</strong> unique animations / ~72 target
  </div>
  <button id="capture-btn" class="capture-action">🎥 Capture</button>
</div>
<div id="capture-saveform" class="capture-saveform" style="display:none">
  <input type="text" id="capture-name" placeholder="capture-...">
  <button class="capture-action" id="capture-save">💾 Save</button>
  <button class="capture-action" id="capture-discard">Discard</button>
</div>
<div id="capture-notice" class="capture-notice"></div>

<div id="anim-list"></div>
<div id="anim-empty" style="display:none">
  No animations yet. Use the Capture button above (or
  <code>python -m cli.main capture --seconds 90</code>) and they'll appear
  here grouped by motion pattern.
</div>
<div id="anim-status"></div>

<script type="module">
const $ = s => document.querySelector(s);
const list = $('#anim-list');
const empty = $('#anim-empty');
const status = $('#anim-status');
const captureBtn = $('#capture-btn');
const captureCount = $('#capture-count');
const saveForm = $('#capture-saveform');
const nameInput = $('#capture-name');
const notice = $('#capture-notice');

const TARGET_TOTAL = 72;
let pollTimer = null;

function setStatus(msg, isError) {
  status.textContent = msg || '';
  status.style.color = isError ? 'var(--danger)' : '';
}

function setNotice(msg, cls) {
  notice.className = 'capture-notice' + (cls ? ' ' + cls : '');
  notice.textContent = msg || '';
}

async function postJSON(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

function paletteSwatches(palette) {
  return palette.map(c => `<span class="swatch" style="background:#${c}"></span>`).join('');
}

function buildRow(anim) {
  const variantCount = anim.members.length;
  const firstStats = (anim.members[0] || {}).frame_stats || {total: 0, unique: 0};
  const stats = firstStats.total > 1
    ? `${firstStats.total} frames (${firstStats.unique} unique)`
    : `${firstStats.total} frame`;

  const pickerInputs = anim.default_palette.map((c, i) =>
    `<input type="color" value="#${c}" data-idx="${i}">`).join('');
  const variantPills = anim.members.map(m =>
    `<span class="v">${m.name}</span>`).join('');

  const row = document.createElement('div');
  row.className = 'anim-row';
  row.dataset.id = anim.id;
  row.innerHTML = `
    <div class="anim-title" data-action="toggle">${anim.name}</div>
    <div class="anim-actions">
      <button data-action="play">▶ Play</button>
      <button data-action="toggle">✎ Edit</button>
    </div>
    <div class="anim-meta">
      <div class="anim-palette">${paletteSwatches(anim.default_palette)}</div>
      <span>${stats}</span>
      <span>·</span>
      <span>${variantCount} variant${variantCount === 1 ? '' : 's'}</span>
    </div>
    <div class="anim-expanded">
      <div>
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:4px">RENAME</div>
        <div class="anim-saverow">
          <input type="text" data-role="rename" value="${anim.name}">
          <button data-action="rename">Save name</button>
        </div>
      </div>
      <div>
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:4px">RECOLOR PALETTE</div>
        <div class="anim-pickers">${pickerInputs}</div>
      </div>
      <div>
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:4px">SAVE AS NEW PRESET</div>
        <div class="anim-saverow">
          <input type="text" data-role="save-name" placeholder="my-variant">
          <button data-action="save">💾 Save</button>
        </div>
      </div>
      <div>
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:4px">VARIANTS</div>
        <div class="anim-variants">${variantPills}</div>
      </div>
    </div>
  `;
  return row;
}

function attachHandlers(row, anim) {
  for (const el of row.querySelectorAll('[data-action="toggle"]')) {
    el.addEventListener('click', () => row.classList.toggle('expanded'));
  }
  row.querySelector('[data-action="play"]').addEventListener('click', async (e) => {
    e.stopPropagation();
    const sourceName = anim.members[0].name;
    const j = await postJSON('/api/preview', {base_name: sourceName});
    setStatus(j.ok === false ? ('error: ' + j.error) : `playing ${sourceName}…`, j.ok === false);
  });
  row.querySelector('[data-action="rename"]').addEventListener('click', async () => {
    const name = row.querySelector('[data-role="rename"]').value.trim();
    if (!name) { setStatus('name required', true); return; }
    const j = await postJSON(`/api/animations/${anim.id}/rename`, {name});
    if (j.ok === false) { setStatus('error: ' + j.error, true); return; }
    setStatus(`renamed to ${name}`);
    await loadAnimations();
  });
  row.querySelector('[data-action="save"]').addEventListener('click', async () => {
    const newName = row.querySelector('[data-role="save-name"]').value.trim();
    if (!newName) { setStatus('save name required', true); return; }
    const palette = Array.from(row.querySelectorAll('.anim-pickers input[type=color]'))
      .sort((a, b) => parseInt(a.dataset.idx, 10) - parseInt(b.dataset.idx, 10))
      .map(input => input.value.replace('#', '').toUpperCase());
    const j = await postJSON(`/api/animations/${anim.id}/save`,
                              {name: newName, palette});
    if (j.ok === false) { setStatus('error: ' + j.error, true); return; }
    setStatus(`saved → ${j.path}`);
    await loadAnimations();
  });
}

async function loadAnimations() {
  try {
    const r = await fetch('/api/animations');
    const j = await r.json();
    list.innerHTML = '';
    const items = j.animations || [];
    captureCount.textContent = items.length;
    if (items.length === 0) {
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';
    for (const anim of items) {
      const row = buildRow(anim);
      attachHandlers(row, anim);
      list.appendChild(row);
    }
  } catch (e) {
    setStatus('failed to load animations: ' + e.message, true);
  }
}

// --- capture flow ----------------------------------------------------------

function setCaptureButtonRunning(running, frameCount) {
  if (running) {
    captureBtn.classList.add('active');
    captureBtn.textContent = `Capturing... ${frameCount} frame${frameCount === 1 ? '' : 's'}`;
  } else {
    captureBtn.classList.remove('active');
    captureBtn.textContent = '🎥 Capture';
  }
}

async function startCapture() {
  setNotice('');
  saveForm.style.display = 'none';
  const j = await postJSON('/api/captures/start', {});
  if (j.ok === false) { setNotice('error: ' + j.error, 'err'); return; }
  setCaptureButtonRunning(true, 0);
  pollCaptureState();
}

async function pollCaptureState() {
  try {
    const r = await fetch('/api/captures/state');
    const j = await r.json();
    if (j.running) {
      setCaptureButtonRunning(true, j.frame_count);
      pollTimer = setTimeout(pollCaptureState, 500);
    } else {
      setCaptureButtonRunning(false, 0);
      if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
      // If a previous capture finished and we have frames + a default
      // name, surface the save form. Server side: state-not-running with
      // frame_count == 0 means there's nothing to save.
      if (j.frame_count > 0 && j.default_name) {
        nameInput.value = j.default_name;
        saveForm.style.display = 'flex';
      }
    }
  } catch (e) {
    setNotice('lost connection to server while polling capture state', 'err');
  }
}

async function submitSave() {
  const name = nameInput.value.trim();
  if (!name) { setNotice('name required', 'err'); return; }
  const j = await postJSON('/api/captures/save', {name});
  if (j.ok === false) { setNotice('error: ' + j.error, 'err'); return; }
  saveForm.style.display = 'none';
  if (j.matched_animation) {
    const m = j.matched_animation;
    setNotice(`Saved as ${name} → matches ${m.name} (now ${m.variant_count} variants)`, 'matched');
  } else {
    setNotice(`Saved as ${name} → new animation`, 'ok');
  }
  await loadAnimations();
}

async function cancelCapture() {
  await postJSON('/api/captures/cancel', {});
  setCaptureButtonRunning(false, 0);
  saveForm.style.display = 'none';
  setNotice('capture discarded');
}

captureBtn.addEventListener('click', () => {
  if (captureBtn.classList.contains('active')) {
    // Already capturing — treat as Save Now: stop polling, then check the
    // current state. If we have frames, surface the save form. If we have
    // zero frames (nothing to save), resume polling so the user isn't
    // stranded with a stuck button.
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    fetch('/api/captures/state').then(r => r.json()).then(j => {
      if (j.frame_count > 0 && j.default_name) {
        nameInput.value = j.default_name;
        saveForm.style.display = 'flex';
        setCaptureButtonRunning(false, 0);
      } else {
        // No frames yet — keep capturing; resume polling so the button
        // and counter stay live. User can still trigger an animation in
        // the Lepro app, or just wait for the idle/cap auto-stop.
        setNotice('No frames yet — trigger an animation in the Lepro app, or wait', '');
        pollCaptureState();
      }
    });
  } else {
    startCapture();
  }
});
$('#capture-save').addEventListener('click', submitSave);
$('#capture-discard').addEventListener('click', cancelCapture);

loadAnimations();
// On page mount, check if a capture is already in flight (so reloading
// the tab while capturing doesn't lose track of it).
pollCaptureState();
</script>
"""

# ---------------------------------------------------------------------------
# Cockpit shell — shared layout for every page. See
# docs/superpowers/specs/2026-05-30-web-ui-redesign.md.

_SHELL_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lepro &middot; {title}</title>
<link rel="stylesheet" href="/static/cockpit.css">
</head><body>
<div class="cockpit">

  <aside class="cockpit-left">
    <div class="brand-row">
      <div class="brand">LEPRO</div>
      <div class="device-id" id="device-id">&mdash;</div>
    </div>

    <div class="viz-wrap">
      <svg id="lamp-viz" width="240" height="240" viewBox="-200 -200 400 400" aria-label="lamp visualizer"></svg>
    </div>

    <div class="active-banner" id="active-banner">
      <div class="label">ACTIVE</div>
      <div class="value" id="active-banner-value">&mdash;</div>
    </div>

    <div class="power-btns">
      <button class="on"  id="pwr-on"  aria-label="Power on">&#x23FB; ON</button>
      <button class="off" id="pwr-off" aria-label="Power off">&#x23FB; OFF</button>
    </div>

    <div class="brightness">
      <div class="brightness-head">
        <span class="label">&#x2600; BRIGHTNESS</span>
        <span class="val" id="brightness-val">&mdash;</span>
      </div>
      <input type="range" id="brightness-slider" min="0" max="100" value="80">
    </div>

    <details class="diag">
      <summary>&#x25B8; DIAGNOSTICS</summary>
      <div class="diag-body" id="diag-body">
        <div>&mdash;</div><div class="v">&mdash;</div>
      </div>
    </details>
  </aside>

  <main class="cockpit-right">
    <nav class="tabs">
      <a href="/" {cls_presets}>&#x1F3A8; Presets</a>
      <a href="/diy" {cls_diy}>&#x270F;&#xFE0F; DIY</a>
      <a href="/ticker" {cls_ticker}>&#x1F4C8; Ticker</a>
      <a href="/clock" {cls_clock}>&#x23F0; Clock</a>
      <a href="/animations" {cls_animations}>&#x1F39E;&#xFE0F; Animations</a>
    </nav>
    <section class="panel">
{panel}
    </section>
  </main>

</div>
<script type="module" src="/static/cockpit.js"></script>
</body></html>
"""


def _render_shell(active: str, panel_html: str, title: str) -> str:
    """Wrap a per-feature panel string in the cockpit shell.

    ``active`` is one of "presets", "diy", "ticker", "clock" — used to mark
    the active tab. ``panel_html`` is dropped verbatim into the right-side
    panel slot (may contain its own <script type="module"> block).
    """
    active_classes = {
        "presets": "",
        "diy": "",
        "ticker": "",
        "clock": "",
        "animations": "",
    }
    if active not in active_classes:
        raise ValueError(f"unknown tab {active!r}; expected one of {list(active_classes)}")
    active_classes[active] = 'class="active"'
    return _SHELL_TEMPLATE.format(
        title=title,
        panel=panel_html,
        cls_presets=active_classes["presets"],
        cls_diy=active_classes["diy"],
        cls_ticker=active_classes["ticker"],
        cls_clock=active_classes["clock"],
        cls_animations=active_classes["animations"],
    )


# Module-level singletons set during lifespan startup.
_client: LeproClient | None = None
_preview_task: asyncio.Task | None = None
_preview_name: "str | None" = None  # set by api_preview; read by api_cockpit_active


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
    return web.Response(text=_render_shell("presets", _PANEL_PRESETS, "Presets"),
                        content_type="text/html")


async def index_diy(_req):
    return web.Response(text=_render_shell("diy", _PANEL_DIY, "DIY"),
                        content_type="text/html")


async def index_ticker(_req):
    return web.Response(text=_render_shell("ticker", _PANEL_TICKER, "Ticker"),
                        content_type="text/html")


_PANEL_TICKER = """
<style>
  /* Feature-specific styles for the Ticker panel.
     Generic page chrome lives in /static/cockpit.css.
     Classes prefixed .ticker- to avoid collisions with other panels. */
  .ticker-ring input[type=text] { width: 100%; padding: 10px 12px;
                                   border-radius: 8px; background: #2a2a30;
                                   color: #eee; border: 1px solid #333;
                                   font: inherit; text-transform: uppercase; }
  .ticker-ring input[type=text][readonly] { background: #1f1f23; color: #aaa; }
  .ticker-price { font: 600 22px ui-monospace, monospace; margin: 10px 0 2px; }
  .ticker-history { font: 12px ui-monospace, monospace; color: #999;
                    margin-top: 6px; white-space: nowrap; overflow-x: auto; }
  .ticker-intervals { display: flex; gap: 4px; background: #2a2a30;
                      padding: 4px; border-radius: 8px; margin-bottom: 12px; }
  .ticker-intervals button { flex: 1; padding: 6px 10px; border: 0;
                              border-radius: 6px; background: transparent;
                              color: #eee; cursor: pointer; font: inherit; }
  .ticker-intervals button.active { background: #5fd9d9; color: #111; font-weight: 700; }
  .ticker-intervals button:disabled { color: #555; cursor: not-allowed; }
  .ticker-controls { display: flex; gap: 8px; }
  .ticker-controls button { flex: 1; padding: 12px; border: 0; border-radius: 10px;
                             background: #2a2a30; color: #eee; cursor: pointer;
                             font: inherit; font-weight: 700; }
  .ticker-controls button.primary { background: #2c8f4f; color: #fff; }
  .ticker-controls button.danger { background: #8f2c2c; color: #fff; }
  .ticker-controls button:disabled { opacity: 0.4; cursor: not-allowed; }
  .ring-head { display: flex; justify-content: space-between; align-items: center;
               margin-bottom: 8px; }
  .ring-head h2 { font-size: 12px; margin: 0; color: #aaa;
                  text-transform: uppercase; letter-spacing: 0.08em; }
  .dot { width: 14px; height: 14px; border-radius: 50%;
         background: #333; border: 1px solid #444; }
  .meta { font-size: 12px; color: #888; }
</style>

  <div class="card ticker-ring" data-ring="outer">
    <div class="ring-head"><h2>Outer</h2><div class="dot"></div></div>
    <input type="text" placeholder="AAPL" maxlength="12">
    <div class="ticker-price">&mdash;</div>
    <div class="meta">no symbol</div>
    <div class="ticker-history"></div>
  </div>

  <div class="card ticker-ring" data-ring="middle">
    <div class="ring-head"><h2>Middle</h2><div class="dot"></div></div>
    <input type="text" placeholder="IBM" maxlength="12">
    <div class="ticker-price">&mdash;</div>
    <div class="meta">no symbol</div>
    <div class="ticker-history"></div>
  </div>

  <div class="card ticker-ring" data-ring="inner">
    <div class="ring-head"><h2>Inner</h2><div class="dot"></div></div>
    <input type="text" placeholder="SPY" maxlength="12">
    <div class="ticker-price">&mdash;</div>
    <div class="meta">no symbol</div>
    <div class="ticker-history"></div>
  </div>

  <div class="card">
    <h2 style="margin:0 0 8px;font-size:12px;color:#aaa;text-transform:uppercase;letter-spacing:.08em">Poll every</h2>
    <div class="ticker-intervals" id="intervals">
      <button data-interval="10">10s</button>
      <button data-interval="30" class="active">30s</button>
      <button data-interval="60">60s</button>
      <button data-interval="300">5m</button>
    </div>
    <div class="ticker-controls">
      <button class="primary" id="start-btn">Start</button>
      <button class="danger" id="stop-btn" disabled>Stop</button>
    </div>
    <div id="status">not running</div>
  </div>

<script type="module">
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));

const state = { interval: 30, running: false };

function setActiveInterval(v) {
  state.interval = v;
  for (const b of $$('#intervals button')) {
    b.classList.toggle('active', parseInt(b.dataset.interval, 10) === v);
  }
}

for (const b of $$('#intervals button')) {
  b.onclick = () => { if (!state.running) setActiveInterval(parseInt(b.dataset.interval, 10)); };
}

function setInputsReadonly(ro) {
  for (const inp of $$('.ticker-ring input[type=text]')) inp.readOnly = ro;
  for (const b of $$('#intervals button')) b.disabled = ro;
}

async function postJSON(path, body) {
  const r = await fetch(path, {method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify(body || {})});
  return r.json();
}

function dotColor(hex) {
  if (!hex || hex === '000000') return '#333';
  return '#' + hex;
}

function arrow(direction) {
  if (direction === 'up') return '\\u2191';
  if (direction === 'down') return '\\u2193';
  if (direction === 'error') return '!';
  return '\\u00b7';
}

function colorName(hex) {
  if (hex === '00FF00') return 'green';
  if (hex === 'FF0000') return 'red';
  if (hex === 'FFFF00') return 'yellow';
  if (hex === 'FFFFFF') return 'white';
  return 'off';
}

function timeAgo(iso) {
  if (!iso) return '';
  const sec = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (sec < 60) return sec + 's ago';
  return Math.floor(sec / 60) + 'm ago';
}

function renderRing(ring, data) {
  const card = $(`.ticker-ring[data-ring="${ring}"]`);
  const dot = card.querySelector('.dot');
  const price = card.querySelector('.ticker-price');
  const meta = card.querySelector('.meta');
  const history = card.querySelector('.ticker-history');
  const input = card.querySelector('input');

  if (!data) {
    dot.style.background = '#333';
    price.textContent = '\\u2014';
    meta.textContent = 'no symbol';
    history.textContent = '';
    return;
  }
  input.value = data.symbol;
  dot.style.background = dotColor(data.color);
  if (data.current_price !== null && data.current_price !== undefined) {
    price.textContent = '$' + data.current_price.toFixed(2);
  } else {
    price.textContent = '\\u2014';
  }
  const lastTick = data.recent_ticks && data.recent_ticks[0];
  const dir = lastTick ? lastTick.direction : '';
  const fast = data.is_fast ? ' \\u2022 \\u26a1 FAST' : '';
  meta.textContent = `${arrow(dir)} ${colorName(data.color)} \\u00b7 updated ${timeAgo(data.last_fetch_at)}${fast}`;
  history.textContent = (data.recent_ticks || []).slice(0, 5).map(t =>
    `${arrow(t.direction)}$${t.price.toFixed(2)} ${t.at.slice(11, 16)}`
  ).join(' \\u00b7 ');
}

function renderState(s) {
  state.running = s.running;
  setInputsReadonly(s.running);
  $('#start-btn').disabled = s.running;
  $('#stop-btn').disabled = !s.running;
  if (!s.running) {
    $('#status').textContent = 'not running';
    return;
  }
  if (s.interval) setActiveInterval(s.interval);
  for (const ring of ['outer', 'middle', 'inner']) {
    renderRing(ring, s.rings ? s.rings[ring] : null);
  }
  $('#status').textContent = `running since ${s.since ? s.since.slice(11, 16) : '?'}`;
}

async function refresh() {
  const j = await fetch('/api/ticker/state').then(r => r.json());
  renderState(j);
}

$('#start-btn').onclick = async () => {
  const body = {interval: state.interval};
  for (const card of $$('.ticker-ring')) {
    const sym = card.querySelector('input').value.trim();
    if (sym) body[card.dataset.ring] = sym;
  }
  if (!body.outer && !body.middle && !body.inner) {
    $('#status').textContent = 'enter at least one symbol';
    return;
  }
  $('#status').textContent = 'starting...';
  const j = await postJSON('/api/ticker/start', body);
  if (!j.ok) { $('#status').textContent = 'error: ' + j.error; return; }
  await refresh();
};
$('#stop-btn').onclick = async () => {
  await postJSON('/api/ticker/stop', {});
  await refresh();
};

refresh();
setInterval(refresh, 5000);
</script>
"""


# Real DIY UI inlined in Task 6.
_PANEL_DIY = """
<style>
  /* Feature-specific styles for the DIY panel.
     Generic page chrome lives in /static/cockpit.css.
     Classes prefixed .diy- to avoid collisions with other panels. */

  .diy-canvas { display: flex; justify-content: center; padding: 12px 0; }
  svg .seg { cursor: pointer; transition: opacity .1s; }
  svg .seg:hover { opacity: .7; }
  .diy-toolbar { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 14px; }
  .diy-toolbar button { padding: 8px 12px; border: 0; border-radius: 8px;
                        background: #2a2a30; color: #eee; cursor: pointer;
                        font: inherit; }
  .diy-toolbar button.active { background: #5fd9d9; color: #111; font-weight: 700; }
  .diy-toolbar .res { margin-left: auto; display: flex; gap: 2px;
                      background: #2a2a30; padding: 2px; border-radius: 8px; }
  .diy-toolbar .res button { padding: 6px 10px; background: transparent;
                              border-radius: 6px; }
  .diy-toolbar .res button.active { background: #5fd9d9; color: #111; }
  h2 { font-size: 12px; margin: 0 0 8px; color: #aaa;
       text-transform: uppercase; letter-spacing: 0.08em; }
  .diy-color-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .diy-color-row input[type=color] { width: 44px; height: 44px; border: 2px solid #444;
                                     border-radius: 50%; cursor: pointer; background: none; }
  .diy-swatch { width: 28px; height: 28px; border-radius: 50%;
                border: 2px solid #333; cursor: pointer; }
  .diy-swatch:hover { border-color: #5fd9d9; }
  .effect-grid { display: grid; grid-template-columns: repeat(3, 1fr);
                 gap: 8px; margin-bottom: 14px; }
  .effect-grid button { padding: 10px; border: 0; border-radius: 8px;
                        background: #2a2a30; color: #eee; cursor: pointer;
                        font: inherit; }
  .effect-grid button.active { background: #5fd9d9; color: #111; font-weight: 700; }
  .slider-row { display: flex; align-items: center; gap: 10px; margin: 8px 0; }
  .slider-row .icon { width: 22px; text-align: center; }
  .slider-row input[type=range] { flex: 1; }
  .slider-row .val { min-width: 38px; text-align: right;
                     font: 13px ui-monospace, monospace; color: #aaa; }
  label { display: block; font-size: 12px; color: #aaa; margin: 12px 0 4px;
          text-transform: uppercase; letter-spacing: 0.08em; }
  input[type=text] { width: 100%; padding: 10px 12px; border-radius: 8px;
                     background: #2a2a30; color: #eee; border: 1px solid #333;
                     font: inherit; }
  .diy-btns { display: flex; gap: 8px; margin-top: 12px; }
  .diy-btns button { flex: 1; padding: 10px; border: 0; border-radius: 10px;
                     background: #2a2a30; color: #eee; cursor: pointer;
                     font: inherit; font-weight: 600; }
  .diy-btns button.primary { background: #5fd9d9; color: #111; }
  #status { font-size: 12px; color: #777; margin-top: 8px; min-height: 1.2em; }
</style>

  <div class="card diy-canvas">
    <svg id="lamp" width="380" height="380" viewBox="-200 -200 400 400"></svg>
  </div>

  <div class="diy-toolbar">
    <button class="tool active" data-tool="draw">&#x270F;&#xFE0F; Draw</button>
    <button class="tool" data-tool="fill">&#x1FAA3; Fill</button>
    <button class="tool" data-tool="erase">&#x1F9FD; Erase</button>
    <button id="back-btn">&#x21A9; Back</button>
    <div class="res">
      <button class="res-btn active" data-res="48">48</button>
      <button class="res-btn" data-res="196">196</button>
    </div>
  </div>

  <div class="card">
    <h2>Color</h2>
    <div class="diy-color-row">
      <input type="color" id="picker" value="#ff8000">
      <div class="diy-swatch" style="background:#FF0000" data-hex="FF0000"></div>
      <div class="diy-swatch" style="background:#FF8000" data-hex="FF8000"></div>
      <div class="diy-swatch" style="background:#FFFF00" data-hex="FFFF00"></div>
      <div class="diy-swatch" style="background:#00C000" data-hex="00C000"></div>
      <div class="diy-swatch" style="background:#00FFFF" data-hex="00FFFF"></div>
      <div class="diy-swatch" style="background:#0000FF" data-hex="0000FF"></div>
      <div class="diy-swatch" style="background:#8000FF" data-hex="8000FF"></div>
    </div>
  </div>

  <div class="card">
    <h2>Effect</h2>
    <div class="effect-grid">
      <button class="fx active" data-fx="Steady">Steady</button>
      <button class="fx" data-fx="Breathe">Breathe</button>
      <button class="fx" data-fx="Gradient">Gradient</button>
      <button class="fx" data-fx="Leftward">Leftward</button>
      <button class="fx" data-fx="Rightward">Rightward</button>
      <button class="fx" data-fx="Circle">Circle</button>
    </div>
    <div class="slider-row">
      <span class="icon">&#x26A1;</span>
      <input type="range" id="speed" min="0" max="100" value="50">
      <span class="val" id="speed-val">50</span>
    </div>
    <div class="slider-row">
      <span class="icon">&#x2600;</span>
      <input type="range" id="bright" min="0" max="100" value="100">
      <span class="val" id="bright-val">100</span>
    </div>
  </div>

  <div class="card">
    <label>Save as</label>
    <input type="text" id="vname" value="">
    <div class="diy-btns">
      <button class="primary" id="save-btn">&#x1F4BE; Save</button>
      <button id="reset-btn">&#x21BA; Reset</button>
    </div>
    <div id="status"></div>
  </div>

<script type="module">
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));

const OUTER = Array.from({length:22}, (_,i) => [i*4, i*4+4]);
const MIDDLE = [
  ...Array.from({length:13}, (_,i) => [88+i*4, 88+i*4+4]),
  [140,145], [145,150],
];
const INNER = [
  ...Array.from({length:9}, (_,i) => [150+i*4, 150+i*4+4]),
  [186,191], [191,196],
];

const state = {
  leds: new Array(196).fill(null),
  tool: 'draw',
  color: 'FF8000',
  effect: 'Steady',
  speed: 50,
  bright: 100,
  res: 48,
  dragging: false,
  history: [],
};

function snapshot() {
  state.history.push(state.leds.slice());
  if (state.history.length > 20) state.history.shift();
}

function arcPath(r0, r1, a0, a1) {
  const toXY = (r, a) => [r*Math.cos(a), r*Math.sin(a)];
  const [x0a, y0a] = toXY(r0, a0);
  const [x1a, y1a] = toXY(r1, a0);
  const [x1b, y1b] = toXY(r1, a1);
  const [x0b, y0b] = toXY(r0, a1);
  const large = (a1 - a0) > Math.PI ? 1 : 0;
  return `M${x0a},${y0a} L${x1a},${y1a} A${r1},${r1} 0 ${large} 1 ${x1b},${y1b}`
       + ` L${x0b},${y0b} A${r0},${r0} 0 ${large} 0 ${x0a},${y0a} Z`;
}

const RING_GEOMETRY = {
  outer:  {r0: 130, r1: 180},
  middle: {r0: 90,  r1: 125},
  inner:  {r0: 50,  r1: 85},
};

function drawCanvas() {
  const svg = $('#lamp');
  svg.innerHTML = '';
  const rings = state.res === 48
    ? [['outer', OUTER], ['middle', MIDDLE], ['inner', INNER]]
    : [['outer', segments196('outer')],
       ['middle', segments196('middle')],
       ['inner', segments196('inner')]];
  for (const [name, segs] of rings) {
    const g = RING_GEOMETRY[name];
    const total = segs.length;
    for (let i = 0; i < total; i++) {
      const [start, stop] = segs[i];
      const a0 = (i / total) * 2 * Math.PI - Math.PI / 2;
      const a1 = ((i + 1) / total) * 2 * Math.PI - Math.PI / 2;
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', arcPath(g.r0, g.r1, a0, a1));
      const color = state.leds[start];
      path.setAttribute('fill', color ? `#${color}` : '#000');
      path.setAttribute('stroke', '#1c1c1f');
      path.setAttribute('stroke-width', '1');
      path.classList.add('seg');
      path.dataset.start = start;
      path.dataset.stop = stop;
      svg.appendChild(path);
    }
  }
}

function segments196(ring) {
  const start = ring === 'outer' ? 0 : ring === 'middle' ? 88 : 150;
  const stop = ring === 'outer' ? 88 : ring === 'middle' ? 150 : 196;
  return Array.from({length: stop - start}, (_, i) => [start + i, start + i + 1]);
}

function paintRange(start, stop, color) {
  for (let i = start; i < stop; i++) state.leds[i] = color;
}

async function applyTool(start, stop) {
  if (state.tool === 'draw')  paintRange(start, stop, state.color);
  if (state.tool === 'erase') paintRange(start, stop, null);
  drawCanvas();
  pushPaint();
}

let throttled = false, pending = null;
async function pushPaint() {
  const body = {leds: state.leds, effect: state.effect, speed: state.speed};
  pending = body;
  if (throttled) return;
  throttled = true;
  const send = pending; pending = null;
  await api('/api/diy/paint', send);
  setTimeout(async () => {
    throttled = false;
    if (pending) { const s = pending; pending = null; await api('/api/diy/paint', s); }
  }, 100);
}

async function api(path, body) {
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                                body: JSON.stringify(body)});
  const j = await r.json();
  if (!j.ok) $('#status').textContent = 'error: ' + j.error;
  else if (j.path) $('#status').textContent = 'saved → ' + j.path;
  else $('#status').textContent = '';
  return j;
}

function setActiveButton(selector, value, attr) {
  for (const b of $$(selector)) b.classList.toggle('active', b.dataset[attr] === value);
}

for (const b of $$('.tool')) b.onclick = () => {
  state.tool = b.dataset.tool;
  setActiveButton('.tool', state.tool, 'tool');
  if (state.tool === 'fill') {
    snapshot();
    state.leds = new Array(196).fill(state.color);
    drawCanvas();
    pushPaint();
  }
};
for (const b of $$('.res-btn')) b.onclick = () => {
  state.res = parseInt(b.dataset.res, 10);
  setActiveButton('.res-btn', String(state.res), 'res');
  drawCanvas();
};
for (const b of $$('.diy-swatch')) b.onclick = () => {
  state.color = b.dataset.hex;
  $('#picker').value = '#' + state.color;
};
$('#picker').oninput = e => state.color = e.target.value.replace('#','').toUpperCase();
for (const b of $$('.fx')) b.onclick = () => {
  state.effect = b.dataset.fx;
  setActiveButton('.fx', state.effect, 'fx');
  pushPaint();
};
$('#speed').oninput = e => {
  state.speed = parseInt(e.target.value, 10);
  $('#speed-val').textContent = state.speed;
  pushPaint();
};
$('#bright').oninput = e => {
  state.bright = parseInt(e.target.value, 10);
  $('#bright-val').textContent = state.bright;
  api('/api/brightness', {value: Math.round(state.bright * 10)});
};
$('#back-btn').onclick = () => {
  if (!state.history.length) return;
  state.leds = state.history.pop();
  drawCanvas();
  pushPaint();
};
$('#reset-btn').onclick = () => {
  snapshot();
  state.leds = new Array(196).fill(null);
  state.effect = 'Steady';
  state.speed = 50;
  state.bright = 100;
  setActiveButton('.fx', 'Steady', 'fx');
  $('#speed').value = 50; $('#speed-val').textContent = 50;
  $('#bright').value = 100; $('#bright-val').textContent = 100;
  drawCanvas();
  pushPaint();
};
$('#pwr-on').onclick = () => api('/api/power', {on: true});
$('#pwr-off').onclick = () => api('/api/power', {on: false});
$('#save-btn').onclick = async () => {
  const name = $('#vname').value.trim();
  if (!name) { $('#status').textContent = 'name required'; return; }
  await api('/api/diy/save', {name, leds: state.leds,
                              effect: state.effect, speed: state.speed});
};

async function setDefaultName() {
  const today = new Date().toISOString().slice(0, 10);
  const j = await fetch('/api/presets').then(r => r.json());
  const names = (j.presets || []).map(p => p.name);
  let n = 1;
  while (names.includes(`diy-${today}-${n}`)) n++;
  $('#vname').value = `diy-${today}-${n}`;
}

const svg = $('#lamp');
svg.addEventListener('mousedown', e => {
  if (e.target.classList.contains('seg')) {
    state.dragging = true;
    if (state.tool === 'draw' || state.tool === 'erase') snapshot();
    applyTool(parseInt(e.target.dataset.start, 10),
              parseInt(e.target.dataset.stop, 10));
  }
});
window.addEventListener('mouseup', () => state.dragging = false);
svg.addEventListener('mouseover', e => {
  if (state.dragging && e.target.classList.contains('seg')) {
    applyTool(parseInt(e.target.dataset.start, 10),
              parseInt(e.target.dataset.stop, 10));
  }
});

// Restore state from the lamp on mount (so nav-away-and-back keeps the
// canvas in sync with the physical lamp). Uses the shared helper module.
import { lampStateToPageLeds } from '/static/lamp-utils.js';
async function loadLampState() {
  try {
    const j = await fetch('/api/lamp/state').then(r => r.json());
    const page = lampStateToPageLeds(j);
    if (!page) return;
    // Treat "000000" as the DIY's null (off) so Erase / Reset stay consistent.
    state.leds = page.map(c => c === '000000' ? null : c);
    drawCanvas();
  } catch (e) { /* silent — keep blank canvas on any failure */ }
}

drawCanvas();
setDefaultName();
loadLampState();
</script>
"""


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


async def api_power(req):
    """Turn the lamp on or off.

    Power-off implicitly stops the ticker (spec: "calls the same shutdown path
    as /api/ticker/stop") so the next poll cannot re-assert d50 and silently
    undo the user's power-off. Power-on leaves the ticker state unchanged.
    """
    global _ticker_session, _clock_session
    try:
        body = await req.json()
        on = bool(body.get("on"))
        if not on:
            # Spec: power-off stops the ticker first.
            if _ticker_session is not None and _ticker_session.running:
                await _ticker_session.stop()
                _ticker_session = None
            if _clock_session is not None and _clock_session.running:
                await _clock_session.stop()
                _clock_session = None
            await _stop_preview()
            await _stop_capture()
        await _client.power(on)
        return web.json_response({"ok": True, "on": on})
    except (LeproError, ValueError, KeyError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def api_preview(req):
    global _preview_task, _preview_name
    try:
        body = await req.json()
        _check_ticker_mutex()
        _check_clock_mutex()
        _check_capture_mutex()
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
        _preview_name = recolored.get("name") or body.get("base_name") or "(unnamed)"
        return web.json_response({"ok": True})
    except web.HTTPConflict:
        raise
    except (LeproError, ValueError, KeyError, FileNotFoundError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def api_stop(_req):
    global _preview_task, _ticker_session, _clock_session
    # Spec: POST /api/stop is a "stop everything" gesture — stop the ticker too.
    if _ticker_session is not None and _ticker_session.running:
        await _ticker_session.stop()
        _ticker_session = None
    if _clock_session is not None and _clock_session.running:
        await _clock_session.stop()
        _clock_session = None
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
        recolored["prompt"] = f"{base_name} recolored via presets editor"
        from datetime import date
        recolored["captured"] = date.today().isoformat()
        path.write_text(json.dumps(recolored, indent=2) + "\n")
        return web.json_response({"ok": True, "path": str(path.relative_to(_HERE))})
    except (ValueError, KeyError, FileNotFoundError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


# --- DIY page route handlers --------------------------------------------------


_VALID_EFFECTS = {"Steady", "Breathe", "Gradient", "Leftward", "Rightward", "Circle"}
_HEX6 = re.compile(r"^[0-9A-Fa-f]{6}$")


def _validate_leds(leds) -> None:
    if not isinstance(leds, list) or len(leds) != 196:
        raise ValueError("leds must be a list of exactly 196 entries")
    for i, c in enumerate(leds):
        if c is None:
            continue
        if not (isinstance(c, str) and _HEX6.match(c)):
            raise ValueError(f"leds[{i}] = {c!r} is not a 6-hex string or null")


async def api_diy_paint(req):
    try:
        body = await req.json()
        _check_ticker_mutex()
        _check_clock_mutex()
        _check_capture_mutex()
        await _stop_preview()
        leds = body["leds"]
        effect = body.get("effect", "Steady")
        speed = int(body.get("speed", 50))
        _validate_leds(leds)
        if effect not in _VALID_EFFECTS:
            raise ValueError(f"unknown effect {effect!r}")
        if not 0 <= speed <= 100:
            raise ValueError(f"speed must be 0..100, got {speed}")
        d50 = build_d50_from_leds(apply_lamp_rotation(leds), effect, speed)
        await _client.send_raw({"d1": 1, "d2": 2, "d50": d50})
        return web.json_response({"ok": True})
    except web.HTTPConflict:
        raise
    except (LeproError, ValueError, KeyError, TypeError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def api_diy_save(req):
    try:
        body = await req.json()
        name = _sanitize_name(body["name"])
        leds = body["leds"]
        effect = body.get("effect", "Steady")
        speed = int(body.get("speed", 50))
        _validate_leds(leds)
        if effect not in _VALID_EFFECTS:
            raise ValueError(f"unknown effect {effect!r}")
        path = _PRESETS_DIR / f"{name}.json"
        if path.exists():
            return web.json_response(
                {"ok": False,
                 "error": f"preset {name!r} already exists; pick a unique name"},
                status=400)
        d50 = build_d50_from_leds(apply_lamp_rotation(leds), effect, speed)
        from datetime import date
        preset = {
            "name": name,
            "description": "Built in the DIY editor.",
            "captured": date.today().isoformat(),
            "prompt": "DIY editor",
            "payload": {"d1": 1, "d2": 2, "d50": d50},
        }
        path.write_text(json.dumps(preset, indent=2) + "\n")
        return web.json_response({"ok": True, "path": str(path.relative_to(_HERE))})
    except (ValueError, KeyError, TypeError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def api_brightness(req):
    try:
        body = await req.json()
        value = int(body["value"])
        if not 0 <= value <= 1000:
            raise ValueError(f"brightness value must be 0..1000, got {value}")
        await _client.send_raw({"d52": value})
        return web.json_response({"ok": True})
    except (LeproError, ValueError, KeyError, TypeError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


# --- Stock ticker endpoints ---------------------------------------------------

from web import ticker as _ticker_mod  # alias keeps namespace tidy

_ticker_session = None  # type: ignore[assignment]


def _check_ticker_mutex():
    """Raise web.HTTPConflict if the ticker session is running."""
    global _ticker_session
    if _ticker_session is not None and _ticker_session.running:
        raise web.HTTPConflict(
            text='{"ok": false, "error": "stock ticker is running; stop it first"}',
            content_type="application/json",
        )


async def api_ticker_start(req):
    global _ticker_session
    try:
        body = await req.json()
        interval = int(body.get("interval", 30))
        symbols = {}
        for ring in ("outer", "middle", "inner"):
            sym = body.get(ring)
            if sym is not None and str(sym).strip() != "":
                symbols[ring] = str(sym).strip().upper()
        if not symbols:
            return web.json_response(
                {"ok": False, "error": "at least one symbol required"},
                status=400,
            )
        if _ticker_session is not None and _ticker_session.running:
            return web.json_response(
                {"ok": False, "error": "stock ticker already running"},
                status=409,
            )
        if _capture_session is not None and _capture_session.running:
            return web.json_response(
                {"ok": False, "error": "a capture is in progress; save or cancel it first"},
                status=409,
            )
        import asyncio
        # First-sample fetch for every symbol; if any return None, abort.
        results = await asyncio.gather(
            *[asyncio.to_thread(_ticker_mod.fetch_price, s) for s in symbols.values()]
        )
        baselines = {}
        failed = []
        for (ring, sym), price in zip(symbols.items(), results):
            if price is None:
                failed.append(sym)
            else:
                baselines[ring] = price
        if failed:
            return web.json_response(
                {"ok": False, "error": f"could not fetch first price for: {', '.join(failed)}"},
                status=400,
            )
        await _stop_preview()
        sess = _ticker_mod.TickerSession(_client, symbols, interval)
        for ring, price in baselines.items():
            sess.set_baseline(ring, price)
        await sess.start()
        _ticker_session = sess
        snap = sess.snapshot()
        return web.json_response(
            {"ok": True, "since": snap["since"], "baselines": baselines}
        )
    except (ValueError, KeyError, TypeError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def api_ticker_stop(_req):
    global _ticker_session
    if _ticker_session is None:
        return web.json_response({"ok": True})
    await _ticker_session.stop()
    _ticker_session = None
    return web.json_response({"ok": True})


async def api_ticker_state(_req):
    if _ticker_session is None:
        return web.json_response({
            "running": False, "since": None, "interval": None,
            "flash_until": None, "rings": None,
        })
    return web.json_response(_ticker_session.snapshot())


async def api_lamp_state(_req):
    """Return the lamp's most recently reported state.

    The workshop's _on_startup spawns _client.listen_forever() which
    populates _client.state[did] from MQTT state-update messages. This
    endpoint returns that cached snapshot plus a poll timestamp so the
    page can show 'last polled X seconds ago'.
    """
    from datetime import datetime, timezone
    if _client is None:
        return web.json_response({"devices": {}, "polled_at": None})
    return web.json_response({
        "devices": dict(_client.state),
        "polled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


def _active_mode() -> dict:
    """Compute the active-mode banner: ``{"mode": ..., "label": ...}``.

    mode is one of "capturing", "off", "clock", "ticker", "preset", "idle".
    Shared by api_cockpit_active (web cockpit) and api_lamp_leds (TUI) so the
    two front-ends always agree.
    """
    # 0. Capture in progress wins over everything — user is actively driving this.
    if _capture_session is not None and _capture_session.running:
        n = _capture_session.frame_count
        return {
            "mode": "capturing",
            "label": f"🎥 Capturing — {n} frame{'s' if n != 1 else ''}",
        }
    # 1. Power off wins.
    if _client is not None:
        for fields in _client.state.values():
            if fields.get("d1") == 0:
                return {"mode": "off", "label": "⏻ Off"}
    # 2. Active background sessions.
    if _clock_session is not None and _clock_session.running:
        snap = _clock_session.snapshot()
        now = (snap.get("now_displayed") or "")
        suffix = now.split("T", 1)[1] if "T" in now else now
        label = f"⏰ Clock — {suffix}" if suffix else "⏰ Clock"
        return {"mode": "clock", "label": label}
    if _ticker_session is not None and _ticker_session.running:
        snap = _ticker_session.snapshot()
        syms = []
        for ring in ("outer", "middle", "inner"):
            r = (snap.get("rings") or {}).get(ring)
            if r and r.get("symbol"):
                syms.append(r["symbol"])
        label = "\U0001F4C8 Ticker — " + ", ".join(syms) if syms else "\U0001F4C8 Ticker"
        return {"mode": "ticker", "label": label}
    if _preview_task is not None and not _preview_task.done():
        nm = _preview_name or "?"
        return {"mode": "preset", "label": f"\U0001F3A8 Preset — {nm}"}
    # 3. On but nothing actively driving.
    return {"mode": "idle", "label": "✨ Idle"}


async def api_cockpit_active(_req):
    """Return the active-mode banner data for the cockpit left panel."""
    return web.json_response(_active_mode())


async def api_lamp_leds(_req):
    """Everything the TUI needs in one round-trip.

    Returns power state, brightness, lamp mode, the active-mode banner, the
    raw d-fields (for the TUI's diagnostics panel), and a 196-entry page-space
    LED color array — or null when the current d50 isn't decodable (e.g.
    official-app N02/N03 animations).
    """
    from datetime import datetime, timezone
    fields = {}
    if _client is not None:
        for f in _client.state.values():
            fields = f
            break    # the workshop only ever has one lamp
    d52 = fields.get("d52")
    return web.json_response({
        "power": fields.get("d1") == 1,
        "brightness_pct": round(d52 / 10) if isinstance(d52, (int, float)) else None,
        "lamp_mode": {0: "white", 1: "rgb", 2: "segmented", 3: "effect"}.get(fields.get("d2")),
        "active": _active_mode(),
        "leds": lampview.fields_to_leds(fields),
        "fields": dict(fields),
        "polled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


# --- Clock endpoints ---------------------------------------------------------

from web import clock as _clock_mod

_clock_session = None  # type: ignore[assignment]


def _check_clock_mutex():
    """Raise web.HTTPConflict if the clock is running."""
    global _clock_session
    if _clock_session is not None and _clock_session.running:
        raise web.HTTPConflict(
            text='{"ok": false, "error": "clock is running; stop it first"}',
            content_type="application/json",
        )


def _check_capture_mutex():
    """Raise HTTPConflict if a capture is in progress.

    Used by ticker/clock/preview/diy-paint starts to refuse the new operation
    rather than silently destroying the user's in-flight capture work."""
    global _capture_session
    if _capture_session is not None and _capture_session.running:
        raise web.HTTPConflict(
            text='{"ok": false, "error": "a capture is in progress; save or cancel it first"}',
            content_type="application/json",
        )


async def api_clock_start(req):
    global _clock_session
    try:
        body = await req.json()
        colors = body.get("colors") or {}
        mode = body.get("mode", "12h")
        if _clock_session is not None and _clock_session.running:
            return web.json_response(
                {"ok": False, "error": "clock already running"}, status=409)
        # Ticker mutex too — only one lamp-driving session at a time.
        if _ticker_session is not None and _ticker_session.running:
            return web.json_response(
                {"ok": False, "error": "stock ticker is running; stop it first"},
                status=409)
        if _capture_session is not None and _capture_session.running:
            return web.json_response(
                {"ok": False, "error": "a capture is in progress; save or cancel it first"},
                status=409)
        await _stop_preview()
        sess = _clock_mod.ClockSession(_client, colors=colors, mode=mode)
        await sess.start()
        _clock_session = sess
        snap = sess.snapshot()
        return web.json_response({"ok": True, "since": snap["since"], "mode": snap["mode"]})
    except (ValueError, KeyError, TypeError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def api_clock_stop(_req):
    global _clock_session
    if _clock_session is None:
        return web.json_response({"ok": True})
    await _clock_session.stop()
    _clock_session = None
    return web.json_response({"ok": True})


async def api_clock_state(_req):
    if _clock_session is None:
        return web.json_response({
            "running": False, "since": None, "mode": None,
            "colors": None, "now_displayed": None,
        })
    return web.json_response(_clock_session.snapshot())


async def index_clock(_req):
    return web.Response(text=_render_shell("clock", _PANEL_CLOCK, "Clock"),
                        content_type="text/html")


# --- Animations API endpoints -------------------------------------------------


async def api_animations(_req):
    """Return the deduped + override-applied animation catalog."""
    groups = _grouped_animations()
    return web.json_response({"animations": [_animation_to_json(g) for g in groups]})


async def api_animation_rename(req):
    """Set a custom display name for an animation group."""
    aid = req.match_info["id"]
    try:
        body = await req.json()
        name = str(body.get("name") or "").strip()
        if not name:
            return web.json_response({"ok": False, "error": "name required"}, status=400)
        overrides = _read_animation_overrides()
        entry = overrides.setdefault(aid, {})
        entry["name"] = name
        _write_animation_overrides(overrides)
        return web.json_response({"ok": True})
    except (ValueError, KeyError, TypeError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def api_animation_merge(req):
    """Declare animation {id} as an alias of {other_id}; the two groups fold into one."""
    aid = req.match_info["id"]
    target = req.match_info["other_id"]
    if aid == target:
        return web.json_response({"ok": False, "error": "cannot merge into self"}, status=400)
    overrides = _read_animation_overrides()
    entry = overrides.setdefault(aid, {})
    entry["alias_of"] = target
    _write_animation_overrides(overrides)
    return web.json_response({"ok": True})


async def api_animation_save(req):
    """Recolor an animation's default preset with a user-supplied palette
    and save the result as a new file in presets/."""
    aid = req.match_info["id"]
    try:
        body = await req.json()
        name = _sanitize_name(body["name"])
        palette = list(body.get("palette") or [])
        groups = _grouped_animations()
        anim = next((g for g in groups if g.id == aid), None)
        if anim is None:
            return web.json_response({"ok": False, "error": f"animation {aid!r} not found"}, status=404)
        if len(palette) != len(anim.default_palette):
            return web.json_response(
                {"ok": False,
                 "error": f"palette length {len(palette)} != animation palette length {len(anim.default_palette)}"},
                status=400)
        for c in palette:
            if not _HEX6.match(c):
                return web.json_response({"ok": False, "error": f"color {c!r} is not 6-hex"}, status=400)
        # Source preset: the first member's file on disk.
        source_name = anim.members[0].name
        source_path = _PRESETS_DIR / f"{source_name}.json"
        if not source_path.exists():
            return web.json_response({"ok": False, "error": f"source preset {source_name} missing"}, status=500)
        source = json.loads(source_path.read_text())
        recolored = _recolor_preset(source, anim.default_palette, palette)
        recolored["name"] = name
        recolored["description"] = f"Recolored variant of {source_name} via Animations tab."
        recolored["prompt"] = f"animations:{aid}"
        from datetime import date
        recolored["captured"] = date.today().isoformat()
        out_path = _PRESETS_DIR / f"{name}.json"
        if out_path.exists():
            return web.json_response({"ok": False, "error": f"preset {name!r} already exists"}, status=400)
        out_path.write_text(json.dumps(recolored, indent=2) + "\n")
        return web.json_response({"ok": True, "path": str(out_path.relative_to(_PROJECT_ROOT))})
    except (KeyError, ValueError, TypeError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


_PANEL_CLOCK = """
<style>
  /* Feature-specific styles for the Clock panel.
     Generic page chrome lives in /static/cockpit.css.
     Classes prefixed .clock- to avoid collisions with other panels. */
  .clock-canvas { display: flex; justify-content: center; padding: 6px 0; }
  .clock-color-row { display: grid; grid-template-columns: 90px 50px 1fr;
                     align-items: center; gap: 10px; margin: 8px 0; }
  .clock-color-row label { font-size: 13px; color: #ccc; }
  .clock-color-row input[type=color] { width: 44px; height: 32px;
                                        border: 2px solid #333; border-radius: 8px;
                                        cursor: pointer; background: none; padding: 0; }
  .clock-color-row .hex { font: 12px ui-monospace, monospace; color: #888; }
  .clock-mode-toggle { display: flex; gap: 4px; background: #2a2a30;
                       padding: 4px; border-radius: 8px; max-width: 180px; }
  .clock-mode-toggle button { flex: 1; padding: 6px 12px; border: 0;
                               border-radius: 6px; background: transparent;
                               color: #eee; cursor: pointer; font: inherit; }
  .clock-mode-toggle button.active { background: #5fd9d9; color: #111; font-weight: 700; }
  .clock-controls { display: flex; gap: 8px; margin-top: 8px; }
  .clock-controls button { flex: 1; padding: 12px; border: 0; border-radius: 10px;
                            background: #2a2a30; color: #eee; cursor: pointer;
                            font: inherit; font-weight: 700; }
  .clock-controls button.primary { background: #2c8f4f; color: #fff; }
  .clock-controls button.danger { background: #8f2c2c; color: #fff; }
  .clock-controls button:disabled { opacity: 0.4; cursor: not-allowed; }
  #clock-status { font-size: 12px; color: #777; margin-top: 10px; min-height: 1.2em; }
  .clock-readout { font: 600 28px ui-monospace, monospace;
                   text-align: center; color: #eee; margin: 4px 0 10px; }
</style>

  <div class="card">
    <div class="clock-readout" id="readout">--:--:--</div>
    <div class="clock-canvas">
      <svg id="clock-face" width="380" height="380" viewBox="-200 -200 400 400"></svg>
    </div>
  </div>

  <div class="card">
    <h2>Colors</h2>
    <div class="clock-color-row">
      <label>Outer (seconds)</label>
      <input type="color" id="color-outer" value="#FF0000">
      <div class="hex" id="hex-outer">FF0000</div>
    </div>
    <div class="clock-color-row">
      <label>Middle (minutes)</label>
      <input type="color" id="color-middle" value="#00FF00">
      <div class="hex" id="hex-middle">00FF00</div>
    </div>
    <div class="clock-color-row">
      <label>Inner (hours)</label>
      <input type="color" id="color-inner" value="#0000FF">
      <div class="hex" id="hex-inner">0000FF</div>
    </div>
  </div>

  <div class="card">
    <h2>Hour format</h2>
    <div class="clock-mode-toggle" id="mode-toggle">
      <button data-mode="12h" class="active">12h</button>
      <button data-mode="24h">24h</button>
    </div>
    <div class="clock-controls">
      <button class="primary" id="start-btn">Start</button>
      <button class="danger" id="stop-btn" disabled>Stop</button>
    </div>
    <div id="clock-status">not running</div>
  </div>

<script type="module">
import { computeClockPositions } from '/static/lamp-utils.js';

const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));

const OUTER = Array.from({length:22}, (_,i) => [i*4, i*4+4]);
const MIDDLE = [
  ...Array.from({length:13}, (_,i) => [88+i*4, 88+i*4+4]),
  [140,145], [145,150],
];
const INNER = [
  ...Array.from({length:9}, (_,i) => [150+i*4, 150+i*4+4]),
  [186,191], [191,196],
];
const RING_GEOMETRY = {
  outer:  {r0: 130, r1: 180},
  middle: {r0: 90,  r1: 125},
  inner:  {r0: 50,  r1: 85},
};

const state = {
  colors: {outer: 'FF0000', middle: '00FF00', inner: '0000FF'},
  mode: '12h',
  running: false,
};

function arcPath(r0, r1, a0, a1) {
  const toXY = (r, a) => [r*Math.cos(a), r*Math.sin(a)];
  const [x0a, y0a] = toXY(r0, a0);
  const [x1a, y1a] = toXY(r1, a0);
  const [x1b, y1b] = toXY(r1, a1);
  const [x0b, y0b] = toXY(r0, a1);
  const large = (a1 - a0) > Math.PI ? 1 : 0;
  return `M${x0a},${y0a} L${x1a},${y1a} A${r1},${r1} 0 ${large} 1 ${x1b},${y1b}`
       + ` L${x0b},${y0b} A${r0},${r0} 0 ${large} 0 ${x0a},${y0a} Z`;
}

function segmentsContaining(ring, ledIdx) {
  const segs = ring === 'outer' ? OUTER : ring === 'middle' ? MIDDLE : INNER;
  const base = ring === 'outer' ? 0 : ring === 'middle' ? 88 : 150;
  const absIdx = base + ledIdx;
  for (let i = 0; i < segs.length; i++) {
    if (absIdx >= segs[i][0] && absIdx < segs[i][1]) return i;
  }
  return null;
}

function drawClock(positions) {
  const svg = $('#clock-face');
  svg.innerHTML = '';
  for (const [name, segs] of [['outer', OUTER], ['middle', MIDDLE], ['inner', INNER]]) {
    const g = RING_GEOMETRY[name];
    const total = segs.length;
    const litSegmentIdx = segmentsContaining(name, positions[name]);
    for (let i = 0; i < total; i++) {
      const a0 = (i / total) * 2 * Math.PI - Math.PI / 2;
      const a1 = ((i + 1) / total) * 2 * Math.PI - Math.PI / 2;
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', arcPath(g.r0, g.r1, a0, a1));
      const lit = i === litSegmentIdx;
      const color = lit ? '#' + state.colors[name] : '#000';
      path.setAttribute('fill', color);
      path.setAttribute('stroke', '#1c1c1f');
      path.setAttribute('stroke-width', '1');
      svg.appendChild(path);
    }
  }
}

function updateReadout(now) {
  const hh = String(now.getHours()).padStart(2, '0');
  const mm = String(now.getMinutes()).padStart(2, '0');
  const ss = String(now.getSeconds()).padStart(2, '0');
  $('#readout').textContent = `${hh}:${mm}:${ss}`;
}

function tickVisualizer() {
  const now = new Date();
  const positions = computeClockPositions(now, state.mode);
  drawClock(positions);
  updateReadout(now);
}

async function postJSON(path, body) {
  const opts = {method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body || {})};
  const r = await fetch(path, opts);
  return r.json();
}

function syncColorPickers() {
  for (const ring of ['outer', 'middle', 'inner']) {
    $(`#color-${ring}`).value = '#' + state.colors[ring];
    $(`#hex-${ring}`).textContent = state.colors[ring];
  }
}

function syncModeToggle() {
  for (const b of $$('#mode-toggle button')) {
    b.classList.toggle('active', b.dataset.mode === state.mode);
  }
}

function setInputsDisabled(disabled) {
  for (const ring of ['outer', 'middle', 'inner']) {
    $(`#color-${ring}`).disabled = disabled;
  }
  for (const b of $$('#mode-toggle button')) b.disabled = disabled;
  $('#start-btn').disabled = disabled;
  $('#stop-btn').disabled = !disabled;
}

for (const ring of ['outer', 'middle', 'inner']) {
  $(`#color-${ring}`).oninput = e => {
    if (state.running) return;
    state.colors[ring] = e.target.value.replace('#', '').toUpperCase();
    $(`#hex-${ring}`).textContent = state.colors[ring];
  };
}
for (const b of $$('#mode-toggle button')) {
  b.onclick = () => {
    if (state.running) return;
    state.mode = b.dataset.mode;
    syncModeToggle();
  };
}

$('#start-btn').onclick = async () => {
  const j = await postJSON('/api/clock/start', {colors: state.colors, mode: state.mode});
  if (!j.ok) { $('#clock-status').textContent = 'error: ' + j.error; return; }
  state.running = true;
  setInputsDisabled(true);
  $('#clock-status').textContent = 'running since ' + (j.since ? j.since.slice(11, 16) : '?');
};
$('#stop-btn').onclick = async () => {
  await postJSON('/api/clock/stop', {});
  state.running = false;
  setInputsDisabled(false);
  $('#clock-status').textContent = 'stopped (last frame left on lamp)';
};

async function refreshFromServer() {
  try {
    const j = await fetch('/api/clock/state').then(r => r.json());
    if (j.running) {
      state.running = true;
      if (j.colors) state.colors = j.colors;
      if (j.mode) state.mode = j.mode;
      syncColorPickers();
      syncModeToggle();
      setInputsDisabled(true);
      $('#clock-status').textContent = 'running since ' + (j.since ? j.since.slice(11, 16) : '?');
    } else {
      state.running = false;
      setInputsDisabled(false);
      if ($('#clock-status').textContent === '' || $('#clock-status').textContent === 'not running') {
        $('#clock-status').textContent = 'not running';
      }
    }
  } catch (e) { /* silent */ }
}

syncColorPickers();
syncModeToggle();
tickVisualizer();
setInterval(tickVisualizer, 1000);
refreshFromServer();
setInterval(refreshFromServer, 5000);
</script>
"""


async def index_state_redirect(_req):
    """State page absorbed into the cockpit left panel; redirect to home."""
    raise web.HTTPFound("/")


_PANEL_PRESETS = """
<style>
  /* Feature-specific styles for the Presets panel.
     Generic page chrome (body, .wrap, .tabs, .power-btns, .card, :root) lives in
     /static/cockpit.css — those rules are intentionally omitted here. */

  h1 { font-size: 18px; margin: 0 0 16px; color: #5fd9d9; }
  h2 { font-size: 14px; margin: 16px 0 8px; color: #aaa;
       text-transform: uppercase; letter-spacing: 0.08em; }
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
</style>

<h2>Preset library</h2>
<div id="preset-list"></div>

<div class="panel" id="editor" style="margin-top:16px">
  <div class="empty">Pick an animation on the left to start.</div>
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

// Power buttons live in the persistent header, wired once at page load.
// cockpit.js handles these globally; this is a harmless double-bind kept for
// backward compatibility — cleanup deferred to a later task.
$('#pwr-on').onclick = async () => {
  const j = await api('/api/power', {on: true});
  $('#status') && ($('#status').textContent = j.ok ? 'lamp on' : 'error: ' + j.error);
};
$('#pwr-off').onclick = async () => {
  const j = await api('/api/power', {on: false});
  $('#status') && ($('#status').textContent = j.ok ? 'lamp off' : 'error: ' + j.error);
};

loadPresets();
</script>"""


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
        web.get("/diy", index_diy),
        web.get("/ticker", index_ticker),
        web.get("/state", index_state_redirect),
        web.get("/api/presets", api_presets),
        web.get(r"/api/presets/{name}", api_preset),
        web.post("/api/power", api_power),
        web.post("/api/preview", api_preview),
        web.post("/api/stop", api_stop),
        web.post("/api/save", api_save),
        # DIY page (handler added in Task 5; routes registered now so the smoke
        # check counts them). The HTML route stays a no-op until Task 5 lands.
        web.post("/api/diy/paint", api_diy_paint),
        web.post("/api/diy/save", api_diy_save),
        web.post("/api/brightness", api_brightness),
        web.post("/api/ticker/start", api_ticker_start),
        web.post("/api/ticker/stop", api_ticker_stop),
        web.get("/api/ticker/state", api_ticker_state),
        web.get("/api/lamp/state", api_lamp_state),
        web.get("/api/lamp/leds", api_lamp_leds),
        web.get("/api/cockpit/active", api_cockpit_active),
        web.get("/clock", index_clock),
        web.post("/api/clock/start", api_clock_start),
        web.post("/api/clock/stop", api_clock_stop),
        web.get("/api/clock/state", api_clock_state),
        web.get("/animations", index_animations),
        web.get("/api/animations", api_animations),
        web.post(r"/api/animations/{id}/rename", api_animation_rename),
        web.post(r"/api/animations/{id}/merge_into/{other_id}", api_animation_merge),
        web.post(r"/api/animations/{id}/save", api_animation_save),
        web.post("/api/captures/start", api_captures_start),
        web.get("/api/captures/state", api_captures_state),
        web.post("/api/captures/cancel", api_captures_cancel),
        web.post("/api/captures/save", api_captures_save),
    ])
    # Static assets — currently just lamp-utils.js shared by /diy and /state.
    app.router.add_static("/static", _HERE / "static")
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


def main() -> None:
    host = os.environ.get("LEPRO_WORKSHOP_HOST", "0.0.0.0")
    port = int(os.environ.get("LEPRO_WORKSHOP_PORT", "8081"))
    web.run_app(build_app(), host=host, port=port)


if __name__ == "__main__":
    main()
