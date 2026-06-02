# Motion Library — Design

**Date:** 2026-06-02
**Status:** Approved (Approach A — motion library as a first-class concept)

## Goal

Catalog every unique animation **motion** the Lepro app can produce, independent of
color, and let the user play any of them on the TB1 in **any palette** — strictly
better than the phone app, which locks each motion to its AI-chosen colors.

## Background & evidence

The Lepro app's "LightGPM Designs" screen shows the same motion patterns for every
prompt/palette — roughly 72 were observed loaded, but that number is soft (it is
whatever the app had loaded; the design does not hardcode it). The unit of capture
is the **motion program**: a d50 string with its palette stripped.

Empirical findings (2026-06-02 analysis of the existing `presets/`):

1. **Palette-stripping works.** `purple-pink-tour` (35 frames) vs `white-blue-tour`
   (its mechanical recolor, verified on the lamp): 35/35 identical signatures after
   replacing palette colors with placeholders.
2. **Cross-palette motion identity is real.** `christmas[0]` (2-color) and
   `cyberpunk[1]` (6-color) are byte-identical after the palette block except for
   **one digit in the `R30xxxx` field**. Same motion, different palette size.
3. **Current corpus:** 141 frames across 7 presets → 95 strict signatures → roughly
   60 unique motions after loose dedup (exact number determined at build time).

The lamp mode for all of this is `d2=2` (segmented); motions are replayed verbatim
(we cannot decode the motion fields themselves yet — that is the grammar project's
frontier, out of scope here).

## Concepts

- **Capture / preset** (`presets/*.json`) — raw evidence. A recording session's
  frames, exactly as the lamp reported them. Never modified by this feature.
- **Motion** — one unique animation program, identified by palette-independent
  signature. Lives in the catalog. Has an auto-ID and an optional user name.
- **Catalog** (`motions.json`, project root — same location convention as `animations.json`) — the database of motions, distilled from
  presets by the merge operation. User edits (names) live here and are never lost.

## Relationship to the existing Animations tab

The Animations tab (`web/animations.py`, spec 2026-06-01) groups whole **presets**
under the working hypothesis "one preset = one animation" — which its own docstring
flags as conjecture. The app screenshot and the cross-palette analysis (this spec's
Background) establish that the real unit is the **frame**. The two features coexist
with distinct jobs:

- **Animations tab** — the capture workflow (start/save/cancel) and preset-level
  management (grouping recolored variants of the same capture session). Unchanged,
  except: its "N unique animations / ~72 target" counter is replaced by the motion
  catalog count ("N unique motions", no hardcoded target).
- **Motions tab** (this feature) — the frame-level catalog: the actual library of
  unique animation programs.

Specific reconciliations:

1. `animations.frame_fingerprint` (first-palette-block-only, 40-char truncation)
   stays for preset grouping; `motions.motion_signature` (all blocks, full length,
   strict+loose) is the frame-level identity. They serve different purposes and do
   not share code.
2. `server._recolor_preset` only recolors the **first** palette block of each frame
   — an existing bug for per-ring (`#I00/#I01/#I02`) and multi-program frames whose
   later blocks keep old colors. It is refactored to delegate to the motion
   engine's palette remapping, which handles all blocks. Existing Animations-tab
   tests must pass unchanged; a new test covers the per-ring case.
3. `api_captures_save` keeps its `matched_animation` response field and gains the
   `motions` merge-result field.

## Section 1 — Motion engine (`web/motions.py`)

Pure functions, no I/O (house style, like `web/lampview.py`).

**`extract_palette(d50) -> list[PaletteRef]`** — finds every palette block:
- `P1000{N}{colors}` — N single-digit, then N×6 hex chars. Appears in flat
  N01/N02/N03 programs and inside each `#I00/#I01/#I02` per-ring section.
- `P4000{N}{...}` (cyberpunk variant, e.g. `P40005e500e500e500e500e500` —
  structure unconfirmed, see D50_FORMAT.md): the block is *detected* (regex
  `P4000\d`) but its internal structure is not parsed. Motions containing P4
  blocks are marked `"recolorable": false` in the catalog and play only in their
  original colors — never risk corrupting a payload we don't understand.
