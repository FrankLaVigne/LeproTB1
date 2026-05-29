# DIY Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/diy` page to `workshop.py` that mimics the Lepro app's DIY editor — clickable 3-ring SVG canvas, Draw/Fill/Erase/Back tools, color picker, 6 motion effects, speed + brightness sliders, Save/Reset — with every stroke live-previewing on the lamp.

**Architecture:** Same `workshop.py` process. Three pure functions (`segments_to_leds`, `effect_tail`, `build_d50_from_leds`) generate the d50 from a canonical 196-LED array + effect + speed. Four new aiohttp routes (`GET /diy`, `POST /api/diy/paint`, `POST /api/diy/save`, `POST /api/brightness`) drive the lamp. Inline HTML/CSS/JS for the editor page.

**Tech Stack:** Python 3.12, existing `aiohttp` + `lepro.LeproClient`. No new deps. Vanilla HTML/CSS/JS, no build step.

---

## File Structure

- `workshop.py` (modify) — add `_OUTER_SEGMENTS`/`_MIDDLE_SEGMENTS`/`_INNER_SEGMENTS` constants, three pure functions, four new route handlers, `_PAGE_DIY` constant, wire routes into `build_app`. Net: ~500 lines added.
- `tests/test_workshop.py` (modify) — add ~15 new tests for the pure functions.
- `README.md` (modify) — extend the existing `## Preset workshop` section with a note about the DIY page at `/diy`.

Eight tasks below; pure functions first (TDD), then routes, then frontend, then docs.

---

### Task 1: Segment-to-LED mapping constants + `segments_to_leds`

**Files:**
- Modify: `workshop.py` (append below the existing constants near `_DEFAULT_FRAME_DURATION_MS`)
- Modify: `tests/test_workshop.py` (append)

- [ ] **Step 1: Append the failing tests to `tests/test_workshop.py`**

```python


# --- segments_to_leds tests ---------------------------------------------------


def test_segments_to_leds_outer_first():
    assert list(workshop.segments_to_leds("outer", 0)) == list(range(0, 4))


def test_segments_to_leds_outer_last():
    assert list(workshop.segments_to_leds("outer", 21)) == list(range(84, 88))


def test_segments_to_leds_middle_first():
    assert list(workshop.segments_to_leds("middle", 0)) == list(range(88, 92))


def test_segments_to_leds_middle_last_two_are_five_LEDs():
    # 13 segments of 4 then 2 segments of 5: indices 13, 14
    assert list(workshop.segments_to_leds("middle", 13)) == list(range(140, 145))
    assert list(workshop.segments_to_leds("middle", 14)) == list(range(145, 150))


def test_segments_to_leds_inner_first():
    assert list(workshop.segments_to_leds("inner", 0)) == list(range(150, 154))


def test_segments_to_leds_inner_last_two_are_five_LEDs():
    # 9 segments of 4 then 2 segments of 5: indices 9, 10
    assert list(workshop.segments_to_leds("inner", 9)) == list(range(186, 191))
    assert list(workshop.segments_to_leds("inner", 10)) == list(range(191, 196))


def test_segments_to_leds_unknown_ring_raises():
    with pytest.raises(ValueError):
        workshop.segments_to_leds("middlering", 0)


def test_segments_to_leds_out_of_range_raises():
    with pytest.raises(IndexError):
        workshop.segments_to_leds("outer", 22)
    with pytest.raises(IndexError):
        workshop.segments_to_leds("middle", 15)
    with pytest.raises(IndexError):
        workshop.segments_to_leds("inner", 11)


def test_segments_total_coverage_is_196():
    total = 0
    for ring, count in [("outer", 22), ("middle", 15), ("inner", 11)]:
        for i in range(count):
            total += len(workshop.segments_to_leds(ring, i))
    assert total == 196
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_workshop.py -k segments_to_leds -v`
Expected: FAIL with `AttributeError: module 'workshop' has no attribute 'segments_to_leds'`

- [ ] **Step 3: Add the constants and function to `workshop.py`**

