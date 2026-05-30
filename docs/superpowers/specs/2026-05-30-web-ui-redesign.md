# Web UI Redesign — Cockpit + Glassmorphism

**Date:** 2026-05-30
**Status:** Approved by user via brainstorm (Cockpit layout + Glassmorphism style + 4 tabs after State absorption + responsive phone/desktop).

## Goal

Replace the five-page, per-page-styled web UI with a single **Cockpit** layout:
the lamp visualizer + power + brightness + active-mode banner + diagnostics
drawer are always visible on the left; the four feature panels (Presets / DIY
/ Ticker / Clock) live as tabs on the right. Glassmorphism aesthetic
(translucent panels, soft blur, glowing accents on lit pixels). Responsive
side-by-side desktop, stacked phone, ~720px breakpoint.

## Approach

A single shared **shell** wraps every page: HTML chrome with the left cockpit
panel, the tab strip, and a slot for the active feature's content. Each
feature's content moves into its own panel string (just the right-side
content — no more per-page header, no more per-page tabs, no more per-page
power buttons). The shell, design tokens, and the cockpit's interactive JS
all live in `static/` so they're shared via aiohttp's static handler.

URLs stay one-per-feature for browser-native nav and deep linking:

- `/` → Presets (new home)
- `/diy`, `/ticker`, `/clock` → as today
- `/state` → **dropped** (visualizer absorbed into left panel; d-fields in a drawer)

## Design system

CSS custom properties in `static/cockpit.css`. Single source of truth — both
Python-rendered pages and any JS that builds DOM elements reference the same
tokens.

### Colors

```css
--bg-grad: radial-gradient(at 30% 20%, #1a1a3a, #06061a 70%);
--text:        rgba(255, 255, 255, 0.92);
--text-dim:    rgba(255, 255, 255, 0.6);
--text-faint:  rgba(255, 255, 255, 0.4);
--panel:       rgba(255, 255, 255, 0.04);
--panel-hi:    rgba(255, 255, 255, 0.08);
--border:      rgba(255, 255, 255, 0.1);
--border-hi:   rgba(255, 255, 255, 0.18);
--accent:      #00ddff;
--accent-soft: rgba(0, 221, 255, 0.15);
--accent-glow: rgba(0, 221, 255, 0.4);
--ok:          #50c878;
--ok-soft:     rgba(80, 200, 120, 0.2);
--danger:      #ff5566;
--danger-soft: rgba(255, 85, 102, 0.15);
--grad-primary:linear-gradient(135deg, #00ff88, #00ddff);
```

### Spacing / radii

```css
--gap-xs: 4px;  --gap-sm: 8px;  --gap: 12px;  --gap-lg: 16px;  --gap-xl: 24px;
--r-sm: 8px;  --r: 12px;  --r-lg: 16px;  --r-pill: 999px;
```

### Glassmorphism panel

```css
.glass {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--r);
  backdrop-filter: blur(20px);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
}
```

### Typography

```css
font-family: system-ui, -apple-system, sans-serif;  /* body */
font-family: ui-monospace, SF Mono, Consolas, monospace;  /* numerics, d-fields */
```

Letter-spacing 0.15-0.25em on small ALL-CAPS labels.

## Cockpit shell

```
┌──────────────────────────────────────────────────────────────────┐
│ ┌────────────────────┐ ┌────────────────────────────────────────┐│
│ │   LEPRO   <devid>  │ │ [Presets] [DIY] [Ticker] [Clock]       ││
│ │                    │ │                                        ││
│ │   ⊙ LAMP VIZ ⊙     │ │ ┌────────────────────────────────────┐ ││
│ │   (always-live     │ │ │                                    │ ││
│ │    SVG)            │ │ │   Active feature's content here    │ ││
│ │                    │ │ │                                    │ ││
│ │   [ACTIVE ⏰ Clock] │ │ │                                    │ ││
│ │                    │ │ │                                    │ ││
│ │   [⏻ ON] [⏻ OFF]   │ │ │                                    │ ││
│ │                    │ │ │                                    │ ││
│ │   ☀ Brightness 80% │ │ │                                    │ ││
│ │   ━━━━━━━━━━──     │ │ │                                    │ ││
│ │                    │ │ │                                    │ ││
│ │   ▸ DIAGNOSTICS    │ │ │                                    │ ││
│ └────────────────────┘ │ └────────────────────────────────────┘ ││
│                        └────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────┘
```

