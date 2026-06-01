# Capture UI — Design Spec

**Date:** 2026-06-01
**Status:** Approved via brainstorm (free-form click-per-capture, always-save with dedup notice, progress counter on Animations tab).

## Goal

Add a UI-based capture flow on the Animations tab so the user can grow the
preset library without dropping to the CLI. Each click captures one
animation from the Lepro app, names it automatically, persists it to
`presets/`, and refreshes the Animations list. Resumable across phone-app
freezes because each save lands immediately on disk.

## Background

Today the user captures new animations via `python -m cli.main capture
--seconds 90`, which subscribes to MQTT, records distinct `d50` changes
during the window, then saves a JSON file. That works but requires
terminal access and forces a fixed 90-second window per capture.

Two operational constraints documented in project memory:

- **Lepro app freezes** after the user cycles through many animation
  changes in a single session ([[lepro-app-animation-count]]).
  The capture UI MUST be resumable: each save lands on disk immediately
  so the user can pick up after restarting the phone app.
- **Single MQTT session per Lepro account** — same constraint as
  ticker/clock/preview; the capture session participates in the same
  mutex pattern.

Target: grow from current 6 unique animations toward the **~72** the
Lepro app exposes (3 × 24, per [[lepro-app-animation-count]]).

## Approach

A free-form, one-click-per-capture model. The user clicks Capture, triggers
ONE animation in the Lepro phone app, and the server auto-stops after a
short idle gap (or a hard cap). The captured frames become a new preset.

Frame collection: the server polls `_client.state[did]["d50"]` every
~200ms during the window, deduplicating consecutive identical values. A
distinct d50 = a new frame.

Architecture: a new `web/captures.py` module holds the pure helpers and
the `CaptureSession` class (mirrors the shape of `TickerSession` and
`ClockSession`). `web/server.py` adds 3-4 routes, a module-level
`_capture_session` global, and extends the active-mode banner with a new
`"capturing"` mode. The Animations tab UI gains a capture bar at the
top: progress counter + Capture button + save form when finished.

## User flow

1. User opens `/animations`. At the top: **"5 unique / ~72 target"**
   counter and a **"🎥 Capture"** button.
2. User clicks Capture.
   - Button morphs to **"Capturing... 0 frames"** with `[Save now]` and
     `[Cancel]` actions.
   - Active-mode banner across all pages shows `🎥 Capturing — N frames`.
3. User triggers an animation in the Lepro phone app.
   - Each distinct `d50` arriving over MQTT bumps the frame counter live
     (the page polls `/api/captures/state` every 500ms).
4. Server auto-stops on whichever comes first:
   - **6 seconds of no new distinct d50** (idle timeout)
   - **90s hard cap**
   - User clicks **[Save now]**
   - User clicks **[Cancel]** (no save)
5. On stop, the capture bar shows an inline save form:
   - Pre-filled name: `capture-YYYY-MM-DD-HHMM-N` (N = sequence within day)
   - `[Save]` and `[Discard]` buttons
6. On save:
   - The preset lands at `presets/<name>.json` in the same shape as today's
     captures (single payload OR `frames` list depending on count)
   - The Animations list refreshes
   - A notice replaces the capture bar:
     - If fingerprint matches an existing animation:
       `"Saved as capture-... — matches Tour (now 3 variants)"`
     - Otherwise:
       `"Saved as capture-... — new animation #6"`
   - Counter updates ("6 unique / ~72 target")

## Routes

### `POST /api/captures/start`

Body: `{}` (no parameters for v1; the only knob is timing constants).

Behaviour:
- 409 if any other lamp-driver is running (`_ticker_session.running`,
  `_clock_session.running`, `_preview_task` alive, OR `_capture_session`
  already running). Reuse the mutex pattern from ticker/clock starts.
- Capture the lamp's current d50 as the "baseline" (so frame 1 is the
  first d50 that DIFFERS from the baseline, not the d50 that was already
  there when the user clicked Capture).
