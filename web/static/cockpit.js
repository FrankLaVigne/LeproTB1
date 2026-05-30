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
