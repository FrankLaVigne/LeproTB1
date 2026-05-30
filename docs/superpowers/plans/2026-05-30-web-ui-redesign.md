# Web UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the five separately-styled pages in `web/server.py` with one Cockpit layout — left panel always shows lamp visualizer + power + brightness + active-mode banner + diagnostics drawer; right panel has 4 tabs (Presets / DIY / Ticker / Clock); glassmorphism aesthetic; responsive desktop/phone. State page is absorbed into the left panel (`/state` 302-redirects to `/`). Preset preview loop auto-stops on power-off, DIY paint, and ticker/clock start (closes the "lamp not responding" bug surfaced 2026-05-30).

**Architecture:** Shared design system in `web/static/cockpit.css` (custom properties + `.glass` + responsive shell). Shared left-panel JS in `web/static/cockpit.js`. `web/server.py` gets a `_render_shell(active_tab, panel_html, page_title)` helper that wraps a per-feature panel string in the cockpit chrome. Each feature's right-side content moves into a `_PANEL_*` constant (drops per-page chrome, keeps per-page controls + script). New `GET /api/cockpit/active` endpoint feeds the active-mode banner. New `_stop_preview()` helper consolidates the preview-task teardown invoked from power-off, ticker-start, clock-start, and DIY paint. Tab nav stays URL-per-tab (`<a href="/...">`) — browser-native, deep-linkable, no SPA complexity.

**Tech Stack:** Python 3.12 + aiohttp + vanilla HTML/CSS/JS. No new deps.

---

## File Structure

- `web/static/cockpit.css` (**new**, ~250 lines) — design tokens (`:root` custom properties), `.glass` panel, responsive cockpit shell (left fixed 280px / right flex on desktop; stacked below 720px), tab strip, button styles, brightness slider, base typography. Shared by every page.
- `web/static/cockpit.js` (**new**, ~150 lines) — boots on every page: polls `/api/lamp/state` every 2s to update the left-panel SVG visualizer; polls `/api/cockpit/active` every 2s to update the active-mode banner; wires the power + brightness controls to existing POST endpoints; opens/closes the diagnostics drawer. Imports the existing `web/static/lamp-utils.js` for d50 parsing and rotation.
- `web/static/lamp-utils.js` (**already exists**) — `parseD50_N01`, `unrotateToPage`, `computeClockPositions`, the rotation constants. Reused as-is.
- `web/server.py` (**modify**, net ~+220 / ~-1500 lines) — adds `_render_shell()`, `_SHELL_TEMPLATE`, `_PANEL_PRESETS`/`_PANEL_DIY`/`_PANEL_TICKER`/`_PANEL_CLOCK`, `api_cockpit_active`, `_stop_preview()`. Each `index_*` handler returns `_render_shell(active="...", panel=_PANEL_..., title="...")`. Drops `_PAGE`, `_PAGE_DIY`, `_PAGE_TICKER`, `_PAGE_STATE`, `_PAGE_CLOCK`, `index_state`. Adds `index_state_redirect` returning `web.HTTPFound("/")`.
- `tests/test_cockpit_shell.py` (**new**, ~80 lines) — shell rendering tests + state-redirect smoke + active-mode endpoint tests.
- `README.md` (**modify**) — replace the per-page descriptions with a Cockpit overview paragraph.

Nine tasks below. Tasks 1-3 build the foundation (design system + shell + active-mode endpoint). Tasks 4-7 migrate each feature panel one at a time (incremental, testable, every restart yields a working server). Task 8 drops the State page. Task 9 wires up the auto-stop and docs.

---

### Task 1: Design system + cockpit shell CSS

**Files:**
- Create: `web/static/cockpit.css`

- [ ] **Step 1: Create the CSS file**

Write the following to `web/static/cockpit.css`:

```css
/* Cockpit shell — shared across all pages. Design tokens + responsive layout. */

:root {
  /* color tokens */
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
  --grad-primary: linear-gradient(135deg, #00ff88, #00ddff);

  /* spacing + radii */
  --gap-xs: 4px;  --gap-sm: 8px;  --gap: 12px;  --gap-lg: 16px;  --gap-xl: 24px;
  --r-sm: 8px;  --r: 12px;  --r-lg: 16px;  --r-pill: 999px;

  color-scheme: dark;
}

* { box-sizing: border-box; }

html, body {
  margin: 0; padding: 0;
  min-height: 100vh;
  background: var(--bg-grad);
  background-attachment: fixed;
  color: var(--text);
  font: 15px/1.4 system-ui, -apple-system, sans-serif;
}

a { color: var(--accent); text-decoration: none; }
button { font: inherit; cursor: pointer; }

/* Reusable glass panel */
.glass {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--r);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
}

.label {
  font-size: 10px;
  color: var(--text-dim);
  letter-spacing: 0.15em;
  text-transform: uppercase;
}

.mono { font: 12px ui-monospace, "SF Mono", Consolas, monospace; }

/* === Cockpit layout ====================================================== */

.cockpit {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: var(--gap-xl);
  padding: var(--gap-xl);
  max-width: 1200px;
  margin: 0 auto;
  min-height: 100vh;
}

@media (max-width: 720px) {
  .cockpit {
    grid-template-columns: 1fr;
    padding: var(--gap-lg);
    gap: var(--gap-lg);
  }
}

/* === Left panel ========================================================== */

.cockpit-left {
  display: flex;
  flex-direction: column;
  gap: var(--gap);
}

.brand-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.brand {
  font-size: 13px;
  font-weight: 300;
  letter-spacing: 0.25em;
  color: var(--text);
}
.device-id {
  font: 10px ui-monospace, monospace;
  color: var(--text-faint);
}

.viz-wrap {
  width: 240px;
  height: 240px;
  border-radius: 50%;
  background: var(--panel);
  border: 1px solid var(--border);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
}
@media (max-width: 720px) {
  .viz-wrap { width: 200px; height: 200px; }
}
.viz-wrap svg .seg { transition: fill 0.3s; }

.active-banner {
  background: var(--accent-soft);
  border: 1px solid var(--border-hi);
  border-color: rgba(0, 221, 255, 0.3);
  padding: 10px 12px;
  border-radius: var(--r);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}
.active-banner .label { color: rgba(0, 221, 255, 0.7); margin-bottom: 4px; }
.active-banner .value { font-size: 13px; color: var(--text); font-weight: 500; }

.power-btns { display: flex; gap: var(--gap-sm); }
.power-btns button {
  flex: 1;
  padding: 10px;
  border-radius: var(--r);
  font-size: 11px;
  font-weight: 600;
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}
.power-btns .on  { background: var(--ok-soft);     color: var(--ok);     border: 1px solid rgba(80, 200, 120, 0.4); }
.power-btns .off { background: rgba(200, 80, 80, 0.08); color: rgba(200, 80, 80, 0.6); border: 1px solid rgba(200, 80, 80, 0.15); }
.power-btns .on:hover  { background: rgba(80, 200, 120, 0.35); }
.power-btns .off:hover { background: rgba(200, 80, 80, 0.2); color: rgba(255, 120, 120, 0.9); border-color: rgba(200, 80, 80, 0.4); }

.brightness {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: var(--gap);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}
.brightness-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--gap-sm);
}
.brightness-head .val { font: 11px ui-monospace, monospace; color: var(--text); }
.brightness input[type=range] {
  width: 100%;
  height: 6px;
  -webkit-appearance: none;
  appearance: none;
  background: var(--border);
  border-radius: 3px;
  outline: none;
}
.brightness input[type=range]::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 16px; height: 16px;
  border-radius: 50%;
  background: var(--grad-primary);
  cursor: pointer;
  box-shadow: 0 0 8px var(--accent-glow);
}
.brightness input[type=range]::-moz-range-thumb {
  width: 16px; height: 16px;
  border: 0;
  border-radius: 50%;
  background: var(--accent);
  cursor: pointer;
  box-shadow: 0 0 8px var(--accent-glow);
}

.diag {
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: var(--r);
  overflow: hidden;
}
.diag summary {
  padding: 10px 12px;
  font-size: 11px;
  color: var(--text-dim);
  letter-spacing: 0.1em;
  cursor: pointer;
  text-transform: uppercase;
  list-style: none;
}
.diag summary::-webkit-details-marker { display: none; }
.diag[open] summary { color: var(--text); }
.diag-body {
  padding: 0 12px 12px;
  font: 10px ui-monospace, monospace;
  color: var(--text-dim);
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 2px 12px;
}
.diag-body .v { text-align: right; color: var(--text); word-break: break-all; }
.diag-body .v-ok { color: var(--ok); }

/* === Right panel ========================================================= */

.cockpit-right {
  display: flex;
  flex-direction: column;
  gap: var(--gap);
  min-width: 0;  /* prevents grid blowout from long content */
}

.tabs {
  display: flex;
  gap: var(--gap-sm);
  flex-wrap: wrap;
}
.tabs a {
  padding: 10px 18px;
  border-radius: var(--r);
  font-size: 12px;
  font-weight: 600;
  color: var(--text-dim);
  border: 1px solid transparent;
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  transition: background 0.15s, color 0.15s;
}
.tabs a:hover { color: var(--text); background: var(--panel); }
.tabs a.active {
  background: var(--accent-soft);
  color: var(--accent);
  border-color: rgba(0, 221, 255, 0.3);
}

.panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  padding: var(--gap-xl);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  flex: 1;
  min-width: 0;
}
@media (max-width: 720px) {
  .panel { padding: var(--gap); }
}

/* Primary action button — used by feature panels */
.btn-primary {
  padding: 14px;
  border: 0;
  border-radius: var(--r-lg);
  background: var(--grad-primary);
  color: #06061a;
  font-size: 13px;
  font-weight: 700;
}
.btn-primary:disabled { opacity: 0.4; cursor: not-allowed; }

.btn-danger {
  padding: 14px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: var(--r-lg);
  background: var(--panel);
  color: var(--text-dim);
  font-size: 13px;
  font-weight: 600;
}
.btn-danger:hover:not(:disabled) {
  background: var(--danger-soft);
  color: var(--danger);
  border-color: rgba(255, 85, 102, 0.4);
}
.btn-danger:disabled { opacity: 0.4; cursor: not-allowed; }

#status {
  font-size: 12px;
  color: var(--text-dim);
  margin-top: var(--gap-sm);
  min-height: 1.2em;
}
```

- [ ] **Step 2: Verify the file is served**

```bash
.venv/bin/python -m web.server &
SERVER_PID=$!
sleep 4
curl -s -o /dev/null -m 3 -w "/static/cockpit.css -> %{http_code}\n" http://127.0.0.1:8081/static/cockpit.css
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
```

Expected: `/static/cockpit.css -> 200` (the existing static handler in `build_app` already serves anything under `web/static/`).

- [ ] **Step 3: Commit**

```bash
git add web/static/cockpit.css
git commit -m "feat(cockpit): design tokens + responsive shell CSS"
```

---

### Task 2: Shell renderer + Python helper

**Files:**
- Modify: `web/server.py` (add `_SHELL_TEMPLATE`, `_render_shell` near the existing `_HERE` constants)
- Create: `tests/test_cockpit_shell.py`

The shell is a Python f-string template (kept inside `web/server.py` to avoid template-engine complexity). `_render_shell()` substitutes `{title}`, `{active}` (the tab key), and `{panel}` (the per-feature panel HTML).

- [ ] **Step 1: Create the failing test**

Create `tests/test_cockpit_shell.py`:

```python
"""Tests for the cockpit shell renderer in web.server."""

from web import server as workshop


def test_render_shell_includes_panel_html():
    panel = '<div id="my-feature">hello</div>'
    out = workshop._render_shell(active="clock", panel_html=panel, title="Clock")
    assert panel in out


def test_render_shell_includes_page_title():
    out = workshop._render_shell(active="presets", panel_html="", title="Presets")
    assert "<title>Lepro &middot; Presets</title>" in out


def test_render_shell_marks_active_tab():
    out = workshop._render_shell(active="diy", panel_html="", title="DIY")
    # The DIY tab anchor should carry class="active"; others should not.
    assert 'href="/diy" class="active"' in out
    assert 'href="/" class="active"' not in out
    assert 'href="/ticker" class="active"' not in out
    assert 'href="/clock" class="active"' not in out


def test_render_shell_links_to_all_four_tabs():
    out = workshop._render_shell(active="presets", panel_html="", title="Presets")
    for href in ('href="/"', 'href="/diy"', 'href="/ticker"', 'href="/clock"'):
        assert href in out


def test_render_shell_does_not_link_to_state():
    # State page is absorbed into the left panel; no tab for it.
    out = workshop._render_shell(active="presets", panel_html="", title="Presets")
    assert 'href="/state"' not in out


def test_render_shell_loads_cockpit_assets():
    out = workshop._render_shell(active="presets", panel_html="", title="Presets")
    assert "/static/cockpit.css" in out
    assert "/static/cockpit.js" in out


def test_render_shell_contains_left_panel_structure():
    out = workshop._render_shell(active="presets", panel_html="", title="Presets")
    # Required hooks for cockpit.js to populate.
    for hook in ('id="lamp-viz"', 'id="active-banner"',
                 'id="brightness-slider"', 'id="brightness-val"',
                 'id="pwr-on"', 'id="pwr-off"', 'id="diag-body"'):
        assert hook in out, f"missing left-panel hook: {hook}"
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `.venv/bin/python -m pytest tests/test_cockpit_shell.py -v`
Expected: FAIL with `AttributeError: module 'web.server' has no attribute '_render_shell'`.

- [ ] **Step 3: Add the shell template and renderer to `web/server.py`**

Find this block near the top of `web/server.py` (around line 274):

```python
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent  # repo root, parent of the web/ package
_PRESETS_DIR = _PROJECT_ROOT / "presets"
```

Immediately AFTER this block, insert:

```python
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
    )
