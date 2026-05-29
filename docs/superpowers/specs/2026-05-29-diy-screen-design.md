# DIY Screen — Design Spec

**Date:** 2026-05-29
**Status:** Approved
**Repo:** git@github.com:FrankLaVigne/LeproTB1.git
**Sibling refs:** `workshop.py` (preset workshop UI), `D50_FORMAT.md` (protocol decode), `2026-05-29-preset-workshop-design.md` (prior workshop spec).

## Goal

A web UI ("the DIY screen") that mimics the Lepro app's DIY editor: a clickable
three-ring lamp visualization the user paints on, four tools
(Draw/Fill/Erase/Back), a color picker, the six confirmed motion effects,
speed + brightness sliders, and Save/Reset. Every paint stroke updates the
physical lamp in real time. Saving writes a single-frame preset into the
existing `presets/` library so it appears in the workshop's preset browser.

Builds on everything we decoded today:
- Static patterns via `N01:P1000{N}{colors}F21000{G}{lengths}U3V3<tail>;`
- 196-LED hardware resolution (`length=1` groups confirmed working)
- Six motion effects with known tail formats
- Brightness on `d52`, orthogonal to `d50`

## Non-goals

- **No Music Sync, Favorites, or AI tabs** (out of scope; we only have AI
  workshop and DIY).
- **No saved-color palette row** (Lepro app's "3 saved colors" — defer; presets
  serve a similar role).
- **No multi-step undo history** (only one-level undo via Back, last action only).
- **No multi-frame DIY-built animations** (DIY saves one static frame +
  effect/speed; the effect tail produces motion at the firmware level).
- **No new dependencies.** Native HTML5 `<input type="color">` for the picker;
  no React/Vue/Svelte; no build step.
- **No file-system writes outside `presets/`.**

## Architecture

One `workshop.py` process. Two top-level pages share one persistent
`LeproClient`:

```
                  GET / ──► existing preset workshop (unchanged)
Browser ──► aiohttp ─┤
                  GET /diy ──► the new DIY editor

       Both pages ──► shared persistent LeproClient ──► MQTT/TLS ──► lamp
```

The DIY page is implemented as a second inline HTML string (`_PAGE_DIY`)
served alongside the existing `_PAGE` constant. Backend gains four new routes
(see "Routes" below); existing routes are untouched.

### Why one process and not a separate `diy.py`

Two `LeproClient` instances from the same account would fight for the single
MQTT session slot (same issue we hit with `stock_lamp.py` + workshop earlier
this session). Co-hosting in `workshop.py` reuses one client and one MQTT
connection across both pages.

## Data model

**The canonical state is a 196-LED array** (`leds: list[str | None]` with
exactly 196 elements). Each element is either:
- A 6-char uppercase hex string (e.g. `"FF8000"`), or
- `null` (= LED off / black).

This is true regardless of which resolution toggle is active. The 48-segment
mode is a **UI overlay** that groups LEDs visually and amplifies clicks across
4-or-5 LEDs at a time — but the underlying array is always 196.

**Switching resolution preserves information:** going 196 → 48 then 48 → 196
loses nothing because the 196-LED array stays authoritative. The 48-mode
display picks one LED per segment as the "shown" color (the first LED of the
segment).

**Effect mode + speed** live separately from `leds`. They compose into the
d50 tail at generation time, not into the LED array. Brightness is the
orthogonal `d52` field, never touched by paint or effect changes.

### Segment → LED mapping (deterministic)

Documented as constants in code so both client and server agree:

```python
OUTER = [(0, 4), (4, 8), (8, 12), ..., (84, 88)]   # 22 segments × 4 LEDs = 88
MIDDLE = [
    (88, 92), (92, 96), (96, 100), (100, 104),
    (104, 108), (108, 112), (112, 116), (116, 120),
    (120, 124), (124, 128), (128, 132), (132, 136),
    (136, 140), (140, 145), (145, 150),              # last 2 are 5-LED
]                                                    # 13×4 + 2×5 = 62
INNER = [
    (150, 154), (154, 158), (158, 162), (162, 166),
    (166, 170), (170, 174), (174, 178), (178, 182),
    (182, 186), (186, 191), (191, 196),              # last 2 are 5-LED
]                                                    # 9×4 + 2×5 = 46
```

5-LED segments are placed at the *end* of each variable-count ring (last 2
segments of middle and inner). This is a simple, predictable distribution.
It may not exactly match the Lepro app's internal mapping — that doesn't
matter, because we generate the d50 ourselves and the firmware honors any
valid grouping.

## Pure functions (testable, no hardware)

### `segments_to_leds(ring: str, segment_idx: int) -> range`
`ring` is `"outer"`, `"middle"`, or `"inner"`. Returns a `range(start, stop)`
over the LED indices in that segment. Pure lookup over the constants above.