Returns ordered references: (position, count, colors) per block.

**`motion_signature(d50) -> (strict, loose)`**
- **strict**: palette colors replaced by `#` placeholders, counts kept, everything
  else byte-preserved. (This is what matched purple-pink ↔ white-blue 35/35.)
- **loose**: strict + palette counts normalized to a fixed token + the **five**
  digits following `R3` masked (corpus-verified 2026-06-02: all 130 `R3` fields
  across every preset have exactly 5 digits, e.g. `R301111`/`R302011`). (This is
  what unifies the christmas ↔ cyberpunk twins.)
Signatures are stored as SHA-1 of the normalized strings.

**`remap_colors(d50, mapping: dict[str, str]) -> str`** — the core primitive:
replaces colors inside palette blocks per an explicit old→new mapping.
Case-insensitive matching; replacements written uppercase; unmapped colors and all
bytes outside palette blocks untouched. This is exactly the operation that created
`white-blue-tour` from `purple-pink-tour` (lamp-verified), so it is tested
byte-for-byte against that ground truth.

**`recolor(d50, new_palette: list[str]) -> str`** — built on `remap_colors`:
the d50's distinct colors (order of appearance across all palette blocks) map to
`new_palette[i % len(new_palette)]`. All occurrences substitute consistently, so
per-ring sections stay in sync. Note: this substitutes *every* distinct color,
including incidental program colors (e.g. the `00004D` accent in N03 programs, the
`ffffff` flash) — the user's palette fully owns the motion. (App-faithful
"incidentals stay fixed" behavior would require cross-palette inference — out of
scope, revisit if it looks wrong on the lamp.)

**`merge_preset(catalog: dict, preset: dict, preset_name: str) -> MergeResult`** —
for each frame's d50: compute signatures; if loose sig is new, create a motion entry
(auto-ID `motion-NNN` in discovery order, reference = this d50 + its extracted
palette); if known, append to `sources` and record the strict variant if unseen.
Returns counts (new / known). Never modifies `name` fields. Idempotent.

**Catalog entry shape:**

```json
{
  "id": "motion-007",
  "name": null,
  "loose_sig": "ab3f09…",
  "strict_variants": ["9c2e41…", "d4108a…"],
  "reference": {
    "d50": "N02:P10002FF0000008000U510…",
    "palette": ["FF0000", "008000"],
    "source": "christmas[0]"
  },
  "format": "N02",
  "recolorable": true,
  "sources": ["christmas[0]", "purple-pink-tour[3]", "cyberpunk[1]"],
  "first_seen": "2026-05-28"
}
```

`format` is one of `N01`, `N02`, `N03`, `per-ring` (has `#I` sections).
`first_seen` is the `captured` date of the source preset.
The reference is the **first capture seen** for that motion; recoloring always
starts from the reference d50.

## Section 2 — Catalog build, endpoints, capture integration

**The catalog is a database, not a generated artifact.** Every build operation
merges; user names are never lost; rebuilds are idempotent.

**Rebuild** — `python -m web.motions` (CLI) and `POST /api/motions/rebuild` (HTTP):
scan `presets/*.json` in sorted filename order, frames in order, merge each into the
catalog, write `motions.json` (project root — keeping it out of `presets/` so the preset scanner never ingests it).

**Endpoints** (handlers in `web/server.py`, logic in `web/motions.py`):

| Endpoint | Body | Returns |
|---|---|---|
| `GET /api/motions` | — | the catalog + counts |
| `POST /api/motions/rebuild` | — | `{added, known, total}` |
| `POST /api/motions/{id}/play` | `{"palette": ["FF0000", …]}` (1–9 colors) | `{ok}` / 409 |
| `POST /api/motions/{id}/rename` | `{"name": "orbit fade"}` | `{ok}` |

