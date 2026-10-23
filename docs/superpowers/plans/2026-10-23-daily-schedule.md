# Daily Schedule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The workshop server drives the lamp through a market-day schedule (opening bell → ticker → closing bell → recap → evening motion rotation → off), configurable from the web cockpit and the TUI.

**Architecture:** Three new modules with no server imports — `web/market_calendar.py` (NYSE days/hours), `web/schedule.py` (settings + pure `phase_at` timeline), `web/scheduler.py` (async runner over an injected `LampDriver`). `web/server.py` implements the driver with its existing helpers, owns the scheduler task, and serves `/api/schedule` + a Schedule tab. The TUI reads/writes the same API.

**Tech Stack:** Python 3, aiohttp, asyncio, zoneinfo, yfinance, Textual 8, pytest (asyncio_mode=auto).

**Spec:** `docs/superpowers/specs/2026-10-23-daily-schedule-design.md`

## Global Constraints

- All times America/New_York via `zoneinfo.ZoneInfo("America/New_York")`; never naive/local time (container is UTC).
- Market 09:30–16:00 ET; early close 13:00 ET.
- Defaults: `enabled=True, symbol="IBM", interval=30, recap_minutes=15, opening_motion="random", closing_motion="random", bell_seconds=20, evening_motions="all", evening_rotate_minutes=10, lights_out="22:30", non_trading_days="evening", evening_start="16:00"`.
- `interval` ∈ {10, 30, 60, 300}.
- No new dependencies.
- `schedule.json` at project root, git-ignored, written atomically (tmp + `os.replace`).
- Manual control wins until the next phase change; the scheduler never re-applies a phase it already applied.
- Phase names: `opening_bell, market, closing_bell, recap, idle_off, evening, night`.

## Review Focus

1. Restart at 02:00 / Saturday noon / 10:00 Tuesday applies night / off-or-evening / market respectively — Task 3 `test_boot_applies_current_phase`.
2. User paints DIY at 20:00 during evening: the next rotation tick must not override — Task 3 `test_takeover_stops_rotation`.
3. Friday evening → Monday open with a Monday holiday: `next_change_at` is Tuesday 09:30, not missing — Task 2 `test_next_change_spans_long_weekend`.
4. Price fetch fails at 09:30 (network down): ticker retries until it starts, scheduler keeps running — Task 3 `test_market_retries_ticker_start`.
5. Corrupt `schedule.json` on disk: server boots with defaults and logs — Task 2 `test_load_corrupt_file_uses_defaults`.

---

### Task 1: NYSE calendar

**Files:**
- Create: `web/market_calendar.py`
- Test: `tests/test_market_calendar.py`

**Interfaces:**
- Produces: `ET: ZoneInfo`, `OPEN: time(9,30)`, `CLOSE: time(16,0)`, `EARLY_CLOSE: time(13,0)`, `nyse_holidays(year: int) -> set[date]`, `is_trading_day(d: date) -> bool`, `close_time(d: date) -> time` (13:00 on early-close days else 16:00; only meaningful on trading days).

- [ ] **Step 1: Failing tests.** `test_holidays_2025`, `_2026`, `_2027` assert exact sets against NYSE published dates:
  - 2025: Jan 1, Jan 20, Feb 17, Apr 18, May 26, Jun 19, Jul 4, Sep 1, Nov 27, Dec 25.
  - 2026: Jan 1, Jan 19, Feb 16, Apr 3, May 25, Jun 19, Jul 3, Sep 7, Nov 26, Dec 25.
  - 2027: Jan 1, Jan 18, Feb 15, Mar 26, May 31, Jun 18, Jul 5, Sep 6, Nov 25, Dec 24.
  - `test_new_year_on_saturday_not_observed`: 2022-01-01 is Saturday → `date(2021,12,31) not in nyse_holidays(2021)`.
  - `test_weekends_not_trading`, `test_holiday_not_trading` (2026-11-26 False), `test_regular_day_trading` (2026-10-23 True).
  - `test_early_closes`: `close_time` is 13:00 on 2025-07-03, 2025-11-28, 2025-12-24, 2026-11-27, 2026-12-24; 16:00 on 2026-07-02 (July 3 2026 is the holiday, so no early close on Jul 2) and 2026-10-23.
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/test_market_calendar.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement.** Easter via the anonymous Gregorian algorithm (Good Friday = Easter − 2). Observance: Saturday → Friday before (except Jan 1), Sunday → Monday after. Early close days: Jul 3 (if trading day and Jul 4 is not a Saturday-observed-Friday), day after Thanksgiving, Dec 24 (if trading day).
- [ ] **Step 4:** tests PASS.
- [ ] **Step 5:** commit `feat(schedule): NYSE trading-day calendar`.