### `build_d50_from_leds(leds, effect, speed) -> str`
The heart of the page. Takes the 196-LED array + effect name + speed (0–100),
returns the d50 string. Algorithm:

1. **Compress** the LED array into runs of equal-color consecutive LEDs.
   `None` (off) is treated as the color `000000`.
2. **Build the palette** from the *distinct colors* of the runs, in
   first-occurrence order. (Duplicates are allowed — we confirmed today that
   `P10003 FFFFFF FF0000 FFFFFF` is a valid palette.)
3. **Build the lengths string** as 4-hex-char-each big-endian values.
4. **Compute the effect tail** via `effect_tail(effect, speed)`.
5. **Assemble:** `f"N01:P1000{N}{colors}F21000{G}{lengths}U3V3{tail};"`.

Total `lengths` must sum to 196; the function asserts this.

### `effect_tail(name: str, speed: int) -> str`
Looks up one of six confirmed tails:

| Effect | Tail template |
|---|---|
| Steady    | `000640000E1` |
| Breathe   | `000640000E4{sp}0000{sp}1664` |
| Gradient  | `100640000E3{sp}C2O6{sp}` |
| Leftward  | `00164{sp}E1` |
| Rightward | `00264{sp}E1` |
| Circle    | `100640000E1C2O6{sp}` |

`{sp}` is a 4-char hex value, computed from the 0–100 speed via the reference
integration's log-scale formula (re-used from existing `_speed_to_hex` in
`lepro.py`).

## Routes

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET`  | `/diy` | — | the DIY HTML page |
| `POST` | `/api/diy/paint` | `{leds, effect, speed}` | `{ok}` |
| `POST` | `/api/diy/save`  | `{name, leds, effect, speed}` | `{ok, path}` |
| `POST` | `/api/brightness` | `{value: 0-1000}` | `{ok}` |

Existing routes (`GET /`, `GET /api/presets`, `GET /api/presets/{name}`,
`POST /api/power`, `POST /api/preview`, `POST /api/stop`, `POST /api/save`)
are untouched.

### `POST /api/diy/paint` semantics

1. Validate `leds` is exactly 196 entries; each is either a 6-hex string or
   `null`.
2. Validate `effect` is one of the six known names.
3. Validate `speed` is `0 ≤ speed ≤ 100`.
4. `d50 = build_d50_from_leds(leds, effect, speed)`.
5. `await _client.send_raw({"d1": 1, "d2": 2, "d50": d50})`.
6. Return `{ok: true}` (no payload echo — frontend already knows its state).

Validation errors return `{ok: false, error: str}` with status 400, never a
traceback.

### `POST /api/diy/save` semantics

1. Sanitize `name` via existing `_sanitize_name`.
2. If `presets/{name}.json` already exists → refuse with
   `{ok: false, error: "preset '{name}' already exists; pick a unique name"}`.
3. Build d50 same as `/api/diy/paint`.
4. Construct preset JSON in the **single-frame format** that
   `mars-colors.json` uses:

   ```json
   {
     "name": "<sanitized name>",
     "description": "Built in the DIY editor on 2026-05-29.",
     "captured": "2026-05-29",
     "prompt": "DIY editor",
     "payload": {
       "d1": 1,
       "d2": 2,
       "d50": "<the d50 string>"
     }
   }
   ```

5. Write to `presets/<name>.json` pretty-printed (2-space indent).
6. Return `{ok: true, path: "presets/<name>.json"}`.

After save, the workshop's preset list refreshes on next visit — no
push-update mechanism needed for this iteration.

### `POST /api/brightness` semantics

1. Validate `value` is an integer `0 ≤ value ≤ 1000`.
2. `await _client.send_raw({"d52": value})`. No other fields touched.
3. Return `{ok: true}`.

## Live-paint flow

```
                  user paints / drags
                          │
                          ▼
            local state.leds[i] = color
                          │
                          ▼
                    pushPaint()
                          │
                  ┌───────┴───────┐
                  ▼               ▼
       throttled (100ms)?    POST /api/diy/paint
                  │               │
                  │ (latest        ▼
                  │  state queued) server builds d50 → send_raw
                  │               │
                  ▼               ▼
        flush after 100ms        lamp updates (~500ms cloud RTT)