```

- [ ] **Step 4: Run the tests — they should pass now**

Run: `.venv/bin/python -m pytest tests/test_cockpit_shell.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Run the full suite — make sure nothing broke**

Run: `.venv/bin/python -m pytest -q`
Expected: 174 + 7 = 181 passed, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add web/server.py tests/test_cockpit_shell.py
git commit -m "feat(cockpit): shell template + _render_shell helper"
```

---

### Task 3: Left-panel JS + active-mode endpoint

**Files:**
- Create: `web/static/cockpit.js`
- Modify: `web/server.py` (add `_stop_preview`, `api_cockpit_active`, register route)
- Modify: `tests/test_cockpit_shell.py` (append active-mode tests)

This task makes the shell *do* something: the left panel becomes live (viz updates, brightness wired, banner polled). Existing pages aren't migrated yet — but the shell + cockpit.js can be smoke-tested by serving any HTML that includes the same DOM hooks.

- [ ] **Step 1: Create `web/static/cockpit.js`**

Write to `web/static/cockpit.js`:

```javascript
// Cockpit shell — left panel runtime. Boots on every page, reads its hooks
// from the shell template (lamp-viz, active-banner, brightness-slider, etc.).

import { lampStateToPageLeds } from '/static/lamp-utils.js';

const $ = s => document.querySelector(s);

// === Lamp visualizer (48-mode segments) ====================================

const OUTER = Array.from({length: 22}, (_, i) => [i * 4, i * 4 + 4]);
const MIDDLE = [
  ...Array.from({length: 13}, (_, i) => [88 + i * 4, 88 + i * 4 + 4]),
  [140, 145], [145, 150],
];
const INNER = [
  ...Array.from({length: 9}, (_, i) => [150 + i * 4, 150 + i * 4 + 4]),
  [186, 191], [191, 196],
];
const RING_GEOMETRY = {
  outer:  {r0: 130, r1: 180},
  middle: {r0: 90,  r1: 125},
  inner:  {r0: 50,  r1: 85},
};

function arcPath(r0, r1, a0, a1) {
  const toXY = (r, a) => [r * Math.cos(a), r * Math.sin(a)];
  const [x0a, y0a] = toXY(r0, a0);
  const [x1a, y1a] = toXY(r1, a0);
  const [x1b, y1b] = toXY(r1, a1);
  const [x0b, y0b] = toXY(r0, a1);
  const large = (a1 - a0) > Math.PI ? 1 : 0;
  return `M${x0a},${y0a} L${x1a},${y1a} A${r1},${r1} 0 ${large} 1 ${x1b},${y1b}`
       + ` L${x0b},${y0b} A${r0},${r0} 0 ${large} 0 ${x0a},${y0a} Z`;
}

function drawViz(pageLeds) {
  const svg = $('#lamp-viz');
  if (!svg) return;
  svg.innerHTML = '';
  for (const [name, segs] of [['outer', OUTER], ['middle', MIDDLE], ['inner', INNER]]) {
    const g = RING_GEOMETRY[name];
    const total = segs.length;
    for (let i = 0; i < total; i++) {
      const [start] = segs[i];
      const a0 = (i / total) * 2 * Math.PI - Math.PI / 2;
      const a1 = ((i + 1) / total) * 2 * Math.PI - Math.PI / 2;
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', arcPath(g.r0, g.r1, a0, a1));
      const color = pageLeds ? pageLeds[start] : null;
      const isLit = color && color !== '000000';
      path.setAttribute('fill', isLit ? '#' + color : '#0a0a14');
      path.setAttribute('stroke', 'rgba(255,255,255,0.04)');
      path.setAttribute('stroke-width', '1');
      if (isLit) path.setAttribute('filter', 'drop-shadow(0 0 4px #' + color + ')');
      path.classList.add('seg');
      svg.appendChild(path);
    }
  }
}

// === D-fields diagnostics drawer ===========================================

function renderDiag(fields) {
  const body = $('#diag-body');
  if (!body) return;
  if (!fields || !Object.keys(fields).length) {
    body.innerHTML = '<div>&mdash;</div><div class="v">no state yet</div>';
    return;
  }
  const keys = Object.keys(fields).sort((a, b) => {
    const na = parseInt(a.replace(/[^0-9]/g, ''), 10) || 0;
    const nb = parseInt(b.replace(/[^0-9]/g, ''), 10) || 0;
    return na - nb;
  });
  body.innerHTML = keys.map(k => {
    let v = fields[k];
    if (typeof v === 'string' && v.length > 60) {
      v = `<span title="${v.replace(/"/g, '&quot;')}">${v.slice(0, 60)}…</span>`;
    }
    const cls = (k === 'd1' && v === 1) ? 'v v-ok' : 'v';
    return `<div>${k}</div><div class="${cls}">${v}</div>`;
  }).join('');
}

// === Polling: lamp state ===================================================

async function refreshLampState() {
  try {
    const r = await fetch('/api/lamp/state');
    const j = await r.json();
    const dids = Object.keys(j.devices || {});
    if (dids.length) {
      const did = dids[0];
      $('#device-id').textContent = did;
      const fields = j.devices[did];
      renderDiag(fields);
      // sync the brightness slider to the lamp's d52 if it's there
      if (typeof fields.d52 === 'number') {
        const pct = Math.round(fields.d52 / 10);
        $('#brightness-slider').value = pct;
        $('#brightness-val').textContent = pct + '%';
      }
    }
    const pageLeds = lampStateToPageLeds(j);
    drawViz(pageLeds);
  } catch (e) { /* silent — keep stale viz on transient failure */ }
}