- Create `_capture_session = CaptureSession(client=_client, baseline_d50=...)`.
- `await sess.start()` — spawns a 200ms-poll task that compares
  `_client.state[did].get("d50")` to the last seen value; if different
  and non-baseline, appends to the session's `frames` list.
- Returns `{"ok": true, "started_at": "<iso>"}`.

### `POST /api/captures/save`

Body: `{"name": "<user-edited-name>"}`.

Behaviour:
- 400 if no `_capture_session` is active.
- 400 if `len(session.frames) == 0` ("no frames captured; nothing to save").
- Validate `name` via existing `_sanitize_name`.
- 400 if `presets/<name>.json` already exists.
- Stop the polling task.
- Build the preset payload (`build_capture_preset(frames, name)`).
- Write `presets/<name>.json`.
- Compute the new preset's fingerprint, look it up against
  `group_presets(_PRESETS_DIR)`. Note: this runs AFTER the write, so the
  new preset is part of the lookup; we need to check whether the matched
  group contains MORE than just the file we just wrote.
- Clear `_capture_session`.
- Response (200):
  ```json
  {
    "ok": true,
    "path": "presets/capture-2026-06-01-1432-1.json",
    "matched_animation": null | {"id": "<hash>", "name": "Tour", "variant_count": 3}
  }
  ```

### `POST /api/captures/cancel`

Behaviour:
- If `_capture_session is None`, return 200 `{"ok": true}` (idempotent).
- Stop the polling task without saving. Clear `_capture_session`.
- Return `{"ok": true}`.

### `GET /api/captures/state`

Always available. Returns:

```json
{
  "running": true,
  "started_at": "2026-06-01T14:32:01",
  "frame_count": 12,
  "auto_stop_at": "2026-06-01T14:33:31",      // started_at + 90s OR last_frame + 6s, whichever is sooner
  "default_name": "capture-2026-06-01-1432-1"
}
```

When `running: false`, all detail fields are `null`.

### Active-mode banner

`api_cockpit_active` in `web/server.py` gains a new top-priority branch:

```
1. if _capture_session is not None and _capture_session.running -> "capturing"
2. if _client.state[did].d1 == 0 -> "off"
3. if _clock_session running -> "clock"
4. if _ticker_session running -> "ticker"
5. if _preview_task alive -> "preset"
6. else -> "idle"
```

Capture-mode label: `🎥 Capturing — N frames`.

## Module structure

### New file: `web/captures.py`

```python
def dedup_consecutive(frames: list[str]) -> list[str]:
    """Drop consecutive duplicates from a frame list. Non-adjacent
    duplicates are kept (the AI cycles through the same frame multiple
    times in some animations)."""

def auto_capture_name(now: datetime, existing_names: list[str]) -> str:
    """Generate capture-YYYY-MM-DD-HHMM-N where N is the next free sequence
    number for that minute. Avoids collisions with existing presets."""

def build_capture_preset(frames: list[str], name: str) -> dict:
    """Assemble the preset JSON shape from the captured frames.
       Single frame -> {name, description, captured, prompt, payload: {d50, d1, d2}}.
       Multi-frame -> {name, description, captured, prompt, frames: [{d50, d1, d2}, ...]}.
       Mirrors today's saved-from-DIY format so the Presets and Animations
       tabs both consume it without special-casing."""

class CaptureSession:
    """Holds the in-flight capture state + the polling task."""

    def __init__(self, client, baseline_d50: str | None,
                 idle_timeout: float = 6.0, hard_cap: float = 90.0): ...

    @property
    def running(self) -> bool: ...

    @property
    def frame_count(self) -> int: ...

    async def start(self) -> None: ...
    async def stop(self) -> None: ...   # cancel the task; preserve frames
    def snapshot(self) -> dict: ...     # for /api/captures/state
    @property
    def frames(self) -> list[str]: ...  # the deduped d50 list, for save
```