### Left panel (280px on desktop, full-width stacked on phone)

Top to bottom:

1. **Brand row**: `LEPRO` (light, letter-spaced) on left, device ID in monospace on right.
2. **Lamp visualizer**: 240×240 glass disc with the 3-ring SVG inside. Polls `/api/lamp/state` every 2s, parses d50, reverse-rotates to page-space (the existing `static/lamp-utils.js` helpers). Lit pixels glow via `box-shadow`. Renders in 48-mode (segment-level) to match diffuser physics, same as the current State viz.
3. **Active-mode banner**: small glass card highlighting what's currently driving the lamp. Backed by a new `GET /api/cockpit/active` endpoint (see Routes below). Shows one of:
   - `idle` — lamp is on but nothing's actively painting it (last frame just sits there)
   - `⏰ Clock — HH:MM:SS` — clock session running
   - `📈 Ticker — AAPL, IBM` — ticker session running, with the symbols
   - `✏️ DIY paint` — last write was a manual paint
   - `🎨 Preset: <name>` — last write was a preset preview
   - `⏻ Off` — lamp is powered off
4. **Power buttons**: ON / OFF (glass-styled). Same `POST /api/power` as today.
5. **Brightness slider**: 0-100, drives the existing `POST /api/brightness` (d52). Numeric % readout in monospace.
6. **Diagnostics disclosure** (`<details>`): collapsed by default. When open, shows the d-fields grid from the current State page (d1, d2, d3, d4, d5, d30, d50 truncated, d52, d60).

### Right panel (flexible width)

1. **Tab strip**: four glass pills (Clock / Presets / DIY / Ticker). Active tab uses `--accent-soft` background. Each tab is a regular `<a href="/path">` — browser-native nav, deep-linkable.
2. **Active content slot**: the feature's panel string is dropped here verbatim. A panel may render as one card or several (DIY has toolbar + canvas + color + effect + save; Ticker has 3 ring cards + interval + controls; Clock has color pickers + format toggle + start/stop). The shell doesn't impose a one-card layout — the panel HTML controls its own internal structure. Each panel keeps its own `<script type="module">` block for feature-specific JS (paint handlers, ticker polling, etc.) which boots after the shell's `cockpit.js` has set up the left panel.

### Responsive

- `>= 720px`: side-by-side, left fixed 280px, right flex.
- `< 720px`: stacked. Left panel becomes full-width and slightly compressed:
  - Brand row keeps ON/OFF as icon-only buttons (saves vertical space).
  - Lamp viz shrinks to 200×200.
  - Diagnostics disclosure stays.
- Tab strip on phone uses `overflow-x: auto` if the labels don't fit.

## Per-feature panel changes

Each existing page becomes "just the right-panel content" — header, tabs,
and power buttons are removed because the shell provides them.

| Page | Today's wrapper | New (just content) |
|---|---|---|
| `/` (Presets) | Full HTML, tab strip, header, power | Preset list + preview/colors/save controls only |
| `/diy` | Full HTML, tab strip, header, power | Tools row + SVG canvas + color/effect/sliders + save |
| `/ticker` | Full HTML, tab strip, header, power | 3 ring cards + interval + Start/Stop + status |
| `/clock` | Full HTML, tab strip, header, power | Color pickers + 12/24h + Start/Stop + status (visualizer in left panel covers the clock face) |
| `/state` | (dropped — absorbed into left panel) | — |