Place these BELOW the existing `_DEFAULT_FRAME_DURATION_MS = 2500` line:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_workshop.py -k segments_to_leds -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Verify the whole suite still passes**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add workshop.py tests/test_workshop.py
git commit -m "feat: segments_to_leds + ring-segment mapping constants"
```

---

### Task 2: `effect_tail` lookup with speed scaling

**Files:**
- Modify: `workshop.py` (append below `segments_to_leds`)
- Modify: `tests/test_workshop.py` (append)

- [ ] **Step 1: Append the failing tests**

```python


# --- effect_tail tests --------------------------------------------------------


def test_effect_tail_steady_no_speed_field():
    # Steady is the only effect with no {sp} field.
    assert workshop.effect_tail("Steady", 50) == "000640000E1"


def test_effect_tail_breathe_at_speed_50():
    # Breathe uses {sp} twice in the tail.
    tail = workshop.effect_tail("Breathe", 50)
    assert tail.startswith("000640000E4")
    assert tail.endswith("1664")
    # the two {sp} segments are identical 4-hex strings
    assert tail[11:15] == tail[19:23]


def test_effect_tail_gradient_has_C2O6():
    tail = workshop.effect_tail("Gradient", 50)
    assert tail.startswith("100640000E3")
    assert "C2O6" in tail


def test_effect_tail_leftward_format():
    tail = workshop.effect_tail("Leftward", 50)
    assert tail.startswith("00164")
    assert tail.endswith("E1")
    assert len(tail) == 11  # 00164 (5) + sp (4) + E1 (2)


def test_effect_tail_rightward_format():
    tail = workshop.effect_tail("Rightward", 50)
    assert tail.startswith("00264")
    assert tail.endswith("E1")


def test_effect_tail_circle_format():
    tail = workshop.effect_tail("Circle", 50)
    assert tail.startswith("100640000E1C2O6")


def test_effect_tail_speed_zero_special_case():
    # Speed 0 should still produce a valid tail with the well-known "1000" speed slot.
    tail = workshop.effect_tail("Leftward", 0)
    assert tail.startswith("00164")
    assert tail.endswith("E1")


def test_effect_tail_speed_clamps_above_100():
    # Anything > 100 should clamp to 100 (same speed-hex as 100).
    assert workshop.effect_tail("Leftward", 500) == workshop.effect_tail("Leftward", 100)


def test_effect_tail_unknown_effect_raises():
    with pytest.raises(ValueError):
        workshop.effect_tail("WiggleJiggle", 50)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_workshop.py -k effect_tail -v`
Expected: FAIL with `AttributeError: module 'workshop' has no attribute 'effect_tail'`

- [ ] **Step 3: Add to `workshop.py`** (place below `segments_to_leds`)

Add `import math` to the imports at the top of `workshop.py` (alongside `import asyncio`, `import copy`, etc.) — alphabetical position is between `logging` and `os`.

Then append:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_workshop.py -k effect_tail -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Verify the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add workshop.py tests/test_workshop.py
git commit -m "feat: effect_tail (6 effects + log-scale speed encoding)"
```

---

### Task 3: `build_d50_from_leds` (LED array → d50 string)

**Files:**
- Modify: `workshop.py` (append below `effect_tail`)
- Modify: `tests/test_workshop.py` (append)

- [ ] **Step 1: Append the failing tests**