// === Polling: active mode ==================================================

async function refreshActiveBanner() {
  try {
    const r = await fetch('/api/cockpit/active');
    const j = await r.json();
    $('#active-banner-value').textContent = j.label || '—';
  } catch (e) { /* silent */ }
}

// === Brightness slider =====================================================

let brightnessThrottle = false;
let pendingBrightness = null;
async function sendBrightness(pct) {
  pendingBrightness = pct;
  if (brightnessThrottle) return;
  brightnessThrottle = true;
  const value = Math.round(pendingBrightness * 10);
  try {
    await fetch('/api/brightness', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({value}),
    });
  } catch (e) { /* silent */ }
  setTimeout(() => {
    brightnessThrottle = false;
    if (pendingBrightness !== null && Math.round(pendingBrightness * 10) !== value) {
      const next = pendingBrightness;
      pendingBrightness = null;
      sendBrightness(next);
    } else {
      pendingBrightness = null;
    }
  }, 150);
}

// === Power buttons =========================================================

async function postPower(on) {
  try {
    await fetch('/api/power', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({on}),
    });
    refreshLampState();
    refreshActiveBanner();
  } catch (e) { /* silent */ }
}

// === Boot ==================================================================

document.addEventListener('DOMContentLoaded', () => {
  drawViz(null);  // initial empty rings so the SVG isn't blank

  $('#brightness-slider').addEventListener('input', e => {
    const pct = parseInt(e.target.value, 10);
    $('#brightness-val').textContent = pct + '%';
    sendBrightness(pct);
  });
  $('#pwr-on').addEventListener('click', () => postPower(true));
  $('#pwr-off').addEventListener('click', () => postPower(false));

  refreshLampState();
  refreshActiveBanner();
  setInterval(refreshLampState, 2000);
  setInterval(refreshActiveBanner, 2000);
});
```

- [ ] **Step 2: Append active-mode tests to `tests/test_cockpit_shell.py`**

Append to `tests/test_cockpit_shell.py`:

```python


# --- _stop_preview helper -----------------------------------------------------


import asyncio
import pytest


def test_stop_preview_when_no_task_does_nothing():
    # Helper must be safe to call when there's no preview running.
    workshop._preview_task = None
    workshop._preview_name = None
    asyncio.run(workshop._stop_preview())
    assert workshop._preview_task is None
    assert workshop._preview_name is None


# --- api_cockpit_active -------------------------------------------------------


@pytest.mark.asyncio
async def test_cockpit_active_off_when_d1_zero():
    workshop._client = type("C", (), {"state": {"abc": {"d1": 0}}})()
    workshop._ticker_session = None
    workshop._clock_session = None
    workshop._preview_task = None
    resp = await workshop.api_cockpit_active(None)
    body = await _json(resp)
    assert body["mode"] == "off"


@pytest.mark.asyncio
async def test_cockpit_active_idle_when_d1_on_and_no_session():
    workshop._client = type("C", (), {"state": {"abc": {"d1": 1}}})()
    workshop._ticker_session = None
    workshop._clock_session = None
    workshop._preview_task = None
    resp = await workshop.api_cockpit_active(None)
    body = await _json(resp)
    assert body["mode"] == "idle"
    assert "label" in body  # always non-null label for the banner


async def _json(resp):
    # aiohttp Response.text is sync; resp.body is bytes.
    import json
    return json.loads(resp.body.decode("utf-8") if isinstance(resp.body, bytes) else resp.body)
```

- [ ] **Step 3: Add `_stop_preview`, `_preview_name`, `api_cockpit_active` to `web/server.py`**

Find the existing `_preview_task` declaration. Right after it (and right after any related comment), add:

```python
_preview_name: str | None = None  # set by api_preview when a preview starts; read by api_cockpit_active
```

Then add this helper somewhere AFTER the `_preview_task` declaration but BEFORE the route handlers that need it (a natural home is right after the existing `_run_preview` function):

```python
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
```

Now add the active-mode endpoint. A good home is right after the existing `api_lamp_state` function (and before the clock handlers added in the clock work):

```python
async def api_cockpit_active(_req):
    """Return the active-mode banner data for the cockpit left panel.

    Returns ``{mode, label}`` where mode is one of:
      "off", "clock", "ticker", "preset", "idle"
    and label is the formatted text shown in the banner.
    """
    # 1. Power off wins.
    if _client is not None:
        for fields in _client.state.values():
            if fields.get("d1") == 0:
                return web.json_response({"mode": "off", "label": "⏻ Off"})
    # 2. Active background sessions.
    if _clock_session is not None and _clock_session.running:
        snap = _clock_session.snapshot()
        now = (snap.get("now_displayed") or "")
        suffix = now.split("T", 1)[1] if "T" in now else now
        label = f"⏰ Clock — {suffix}" if suffix else "⏰ Clock"
        return web.json_response({"mode": "clock", "label": label})
    if _ticker_session is not None and _ticker_session.running:
        snap = _ticker_session.snapshot()
        syms = []
        for ring in ("outer", "middle", "inner"):
            r = (snap.get("rings") or {}).get(ring)
            if r and r.get("symbol"):
                syms.append(r["symbol"])
        label = "Ὄ8 Ticker — " + ", ".join(syms) if syms else "Ὄ8 Ticker"
        return web.json_response({"mode": "ticker", "label": label})
    if _preview_task is not None and not _preview_task.done():
        nm = _preview_name or "?"
        return web.json_response({"mode": "preset", "label": f"\U0001F3A8 Preset — {nm}"})
    # 3. On but nothing actively driving.
    return web.json_response({"mode": "idle", "label": "✨ Idle"})
```

Register the route in `build_app`'s `app.add_routes([...])` call. Find the existing `web.get("/api/lamp/state", api_lamp_state),` line and add immediately after it:

```python
        web.get("/api/cockpit/active", api_cockpit_active),
```

- [ ] **Step 4: Update `api_preview` to record the preview's name**

Find the `api_preview` function. Locate the line that creates the task:

```python
        _preview_task = asyncio.create_task(_run_preview(recolored, did, _client))
```

Replace with:

```python
        global _preview_name  # noqa: PLW0603 — module-level state by design
        _preview_task = asyncio.create_task(_run_preview(recolored, did, _client))
        _preview_name = recolored.get("name") or body.get("name") or "(unnamed)"
