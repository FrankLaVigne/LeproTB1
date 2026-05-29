# Preset Workshop — Design Spec

**Date:** 2026-05-29
**Status:** Approved
**Repo:** git@github.com:FrankLaVigne/LeproTB1.git
**Sibling refs:** `app.py` (simple lamp UI), `play_preset.py` (CLI preset replay).

## Goal

A web UI ("the workshop") for browsing the captured preset library, picking a
base preset, **recoloring its palette** through a color-combo picker, **naming**
the variant, **previewing** it on the lamp, and **saving** the result as a new
`presets/*.json`. Aesthetic deliberately mirrors the Lepro app's *LightGPM
Designs* screen the user referenced.

This iteration ships **Screen 1 only** (the workshop). The DIY editor (Screen
2) is explicitly deferred until a separate reverse-engineering session captures
custom-drawn patterns + tests Steady/Breathe/Gradient/Leftward/Rightward/Circle
on the TB1 and resolves brightness layering.

## Non-goals

- **No DIY / per-LED draw editor** (Screen 2 — separate iteration).
- **No working speed slider** (decode pending — shown disabled with tooltip).
- **No working brightness slider** (same; layering on top of d50 untested).
- No music sync, favorites, sharing, EID codes — out of scope.
- No build step. Vanilla HTML / CSS / ES modules inlined in `workshop.py`.
- No new dependencies. Uses existing `aiohttp` + `lepro.LeproClient`.
- No formal automated UI tests. Pure functions get unit tests; the playback
  loop gets one fake-client integration test; the front end is verified
  manually.

## Architecture

One `workshop.py` script — sibling to `app.py`. Single `aiohttp` server, one
persistent `LeproClient` (cached session, MQTT, background `listen_forever`),
one mutable `_preview_task: asyncio.Task | None` for the live playback loop.

```
Browser ──► aiohttp routes ──► palette_extractor / apply_color_map (pure)
                          └─► _preview_task (asyncio loop) ──► LeproClient.send_raw
                                                          ──► MQTT publish
```

Defaults to `0.0.0.0:8081` so it coexists with `app.py` (8080) and the MCP
server (8765). Override via `LEPRO_WORKSHOP_HOST` / `LEPRO_WORKSHOP_PORT`.

## Backend routes

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET`  | `/` | — | the single HTML page |
| `GET`  | `/api/presets` | — | `[{name, frame_count, palette: [hex,...]}]` |
| `GET`  | `/api/presets/{name}` | — | the full preset JSON |
| `POST` | `/api/preview` | `{base_name, color_map: {old: new}}` | `{ok:true}` |
| `POST` | `/api/stop` | — | `{ok:true}` |
| `POST` | `/api/save` | `{new_name, base_name, color_map}` | `{ok:true, path}` |

All mutating routes return `{ok:false, error:str}` JSON (HTTP 400) on
`LeproError`, `ValueError`, `KeyError`, or `FileExistsError`. No tracebacks
leak.

## Pure functions (testable, no hardware)

### `extract_palette(preset: dict) -> list[str]`
Regex-scan every frame's `d50` for `P1000{N}{colors_hex}` blocks. Return the
distinct 6-hex-char color strings in **first-occurrence order**. Handles both
single-frame (`preset["payload"]["d50"]`) and multi-frame
(`preset["frames"][*]["d50"]`) shapes. Uppercases everything to match what the
Lepro app emits.

Regex: `r"P1000(\d)((?:[0-9A-Fa-f]{6})+)"` — match the count then the run of
6-hex tuples; iterate tuples in the second capture group. (We also accept the
lowercase `e500e5`-style hex seen in `cyberpunk`'s `P4` palette — we
case-insensitively match but normalize to uppercase for the returned list.)

### `apply_color_map(preset: dict, color_map: dict[str, str]) -> dict`
Return a deep copy with every `d50` string substituted per the map. Keys and
values are case-insensitive on input; substitution emits uppercase hex. Works
for single-frame and multi-frame shapes.

Safe to do pure string substitution because:
1. Palette hex is always 6 chars (no collision with shorter hex).
2. Our white-blue-tour experiment proved palette colors live ONLY inside
   `P1000{N}{colors}` blocks — motion fields don't reference RGB.

### Both are unit-tested
No network, no hardware. ~6 tests covering: single-frame round-trip, multi-frame
round-trip, P4 (lowercase) palette in cyberpunk, color reused across frames,
unknown color in map is a no-op (warns but doesn't raise), empty map returns
deep copy unchanged.

## Live preview lifecycle

- `_preview_task: asyncio.Task | None` (module-level singleton).
- `POST /api/preview` → cancel-and-await old task → build the recolored preset
  via `apply_color_map` → start a new task running `_run_preview`.
- `_run_preview` infinite-loops the frame publish + sleep loop (same shape as
  `play_preset.py`). Single-frame presets skip the inter-frame sleep.
- `POST /api/stop` → cancel-and-await → set to `None`. Lamp holds whatever
  frame was last published (no return-to-off — matches existing replay
  behavior).
- App `on_cleanup` → cancel-and-await preview, then `await client.close()`.

## Frontend layout

Two-column responsive layout (stacks on mobile):

**Left column — Preset Library**
- Vertically scrolling list, one row per preset.
- Each row: preset name + tight row of small colored dots (palette preview
  served by `/api/presets`).
- Selected preset has a highlighted border.

**Right column — Variant Editor** (empty state: *"Pick an animation on the
left to start."*)
- Header: `Base: <name>  (<N> frames)`.
- **Variant name** input. Default `<base>-recolored`. Required.
- **Color Combo** — N round swatches, one per distinct palette color. Each is
  a clickable `<input type="color">`. Each swatch has a tooltip showing the
  *original* hex.
- **Speed** slider — disabled, greyed. Tooltip: *"Decode pending — see Screen 2
  RE notes."*
- **Brightness** slider — same.
- **Buttons:** `▶ Preview`  `■ Stop`  `💾 Save`.

Style: dark theme matching `app.py` (`#111` bg, `#1c1c1f` cards, cyan accents
`#5fd9d9`). Round-pill buttons. Round color dots. System font stack. No
external CSS or JS dependencies; everything inlined.