```python


# --- build_d50_from_leds tests ------------------------------------------------


def _all(n, color):
    return [color] * n


def test_build_d50_from_leds_all_white_steady():
    leds = _all(196, "FFFFFF")
    d50 = workshop.build_d50_from_leds(leds, "Steady", 50)
    # Expect a single-color palette, single 196-LED group, steady tail.
    assert d50 == "N01:P10001FFFFFFF21000100C4U3V3000640000E1;"


def test_build_d50_from_leds_treats_None_as_black():
    leds = _all(196, None)
    d50 = workshop.build_d50_from_leds(leds, "Steady", 50)
    assert d50 == "N01:P10001000000F21000100C4U3V3000640000E1;"


def test_build_d50_from_leds_two_color_compression():
    # Outer 88 red, middle+inner 108 off (matches the all-outer-only DIY capture).
    leds = _all(88, "FF0000") + _all(108, None)
    d50 = workshop.build_d50_from_leds(leds, "Steady", 50)
    assert d50 == "N01:P10002FF0000000000F2100020058006CU3V3000640000E1;"


def test_build_d50_from_leds_three_ring_pattern():
    # Outer white, middle blue, inner yellow — same shape as a real DIY capture.
    leds = _all(88, "FFFFFF") + _all(62, "0000FF") + _all(46, "FFFF00")
    d50 = workshop.build_d50_from_leds(leds, "Steady", 50)
    assert d50 == "N01:P10003FFFFFF0000FFFFFF00F2100030058003E002EU3V3000640000E1;"


def test_build_d50_from_leds_single_LED_lit():
    # 47 white + 1 red + 148 white.
    leds = _all(47, "FFFFFF") + ["FF0000"] + _all(148, "FFFFFF")
    d50 = workshop.build_d50_from_leds(leds, "Steady", 50)
    # The palette has 3 colors (white, red, white) because duplicates ARE allowed
    # and group K uses palette index K — see D50_FORMAT.md.
    assert d50 == "N01:P10003FFFFFFFF0000FFFFFFF210003002F00010094U3V3000640000E1;"


def test_build_d50_from_leds_circle_effect_changes_tail_only():
    leds = _all(196, "FFFFFF")
    steady = workshop.build_d50_from_leds(leds, "Steady", 50)
    circle = workshop.build_d50_from_leds(leds, "Circle", 50)
    # palette + lengths are identical; only the tail after U3V3 changes.
    assert steady.split("U3V3")[0] == circle.split("U3V3")[0]
    assert steady.endswith("000640000E1;")
    assert circle.endswith("C2O60018;")  # speed 50 → 0018


def test_build_d50_from_leds_lowercase_hex_normalized_to_uppercase():
    leds = _all(196, "ff8000")
    d50 = workshop.build_d50_from_leds(leds, "Steady", 50)
    assert "FF8000" in d50
    assert "ff8000" not in d50


def test_build_d50_from_leds_rejects_wrong_length():
    with pytest.raises(ValueError):
        workshop.build_d50_from_leds(_all(195, "FFFFFF"), "Steady", 50)
    with pytest.raises(ValueError):
        workshop.build_d50_from_leds(_all(197, "FFFFFF"), "Steady", 50)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_workshop.py -k build_d50_from_leds -v`
Expected: FAIL with `AttributeError: module 'workshop' has no attribute 'build_d50_from_leds'`

- [ ] **Step 3: Implement in `workshop.py`** (place below `effect_tail`)

```python
def build_d50_from_leds(leds: list[str | None], effect: str, speed: int) -> str:
    """Compose a full d50 string from a 196-LED array + effect + speed.

    None values are treated as the color "000000" (off). Duplicate palette
    entries are emitted as-is (verified to work by experiment 2026-05-29 —
    see D50_FORMAT.md). The output is uppercase normalized to match what the
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_workshop.py -k build_d50_from_leds -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Verify the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add workshop.py tests/test_workshop.py
git commit -m "feat: build_d50_from_leds (LED array → d50 string, duplicate palette allowed)"
```

---

### Task 4: Backend routes — `/api/diy/paint`, `/api/diy/save`, `/api/brightness`

**Files:**
- Modify: `workshop.py` — add three POST handlers + register them in `build_app`.

- [ ] **Step 1: Add the three handlers above `_PAGE = """..."""`**

```python
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
        leds = body["leds"]
        effect = body.get("effect", "Steady")
        speed = int(body.get("speed", 50))
        _validate_leds(leds)
        if effect not in _VALID_EFFECTS:
            raise ValueError(f"unknown effect {effect!r}")
        if not 0 <= speed <= 100:
            raise ValueError(f"speed must be 0..100, got {speed}")
        d50 = build_d50_from_leds(leds, effect, speed)
        await _client.send_raw({"d1": 1, "d2": 2, "d50": d50})
        return web.json_response({"ok": True})
    except (LeproError, ValueError, KeyError) as e:
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
        d50 = build_d50_from_leds(leds, effect, speed)
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
    except (ValueError, KeyError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def api_brightness(req):
    try:
        body = await req.json()
        value = int(body["value"])
        if not 0 <= value <= 1000:
            raise ValueError(f"brightness value must be 0..1000, got {value}")
        await _client.send_raw({"d52": value})
        return web.json_response({"ok": True})
    except (LeproError, ValueError, KeyError) as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
```