### Task 2: Settings + phase timeline

**Files:**
- Create: `web/schedule.py`
- Modify: `.gitignore` (add `schedule.json`)
- Test: `tests/test_schedule.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `DEFAULTS: dict` (Global Constraints values).
  - `validate(settings: dict) -> dict` — returns a normalized full copy (symbol upper-stripped); raises `ValueError("<field>: <reason>")`. Unknown keys rejected.
  - `merge(current: dict, changes: dict) -> dict` = `validate({**current, **changes})`.
  - `load_settings(path: Path, env: Mapping) -> dict` — file missing → DEFAULTS with symbol/interval seeded from `LEPRO_TICKER_AUTOSTART`/`LEPRO_TICKER_INTERVAL`; corrupt/invalid file → same, logs a warning.
  - `save_settings(path: Path, settings: dict) -> None` (atomic).
  - `@dataclass(frozen=True) PhaseInfo: phase: str, since: datetime | None, next_phase: str | None, next_change_at: datetime | None, trading_day: bool`.
  - `phase_at(now: datetime, s: dict) -> PhaseInfo` (now tz-aware; converted to ET).

Timeline algorithm (the part the tests don't pin): build events `(start_dt, phase)` for each day in `[today-3, today+8]`:
- trading day: 09:30 → `opening_bell` (skip if bell_seconds=0), 09:30+bell → `market`; close → `closing_bell` (skip if 0); close+bell → `recap` (skip if recap 0); then evening start E = close+bell+recap; on an early-close day emit `idle_off` at E and set E = max(E, day@evening_start); emit `evening` at E.
- non-trading day with `non_trading_days="evening"`: E = day@evening_start, emit `evening`.
- for each emitted `evening` at E: lights-out L = first datetime ≥ E with HH:MM = lights_out (same day or next); emit `night` at L.
- Sort, drop consecutive duplicates. Phase = last event ≤ now (none → `night`); `since` = that event's start; next = first later event.

Validation of lights_out: let normal_E = 16:00 + bell_seconds + recap_minutes (as minutes of day) and E_max = max(normal_E, evening_start). Reject (`ValueError("lights_out: must be after the evening starts (… ) or before 09:30")`) when 09:30 ≤ lights_out < E_max.

- [ ] **Step 1: Failing tests** (`ET` datetimes; `s = DEFAULTS` unless noted):
  - `test_trading_day_timeline` on Fri 2026-10-23: 09:29 → night; 09:30:05 → opening_bell; 09:30:20 → market; 15:59 → market; 16:00:10 → closing_bell; 16:00:20 → recap; 16:15:20 → evening; 22:29 → evening; 22:30 → night.
  - `test_bells_disabled`: bell_seconds=0 → 09:30 market, 16:00 recap, never a bell phase.
  - `test_recap_disabled`: recap_minutes=0 → 16:00:20 evening.
  - `test_early_close_gap`: 2026-11-27: 13:00:10 closing_bell, 13:00:20 recap, 13:15:20 idle_off, 16:00 evening.
  - `test_weekend_evening_mode`: Sat 2026-10-24 12:00 → night with next (`evening`, 16:00); 16:00 → evening.
  - `test_weekend_off_mode`: non_trading_days="off", Sat 12:00 and 18:00 → night, next_change_at = Mon 2026-10-26 09:30 opening_bell.
  - `test_holiday_like_weekend`: Thanksgiving 2026-11-26 17:00 → evening.
  - `test_lights_out_after_midnight`: lights_out="00:30": Fri 23:59 → evening; Sat 00:29 → evening; Sat 00:31 → night.
  - `test_next_change_spans_long_weekend`: off mode, Fri 2026-09-04 23:00 (Labor Day Mon 09-07) → next_change_at Tue 2026-09-08 09:30.
  - `test_dst_day`: Sun 2026-11-01 and Mon 2026-11-02 09:30 ET → opening_bell (wall-clock, not UTC-shifted).
  - `test_now_in_utc_is_converted`: `datetime(2026,10,23,13,31,tzinfo=UTC)` → market.
  - `test_validate_rejects`: interval 15; symbol ""; lights_out "15:00"; lights_out "25:00"; non_trading_days "maybe"; bell_seconds -1; evening_motions "x"; unknown key `foo` — each raises ValueError whose message starts with the field name.
  - `test_validate_accepts_lights_out_before_open`: "08:00" ok.
  - `test_load_missing_seeds_from_env` (`LEPRO_TICKER_AUTOSTART=" aapl "` → "AAPL"), `test_save_load_roundtrip`, `test_load_corrupt_file_uses_defaults` (file `"{not json"`).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement per Interfaces + algorithm.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(schedule): settings and phase timeline`.