## Save flow

`POST /api/save` with `{new_name, base_name, color_map}`:
1. Sanitize `new_name`: lowercase, kebab-case, must match
   `[a-z0-9][a-z0-9-]*`. Reject otherwise.
2. If `presets/{new_name}.json` already exists → return error
   `{ok:false, error: "preset '{new_name}' already exists; pick a unique name"}`.
   No silent overwrites in this iteration.
3. Build recolored preset via `apply_color_map(base, color_map)`; replace its
   top-level `name` with `new_name`; set `prompt` to
   `"<base_name> recolored via workshop"`; set `captured` to today.
4. Write `presets/<new_name>.json` (pretty-printed, 2-space indent — matches
   existing presets).
5. Return `{ok:true, path: "presets/<new_name>.json"}`.
6. Browser re-fetches `/api/presets`, the new preset auto-selects, user can
   immediately iterate further.

## Lifecycle, error handling, security

- **Startup:** read credentials via `load_config()`. If missing,
  `SystemExit("Missing credentials.")`. Otherwise login (cached-session-friendly)
  + `connect_mqtt` + `listen_forever`. Log `workshop ready on http://{host}:{port}`.
- **Shutdown:** cancel preview, cancel listener, `await client.close()`.
- **All routes** wrap their inner work in a `_guard` decorator returning
  `{ok:false, error: str(e)}` on `LeproError`/`ValueError`/`KeyError`/`FileExistsError`.
  HTTP 400 on caught errors, 500 on unexpected (which won't normally happen).
- **No auth.** This is a LAN-only dev tool. The Lepro MCP server has a token
  for that reason; the workshop does not. Document this in the spec and the
  startup banner.

## Testing strategy

- **`tests/test_workshop.py`** — pure-function tests for `extract_palette` and
  `apply_color_map`. ~10 tests, no network, no hardware. Run in <1s.
- **Manual smoke test** — start the server, open in browser, click a preset,
  change one color, hit Preview, watch the lamp.
- **No automated UI / e2e tests.** This is a personal-use tool; the cost of
  Selenium/Playwright vastly exceeds the value here. Manual verification covers
  the dev workflow.

## File plan

- `workshop.py` (create) — the script. ~400 lines incl. inlined HTML/CSS/JS.
- `tests/test_workshop.py` (create) — pure-function tests.
- `README.md` (modify) — add `## Preset workshop` section with run command +
  feature description.

## On Screen 2 (deferred)

Once this ships, the next research session should:

1. Capture the Lepro app's DIY screen's six animation modes
   (Steady/Breathe/Gradient/Leftward/Rightward/Circle) and verify their d50
   on the TB1.
2. Capture a custom-drawn per-LED pattern and decode how each ring's
   `#I00:N01:P1000{N}{colors}...` carries per-segment color assignments.
3. Test the brightness slider in the DIY screen and capture the resulting d50
   diffs to learn whether brightness lives in d3/d52 or inside d50.
4. Repeat steps 1-3 with the speed slider.

These results unblock the Screen 2 build as its own brainstorm-spec-plan
iteration.

## Open questions

None. Defaults baked in. Spec is implementation-ready.