- [ ] **Step 2: Register the routes in `build_app`**

In `build_app`, locate the existing `app.add_routes([...])` call and REPLACE it with:

```python
    app.add_routes([
        web.get("/", index),
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
    ])
```

- [ ] **Step 3: Smoke-test route registration**

Run:
```bash
.venv/bin/python -c "
import workshop
app = workshop.build_app()
got = sorted(r.method + ' ' + str(r.resource.canonical) for r in app.router.routes())
expected = sorted([
    'GET /', 'HEAD /', 'GET /api/presets', 'HEAD /api/presets',
    'GET /api/presets/{name}', 'HEAD /api/presets/{name}',
    'POST /api/power', 'POST /api/preview', 'POST /api/stop', 'POST /api/save',
    'POST /api/diy/paint', 'POST /api/diy/save', 'POST /api/brightness',
])
assert got == expected, set(expected) ^ set(got)
print('all', len(got), 'routes registered')
"
```
Expected: prints `all 13 routes registered`.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 5: Commit**

```bash
git add workshop.py
git commit -m "feat: DIY backend routes (paint, save, brightness)"
```

---

### Task 5: Wire up `GET /diy` and add a placeholder page

**Files:**
- Modify: `workshop.py` — add `index_diy` handler + register `GET /diy` route + placeholder `_PAGE_DIY` constant (real UI in Task 6).

- [ ] **Step 1: Add the handler and placeholder above the existing `_PAGE = ...` line**

```python
async def index_diy(_req):
    return web.Response(text=_PAGE_DIY, content_type="text/html")


# Real DIY UI inlined in Task 6.
_PAGE_DIY = "<!doctype html><title>diy</title><body>diy loading...</body>"
```

- [ ] **Step 2: Register the route**

In `build_app`'s `add_routes`, ADD `web.get("/diy", index_diy)` directly after `web.get("/", index)`:

```python
    app.add_routes([
        web.get("/", index),
        web.get("/diy", index_diy),
        web.get("/api/presets", api_presets),
        web.get(r"/api/presets/{name}", api_preset),
        web.post("/api/power", api_power),
        web.post("/api/preview", api_preview),
        web.post("/api/stop", api_stop),
        web.post("/api/save", api_save),
        web.post("/api/diy/paint", api_diy_paint),
        web.post("/api/diy/save", api_diy_save),
        web.post("/api/brightness", api_brightness),
    ])
```

- [ ] **Step 3: Smoke-test routes count**

Run:
```bash
.venv/bin/python -c "
import workshop
app = workshop.build_app()
got = sorted(r.method + ' ' + str(r.resource.canonical) for r in app.router.routes())
expected_methods_paths = sorted([
    'GET /', 'HEAD /', 'GET /diy', 'HEAD /diy',
    'GET /api/presets', 'HEAD /api/presets',
    'GET /api/presets/{name}', 'HEAD /api/presets/{name}',
    'POST /api/power', 'POST /api/preview', 'POST /api/stop', 'POST /api/save',
    'POST /api/diy/paint', 'POST /api/diy/save', 'POST /api/brightness',
])
assert got == expected_methods_paths, set(expected_methods_paths) ^ set(got)
print('all', len(got), 'routes including /diy registered')
"
```
Expected: prints `all 15 routes including /diy registered`.

- [ ] **Step 4: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 5: Commit**

```bash
git add workshop.py
git commit -m "feat: GET /diy route + placeholder page"
```

---

### Task 6: The DIY page (HTML/CSS/JS)

**Files:**
- Modify: `workshop.py` — replace the placeholder `_PAGE_DIY` string.

- [ ] **Step 1: Replace `_PAGE_DIY`** with the full inline page.

Find the line:
```python
_PAGE_DIY = "<!doctype html><title>diy</title><body>diy loading...</body>"
```

Replace with:

```python
_PAGE_DIY = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lepro DIY</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { font: 15px/1.4 system-ui, sans-serif; margin: 0;
         background: #111; color: #eee; min-height: 100vh; }
  .wrap { max-width: 540px; margin: 0 auto; padding: 16px; }
  .header { display: flex; align-items: center; justify-content: space-between;
            gap: 12px; margin-bottom: 12px; }
  .tabs a { color: #aaa; text-decoration: none; padding: 6px 12px;
            border-radius: 8px; }
  .tabs a.active { color: #5fd9d9; background: #1f2a2a; font-weight: 700; }
  .power-btns { display: flex; gap: 6px; }
  .power-btns button { padding: 6px 12px; font-size: 13px; border: 0;
                       border-radius: 8px; cursor: pointer; font-weight: 600; }
  .power-btns button.on { background: #2c8f4f; color: #fff; }
  .power-btns button.off { background: #8f2c2c; color: #fff; }
  .card { background: #1c1c1f; padding: 14px; border-radius: 14px;
          box-shadow: 0 4px 16px rgba(0,0,0,.4); margin-bottom: 14px; }
  .lamp-canvas { display: flex; justify-content: center; padding: 12px 0; }
  svg .seg { cursor: pointer; transition: opacity .1s; }
  svg .seg:hover { opacity: .7; }
  .toolbar { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 14px; }
  .toolbar button { padding: 8px 12px; border: 0; border-radius: 8px;
                    background: #2a2a30; color: #eee; cursor: pointer;
                    font: inherit; }
  .toolbar button.active { background: #5fd9d9; color: #111; font-weight: 700; }
  .toolbar .res { margin-left: auto; display: flex; gap: 2px;
                  background: #2a2a30; padding: 2px; border-radius: 8px; }
  .toolbar .res button { padding: 6px 10px; background: transparent;
                         border-radius: 6px; }
  .toolbar .res button.active { background: #5fd9d9; color: #111; }
  h2 { font-size: 12px; margin: 0 0 8px; color: #aaa;
       text-transform: uppercase; letter-spacing: 0.08em; }
  .color-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .color-row input[type=color] { width: 44px; height: 44px; border: 2px solid #444;
                                  border-radius: 50%; cursor: pointer; background: none; }
  .swatch { width: 28px; height: 28px; border-radius: 50%;
            border: 2px solid #333; cursor: pointer; }
  .swatch:hover { border-color: #5fd9d9; }
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
  .btns { display: flex; gap: 8px; margin-top: 12px; }
  .btns button { flex: 1; padding: 10px; border: 0; border-radius: 10px;
                 background: #2a2a30; color: #eee; cursor: pointer;
                 font: inherit; font-weight: 600; }
  .btns button.primary { background: #5fd9d9; color: #111; }
  #status { font-size: 12px; color: #777; margin-top: 8px; min-height: 1.2em; }
</style></head>
<body><div class="wrap">
  <div class="header">
    <div class="tabs">
      <a href="/">🎨 Workshop</a>
      <a href="/diy" class="active">✏️ DIY</a>
    </div>
    <div class="power-btns">
      <button class="on" id="pwr-on">⏻ On</button>
      <button class="off" id="pwr-off">⏻ Off</button>
    </div>
  </div>

  <div class="card lamp-canvas">
    <svg id="lamp" width="380" height="380" viewBox="-200 -200 400 400"></svg>
  </div>

  <div class="toolbar">
    <button class="tool active" data-tool="draw">✏️ Draw</button>
    <button class="tool" data-tool="fill">🪣 Fill</button>
    <button class="tool" data-tool="erase">🧽 Erase</button>
    <button id="back-btn">↩ Back</button>
    <div class="res">
      <button class="res-btn active" data-res="48">48</button>
      <button class="res-btn" data-res="196">196</button>
    </div>
  </div>

  <div class="card">
    <h2>Color</h2>
    <div class="color-row">
      <input type="color" id="picker" value="#ff8000">
      <div class="swatch" style="background:#FF0000" data-hex="FF0000"></div>
      <div class="swatch" style="background:#FF8000" data-hex="FF8000"></div>
      <div class="swatch" style="background:#FFFF00" data-hex="FFFF00"></div>
      <div class="swatch" style="background:#00C000" data-hex="00C000"></div>
      <div class="swatch" style="background:#00FFFF" data-hex="00FFFF"></div>
      <div class="swatch" style="background:#0000FF" data-hex="0000FF"></div>
      <div class="swatch" style="background:#8000FF" data-hex="8000FF"></div>
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
      <span class="icon">⚡</span>
      <input type="range" id="speed" min="0" max="100" value="50">
      <span class="val" id="speed-val">50</span>
    </div>
    <div class="slider-row">
      <span class="icon">☀</span>
      <input type="range" id="bright" min="0" max="100" value="100">
      <span class="val" id="bright-val">100</span>
    </div>
  </div>

  <div class="card">
    <label>Save as</label>
    <input type="text" id="vname" value="">
    <div class="btns">
      <button class="primary" id="save-btn">💾 Save</button>
      <button id="reset-btn">↺ Reset</button>
    </div>
    <div id="status"></div>
  </div>
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

// --- wire UI ---
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
for (const b of $$('.swatch')) b.onclick = () => {
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

// Default save-name: diy-YYYY-MM-DD-N where N is unique.
async function setDefaultName() {
  const today = new Date().toISOString().slice(0, 10);
  const j = await fetch('/api/presets').then(r => r.json());
  const names = (j.presets || []).map(p => p.name);
  let n = 1;
  while (names.includes(`diy-${today}-${n}`)) n++;
  $('#vname').value = `diy-${today}-${n}`;
}

// Mouse handling on the SVG canvas: mousedown + mouseover paint.
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

drawCanvas();
setDefaultName();
</script></body></html>"""
```