```

**Throttle is client-side, not server-side.** Frontend coalesces rapid changes
to at most 10 publishes/sec, always sending the *latest* state when the
throttle window expires (a "send latest" pattern, not a queue).

Brightness slider changes follow the same throttle but call `/api/brightness`
instead of `/api/diy/paint`. Effect button changes are immediate (no throttle
— single click, single publish).

## Frontend layout

Two-column-less single-page layout (centered narrow column on desktop,
full-width on mobile). Top to bottom:

1. **Header strip**: two tabs (`🎨 Workshop` / `✏️ DIY*`) on the left, power
   buttons (`⏻ On` / `⏻ Off`) on the right.
2. **3-ring SVG canvas**: clickable arcs. 48-mode shows 22+15+11=48 arcs;
   196-mode shows 88+62+46=196 thinner arcs. Off LEDs render with a thin
   border but background-color fill so the structure is visible.
3. **Toolbar row**: `✏️ Draw* | 🪣 Fill | 🧽 Erase | ↩ Back`  on the left;
   `48 | 196` resolution toggle on the right.
4. **Color card**: native `<input type="color">` (HSL via OS picker) + 7
   quick-pick swatches (red, orange, yellow, green, cyan, blue, purple).
5. **Effect card**: 3×2 grid of effect buttons (Steady highlighted by default).
   Below: Speed slider (⚡ icon, 0–100) and Brightness slider (☀ icon, 0–100).
6. **Save row**: variant name input (default `diy-YYYY-MM-DD-N` where `N`
   increments to avoid clashes against existing `presets/`), `💾 Save`
   button, `↺ Reset` button.

### Visual style

Same as existing `workshop.py`:
- Dark theme (`#111` background, `#1c1c1f` cards)
- Cyan accents (`#5fd9d9`) for selected/active states
- Round-pill buttons
- System font stack
- No external CSS or JS dependencies; inlined in `_PAGE_DIY`

### SVG canvas details

- Outer radius: ~140px on desktop, scaling for mobile.
- Inner hole: ~40px radius (matches the lamp's physical center).
- Three concentric rings with a small gap between (~6px).
- Each arc is a SVG `<path>` element with a `data-led-start` / `data-led-end`
  attribute identifying its LED range. Click and drag-while-mousedown both
  call the paint handler with the start/end range.

## Tools

- **Draw** — click or click+drag paints arcs with the current color.
- **Fill** — single click anywhere on the canvas → set all 196 LEDs to the
  current color. Same as Lepro's Fill bucket.
- **Erase** — click+drag sets LEDs to `null` (off).
- **Back** — undoes the *last action* (single-step undo).

Action granularity for Back: each user gesture (single click in Draw, single
Fill, single Erase drag) is one action. The previous full `leds` snapshot is
stashed before applying the action; Back restores it.

## Resolution toggle

A small `48 | 196` segmented toggle in the toolbar. Default: **48** (matches
the Lepro app). Click `196` to switch to per-LED mode.

Switching 48 → 196 re-renders the canvas with 196 arcs; the underlying `leds`
array is unchanged so any painted state is preserved.

Switching 196 → 48 re-renders with 48 arcs. The canvas display picks one LED
per segment as the "shown" color (the first LED of the segment, by index).
Per-LED variation within a segment is preserved in the underlying array; it
just isn't visible in 48-mode display.

## Save naming convention

Default value of the variant name input: `diy-YYYY-MM-DD-N` where:
- `YYYY-MM-DD` is today's date.
- `N` is the smallest positive integer such that
  `presets/diy-YYYY-MM-DD-N.json` does not already exist.

User can override the name freely. Names must pass `_sanitize_name`
(kebab-case, no path separators, etc.) — same validator as the workshop.

## Lifecycle, error handling, security

- **Startup:** unchanged from current `workshop.py`. Page-serving is
  declarative; no per-page initialization needed.
- **Validation:** every paint/save/brightness payload is fully validated
  server-side. Bad input returns `{ok:false, error}` with HTTP 400. No
  tracebacks leak.
- **Concurrency:** paint requests are not queued — the latest one wins. If
  two browser tabs are open and both paint, the order is whatever order the
  server receives them; the latest publish wins on the lamp. Acceptable for
  this iteration.
- **Auth:** still LAN-only, no token (same as workshop today). Documented in
  the workshop README section.

## Testing

- **`tests/test_workshop.py`** — gains tests for:
  - `segments_to_leds` (one test per ring + a "no segment overlap" coverage
    test for each ring).
  - `build_d50_from_leds` (one-color, two-color compression, three-color,
    all-off, single-LED-lit, single-LED-off-among-lit, single full ring per
    color × three rings, plus effect-tail composition for each of the six
    effects).
  - `effect_tail` (6 cases, one per effect; plus a speed scaling test).
- **Manual smoke test**: open the DIY page, paint a segment, watch the lamp
  update; switch resolution; try each tool; switch effects; save → verify it
  shows in workshop; reset → verify canvas clears.
- **No automated UI / e2e tests.** Same call as the workshop: cost of
  Selenium/Playwright vastly exceeds value for a personal tool.

## File plan

- `workshop.py` (modify) — add pure functions (`segments_to_leds`,
  `build_d50_from_leds`, `effect_tail`), four new route handlers, new
  `_PAGE_DIY` constant, wire routes into `build_app`. Net: ~500 lines added.
- `tests/test_workshop.py` (modify) — add ~12 new tests for the pure
  functions.
- `README.md` (modify) — extend the existing `## Preset workshop` section
  with a note about the DIY page at `/diy`.

## Open questions

None. Defaults baked in.