The clock page's client-side visualizer is REMOVED — the left-panel
visualizer is now showing the clock pixels live (since the lamp itself is
updating). The right panel becomes just the configuration.

## Architecture

### New / restructured files

```
static/
  cockpit.css         (new — design tokens + .glass + responsive shell)
  cockpit.js          (new — left panel updates: lamp viz, mode banner, brightness, diagnostics)
  lamp-utils.js       (already exists — parseD50_N01, unrotateToPage, computeClockPositions)
workshop.py
  _SHELL              (new — module function or template string that wraps a per-feature panel string)
  _PANEL_PRESETS      (new — just the right-side content, replaces _PAGE)
  _PANEL_DIY          (new — replaces _PAGE_DIY)
  _PANEL_TICKER       (new — replaces _PAGE_TICKER)
  _PANEL_CLOCK        (new — replaces _PAGE_CLOCK)
  (drop _PAGE_STATE entirely; drop index_state and the /state route)
  api_cockpit_active  (new — returns the active-mode banner data)
```

### Shell composition

`workshop.py` gets a small Python helper:

```python
def _render_shell(active_tab: str, panel_html: str, page_title: str) -> str:
    """Wrap a per-feature panel string in the cockpit shell."""
    return _SHELL_TEMPLATE.format(
        active=active_tab,
        panel=panel_html,
        page_title=page_title,
    )
```

Each `index_*` handler returns `_render_shell("clock", _PANEL_CLOCK, "Clock")`
etc. Pure string substitution; no template engine.

The shell pulls in `cockpit.css` and `cockpit.js` via static tags, sets the
HTML skeleton (`<html>`, `<head>`, `<body>`), renders the left panel + tab
strip + content slot, and provides global `<script type="module">` that boots
`cockpit.js`.

### Active-mode endpoint

```
GET /api/cockpit/active
→ {
    "mode": "idle" | "clock" | "ticker" | "diy" | "preset" | "off",
    "label": "⏰ Clock — 14:32:07",  // formatted text for the banner
    "detail": null | {...mode-specific...}
  }
```