`CaptureSession._run` is the background polling loop: every 200ms, read
`self._client.state[did].get("d50")`. If different from baseline AND
different from last-recorded, append. Track `_last_frame_at`. Auto-stop
when `now - _last_frame_at > idle_timeout` OR `now - _started_at > hard_cap`.

### Modified file: `web/server.py`

- Module-level `_capture_session: CaptureSession | None = None`.
- 4 new route handlers + their registration in `build_app`.
- `api_cockpit_active` updated to surface `capturing` mode.
- `_PANEL_ANIMATIONS` extended with the capture bar + state polling JS.
- Mutex teardowns: `api_ticker_start`, `api_clock_start`, `api_preview`
  refuse if a capture is running (409). The capture refuses if any of
  THEM are running (parallel pattern). Power-off stops the capture as
  part of the existing teardown helper.

### Modified file: `web/static/cockpit.css`

- `.capture-bar`, `.capture-counter`, `.capture-button`, `.capture-active`,
  `.capture-save-form`, `.capture-notice` — scoped to avoid collision.

### New file: `tests/test_captures.py`

- Pure: `dedup_consecutive`, `auto_capture_name`, `build_capture_preset`.
- `CaptureSession`: idle-timeout fires correctly, hard-cap fires correctly,
  baseline d50 is NOT recorded as a frame, repeated identical d50s are
  deduped.
- HTTP: start → 409 if other session running; save → 400 with no frames;
  save → writes file + returns matched_animation info; cancel → idempotent.

~12 tests total.

## Storage

No new persistent storage. Captured presets land in `presets/` exactly
like CLI captures and DIY saves. `_capture_session` lives in memory; a
workshop restart drops any in-flight capture (acceptable for v1 — the user
just clicks Capture again).

## Progress counter

The Animations tab's existing `loadAnimations()` (from the
`_PANEL_ANIMATIONS` script) already fetches the grouped list. Add a
small header element above the list:

```html
<div class="capture-bar">
  <div class="capture-counter">
    <strong>N</strong> unique animations / ~72 target
  </div>
  <button id="capture-btn">🎥 Capture</button>
</div>
```

`N` = `groups.length` from the API response. `72` is a constant baked
into the page constant (or fetched from a tiny config endpoint if we
ever want it adjustable — YAGNI for v1).

## Edge cases

- **No frames captured** (user clicked Capture, never triggered the app,
  timeout expired): the save flow returns 400 with `"nothing to save"`;
  the UI shows the message and resets to the idle Capture button.
- **First d50 == baseline** (lamp's current state IS what the user
  triggered): if the user triggered an animation that happens to look
  identical to what was already on the lamp, no frames register. We accept
  this — it's a degenerate case and the user can power-cycle the lamp before
  re-trying. Out of scope for v1.
- **Lepro app freezes mid-capture**: we get whatever frames came in before
  the freeze. The 6-second idle timeout fires, the user can save the
  partial. Resumability is preserved by the immediate disk write.