```

(The exact variable holding the preset's name in `api_preview` may differ; the rule is: whatever name the user clicked Preview on gets stored in `_preview_name`. If you find a different variable carrying the name in the scope, use that.)

- [ ] **Step 5: Run the new tests + full suite**

```bash
.venv/bin/python -m pytest tests/test_cockpit_shell.py -v
.venv/bin/python -m pytest -q
```

Expected: all of the new tests pass; full suite at 174 + 7 (Task 2) + 3 (this task) = 184 passed, 0 failed.

- [ ] **Step 6: Smoke-test the static file is served + endpoint works**

```bash
.venv/bin/python -m web.server >/tmp/wsmoke.log 2>&1 &
SERVER_PID=$!
sleep 5
curl -s -o /dev/null -m 3 -w "/static/cockpit.js     -> %{http_code}\n" http://127.0.0.1:8081/static/cockpit.js
curl -s -m 3 http://127.0.0.1:8081/api/cockpit/active
echo
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
```

Expected: cockpit.js returns 200, /api/cockpit/active returns a JSON `{"mode": "...", "label": "..."}` body.

- [ ] **Step 7: Commit**

```bash
git add web/static/cockpit.js web/server.py tests/test_cockpit_shell.py
git commit -m "feat(cockpit): left-panel JS + active-mode endpoint + _stop_preview"
```

---

### Task 4: Migrate Presets page to the shell

**Files:**
- Modify: `web/server.py`

This task drops the old `_PAGE` HTML (with its own header / tabs / power / brightness chrome) and replaces it with a `_PANEL_PRESETS` that contains only the right-side feature controls. `index` is rewritten to call `_render_shell("presets", _PANEL_PRESETS, "Presets")`.

The Presets page's feature controls are the existing preset-browser UI (left column with the preset list, right column with the variant editor / preview / save). The whole two-column inner layout moves into `_PANEL_PRESETS` unchanged — only the outer chrome goes.

- [ ] **Step 1: Identify the current `_PAGE` boundaries**

Find `_PAGE = """<!doctype html>` and the matching closing `"""`. Everything inside that string is the current full Presets page HTML.

- [ ] **Step 2: Extract the feature content**

The current `_PAGE` is structured roughly as:
```
<!doctype html>...<style>...</style>...<body>
  <div class="wrap">
    <div class="card">          <!-- header -->
      <div class="header">
        <div class="tabs">...</div>
        <div class="power-btns">...</div>
      </div>
    </div>
    [...preset browser two-column UI...]
  </div>
  <script>...</script>
</body></html>
```

In `web/server.py`, REPLACE the entire `_PAGE = """..."""` block with `_PANEL_PRESETS = """..."""` containing:
- ONLY the "[...preset browser two-column UI...]" portion (everything between the header card's closing tag and the closing `</div>` of `.wrap`).
- Followed by the existing `<script>...</script>` block.
- DROP the `<!doctype html>`, `<html>`, `<head>`, all of `<style>`, all of `<body>`, the `.wrap`, and the header card.

The CSS in the old `<style>` block falls into two categories:
1. Generic stuff (`:root`, `body`, `.card`, `.tabs`, `.power-btns`, `.wrap`) — DROP, provided by `cockpit.css` now.
2. Feature-specific stuff (`.preset-list`, `.preset-item`, the swatch styles, the color-combo grid, etc.) — KEEP, in a `<style>` tag at the top of `_PANEL_PRESETS`. Namespace these with a `.presets-` prefix where practical (e.g., rename `.preset-list` to `.presets-list`).

The exact lines to drop / keep depend on the current `_PAGE` content. Read the file first; do the surgical extraction.

A concrete template for `_PANEL_PRESETS`:

```python
_PANEL_PRESETS = """
<style>
  /* Feature-specific styles for the Presets panel only.
     Generic page chrome lives in /static/cockpit.css. */
  .presets-grid { display: grid; grid-template-columns: 240px 1fr; gap: 16px; }
  @media (max-width: 720px) { .presets-grid { grid-template-columns: 1fr; } }
  .presets-list { max-height: 60vh; overflow-y: auto; }
  /* ... copy the remaining feature-specific rules from the original _PAGE
         <style> block here, dropping anything that styled .wrap, .card,
         .tabs, .power-btns, :root, or body. ... */
</style>

<!-- copy the body content of the OLD _PAGE here, MINUS the outer .wrap, MINUS
     the header card with tabs + power. The first element should be the start
     of the preset-browser UI (typically a two-column grid). -->

<script type="module">
  /* copy the existing _PAGE <script> body here, verbatim. */
</script>
"""
```

The detailed surgical work: open the existing `_PAGE` string in your editor, copy what's described above into `_PANEL_PRESETS`, then delete the original `_PAGE`.

- [ ] **Step 3: Rewrite the `index` handler**

Find:

```python
async def index(_req):
    return web.Response(text=_PAGE, content_type="text/html")
```

Replace with:

```python
async def index(_req):
    return web.Response(text=_render_shell("presets", _PANEL_PRESETS, "Presets"),
                        content_type="text/html")