- [ ] **Step 2: Smoke-test the page**

Run:
```bash
.venv/bin/python -c "
import workshop
p = workshop._PAGE_DIY
for marker in ('Lepro DIY', 'lamp-canvas', 'Steady', 'Circle', 'Leftward',
               'Rightward', 'Gradient', 'Breathe', 'pwr-on', '/api/diy/paint',
               '/api/diy/save', '/api/brightness', 'res-btn'):
    assert marker in p, 'missing ' + repr(marker)
print('page OK')
"
```
Expected: prints `page OK`.

- [ ] **Step 3: Verify the full repo suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 4: Commit**

```bash
git add workshop.py
git commit -m "feat: DIY page UI (canvas + tools + color + effects + sliders + save)"
```

---

### Task 7: Add Workshop ↔ DIY tab navigation to the existing workshop page

**Files:**
- Modify: `workshop.py` — update the existing `_PAGE` constant's header to include the tabs (the DIY page already has them; we need parity on the workshop page).

- [ ] **Step 1: In `workshop.py`, locate this block inside the existing `_PAGE` constant:**

```html
  <div class="card">
    <div class="header">
      <h1>← Workshop</h1>
      <div class="power-btns">
```

Replace `<h1>← Workshop</h1>` with a tabs block:

```html
      <div class="tabs">
        <a href="/" class="active" style="color:#5fd9d9;background:#1f2a2a;padding:6px 12px;border-radius:8px;text-decoration:none;font-weight:700">🎨 Workshop</a>
        <a href="/diy" style="color:#aaa;padding:6px 12px;border-radius:8px;text-decoration:none">✏️ DIY</a>
      </div>
```

So the resulting block reads:

```html
  <div class="card">
    <div class="header">
      <div class="tabs">
        <a href="/" class="active" style="color:#5fd9d9;background:#1f2a2a;padding:6px 12px;border-radius:8px;text-decoration:none;font-weight:700">🎨 Workshop</a>
        <a href="/diy" style="color:#aaa;padding:6px 12px;border-radius:8px;text-decoration:none">✏️ DIY</a>
      </div>
      <div class="power-btns">
```

