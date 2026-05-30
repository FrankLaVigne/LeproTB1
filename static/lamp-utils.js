// Shared front-end helpers for the Lepro lamp pages.
// Imported by /diy and /state via <script type="module">.

// Page→physical ring rotation. MUST mirror workshop.py's _OUTER_ROTATION,
// _MIDDLE_ROTATION, _INNER_ROTATION. See docs/CALIBRATION.md for derivation.
export const OUTER_ROT = 31;
export const MIDDLE_ROT = 22;
export const INNER_ROT = 4;

// Parse the N01 d50 format (the one we generate from DIY paints + ticker
// frames). Multi-program presets from the official app use N02/N03/#V:
// prefixes we don't decode here. Returns a 196-entry physical-space color
// array, or null if the d50 isn't an N01 we recognise.
export function parseD50_N01(d50) {
  if (!d50 || typeof d50 !== 'string') return null;
  // N and G are single digits in our format (max 9 groups).
  const m = d50.match(/^N01:P1000([0-9])([0-9A-Fa-f]+)F21000([0-9])([0-9A-Fa-f]+)U3V3/);
  if (!m) return null;
  const N = parseInt(m[1], 10);
  const colors = m[2];
  if (colors.length < N * 6) return null;
  const G = parseInt(m[3], 10);
  const lengthsHex = m[4];
  if (lengthsHex.length < G * 4) return null;
  const palette = [];
  for (let i = 0; i < N; i++) palette.push(colors.slice(i * 6, i * 6 + 6).toUpperCase());
  const physical = [];
  for (let g = 0; g < G; g++) {
    const len = parseInt(lengthsHex.slice(g * 4, g * 4 + 4), 16);
    const color = palette[g % N];
    for (let j = 0; j < len; j++) physical.push(color);
  }
  return physical.length === 196 ? physical : null;
}

// Reverse the page→physical rotation so the canvas can render in page-space
// (the orientation the DIY editor paints in, with each ring's index 0 at the
// top of the screen). Symmetric to apply_lamp_rotation() in workshop.py.
export function unrotateToPage(physical) {
  const page = new Array(196).fill('000000');
  for (let i = 0; i < 88; i++) page[i] = physical[(i + OUTER_ROT) % 88];
  for (let k = 0; k < 62; k++) page[88 + k] = physical[88 + (k + MIDDLE_ROT) % 62];
  for (let k = 0; k < 46; k++) page[150 + k] = physical[150 + (k + INNER_ROT) % 46];
  return page;
}

// Convenience: take the /api/lamp/state JSON and return a 196-entry page-space
// color array (or null if no parseable d50). Used by /diy on mount and by
// /state on every poll.
export function lampStateToPageLeds(stateJson) {
  if (!stateJson) return null;
  const dids = Object.keys(stateJson.devices || {});
  if (!dids.length) return null;
  const d50 = stateJson.devices[dids[0]].d50;
  if (!d50) return null;
  const physical = parseD50_N01(d50);
  if (!physical) return null;
  return unrotateToPage(physical);
}

// Client-side clock position math (mirrors clock.compute_positions in
// Python). Used by /clock's visualizer to render at 1 Hz without
// polling the server. `now` is a JS Date; `mode` is "12h" or "24h".
// Returns {outer, middle, inner} per-ring page-space LED indices.
export function computeClockPositions(now, mode) {
  if (mode !== "12h" && mode !== "24h") {
    throw new Error(`mode must be "12h" or "24h", got ${mode}`);
  }
  const secFrac = now.getSeconds() + now.getMilliseconds() / 1000;
  const outer = Math.round(secFrac * 88 / 60) % 88;

  const minFrac = now.getMinutes() + secFrac / 60;
  const middle = Math.round(minFrac * 62 / 60) % 62;

  let hourUnit, cycle;
  if (mode === "12h") {
    hourUnit = (now.getHours() % 12) + now.getMinutes() / 60;
    cycle = 12;
  } else {
    hourUnit = now.getHours() + now.getMinutes() / 60;
    cycle = 24;
  }
  const inner = Math.round(hourUnit * 46 / cycle) % 46;

  return { outer, middle, inner };
}
