# Preset Capture Session — Design Spec

**Date:** 2026-05-28
**Status:** Approved (no implementation plan required — see "On the plan step" below)
**Repo:** git@github.com:FrankLaVigne/LeproTB1.git

## Goal

**Grow the `presets/` library by capturing more Lepro AI / app animations**, using
the existing `cli.py capture` workflow we've already used for `mars_colors`,
`christmas`, `cyberpunk`, and `hulk`. The user's actual usage pattern is to set
a color palette in the Lepro app, then cycle through the motion animations the
app offers for that palette — so each captured preset is one animation in one
palette, saved under a `<palette>-<animation>.json` filename.

This spec exists to make the loop explicit and the exit criteria honest. No new
code, no new dependencies. The deliverable is **new JSON files in `presets/`**.

## Non-goals

- No parser for the d50 format (the user's earlier pasted spec is intentionally
  shelved as a future iteration; capturing more material first makes parsing
  more useful later).
- No automation / helper script around `cli.py capture` (Option B from
  brainstorming; can be added in a 10-minute iteration if friction warrants).
- No remix / recolor / re-time logic.
- No new docs beyond what naturally falls out of the JSON files' own
  `description` fields. `README.md` and `REVERSE_ENGINEERING.md` already cover
  the format and the workflow.
- No multi-animation-per-capture chunking. Each capture is one animation.
- No formal implementation plan in `docs/superpowers/plans/`. This spec
  *replaces* the plan step because the work is a procedural session, not code.

## Workflow

Per animation (repeat until exit criterion is hit):

1. **User** sets the color palette in the Lepro app (once per palette family),
   selects one specific animation, lets it play.
2. **User** tells the operator the palette name + animation name (e.g.
   "red" / "wave"). Names become kebab-case in the filename.
3. **Operator** runs `.venv/bin/python cli.py capture --seconds 30` and
   announces "go" so the user can switch to the chosen animation in the app
   within the first ~5 seconds of the window.
4. The capture prints distinct d50 payloads as the lamp emits state updates.
5. **Operator** reads the output, deduplicates identical d50 strings, and
   writes `presets/<palette>-<animation>.json` in the **multi-frame format**
   (same shape as `presets/christmas.json` — a `frames` array with
   `frame_duration_ms`), or the **single-frame format** if only one distinct
   payload was captured (same shape as `presets/mars_colors.json` — a single
   `payload` object).
6. **Operator** test-plays the preset to verify it visibly animates (or
   visibly does what's expected for a static look). The verification result is
   recorded honestly in the preset's `description`.
7. **Operator** commits the new preset file. Optionally pushes at the end of
   the session rather than per preset.

## File naming

- Format: `<palette>-<animation>.json`
- Both segments are kebab-case (`red-wave`, not `RedWave` or `red_wave`).
- The palette segment may match an existing preset family (e.g. `cyberpunk`
  already exists; `cyberpunk-pulse.json` extends that family).
- If the user prefers a single descriptive name (e.g. `aurora.json`) for a
  one-off look, that's allowed but discouraged — the family convention makes
  the library easier to browse as it grows.

## Capture window

Default **30 seconds** per capture. Rationale:

- A focused single animation cycles fully in ~10–15s; 30s gives buffer + 2–3
  cycles.
- Shorter than the 90s windows we used for AI-prompt captures, where we needed
  to wait for the LightGPM AI to finish generating before the lamp settled into
  the effect.

Window can be bumped to 60s or 90s if a 30s capture looks incomplete (e.g. the
animation has a long phase the script didn't catch). Window can be shortened to
15s for clearly-static "look" presets.

## Per-preset honesty fields

Each `presets/<name>.json` includes a `description` field with:

- The palette + animation name as the user named them in the app (so the
  filename and prose match).
- Whether the captured payloads **actually animate when replayed** via
  `play_preset.py`. The operator tests at least once and writes one of:
  - `"replays as animation"` — motion confirmed on the TB1 via replay
  - `"replays as solid color"` — payload accepted but no motion (lamp goes
    static — known TB1 limitation for some d50 effect tails)
  - `"not yet verified on lamp"` — captured but not test-played in this session
- Capture date (`captured: YYYY-MM-DD`).
- Optional `notes:` for anything else worth recording (e.g. "captured during
  after-hours session", "palette dominantly red+green+gold").

This honesty matters because it prevents shipping presets we *think* animate
but actually replay as solid — a failure mode we already saw with `clockwise` /
`circular` from the reference d50 catalog.

## Exit criteria

The session ends when ANY of these is true:

- The user signals they've gotten the looks they want.
- The user is tired of cycling animations in the app.
- We reach ~10–15 new presets (a natural batch size that doubles the library).
- A connectivity / session-conflict issue makes captures unreliable for more
  than 2 consecutive tries.

After exit:
- Single batch push to `main` of all new preset commits (or one squash commit
  if there are many).
- Operator notes any captured animations that replayed as solid color (so
  future iterations can investigate those payloads specifically).

## On the plan step

A normal brainstorming iteration would now transition to `writing-plans` to
produce a step-by-step implementation plan. **This iteration intentionally does
not.** The "implementation" is a series of capture sessions whose outcome is
empirical data (JSON files) rather than code; a written plan would be either
trivial ("step 1: run capture; step 2: save JSON; step 3: commit; repeat") or
fabricated detail (we don't know which specific animations the user will pick).

The brainstorming `writing-plans` link is intentionally skipped here. Future
iterations that *do* involve code (e.g. the parser, the helper script, the
remix logic) will go through the full brainstorming → spec → plan → execute
cycle as usual.

## What we explicitly skip (cross-referenced)

- **Parser for d50 format** — deferred. Pasted prompt in the conversation
  remains valid future scope; capturing more material first improves the
  parser's signal.
- **`capture_to_preset.py` helper** — deferred. The manual JSON-assembly step
  is ~45s per preset; only worth automating if the session feels chore-like.
- **Remix / recolor** — deferred. Needs the parser first.
- **Comparison / analyzer CLI** — deferred. Needs the parser first.
- **MCP tool exposure of presets** — already planned but separately. Adding
  `play_preset` to the MCP server is its own small iteration.

## Open questions

None. Operator follows the workflow above; user supplies palette + animation
names; session ends when an exit criterion is hit.
