# Preset Capture Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: This plan is procedural (no code), executed manually by an operator paired with the user. It is not subagent- or executing-plans-driven; checklist items below are per-preset gates, not TDD steps. Steps use checkbox (`- [ ]`) syntax for tracking session progress.

**Goal:** Capture additional Lepro app animations into the `presets/` library following the spec at `docs/superpowers/specs/2026-05-28-preset-capture-session-design.md`.

**Architecture:** Procedural — no new code. Per animation: run `cli.py capture`, dedupe the printed d50 payloads, write `presets/<palette>-<animation>.json`, optionally verify via `play_preset.py`, commit. Loop until an exit criterion is hit.

**Tech Stack:** Existing tools only — `cli.py capture` (CLI subcommand), `play_preset.py` (helper script), `git`.

---

## Why this plan is single-task

The brainstorming → spec → plan → execute flow normally produces a multi-task TDD plan. This work has none of those: there is no new code, no tests, no architectural decisions to lock in step by step. The "implementation" is operator-paired-with-user running a capture session whose outcomes (which animations the user picks, how many they pick) cannot be enumerated upfront.

The honest plan is one repeating gate. Listing 15 nearly-identical "capture animation N" tasks would be fabricated detail; this single template is the real shape.

---

## Pre-session checks

- [ ] **Step 1: Confirm credentials and session cache**

Run: `.venv/bin/python -c "import json; c=json.load(open('config.json')); print('account:', c['account'], 'region:', c['region'])"`
Expected: prints your Lepro account email + region `na`.

Run: `ls -la certs/session.json 2>/dev/null && echo cached || echo no-cache`
Expected: either `cached` (good — resumed session won't boot the phone) or `no-cache` (acceptable; first capture will re-auth).

- [ ] **Step 2: Confirm the existing presets you'll be extending**

Run: `ls -1 presets/`
Expected: at minimum `christmas.json`, `cyberpunk.json`, `hulk.json`, `mars_colors.json`.

- [ ] **Step 3: Confirm you're on `main` and clean**

Run: `git status -sb`
Expected: `## main...origin/main` with no `[ahead N]` / `[behind N]` markers and no uncommitted changes.

---

## Per-animation gate (repeat until exit)

Each iteration of this gate produces **one** new preset file and one commit.

- [ ] **Gate 1: User names the palette + animation**

User sets the color palette and selects an animation in the Lepro app, then tells the operator:
- palette name (kebab-case, e.g. `red`, `mars`, `aurora`)
- animation name (kebab-case, e.g. `wave`, `chase`, `breath`, `spin`)

Filename will be `presets/<palette>-<animation>.json`. If that filename already exists in `presets/`, append a numeric suffix (`-2`, `-3`, etc.).

- [ ] **Gate 2: Operator runs a 30s capture**

Run: `.venv/bin/python cli.py capture --seconds 30`
The user has the first ~5s of the window to confirm the animation is playing on the lamp.

If the connection is reliably dropping (the script reports being kicked within a few seconds), abort and consult the "Connectivity issues" section below.

- [ ] **Gate 3: Operator extracts the distinct d50 payloads**

From the capture output, collect every line of the form `[<did>] {'d2': 2, 'd50': '...', ...}` and:
1. Extract the `d50` string value.
2. Deduplicate identical strings (keep first occurrence order).
3. If only one distinct `d50` survives, this is a single-frame preset; otherwise multi-frame.

- [ ] **Gate 4: Operator writes the JSON file**

For **single-frame** presets (one distinct `d50`), use the `mars_colors.json` shape:
```json
{
  "name": "<palette>-<animation>",
  "description": "<from gate 1, plus replay status from gate 5>",
  "captured": "2026-05-28",
  "prompt": "<palette + animation as user named them>",
  "payload": {
    "d1": 1,
    "d2": 2,
    "d50": "<the single distinct d50 string>"
  }
}
```

For **multi-frame** presets (multiple distinct `d50`s), use the `christmas.json` shape:
```json
{
  "name": "<palette>-<animation>",
  "description": "<from gate 1, plus replay status from gate 5>",
  "captured": "2026-05-28",
  "prompt": "<palette + animation as user named them>",
  "frame_duration_ms": 2000,
  "frames": [
    {"d2": 2, "d50": "<first distinct d50>"},
    {"d2": 2, "d50": "<second distinct d50>"},
    ...
  ]
}
```

Frame duration defaults to 2000 ms; bump to 2500–3000 for slower animations if 2000 looks rushed on replay.

- [ ] **Gate 5: Verify on the lamp (one pass)**

Run: `.venv/bin/python play_preset.py <palette>-<animation> --once`

Watch the lamp. Update the preset's `description` with exactly one of:
- `"replays as animation"` — visible motion (pulsing, chasing, cycling)
- `"replays as solid color"` — the lamp accepted the payload but is static (known TB1 limitation for some d50 effect tails)
- `"not yet verified on lamp"` — skipped verification this session

Honesty here matters. If the preset replays as solid when the user expected motion, that is a meaningful data point for the future d50 parser project — record it accurately rather than wishfully.

- [ ] **Gate 6: Commit**

Run:
```bash
git add presets/<palette>-<animation>.json
git commit -m "Add '<palette>-<animation>' preset (from Lepro app '<palette>' palette + '<animation>' animation)"
```

Push can be batched at the end of the session (Gate 7) rather than per preset.

- [ ] **Gate 7 (loop): more, or done?**

If an exit criterion from the spec is met → proceed to Post-session. Otherwise loop back to Gate 1 with the next animation.

---

## Post-session

- [ ] **Step 1: Push all session commits**

Run: `git push 2>&1 | tail -2`
Expected: pushes one or more commits to `origin/main`.

- [ ] **Step 2: Summary**

Operator reports to the user:
- How many new presets were added (and their names).
- How many of those replay as visible animation vs solid color.
- Any animations that consistently produced unusual capture patterns (e.g. a new `N0X` header value, or unusually long `#V:` prefixes), since these are clues for the future d50 parser project.
- Any palettes/animations the user asked for but couldn't be captured cleanly.

---

## Connectivity issues

If captures are repeatedly dropped within a few seconds (single-session conflict with the phone):

1. Note the issue and the affected animation(s) for retry.
2. The fix is the **dedicated Gmail-account share** documented in `REVERSE_ENGINEERING.md` and `lepro-tb1-project` memory — outside the scope of this session.
3. Abort the session cleanly: push any saved presets so far, then exit.

---

## Self-Review

**Spec coverage:**
- Workflow per animation → Gate 1–6 ✓
- Filename naming convention (kebab-case) → Gate 1 ✓
- 30 s default window → Gate 2 ✓
- Dedup + single-frame vs multi-frame formats → Gates 3–4 ✓
- Per-preset honesty (replay status in description) → Gate 5 ✓
- Exit criteria → Gate 7 + Connectivity ✓
- Batched push → Post-session step 1 ✓

**Placeholder scan:** none. The `<palette>-<animation>` placeholders are user-supplied input shape, not unfilled TODOs. Date `2026-05-28` is a real date the operator should update if the session spans midnight.

**Type consistency:** N/A — no code; the JSON shapes for single-frame and multi-frame match the existing `mars_colors.json` and `christmas.json` exactly.

**Note for operator:** if at Gate 3 the capture produced zero distinct d50 lines, the session-conflict probably kicked the script before the lamp emitted state. Restart the capture; do not fabricate a preset.