(Inline styles are intentional — keeps the existing CSS untouched, no risk of breaking the workshop's other elements.)

- [ ] **Step 2: Verify both pages render with the tabs**

Run:
```bash
.venv/bin/python -c "
import workshop
for page, label in [(workshop._PAGE, 'workshop'), (workshop._PAGE_DIY, 'diy')]:
    assert 'href=\"/\"' in page and 'href=\"/diy\"' in page, f'{label} page missing tabs'
print('both pages have tabs')
"
```
Expected: prints `both pages have tabs`.

- [ ] **Step 3: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 4: Commit**

```bash
git add workshop.py
git commit -m "feat: Workshop ↔ DIY tab nav on the workshop page"
```

---

### Task 8: README update + final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Locate the existing `## Preset workshop` section and the next `##` heading after it.** Insert this paragraph as the LAST paragraph of the section (just before the next `##`):

```markdown

The workshop now also includes a **DIY editor** at `http://<vm-ip>:8081/diy`,
mimicking the Lepro app's DIY screen — a clickable 3-ring SVG canvas (48 app-
matched segments or 196 per-LED resolution via toggle), Draw/Fill/Erase/Back
tools, color picker with quick-pick swatches, the six confirmed motion effects
(Steady/Breathe/Gradient/Leftward/Rightward/Circle), speed and brightness
sliders, and Save (which writes a single-frame preset into `presets/`). Every
stroke updates the lamp live via the cloud, with client-side 100 ms throttling
to coalesce drag movements.
```

- [ ] **Step 2: Verify it landed in the right place**

Run: `grep -B1 -A2 "DIY editor" README.md`
Expected: shows the new paragraph nested inside `## Preset workshop`, not after `## Protocol notes`.

- [ ] **Step 3: Final full-suite run**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failures.

- [ ] **Step 4: Final app build smoke**

Run:
```bash
.venv/bin/python -c "
import workshop
app = workshop.build_app()
print('routes:', len(list(app.router.routes())))
print('build ok')
"
```
Expected: prints `routes: 15` and `build ok`.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: document the DIY editor at /diy"
```

---

## Self-Review

**Spec coverage:**
- Three pure functions (`segments_to_leds`, `effect_tail`, `build_d50_from_leds`) → Tasks 1-3 ✓
- Four routes (`GET /diy`, `POST /api/diy/paint`, `POST /api/diy/save`, `POST /api/brightness`) → Tasks 4-5 ✓
- Single-frame preset save format → Task 4 ✓
- Canonical 196-LED array data model → Task 3 + Task 6 ✓
- 48-segment vs 196-LED resolution toggle → Task 6 (`drawCanvas()` switches on `state.res`) ✓
- Six effects, three tools (Draw/Fill/Erase) + Back undo → Task 6 ✓
- Color picker (native + 7 quick swatches) → Task 6 ✓
- Speed + brightness sliders (live preview, debounced 100 ms) → Task 6 (`pushPaint`/throttle) ✓
- Save naming convention (`diy-YYYY-MM-DD-N`) → Task 6 (`setDefaultName`) ✓
- Workshop ↔ DIY tab navigation on both pages → Tasks 6 + 7 ✓
- Validation + error returns → Task 4 (`_validate_leds`, `_VALID_EFFECTS` set) ✓
- README documentation → Task 8 ✓

**Placeholder scan:** no TBD/TODO. Every step has the actual code or the actual command + expected output. Task 4 references `_HERE` and `_PRESETS_DIR` and `_PAGE` and `_sanitize_name` which are all already in `workshop.py` from earlier work.

**Type consistency:**
- `segments_to_leds(ring: str, segment_idx: int) -> range` — used identically in tests and the routes/UI (frontend has the equivalent JS constants).
- `effect_tail(name: str, speed: int) -> str` — used by `build_d50_from_leds`.
- `build_d50_from_leds(leds, effect, speed) -> str` — called from `api_diy_paint` and `api_diy_save` with the same shape.
- `_VALID_EFFECTS` set referenced in both POST handlers.
- All payload field names (`leds`, `effect`, `speed`, `name`, `value`) match between frontend JS and backend validation.

**Note for the implementer:** Task 1's constants build on Python list comprehensions. If you're confused about why some segments have 5 LEDs and not 4, re-read the spec's "Segment → LED mapping" section. The reason is `62 / 15 = 4.13` and `46 / 11 = 4.18` — not integer. We pick the simplest workable distribution.