### Task 3: Scheduler runner

**Files:**
- Create: `web/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: Task 2 (`phase_at`, `merge`, `save_settings`, `load_settings`).
- Produces:
  - `class LampDriver(Protocol)`: `async start_ticker(symbol: str, interval: int) -> object` (raises on failure; returns a handle), `async play_motion(motion_id: str) -> object` (handle), `async solid(color: str) -> None`, `async power_off() -> None`, `owns(handle: object) -> bool` (that handle is still what drives the lamp), `capture_busy() -> bool`, `motion_ids() -> list[str]`, `motion_name(motion_id: str) -> str`, `async day_change(symbol: str) -> float | None`, `last_ticker_color() -> str | None` (hex of the ticker's last color).
  - `class Scheduler(driver, path: Path, env: Mapping, now=lambda: datetime.now(ET), sleep=asyncio.sleep, rng=random.Random())`: `settings: dict`, `async run() -> None` (forever), `async update(changes: dict) -> dict` (merge → save → wake loop; raises ValueError), `status() -> dict` with keys `enabled, phase, phase_since, next_phase, next_change_at, trading_day, current_motion` (ISO strings / None), `driving() -> bool` (scheduler's last handle still owned, or last phase was a solid/off one and nothing else has taken over is NOT tracked — only handle-based phases count; `night`/`recap`/`idle_off` return False).
  - Recap color: `day_change > 0` → `"00FF00"`, `< 0` → `"FF0000"`, `== 0` → `"FFFFFF"`, None → `last_ticker_color()` if it is `"00FF00"`/`"FF0000"` else `"FFFFFF"`.
  - Evening fallback when no motions: `solid("FFB46B")`.

Loop shape: each iteration compute `info = phase_at(now(), settings)`; if `enabled` and `info.phase != applied` → apply (on exception: log, retry in 30 s, `applied` unchanged); if evening and rotation due and `driver.owns(handle)` → play another (not the same id when >1 available); if the handle is no longer owned during evening → stop rotating until the next phase. Capture busy → don't apply, retry in 60 s. Sleep until the earliest of next change, next rotation, retry — interruptible by an `asyncio.Event` set by `update()`. A `symbol`/`interval` change while `applied == "market"` and `owns(ticker handle)` → restart ticker. Disabling: stop acting, leave lamp. Enabling: `applied = None` so the current phase applies.

- [ ] **Step 1: Failing tests** with a `FakeDriver` recording calls (list of tuples) and a fake clock whose `sleep` advances time:
  - `test_boot_applies_current_phase` (parametrized: Tue 2026-10-27 02:00 → `power_off`; Sat 12:00 evening-mode → `power_off`; Tue 10:00 → `start_ticker("IBM",30)`; Tue 18:00 → `play_motion`).
  - `test_opening_bell_then_ticker`: start 09:29:50 → play_motion(configured id) at 09:30, start_ticker at 09:30:20.
  - `test_random_bell_uses_catalog`: opening_motion="random" plays an id from `motion_ids()`; missing configured id → random.
  - `test_recap_colors` (parametrized day_change 1.5/−2/0/None+last "FF0000"/None+None → 00FF00/FF0000/FFFFFF/FF0000/FFFFFF).
  - `test_evening_rotates_without_repeat`: 3 ids, 35 minutes of evening → 4 plays, no consecutive repeats.
  - `test_takeover_stops_rotation`: after first evening play, `driver.owned=False` → no further play_motion until night → `power_off` at lights-out.
  - `test_manual_change_survives_until_next_phase`: in market, driver reports not owned; no re-apply until closing_bell.
  - `test_market_retries_ticker_start`: start_ticker raises twice then succeeds → 3 calls, loop still alive.
  - `test_capture_blocks_then_applies`: capture_busy True for the first 2 minutes → apply after it clears.
  - `test_update_saves_and_reapplies`: in evening at 21:00, `update({"lights_out": "20:30"})` → `power_off`, file contains 20:30; invalid change raises ValueError and file unchanged.
  - `test_symbol_change_restarts_ticker`: market, `update({"symbol": "AAPL"})` → `start_ticker("AAPL", 30)`.
  - `test_disable_leaves_lamp_enable_applies`.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(schedule): scheduler runner`.

