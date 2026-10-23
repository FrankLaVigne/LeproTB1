# Daily Schedule — Design

**Date:** 2026-10-23
**Status:** Draft — awaiting approval (builder check done)

## Intent

Today the lamp runs the IBM ticker forever (boot autostart, `LEPRO_TICKER_AUTOSTART`).
Outside market hours that's pointless: the price doesn't move, so the lamp sits on a
stale solid color all evening and all night. Frank wants the lamp to follow the
market's day on its own — ticker while the market is open, a recap of how the day
ended at the close, something pleasant to look at in the evening, dark overnight —
and to be able to change all of that from the web cockpit **and** the TUI without
editing files or restarting the container.

## Goals

- One daily schedule, run by the workshop server (`web/server.py`), that moves the
  lamp through these **phases**:

  | Phase | When (defaults) | Lamp does |
  |---|---|---|
  | `market` | market open → close on trading days (9:30–16:00 ET) | whole-lamp ticker for the configured symbol/interval (today's behavior) |
  | `recap` | first N minutes after the close (15) | solid green if the symbol closed up vs. the previous close, red if down, white if flat/unknown |
  | `evening` | end of recap → lights-out | a random motion from the motion library, switching to a different random one every M minutes (10) |
  | `night` | lights-out → next market open (22:30) | lamp powered off |

- **Non-trading days** (weekends + NYSE holidays): a setting chooses between
  `off` (lamp off all day) and `evening` (lamp off until `evening_start`,
  default 16:00, then the evening rotation until lights-out, then night).
- **Early-close days** (13:00 close): `market` and `recap` run as usual, then the
  lamp is off (a `night`-style gap, reported as phase `idle_off`) until
  `evening_start`, then evening as normal — so the rotation never runs through
  the afternoon. *(Claude's call.)*
- Every setting below is editable from a new **Schedule** tab in the web cockpit and
  from a **Schedule** screen in the TUI, and survives restarts:

  | Setting | Default |
  |---|---|
  | `enabled` (master switch) | `true` |
  | `symbol` | `"IBM"` |
  | `interval` (s; 10/30/60/300 — the ticker's allowed set) | `30` |
  | `recap_minutes` (0 disables recap) | `15` |
  | `evening_motions` — `"all"` or a list of motion ids | `"all"` |
  | `evening_rotate_minutes` | `10` |
  | `lights_out` (HH:MM) | `"22:30"` |
  | `non_trading_days` — `"off"` or `"evening"` | `"evening"` |
  | `evening_start` (HH:MM; non-trading and early-close days) | `"16:00"` |

  All times are America/New_York (Frank is in Maryland; the market is in New
  York). No timezone setting. *(Claude's call — dropping it removes the only way
  lights-out could land inside market hours.)*

- Both front-ends show the current phase and what happens next
  (e.g. "📅 Evening — next: lights out at 22:30").

## Non-goals

- Per-weekday schedules or multiple named schedules. One schedule, plus the
  trading/non-trading split. (Frank was asked; he accepted the single-schedule list.)
- Pre-market / after-hours ticker. `market` is regular session only.
- Multiple symbols or per-ring tickers in the schedule (the Ticker tab still does that manually).
- Brightness schedules, sunrise/sunset, or a morning wake-up animation.

## Anti-goals (would make this a failure even if it "works")

- **Fighting the user.** If Frank picks a preset, paints DIY, or turns the lamp off
  by hand, the scheduler must not snap it back on its next tick. Manual control
  wins until the next phase change.
- **Silent death.** A failed price fetch or MQTT hiccup at a phase change must not
  leave the scheduler stopped forever (cf. the reconnect bug fixed 2026-10-23).
- Lighting up the room at night after a container restart or a missed transition.

## Behavior

### Phase changes are edge-triggered; boot is level-triggered

- The scheduler computes the phase for "now" from the settings + calendar. It acts
  only when the phase **changes** (and once at startup, applying whatever phase it
  is right now — so a 2am restart leaves the lamp off, a 10am restart starts the ticker).
- Between changes it does nothing except the evening rotation timer. Manual actions
  made through any endpoint persist until the next phase change.
- Saving new settings re-evaluates immediately: if the phase for "now" differs from
  the phase the scheduler last applied, apply the new one. Changing a setting that
  doesn't change the current phase (e.g. lights-out later tonight) does not
  interrupt what's playing — with one exception: changing `symbol`/`interval`
  during `market` restarts the ticker with the new values.
- Turning `enabled` off stops scheduling but leaves the lamp as it is.
  Turning it on applies the current phase.

### Applying a phase

Each phase uses the server's existing one-lamp-driver mechanics (stop whatever else
is driving the lamp first — ticker, clock, preview — as the existing endpoints do):

- `market`: `_start_ticker({"outer": symbol}, interval, whole_lamp=True)`. Its
  first-price fetch may fail (network down at 9:30); retry like `_autostart_ticker`
  does today until it starts or the phase ends.
- `recap`: stop the ticker, fetch today's close and previous close for the symbol,
  paint solid green/red/white. If the fetch fails, use the ticker's last tick
  direction; if there's none, white.
- `evening`: power on, play a random motion (from `evening_motions`, or all motions
  in the catalog) in its reference colors via the same path as `api_motion_play`.
  Every `evening_rotate_minutes`, pick another — never the same id twice in a row
  when more than one is available. If the catalog/list is empty, fall back to a
  solid warm white.
- `night`: same shutdown path as `POST /api/power {"on": false}`.
- A capture in progress blocks applying a phase (captures are deliberate user
  work). Retry every minute until the capture ends or the phase changes.

### Calendar

- Trading day = Mon–Fri in America/New_York that isn't an NYSE full-day holiday.
  Market hours are 9:30–16:00 America/New_York.

### Lights-out across midnight *(Claude's call)*

- `lights_out` means the first time that HH:MM comes around **after the evening
  starts** (16:00 + recap on a trading day, `evening_start` otherwise). So 22:30 is
  tonight, 00:30 is after midnight, 15:00 on a trading day would be 15:00
  *tomorrow*.
- The next market open always ends the evening/night, whatever lights-out says.
- Validation: the API rejects (400) a `lights_out` that falls between 09:30 and
  the evening start (16:00 + recap, or `evening_start` if later) — those would
  leave no night at all. The web form and TUI say so in the error text.
- `evening_start` must be earlier in the day than `lights_out` is reached, by the
  same rule; otherwise 400.
- NYSE holidays: New Year's, MLK Day, Presidents' Day, Good Friday, Memorial Day,
  Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas, with NYSE's
  weekend observance rules (Saturday → Friday before, except New Year's on a
  Saturday is not observed; Sunday → Monday after).
- NYSE early-close days (13:00): day before Independence Day, day after
  Thanksgiving, Christmas Eve, when they fall on a trading day.
- No new dependency: a small rule-based module (`web/market_calendar.py`) with
  tests against known 2025–2027 dates. *(Builder's call is fine on implementation;
  the "no new dependency" part is Claude's call.)*
- The container runs in UTC; all comparisons use `zoneinfo`, never local time.

### Persistence

- `schedule.json` at the project root (git-ignored, like `config.json`),
  written atomically. Missing file → defaults, with `symbol`/`interval` seeded
  from `LEPRO_TICKER_AUTOSTART` / `LEPRO_TICKER_INTERVAL` if set.
- The schedule **replaces** the boot autostart: `_autostart_ticker` and its env
  vars become only the seed for the defaults. `docker-compose.yml` and the README
  are updated to say so.
- Invalid saved values are rejected by the API with a 400 and a message naming the
  field; a corrupt file on disk is logged and treated as defaults (not a crash).

## API (web/server.py)

- `GET /api/schedule` → `{"settings": {...}, "status": {"enabled", "phase",
  "phase_since", "next_phase", "next_change_at", "trading_day", "current_motion"}}`
  — times as ISO strings with offset.
- `PUT /api/schedule` → partial update of settings; validates, saves, re-evaluates,
  returns the same shape as GET.
- `_active_mode()` reports the schedule phase in its label when the scheduler is
  what's driving the lamp (e.g. "📅 Evening — <motion name>"), so the cockpit banner
  and the TUI agree. Existing modes keep their labels when the user took over.

## Web cockpit — Schedule tab

New tab `📅 Schedule` at `/schedule`, same shell and styling as the other tabs:

- Status card at top: current phase, since when, next change and when.
- Form with every setting from the table. `evening_motions`: an "All motions"
  toggle; when off, a checklist of motions (named ones first, by name; unnamed by id).
- Save button → `PUT /api/schedule`; show the server's validation error inline.

## TUI — Schedule screen

- Key `c` opens a Schedule screen (Textual `Screen` or modal) with the same fields
  as the web form; `evening_motions` here is an All / Selected toggle plus a
  scrollable checklist. Save / Cancel; validation errors shown as notifications,
  like other TUI errors.
- The main screen's active-mode banner already shows `_active_mode()`; add the
  "next change" text to it from `/api/schedule`.
- `cli/tui_api.py` gets `get_schedule()` / `put_schedule(changes)`.

## Testing

- Pure phase computation (`phase_at(now, settings, calendar)` → phase + next
  change) is the core and gets thorough table tests: trading day timeline, early
  close (gap until `evening_start`), holiday, weekend in both `non_trading_days`
  modes, `recap_minutes=0`, lights-out after midnight (e.g. 00:30 — the night
  phase spans midnight correctly), Friday evening into the weekend, DST change
  days, and rejected lights-out values.
- Calendar tests against published NYSE holiday dates for 2025–2027.
- Scheduler tests with a fake clock: edge-triggered (manual change survives
  until next transition), boot applies current phase, settings change mid-phase,
  ticker start retry, capture blocks then retries, evening rotation never repeats
  back-to-back.
- API tests for GET/PUT, validation errors, persistence round-trip.
- TUI screen test in the style of `tests/test_tui_app.py`.

## Settled during review *(Claude's calls)*

- A manual takeover during `evening` also stops the rotation until the next phase
  change (otherwise the next rotation tick would override the user).
- A `symbol`/`interval` change mid-`market` restarts the ticker only if the
  scheduler's own ticker is the one running.
- Unsaved capture frames block applying a phase, same as a running capture.
- Where holiday rules are ambiguous, NYSE's published 2025–2027 dates win; they
  are the test oracle.

## Left to the builder

- Scheduler internals (single asyncio task with a sleep-until-next-event loop is
  the expected shape), module layout beyond `web/market_calendar.py`, and exact
  form markup / Textual widgets.
- How the recap fetches close vs. previous close (yfinance `fast_info` or a 2-day
  history are both fine).
