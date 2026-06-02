# Lamp TUI — Design

**Date:** 2026-06-02
**Status:** Approved (Approach A — thin Textual client over the workshop HTTP API, on `main`)

## Goal

A terminal cockpit for the Lepro TB1: live ring visualizer + power / brightness /
fill / stop controls, talking to the existing workshop server over HTTP. The TUI
never speaks MQTT itself.

```
┌─────────────┐   GET /api/lamp/leds (1s poll)   ┌──────────────┐   MQTT   ┌──────┐
│  cli/tui.py  │ ───────────────────────────────▶ │ web/server.py │ ◀──────▶ │ Lamp │
│  (Textual)   │   POST /api/power, /brightness,  │  (existing)   │          └──────┘
│              │        /diy/paint, /stop         │               │
└─────────────┘                                   └──────────────┘
```

## Section 1 — Server side

**New module `web/lampview.py`** (pure functions, no I/O):

- `parse_d50_n01(d50) -> list[str] | None` — Python port of `lamp-utils.js
  parseD50_N01`; physical-space 196 colors
- `unrotate_to_page(physical) -> list[str]` — inverse of `apply_lamp_rotation`
- `hsv_hex_to_rgb(d5) -> str | None` — for RGB mode (`d2=1`)
- `cct_to_rgb(d4) -> str` — warm/cool white approximation for white mode (`d2=0`)
- `fields_to_leds(fields) -> list[str] | None` — dispatcher on `d2`:
  segmented → parse d50; RGB/white → fill all 196 with one color;
  N02/N03 or unknown → `None`

This makes the TUI's visualizer *better* than the web one in two cases (RGB and
white modes render as a solid-color lamp instead of nothing), and identical in
the worst case (undecodable app animations → `None`).

**New endpoint `GET /api/lamp/leds`** — everything the TUI needs in one round-trip:

```json
{
  "power": true,
  "brightness_pct": 80,
  "lamp_mode": "segmented",
  "active": {"mode": "idle", "label": "✨ Idle"},
  "leds": ["FF0000", "..."],
  "fields": {"d1": 1, "d2": 2, "d50": "...", "d52": 800},
  "polled_at": "2026-06-02T..."
}
```

- `leds`: 196 page-space colors, or `null` when the current d50 isn't decodable
  (official-app N02/N03 animations).
- `fields`: raw d-fields, for the TUI's diagnostics panel (amended during
  planning — saves a second endpoint).
- The active-mode logic is extracted from `api_cockpit_active` into a shared
  `_active_mode()` helper so both endpoints return identical answers. No
  existing endpoint changes behavior.

## Section 2 — The TUI

**Files:**

- `cli/tui.py` — Textual app: widgets, keybindings, polling
- `cli/tui_api.py` — `LampApi`, thin aiohttp wrapper over the workshop API
- `cli/tui_render.py` — pure render math (no Textual/Rich imports, unit-testable)

**Run:** `python -m cli.tui [--server http://localhost:8081]`
(default from `LEPRO_WORKSHOP_URL`, falling back to `http://localhost:8081`).

**New dependency:** `textual` in `requirements.txt`. The server never imports it.

**Layout:**

```
┌──────────────────────────────────────────────────────────────┐
│ ⏻ On   ✨ Idle                      brightness ████████░░ 80% │  status bar
├───────────────────────────────────┬──────────────────────────┤
│                                   │  Raw d-fields            │
│          ring visualizer          │  d1   1                  │
│      (3 concentric rings of       │  d2   2                  │
│        colored LED cells)         │  d50  N01:P10004FF00…    │
│                                   │  (toggle with `d`)       │
├───────────────────────────────────┴──────────────────────────┤
│ p Power  ↑↓ Brightness  s Stop  1-8 Fill  v View  q Quit     │  footer
└──────────────────────────────────────────────────────────────┘
```

**Visualizer — two views, toggled with `v` (user decision):**

1. **Rings (default):** per-pixel inverse mapping — for each terminal pixel,
   compute radius/angle from center, decide which ring band it falls in (bands
   scaled from the web SVG geometry), look up the page-space LED at that angle.
   Rendered with half-block characters (`▀` fg+bg = 2 pixels/cell, 24-bit RGB).
   The exact inverse of how `cockpit.js` draws arcs, so the two visualizers
   agree by construction.
2. **Strips:** each ring unrolled into a horizontal run of one colored block
   per LED. Shows exact LED indices (useful for calibration work); fits any
   terminal.

Power-off renders dimmed, same as the web's `viz-dimmed`.

**Controls** (mirroring `cockpit.js` patterns: optimistic UI + burst poll
250/600/1200ms, brightness debounce):

| Key | Action | Endpoint |
|---|---|---|
| `p` | power toggle | `POST /api/power` |
| `↑`/`↓` | brightness ±5%, debounced | `POST /api/brightness` |
| `s` | stop everything | `POST /api/stop` |
| `1`–`8` | fill whole lamp with palette color (user decision: in v1) | `POST /api/diy/paint` |
| `v` | toggle rings/strips view | — |
| `d` | toggle raw d-fields panel | — |
| `r` | force refresh | — |
| `q` | quit | — |

**Polling:** `set_interval(1.0, …)` → `GET /api/lamp/leds`. Server unreachable →
keep last frame, show `⚠ server unreachable` in the status bar.

**Testing:**

1. `tests/test_lampview.py` — pure d50 parsing, validated against real captured
   d50 strings from `docs/D50_FORMAT.md` and `presets/`
2. `tests/test_lamp_leds.py` — HTTP endpoint with a fake client (existing
   test_captures.py pattern)
3. `tests/test_tui_render.py` — pure pixel math
4. `tests/test_tui_api.py` — `LampApi` against an aiohttp `TestServer`
5. `tests/test_tui_app.py` — Textual `Pilot` tests with a stub API object