Backend logic:
1. If `_client.state[did]["d1"] == 0` → `off`
2. Else if `_clock_session is not None and .running` → `clock` with `.snapshot()["now_displayed"]`
3. Else if `_ticker_session is not None and .running` → `ticker` with comma-joined symbols
4. Else (lamp is on but nothing's actively driving it) → `idle` showing the most recent activity by best guess
   - For simplicity in v1: just return `idle` with no detail. We can refine if it feels lacking.

The "diy" and "preset" modes need server-side tracking we don't currently
have. **Deferred to v2.** For v1: `idle` is good enough — the user can see
the last-painted state via the visualizer.

### Tab swap = navigation, not SPA

Tab anchors are `<a href="/clock">` etc. Click reloads the page. The shell is
identical across tabs so the only thing that visually changes is the right
panel content + which tab pill is highlighted. Browser back button works.
Bookmarkable. Minimal JS state.

Cost: each tab switch reloads the shell. With static CSS/JS cached and the
panel HTML being small, this is fast on LAN. Acceptable trade for simplicity.

## Migration of existing per-page bits

The current pages have header chrome that becomes redundant under the shell:

- `<div class="header"><div class="tabs">...</div><div class="power-btns">...</div></div>` → removed everywhere
- Per-page `:root`, `body`, `.wrap`, `.card`, `.tabs`, `.power-btns` CSS → removed; provided by `cockpit.css`
- Per-page emoji-encoding inconsistency → unified (all entities in the shell)

What stays per-page:

- The actual feature controls (color pickers, sliders, ring cards, etc.)
- The feature-specific CSS (`.ring-card`, `.intervals`, `.color-row`, etc.)
- The feature-specific JS (DIY paint mouse handlers, Ticker polling, etc.)
- The feature-specific class names — but namespaced with a prefix to avoid
  collisions (e.g., `.diy-canvas`, `.ticker-ring`, `.clock-readout`).

## Routes

| Route | Today | After |
|---|---|---|
| `GET /` | Presets page (full HTML) | Presets shell-wrapped |
| `GET /diy` | DIY page (full HTML) | DIY shell-wrapped |
| `GET /ticker` | Ticker page (full HTML) | Ticker shell-wrapped |
| `GET /clock` | Clock page (full HTML) | Clock shell-wrapped |
| `GET /state` | State page (full HTML) | **302 redirect to `/`** (keeps existing browser tabs landing somewhere useful; one extra route handler that returns `web.HTTPFound("/")`) |
| `GET /api/cockpit/active` | — | **new** |
| Everything else | unchanged | unchanged |

Existing POST endpoints all stay as-is.

## Mutex / ticker / clock integration

Cockpit-side power-off and brightness behaviour stays identical (they're
hitting the same endpoints). The active-mode banner is the only new thing
that needs to know about ticker/clock — it queries `_ticker_session` and
`_clock_session` directly in `api_cockpit_active`.

## Backward compatibility

- Existing API endpoints (POST /api/diy/paint, etc.) unchanged — only the
  *pages* change.
- The static asset directory grows but `lamp-utils.js` keeps its existing
  exports for backward compatibility (the visualizer code in cockpit.js
  imports from it).
- `/state` is dropped. Anyone with a bookmark gets a 404 — acceptable;
  internal tool, no public users.

## Testing

Pure-function tests stay green (clock.py, ticker.py, the workshop helpers).
Page rendering changes are visual — verified by:
1. `pytest -q` for the full unit suite — should be 174+ passing, unchanged.
2. `import workshop; build_app()` smoke — confirms route count and that no
   handler imports break.
3. `curl http://127.0.0.1:8081/clock` — verifies the shell wraps the panel
   correctly (response contains both shell markers and panel-specific markers).
4. Manual: open `/`, `/diy`, `/ticker`, `/clock` in a browser, confirm the
   left panel is identical across all four and the right panel shows the
   correct feature.

The `/state` route should now return 302 to `/` — add a smoke check.

A new test file `tests/test_cockpit_shell.py` covers:

- `test_shell_includes_panel_content` — shell-wrapped output contains the panel HTML
- `test_shell_highlights_active_tab` — active="clock" puts `class="active"` on the Clock tab
- `test_shell_links_to_all_four_tabs` — output has `href="/"`, `/diy`, `/ticker`, `/clock`
- `test_shell_does_not_link_to_state` — no `href="/state"` anywhere (proves we dropped it from nav)
- `test_state_route_redirects_to_root` — `GET /state` returns 302 with `Location: /`

## Deliberately deferred (YAGNI)

- Active-mode `diy` / `preset` tracking (server doesn't know which page made the last paint; defer to a future "activity log" feature).
- Page transitions / animations between tabs.
- Light theme.
- Keyboard shortcuts (number keys to switch tabs, space to toggle power).
- Saved layout preferences (left panel collapse, etc.).
- Live-reload via WebSocket for the visualizer (current 2-second polling is fine).

## File-change summary

| File | Change | Lines (est.) |
|---|---|---|
| `static/cockpit.css` (new) | Design tokens, .glass, responsive shell, base styles | ~250 |
| `static/cockpit.js` (new) | Left panel: viz update, brightness wire, mode banner poll, power buttons, diagnostics | ~150 |
| `workshop.py` | Drop 4 page constants, add `_SHELL_TEMPLATE` + `_render_shell` + 4 `_PANEL_*` constants, drop `_PAGE_STATE` + state route, add `api_cockpit_active` route | ~+200, ~-1500 (existing inline HTML) |
| `tests/test_cockpit_shell.py` (new) | Shell tests | ~60 |
| `README.md` | Replace per-page descriptions with the cockpit overview | ~-20, ~+10 |

Net: **~580 lines added, ~1520 removed.** Significant simplification because the per-page CSS / chrome duplication goes away.