- **MQTT connection drops during capture**: `_client.state[did]` stops
  updating; we never see new d50s; the idle timeout fires; the user sees
  "0 frames captured" and can retry. The cockpit's active-mode banner
  would also surface the MQTT death (task #107) once that fix lands.
- **User starts a clock/ticker while capture is running** (or vice versa):
  409 in both directions. Same mutex pattern as everything else.

## Testing

`tests/test_captures.py`:

- `test_dedup_consecutive_keeps_non_adjacent_duplicates` — `["A", "B", "A"]`
  stays 3 entries; `["A", "A", "B"]` becomes `["A", "B"]`.
- `test_dedup_consecutive_empty` — `[]` stays `[]`.
- `test_auto_capture_name_first_of_minute` — produces `capture-...-1`.
- `test_auto_capture_name_collides_increments` — if `capture-...-1` exists,
  returns `-2`.
- `test_build_capture_preset_single_frame_payload_shape` — one frame
  produces a `payload: {d50, d1, d2}` shape.
- `test_build_capture_preset_multi_frame_frames_shape` — multiple frames
  produce a `frames: [...]` shape.
- `test_capture_session_baseline_not_recorded` — if poll returns the baseline,
  no frames append.
- `test_capture_session_distinct_d50_appends` — three distinct d50s yield
  3 frames.
- `test_capture_session_idle_timeout_fires` — after `idle_timeout`s of no
  new frames, `running` becomes False.
- `test_capture_session_hard_cap_fires` — after `hard_cap` from start,
  `running` becomes False even if frames keep arriving.
- HTTP layer (3 tests via monkeypatch like `test_animations.py`):
  - `POST /api/captures/start` 409 when ticker is running
  - `POST /api/captures/save` 400 when no frames
  - `POST /api/captures/save` writes file + returns matched_animation

## Known risk: MQTT session fight during capture

The Lepro account has a single MQTT session slot. While our `web/server.py`
holds it (for the lamp visualizer + active-mode polling), the Lepro phone
app's MQTT publishes are blocked. The capture flow assumes the user
**triggers an animation in the phone app while the workshop is running**.
Whether the phone app can actually trigger animations through some
secondary path (REST? a different auth channel?) during that window has
NOT been end-to-end verified by this work.

The user has captured 6 animations before — so this is empirically
possible, but the exact workflow that makes it work has not been
documented. Likely workflows (any one of which would let v1 ship):

- **A.** Phone app uses a REST `set-effect` endpoint (not MQTT) that
  still works when its MQTT session is dropped.
- **B.** User triggers the effect on the phone app BEFORE the workshop
  reclaims the slot (e.g., before starting capture). The lamp keeps
  playing locally; workshop captures the ongoing echoes.
- **C.** The session-per-account rule is laxer than we think and both
  can subscribe concurrently for short windows.

If A or B turns out to be the case, the capture UI works as designed.
If C is the case, even better. If NONE of these work and the phone
app is fully locked out during workshop runtime, the capture-from-UI
feature requires the [[lepro-tb1-project]] **dedicated second Lepro
account** pending work to land first — at which point mcphost (or a new
listener-only process) holds its own slot and the phone app keeps the
daily account's slot.

**Validation plan (do this in Task 1 before writing any code):** start
the workshop, trigger an animation in the Lepro app, check the workshop
log for incoming MQTT state messages with new d50 values. If nothing
arrives, file BLOCKED on this plan and pivot to the second-account work.

## Deliberately deferred (v2)

- **Bulk / live / wizard modes** — start with single-shot; revisit only
  if free-form turns out to be too tedious for the 66 captures ahead.
- **Visual frame previewer** — show the captured frames as a thumbnail strip
  before save. Nice-to-have; users can preview by playing the saved preset.
- **Capture mid-edit** — let the user start typing the save name while the
  capture window is still open. v1 keeps these phases sequential.
- **Custom auto-stop knobs** — `idle_timeout` and `hard_cap` are hardcoded
  in v1. Tunable via constants in `web/captures.py` if needed.

## File-change summary

| File | Change | Lines (est.) |
|---|---|---|
| `web/captures.py` (new) | Pure helpers + `CaptureSession` | ~180 |
| `web/server.py` | 4 routes + mutex integration + active-mode update + power-off teardown | ~+150 |
| `_PANEL_ANIMATIONS` (in web/server.py) | Capture bar + save form + state polling JS | ~+90 |
| `web/static/cockpit.css` | `.capture-*` styles | ~+40 |
| `tests/test_captures.py` (new) | Pure + async + HTTP tests | ~150 |
| `README.md` | One sentence in the Animations bullet | ~+3 |

~610 LOC added; no removals.
