# Per-Ring Color + Brightness ("Rings" tab) — Design

**Date:** 2026-06-04
**Status:** Approved, pending implementation plan

## Goal

Add a fast, dedicated cockpit control for setting each of the lamp's three
concentric rings to its own solid color and brightness — without painting
individual pixels in the DIY canvas.

The lamp has three rings: **outer** (LEDs 0–87, 88 LEDs), **middle**
(88–149, 62 LEDs), **inner** (150–195, 46 LEDs) — 196 total.

## Non-goals (YAGNI)

- Per-ring *effects* (breathe/chase/etc). The d50 effect tail is global, so
  per-ring effects aren't cleanly expressible. Rings render as `Steady`.
- Reading current ring colors back from the lamp. State reads return `{}`
  while the phone app holds the single MQTT session slot, so any "current
  state" would be unreliable. The UI starts from fixed defaults instead.
- Saving/recalling ring combos as presets. Can be layered on later via the
  existing preset library if wanted.

## Backend

### Pure helpers (unit-testable in isolation)

- `scale_hex(hex6: str, pct: int) -> str` — scale an RGB hex by a brightness
  percentage, linear per channel. `pct=0 → "000000"`, `pct=100 → hex6`
  unchanged. Rounds each channel; clamps `pct` to 0–100.
- A ring-list builder that takes the three `{color, bright}` ring specs and
  returns a 196-entry LED list: 88 × scaled-outer + 62 × scaled-middle +
  46 × scaled-inner. Asserts the segment counts sum to 196.

### Endpoint: `POST /api/rings`

Request body:

```json
{ "outer":  {"color": "FF0000", "bright": 100},
  "middle": {"color": "00FF00", "bright": 60},
  "inner":  {"color": "0000FF", "bright": 30} }
```

Handler mirrors `api_diy_paint` (`web/server.py`) step for step:

1. `_check_ticker_mutex()`, `_check_clock_mutex()`, `_check_capture_mutex()`
   — error (409) if any is running. Setting rings does **not** auto-stop
   them; this matches the existing DIY-paint convention.
2. `await _stop_preview()`.
3. Validate: three rings present, each `color` a 6-char hex, each `bright`
   an int 0–100.
4. Build the 196-LED list via the ring-list builder (per-ring brightness
   baked into the RGB — the lamp's global `d3` brightness dims everything at
   once, so per-ring dimming must live in the color values).
5. `d50 = build_d50_from_leds(leds, "Steady", 50)`.
   `apply_lamp_rotation` is intentionally **skipped** — each ring is one
   uniform color, so rotation has no visible effect.
6. `await _client.send_raw({"d1": 1, "d2": 2, "d50": d50})`.
7. Return `{"ok": true}`.

## Frontend — new "Rings" tab

A 6th tab alongside Presets / DIY / Ticker / Clock / Motions.

- Three ring cards (**Outer / Middle / Inner**), each with:
  - a color picker reusing the DIY pattern (`<input type="color">` +
    quick swatches), and
  - a brightness slider 0–100 with a live numeric readout.
- **Live, throttled updates (~100 ms)**, copying DIY's `pushPaint` throttle:
  any color or brightness change rebuilds the full three-ring payload and
  POSTs `/api/rings` once. A trailing pending update flushes after the
  throttle window so the final position always lands.
- **Defaults on open:** white (`FFFFFF`) @ 100% for all three rings.
- Errors (e.g. 409 while the ticker runs) surface in the shared status line,
  same as other tabs.

## Brightness model

Linear RGB scaling (`bright% × channel`). Predictable and simple; may look
perceptually dim at low values, accepted as the starting point. Can revisit
with gamma correction later if desired.

## Testing

- `scale_hex`: exact output at 0 / 50 / 100% for representative colors;
  clamping below 0 / above 100.
- Ring-list builder: segment counts (88 / 62 / 46 = 196); each segment holds
  the correctly scaled color.
- `/api/rings`: valid payload → expected d50 string; malformed payload →
  400; running ticker → 409.