### Task 4: Server integration + API

**Files:**
- Modify: `web/server.py` (driver, startup/cleanup, routes, `_active_mode`, `api_lamp_leds`), `web/ticker.py` (`fetch_day_change`), `docker-compose.yml` (comment: env vars now seed the schedule), `README.md`
- Test: `tests/test_schedule_api.py`, `tests/test_ticker.py` (fetch_day_change), adjust `tests/test_ticker_autostart.py`

**Interfaces:**
- Consumes: Tasks 2–3; existing `_start_ticker`, `_stop_preview`, `_run_preview`, `_load_motion_catalog`, `_find_motion`, power-off path from `api_power`, `build_d50_from_leds`, `apply_lamp_rotation`.
- Produces:
  - `web/ticker.py: fetch_day_change(symbol) -> float | None` (`fast_info` last_price − previous_close; None on any error).
  - `class _ServerDriver` implementing `LampDriver`; ticker handle = the `TickerSession`; motion handle = the preview `asyncio.Task`; `owns(h)` = `h is _ticker_session and running` or `h is _preview_task and not done`.
  - Extract `async def _power_off_all()` from `api_power`'s off branch (api_power calls it).
  - Extract `async def _play_motion_entry(entry: dict, palette: list[str]) -> asyncio.Task` from `api_motion_play` (it calls it; sets `_preview_kind="motion"`).
  - `_scheduler: Scheduler | None`; `_on_startup` creates it with `_PROJECT_ROOT / "schedule.json"` and `os.environ`, runs `run()` as `app["scheduler"]`; replaces the `_autostart_ticker` call (delete `_autostart_ticker` + `_ticker_autostart_config`; seeding now lives in `load_settings`).
  - `GET /api/schedule` → `{"settings", "status"}`; `PUT /api/schedule` (JSON body = changes) → same, 400 `{"ok": False, "error"}` on ValueError.
  - `_active_mode()`: when `_scheduler.driving()`, label becomes `"📅 " + <existing label>`. `api_lamp_leds` adds `"schedule": {"phase", "next_phase", "next_change_at"}` (None when no scheduler).