```

- [ ] **Step 4: Run the suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: 0 failures. (The Presets page doesn't have unit tests for its inline HTML, but `test_cockpit_shell.py` tests still apply because `_render_shell` is the same function.)

- [ ] **Step 5: Smoke-test the page**

```bash
.venv/bin/python -m web.server >/tmp/wsmoke.log 2>&1 &
SERVER_PID=$!
sleep 5
curl -s http://127.0.0.1:8081/ | head -c 200
echo
curl -s -o /dev/null -m 3 -w "/  -> %{http_code}\n" http://127.0.0.1:8081/
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
```

Expected: 200, first 200 chars contain `<title>Lepro · Presets</title>` and the cockpit CSS link.

- [ ] **Step 6: Commit**

```bash
git add web/server.py
git commit -m "feat(cockpit): migrate Presets page to shell layout"
```

---

### Task 5: Migrate DIY page to the shell

**Files:**
- Modify: `web/server.py`

Same pattern as Task 4: extract `_PANEL_DIY` from `_PAGE_DIY`, drop the per-page chrome, rewrite `index_diy` to use `_render_shell`.

- [ ] **Step 1: Extract `_PANEL_DIY` from `_PAGE_DIY`**

`_PAGE_DIY` currently contains:
- `<!doctype html>` + `<html>` + `<head>` + extensive `<style>`
- `<body>` with the tabs strip, the power buttons, the canvas card, the toolbar, the color card, the effect card, the save row
- `<script type="module">` block with `parseD50_N01`, drawCanvas, paint handlers, etc.

REPLACE the entire `_PAGE_DIY = """..."""` definition with `_PANEL_DIY = """..."""`:

```python
_PANEL_DIY = """
<style>
  /* DIY-specific styles only. Drop generic .wrap/.card/.tabs/.power-btns. */
  .diy-canvas { display: flex; justify-content: center; padding: 12px 0; }
  .diy-toolbar { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 14px; }
  /* ... copy the remaining .toolbar, .color-row, .swatch, .effect-grid,
         .slider-row, label[for=vname], .btns, #status styles here ... */
  /* Rename .toolbar -> .diy-toolbar, .lamp-canvas -> .diy-canvas, etc.,
     wherever the rename avoids collision with other panels. */
</style>

<!-- Copy the OLD _PAGE_DIY's body content MINUS the outer .wrap, MINUS the
     header (.tabs and .power-btns). Start at the first content card
     (the SVG canvas card). End at the </div> just before the closing
     </body>. -->

<script type="module">
  /* copy the existing _PAGE_DIY <script> body here, verbatim. */
</script>
"""
```

- [ ] **Step 2: Rewrite the `index_diy` handler**

Find:

```python
async def index_diy(_req):
    return web.Response(text=_PAGE_DIY, content_type="text/html")
```

Replace with:

```python
async def index_diy(_req):
    return web.Response(text=_render_shell("diy", _PANEL_DIY, "DIY"),
                        content_type="text/html")
```

- [ ] **Step 3: Run the suite**

`.venv/bin/python -m pytest -q` — 0 failures.

- [ ] **Step 4: Smoke-test**

```bash
.venv/bin/python -m web.server >/tmp/wsmoke.log 2>&1 &
SERVER_PID=$!
sleep 5
curl -s -o /dev/null -m 3 -w "/diy -> %{http_code}\n" http://127.0.0.1:8081/diy
# Verify the page contains DIY-specific markers + the shell hooks
curl -s http://127.0.0.1:8081/diy | grep -c "id=\"lamp-viz\"\|data-tool=\"draw\""
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
```

Expected: `/diy -> 200`, grep count >= 2 (one shell hook + one DIY hook).

- [ ] **Step 5: Commit**

```bash
git add web/server.py
git commit -m "feat(cockpit): migrate DIY page to shell layout"
```

---

### Task 6: Migrate Ticker page to the shell

**Files:**
- Modify: `web/server.py`

- [ ] **Step 1: Extract `_PANEL_TICKER` from `_PAGE_TICKER`**

REPLACE the entire `_PAGE_TICKER = """..."""` with `_PANEL_TICKER = """..."""`:

```python
_PANEL_TICKER = """
<style>
  /* Ticker-specific only. */
  .ticker-ring { background: rgba(255,255,255,0.04); padding: 14px;
                 border-radius: 12px; margin-bottom: 12px;
                 border: 1px solid rgba(255,255,255,0.1); }
  /* ... copy remaining ring-card, intervals, history etc. styles,
     renamed with .ticker- prefix to avoid collision. ... */
</style>

<!-- Copy old _PAGE_TICKER body MINUS .wrap and header. Start at the first
     ring card (outer). -->

<script type="module">
  /* Copy the existing _PAGE_TICKER <script> body, verbatim. */
</script>
"""
```

- [ ] **Step 2: Rewrite `index_ticker`**

Find and replace:

```python
async def index_ticker(_req):
    return web.Response(text=_PAGE_TICKER, content_type="text/html")
```

with:

```python
async def index_ticker(_req):
    return web.Response(text=_render_shell("ticker", _PANEL_TICKER, "Ticker"),
                        content_type="text/html")
```

- [ ] **Step 3: Run the suite + smoke-test**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m web.server >/tmp/wsmoke.log 2>&1 &
SERVER_PID=$!
sleep 5
curl -s -o /dev/null -m 3 -w "/ticker -> %{http_code}\n" http://127.0.0.1:8081/ticker
curl -s http://127.0.0.1:8081/ticker | grep -c "id=\"lamp-viz\"\|data-ring=\"outer\""
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
```

Expected: tests pass, `/ticker -> 200`, grep count >= 2.

- [ ] **Step 4: Commit**

```bash
git add web/server.py
git commit -m "feat(cockpit): migrate Ticker page to shell layout"
```

---

### Task 7: Migrate Clock page to the shell

**Files:**
- Modify: `web/server.py`