**Play** reuses the `_preview_task` infrastructure: recolor the reference d50 with
the request palette, send `{"d1": 1, "d2": 2, "d50": <recolored>}` to the lamp,
hold it (single send — the firmware animates autonomously). Mutex rules identical to
preset preview: 409 if capture/clock/ticker is running; `/api/stop` stops it. The
active-mode banner shows `🎨 Motion — <name or id>` (via the shared `_active_mode()`,
so the web cockpit and the TUI both display it).

**Capture integration.** `/api/captures/save`, after writing the preset file, calls
`merge_preset` for it and includes the result in its response:

```json
{"ok": true, "saved": "capture-…", "motions": {"new": 2, "known": 2, "catalog_total": 62}}
```

The Animations tab surfaces this: "2 new motions discovered! Catalog: 62 unique."
Completeness signal = captures repeatedly reporting 0 new motions (no fixed
denominator; the ~72 figure is not hardcoded anywhere).

## Section 3 — Motions tab (cockpit UI)

New tab in the cockpit shell, same pattern as the Animations tab.

- **Shared palette picker** (top bar): native color inputs, add/remove, 1–9 colors.
  Every Play uses the current picker palette. Defaults to 2 colors (red/blue).
- **Motion cards** (grid): name or auto-ID, format badge, reference palette
  swatches, source count, Play button. Currently-playing card highlighted.
  Non-recolorable motions (P4 blocks) show a "original colors only" badge and
  ignore the picker palette when played.
- **Inline rename**: click the name → edit → `POST /api/motions/{id}/rename` (same convention as the existing `/api/animations/{id}/rename`).
- **"▶ next unnamed"**: plays the first unnamed motion and focuses its name input —
  the workflow for naming the whole catalog by watching the lamp.
- **Rebuild button** + catalog counters ("62 motions · 14 named").
- **No motion thumbnails** — motions can't be rendered (undecoded formats); the
  lamp is the preview.
- Mutex conflicts surface as the existing 409 toast pattern.

## Section 4 — Testing

**Ground-truth recolor test (the strongest):** `white-blue-tour` is a lamp-verified
mechanical recolor of `purple-pink-tour` (8000FF→FFFFFF, FFC0CB→0000FF). Therefore,
for all 35 frames:
`remap_colors(purple_pink[i], {"8000FF": "FFFFFF", "FFC0CB": "0000FF"}) == white_blue[i]`
byte-for-byte.

**`tests/test_motions.py`** (pure, real captured data as fixtures):
- `extract_palette` on every format: N01, N02, N03, `#V:` prefix, per-ring `#I`,
  cyberpunk `P4`
- strict signature: all 35 purple-pink ↔ white-blue pairs match
- loose signature: christmas[0] ↔ cyberpunk[1] unify
- recolor ground truth (above); recolor with fewer colors than reference (cycling)
- merge: new/known counting, idempotent rebuild, name preservation across rebuilds

**`tests/test_motions_api.py`** (fake client + monkeypatched globals, same pattern
as `test_lamp_leds.py`): GET catalog, play (success + 409 mutex), rename, rebuild,
capture-save integration response shape.

**UI:** not unit-tested (consistent with existing cockpit JS); exercised manually.

## File layout

| File | New/Modify |
|---|---|
| `web/motions.py` | new — engine + merge + CLI entry |
| `motions.json` (project root) | new — the catalog (created by first rebuild) |
| `web/server.py` | modify — 4 endpoints, capture-save hook, `_PANEL_MOTIONS` + tab registration, `_recolor_preset` delegation, `_active_mode()` motion branch, Animations-tab counter |
| `tests/test_motions.py`, `tests/test_motions_api.py` | new |
| `tests/test_server.py` / `test_animation.py` | existing tests must keep passing through the `_recolor_preset` refactor |

The Motions panel follows the established pattern: a `_PANEL_MOTIONS` string
constant in `web/server.py` with inline `<style>`/`<script type="module">`, same
as `_PANEL_ANIMATIONS`/`_PANEL_CLOCK`/etc.

## Out of scope

- Decoding the motion fields themselves (U/T/X/S/O/R semantics) — grammar project
- Motion thumbnails/previews in the UI (requires decode)
- Editing or composing new motions from scratch
- Multi-frame *sequence* playback of motion sets (presets already do this)