- [ ] **Step 1: Failing tests** (`_FakeReq` style from `tests/test_motions_api.py`, scheduler built on a tmp path with a fake driver): `test_get_schedule_shape`, `test_put_schedule_updates_and_persists`, `test_put_schedule_invalid_400`, `test_active_mode_prefix_when_scheduler_driving`, `test_lamp_leds_includes_schedule`, `test_fetch_day_change` (monkeypatched yfinance like existing fetch_price tests: 253.0/250.0 → 3.0; error → None). Replace `tests/test_ticker_autostart.py` with seeding coverage already in Task 2 (delete the file).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** full suite PASS (`.venv/bin/python -m pytest -q`).
- [ ] **Step 5:** commit `feat(schedule): run the schedule in the workshop server`.

### Task 5: Web Schedule tab

**Files:**
- Modify: `web/server.py` (`_PANEL_SCHEDULE`, `index_schedule`, nav link `📅 Schedule`, route `/schedule`, `_render_shell` active class)
- Test: `tests/test_cockpit_shell.py` (follow its existing per-tab tests)

- [ ] **Step 1: Failing tests:** `/schedule` renders the shell with the Schedule tab active; nav contains `href="/schedule"`; panel contains inputs named for every DEFAULTS key.
- [ ] **Step 2:** FAIL. **Step 3:** implement — status card (phase, since, next change, refreshed every 5 s from `GET /api/schedule`), form for every setting (time inputs for HH:MM, select for interval / non_trading_days, number inputs, enabled checkbox, motion selects for bells with "Random" first, "All motions" checkbox + checklist from `/api/motions`, named first by name, unnamed by id), Save → `PUT`, inline error text from the response. Styling follows the existing panels (prefix classes `.sched-`).
- [ ] **Step 4:** PASS; manually load `http://localhost:8081/schedule` after deploy (Task 7).
- [ ] **Step 5:** commit `feat(schedule): Schedule tab in the web cockpit`.

### Task 6: TUI Schedule screen

**Files:**
- Modify: `cli/tui_api.py` (`get_schedule() -> dict`, `put_schedule(changes: dict) -> dict`, `get_motions() -> list[dict]`), `cli/tui.py` (binding `c` → `ScheduleScreen`; banner shows `next: <phase> at HH:MM` from `get_leds()["schedule"]`)
- Create: `cli/tui_schedule.py` (`class ScheduleScreen(ModalScreen[bool])`)
- Test: `tests/test_tui_api.py`, `tests/test_tui_app.py` (extend `StubApi`)

- [ ] **Step 1: Failing tests:** tui_api round-trips against aiohttp TestServer like existing tests; app tests: `c` opens ScheduleScreen populated from `get_schedule`; editing lights_out + Save calls `put_schedule({"lights_out": "23:00"})` with only changed fields; an `{"ok": False, "error": ...}` reply shows a notification and keeps the screen open; Escape closes without PUT; banner text includes "next:".
- [ ] **Step 2:** FAIL. **Step 3:** implement with Textual `Input`, `Switch`, `Select`, `SelectionList` (evening motions; disabled when All is on). **Step 4:** PASS. **Step 5:** commit `feat(tui): Schedule screen`.

### Task 7: Docs, deploy, verify

- [ ] README: replace "Autostart on boot" with a "Daily schedule" section (phases table, settings, where to edit, env vars seed defaults); TUI keys list gains `c`.
- [ ] Full suite green.
- [ ] Redeploy: `docker restart lepro-web` (code is bind-mounted; env unchanged). Verify `GET /api/schedule` shows the right phase for now, banner shows 📅, `/schedule` loads.
- [ ] Commit `docs(schedule): README` and `git push origin main`.