The Clock page also keeps its own client-side visualizer (the live SVG clock face that renders the dot positions every second). That stays — it's a feature-specific control, not redundant with the cockpit's lamp viz (which shows the actual lamp's d50). The cockpit viz on this page will show the clock pixels live (since the lamp is updating); the panel-side viz shows what the page believes the lamp SHOULD be showing. Both should agree.

- [ ] **Step 1: Extract `_PANEL_CLOCK` from `_PAGE_CLOCK`**

REPLACE `_PAGE_CLOCK = """..."""` with `_PANEL_CLOCK = """..."""`:

```python
_PANEL_CLOCK = """
<style>
  /* Clock-specific only. */
  .clock-readout { font: 600 28px ui-monospace, monospace;
                   text-align: center; color: #eee; margin: 4px 0 10px; }
  .clock-canvas { display: flex; justify-content: center; padding: 6px 0; }
  /* ... copy color-row, mode-toggle, controls etc., renamed .clock- prefix
     where they collide with other panels. ... */
</style>

<!-- Copy old _PAGE_CLOCK body MINUS .wrap and header. Start at the first
     content card (the clock readout + canvas). -->

<script type="module">
  /* Copy existing _PAGE_CLOCK <script> body verbatim. */
</script>
"""
```

- [ ] **Step 2: Rewrite `index_clock`**

```python
async def index_clock(_req):
    return web.Response(text=_render_shell("clock", _PANEL_CLOCK, "Clock"),
                        content_type="text/html")
```

- [ ] **Step 3: Run the suite + smoke-test**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m web.server >/tmp/wsmoke.log 2>&1 &
SERVER_PID=$!
sleep 5
curl -s -o /dev/null -m 3 -w "/clock -> %{http_code}\n" http://127.0.0.1:8081/clock
curl -s http://127.0.0.1:8081/clock | grep -c "id=\"lamp-viz\"\|data-mode=\"12h\""
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
```

Expected: tests pass, `/clock -> 200`, grep count >= 2.

- [ ] **Step 4: Commit**

```bash
git add web/server.py
git commit -m "feat(cockpit): migrate Clock page to shell layout"
```

---

### Task 8: Drop the State page; redirect /state to /

**Files:**
- Modify: `web/server.py`
- Modify: `tests/test_cockpit_shell.py` (add redirect test)

State info is now in the left panel of every page (visualizer + diagnostics drawer). The standalone State page goes away. `/state` 302-redirects to `/` so existing browser tabs / bookmarks land somewhere useful.

- [ ] **Step 1: Add the failing redirect test**

Append to `tests/test_cockpit_shell.py`:

```python


# --- /state redirect ----------------------------------------------------------


@pytest.mark.asyncio
async def test_state_route_redirects_to_root():
    from aiohttp import web as _web
    with pytest.raises(_web.HTTPFound) as exc:
        await workshop.index_state_redirect(None)
    assert exc.value.location == "/"
```

- [ ] **Step 2: Run the test — confirm it fails**

`.venv/bin/python -m pytest tests/test_cockpit_shell.py::test_state_route_redirects_to_root -v`
Expected: FAIL with `AttributeError: module 'web.server' has no attribute 'index_state_redirect'`.

- [ ] **Step 3: Replace `index_state` and drop `_PAGE_STATE`**

In `web/server.py`, find and DELETE the entire `_PAGE_STATE = """..."""` constant.

Find:

```python
async def index_state(_req):
    return web.Response(text=_PAGE_STATE, content_type="text/html")
```

REPLACE with:

```python
async def index_state_redirect(_req):
    """State page absorbed into the cockpit left panel; redirect to home."""
    raise web.HTTPFound("/")
```

In `build_app`'s `app.add_routes([...])`, find:

```python
        web.get("/state", index_state),
```

REPLACE with:

```python
        web.get("/state", index_state_redirect),
```

- [ ] **Step 4: Run the test + full suite**

```bash
.venv/bin/python -m pytest tests/test_cockpit_shell.py -v
.venv/bin/python -m pytest -q
```

Expected: redirect test passes; full suite 0 failures.

- [ ] **Step 5: Smoke-test the redirect**

```bash
.venv/bin/python -m web.server >/tmp/wsmoke.log 2>&1 &
SERVER_PID=$!
sleep 5
curl -s -o /dev/null -m 3 -w "/state -> %{http_code}\n" http://127.0.0.1:8081/state
curl -s -I -m 3 http://127.0.0.1:8081/state | grep -i "location:"
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
```

Expected: `/state -> 302`; `Location: /` header.

- [ ] **Step 6: Commit**

```bash
git add web/server.py tests/test_cockpit_shell.py
git commit -m "feat(cockpit): drop State page; /state 302-redirects to /"
```

---

### Task 9: Auto-stop preview loop + README

**Files:**
- Modify: `web/server.py` (wire `_stop_preview()` into power-off, ticker-start, clock-start, DIY paint)
- Modify: `README.md`

The bug surfaced 2026-05-30 was that hitting Preview from the Presets page spawned an animation-replay loop that overwrote any subsequent DIY paint / brightness / color command. Now that the active-mode banner shows when the preview is active (Task 3), we close the loop by auto-cancelling it when any other lamp-driving action starts.

- [ ] **Step 1: Wire `_stop_preview()` into the four call sites**

In `web/server.py`:

**1a. `api_power` (off path).** Find the off-branch teardown that already stops ticker + clock. Add `await _stop_preview()` right after the existing teardowns (and BEFORE the `_client.power(False)` call):

Currently looks something like:
```python
if not on:
    global _ticker_session, _clock_session
    if _ticker_session is not None and _ticker_session.running:
        await _ticker_session.stop()
        _ticker_session = None
    if _clock_session is not None and _clock_session.running:
        await _clock_session.stop()
        _clock_session = None
```

Add:
```python
    await _stop_preview()
```

at the end of the `if not on:` block, just before `await _client.power(False)` (or whichever call sends the actual power-off).

**1b. `api_ticker_start`.** Find the body of `api_ticker_start`. Right before the line that creates the `TickerSession`, add:

```python
        await _stop_preview()
```

**1c. `api_clock_start`.** Find `api_clock_start`. Right before the line that creates the `ClockSession`, add:

```python
        await _stop_preview()
```

**1d. `api_diy_paint`.** Find `api_diy_paint`. Right after the existing mutex checks (`_check_ticker_mutex()` and `_check_clock_mutex()`), add:

```python
        await _stop_preview()
```

The order matters: mutex checks first (so we 409 if ticker/clock is running) → then auto-stop preview → then do the DIY paint.

- [ ] **Step 2: Add a regression smoke test**

Append to `tests/test_cockpit_shell.py`:

```python


# --- preview auto-stop integration -------------------------------------------


@pytest.mark.asyncio
async def test_stop_preview_cancels_running_task():
    """Helper actually cancels a running task and clears the name."""
    async def _loop():
        try:
            while True:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise

    workshop._preview_task = asyncio.create_task(_loop())
    workshop._preview_name = "fake"
    # Give the task time to actually start.
    await asyncio.sleep(0)
    assert workshop._preview_task is not None
    assert workshop._preview_task.done() is False

    await workshop._stop_preview()
    assert workshop._preview_task is None
    assert workshop._preview_name is None
```

- [ ] **Step 3: Update the README**

Open `README.md`. The current "Preset workshop" section describes 5 separate pages with individual paragraphs for DIY, Ticker, State, Clock. Replace that whole multi-paragraph section (from the heading `## Preset workshop` down to but NOT including `## Protocol notes`) with:

```markdown
## Web UI (cockpit)

```bash
.venv/bin/python -m web.server        # serves on 0.0.0.0:8081
```

Open `http://<vm-ip>:8081`. The interface is a single "cockpit" layout:

- **Left panel (always visible):** the lamp visualizer (live 3-ring SVG of
  what the lamp is currently showing), the active-mode banner (Idle / Off /
  Preset / Ticker / Clock), power on/off, brightness slider (0-100 %), and
  a collapsible diagnostics drawer with the raw d-field values.
- **Right panel:** four tabs.
  - **🎨 Presets** — browse / recolor / preview / save captured presets
    (`presets/*.json`). Click Preview to loop the preset on the lamp.
  - **✏️ DIY** — click-to-paint 3-ring SVG canvas; Draw / Fill / Erase /
    Back tools; color picker with quick-pick swatches; six confirmed motion
    effects (Steady / Breathe / Gradient / Leftward / Rightward / Circle);
    speed slider; Save to a single-frame preset.
  - **📈 Ticker** — assign up to 3 Yahoo Finance symbols to the rings;
    each ring shows its symbol's most recent direction as a solid color,
    with a 5-second whole-lamp breathe flash on every tick. Sustained
    moves earn a ⚡ FAST badge and switch the base effect to Breathe.
  - **⏰ Clock** — three-handed analog clock (outer = seconds, middle =
    minutes, inner = hours), per-ring configurable colors, 12 h / 24 h
    toggle, 1-second cadence.

While the ticker or clock is running, the DIY paint and the Presets-page
preview endpoint return HTTP 409. Power off stops every active driver
(ticker, clock, preset preview) before turning the lamp off. Starting
the ticker or clock — or sending a DIY paint — auto-stops the preset
preview loop so manual paints aren't overwritten.

Responsive layout: side-by-side cockpit on desktop (≥ 720 px), stacked
on phone. LAN-only — no auth. Override the bind address with
`LEPRO_WORKSHOP_HOST` / `LEPRO_WORKSHOP_PORT`.

`/state` (the old standalone state page) is gone — it was absorbed into
the left panel. The URL 302-redirects to `/` so old bookmarks still land
somewhere useful.
```

- [ ] **Step 4: Final full-suite run + final smoke**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -c "
import workshop_module
" 2>&1 | head -3 || true
.venv/bin/python -m web.server >/tmp/wsmoke.log 2>&1 &
SERVER_PID=$!
sleep 5
for path in / /diy /ticker /clock /state /api/cockpit/active /static/cockpit.css /static/cockpit.js; do
  curl -s -o /dev/null -m 3 -w "  $path -> %{http_code}\n" "http://127.0.0.1:8081$path"
done
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
```

Expected: tests pass; `/`, `/diy`, `/ticker`, `/clock` all 200; `/state` 302; `/api/cockpit/active` 200; both static assets 200.

- [ ] **Step 5: Commit**

```bash
git add web/server.py tests/test_cockpit_shell.py README.md
git commit -m "feat(cockpit): auto-stop preview on power-off / ticker-start / clock-start / diy-paint + README"
```

---

## Self-Review

**1. Spec coverage:**

- Design system (CSS custom properties, glass panel, responsive shell) → Task 1 ✓
- Shell composition (`_render_shell` Python helper, single `_SHELL_TEMPLATE`) → Task 2 ✓
- Left panel hooks (lamp-viz, active-banner, brightness, power, diagnostics) → Task 2 ✓
- `cockpit.js` runtime (viz update, mode banner poll, brightness wire, power, diagnostics) → Task 3 ✓
- `/api/cockpit/active` endpoint with off / clock / ticker / preset / idle modes → Task 3 ✓
- Per-feature panel migration → Tasks 4 (Presets), 5 (DIY), 6 (Ticker), 7 (Clock) ✓
- Drop State page; `/state` 302 → / → Task 8 ✓
- Auto-stop preview loop on power-off / ticker-start / clock-start / diy-paint → Task 9 ✓
- Mutex against /api/diy/paint and /api/preview while ticker/clock running → unchanged from current code; still in place because we didn't touch those mutex checks ✓
- README updated → Task 9 ✓
- Tests: `tests/test_cockpit_shell.py` → Tasks 2, 3, 8, 9 ✓
- Responsive (≥720 px side-by-side, <720 px stacked) → Task 1 (CSS @media query) ✓
- Glassmorphism aesthetic (gradient bg, backdrop-filter, glowing accents) → Task 1 ✓

**2. Placeholder scan:** Tasks 4-7 have a deliberate "do the surgical HTML extraction" instruction because the existing per-page HTML is too large to inline in the plan verbatim, and the engineer needs to read the actual current file to do the migration. This is NOT a placeholder — it's a precise, bounded instruction with a concrete template, an explicit list of what to drop (`.wrap`, `.card`, `.tabs`, `.power-btns`, `:root`, `body` rules) and what to keep (feature-specific styles + body content + the existing `<script>` block), and a smoke test that verifies both shell and feature markers are present.

**3. Type consistency:**

- `_render_shell(active, panel_html, title)` — same signature across Tasks 2, 4, 5, 6, 7 ✓
- `_stop_preview()` — same async-no-args call site everywhere it's used (Tasks 3, 9) ✓
- `_PANEL_PRESETS` / `_PANEL_DIY` / `_PANEL_TICKER` / `_PANEL_CLOCK` — naming consistent across Tasks 4-7 ✓
- Shell-template variables (`{title}`, `{panel}`, `{cls_presets}`, `{cls_diy}`, `{cls_ticker}`, `{cls_clock}`) — consistent in Task 2 ✓
- Active-mode keys (`presets`, `diy`, `ticker`, `clock`) — consistent across `_render_shell` and `api_cockpit_active` ✓
- DOM hook IDs (`lamp-viz`, `active-banner`, `active-banner-value`, `brightness-slider`, `brightness-val`, `pwr-on`, `pwr-off`, `diag-body`, `device-id`) — same in the shell template (Task 2) and in `cockpit.js` (Task 3) ✓

**4. Notes for the implementer:**

- The HTML/CSS/JS extraction work in Tasks 4-7 is the labor-intensive part. Read the existing `_PAGE_*` constants first; the "surgical extraction" instruction in each task means: copy the body content minus the chrome, copy the script minus tab/power wiring, drop the generic CSS, keep the feature CSS. Smoke-test after each migration before moving to the next — if the page doesn't load, the failure is local to that one task.
- Per-feature panels MAY collide on CSS class names (e.g., both DIY and Clock used `.color-row`). Rename feature-specific classes with the panel's prefix (`.diy-color-row`, `.clock-color-row`) where collisions occur, and update the panel's `<script>` if it references the renamed class. The shell's CSS (Task 1) intentionally uses generic names (`.tabs`, `.panel`, `.brightness`) that the feature panels should NOT define.
- The `_preview_name` global in Task 3 is read by `api_cockpit_active` for the banner label. If you can't find a `name` field in the body or recolored preset dict during the `api_preview` migration, fall back to `"(unnamed)"` — the banner still works, it just shows a generic label.
- After every task's commit, restart the web server (`pkill -f web.server; .venv/bin/python -m web.server &`) and visually verify the page in a browser. The test suite catches the Python contract; only your eyes catch the visual regression.
