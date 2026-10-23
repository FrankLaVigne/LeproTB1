"""Daily schedule — settings, persistence, and the pure phase timeline.

No server imports: web/scheduler.py runs it, web/server.py wires it up.
Design: docs/superpowers/specs/2026-10-23-daily-schedule-design.md
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Mapping

from web import market_calendar as mc

_LOG = logging.getLogger("schedule")

PHASES = ("opening_bell", "market", "closing_bell", "recap", "idle_off", "evening", "night")

DEFAULTS: dict = {
    "enabled": True,
    "symbol": "IBM",
    "interval": 30,
    "recap_minutes": 15,
    "opening_motion": "random",
    "closing_motion": "random",
    "bell_seconds": 20,
    "evening_motions": "all",
    "evening_rotate_minutes": 10,
    "lights_out": "22:30",
    "non_trading_days": "evening",
    "evening_start": "16:00",
}

_INTERVALS = (10, 30, 60, 300)
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _parse_hhmm(field: str, value) -> time:
    m = _HHMM.match(value) if isinstance(value, str) else None
    if not m:
        raise ValueError(f"{field}: must be HH:MM (24-hour), got {value!r}")
    return time(int(m.group(1)), int(m.group(2)))


def _int_in(field: str, value, lo: int, hi: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise ValueError(f"{field}: must be a whole number {lo}..{hi}, got {value!r}")
    return value


def _secs(t: time) -> int:
    return t.hour * 3600 + t.minute * 60


def validate(settings: dict) -> dict:
    """Return a normalized full copy of ``settings``; raise ValueError("<field>: ...")."""
    for key in settings:
        if key not in DEFAULTS:
            raise ValueError(f"{key}: unknown setting")
    s = {**DEFAULTS, **settings}
    out: dict = {}
    if not isinstance(s["enabled"], bool):
        raise ValueError(f"enabled: must be true or false, got {s['enabled']!r}")
    out["enabled"] = s["enabled"]
    sym = s["symbol"].strip().upper() if isinstance(s["symbol"], str) else ""
    if not sym:
        raise ValueError("symbol: must be a ticker symbol like IBM")
    out["symbol"] = sym
    if s["interval"] not in _INTERVALS or isinstance(s["interval"], bool):
        raise ValueError(f"interval: must be one of {', '.join(map(str, _INTERVALS))} seconds")
    out["interval"] = s["interval"]
    out["recap_minutes"] = _int_in("recap_minutes", s["recap_minutes"], 0, 360)
    out["bell_seconds"] = _int_in("bell_seconds", s["bell_seconds"], 0, 600)
    out["evening_rotate_minutes"] = _int_in("evening_rotate_minutes",
                                            s["evening_rotate_minutes"], 1, 1440)
    for field in ("opening_motion", "closing_motion"):
        v = s[field]
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{field}: must be a motion id or \"random\"")
        out[field] = v.strip()
    em = s["evening_motions"]
    if em != "all" and not (isinstance(em, list) and all(isinstance(x, str) and x for x in em)):
        raise ValueError("evening_motions: must be \"all\" or a list of motion ids")
    out["evening_motions"] = em if em == "all" else list(em)
    if s["non_trading_days"] not in ("off", "evening"):
        raise ValueError("non_trading_days: must be \"off\" or \"evening\"")
    out["non_trading_days"] = s["non_trading_days"]
    evening_start = _parse_hhmm("evening_start", s["evening_start"])
    lights_out = _parse_hhmm("lights_out", s["lights_out"])
    out["evening_start"] = s["evening_start"]
    out["lights_out"] = s["lights_out"]
    # Lights-out between the open and the latest evening start would leave no night.
    normal_start = _secs(mc.CLOSE) + out["bell_seconds"] + out["recap_minutes"] * 60
    latest_start = max(normal_start, _secs(evening_start))
    if _secs(mc.OPEN) <= _secs(lights_out) < latest_start:
        h, rem = divmod(min(latest_start, 86399), 3600)
        raise ValueError(f"lights_out: must be after the evening starts "
                         f"({h:02d}:{rem // 60:02d}) or before 09:30")
    return out


def merge(current: dict, changes: dict) -> dict:
    return validate({**current, **changes})


def load_settings(path: Path, env: Mapping) -> dict:
    """Saved settings, or defaults seeded from LEPRO_TICKER_AUTOSTART/_INTERVAL."""
    path = Path(path)
    if path.exists():
        try:
            return validate(json.loads(path.read_text()))
        except (ValueError, TypeError, AttributeError) as e:
            _LOG.warning("ignoring invalid %s (%s); using defaults", path.name, e)
    seed = dict(DEFAULTS)
    sym = env.get("LEPRO_TICKER_AUTOSTART", "").strip().upper()
    if sym:
        seed["symbol"] = sym
    try:
        seed["interval"] = int(env.get("LEPRO_TICKER_INTERVAL", seed["interval"]))
        return validate(seed)
    except ValueError as e:
        _LOG.warning("ignoring LEPRO_TICKER_* seed (%s)", e)
        return dict(DEFAULTS)


def save_settings(path: Path, settings: dict) -> None:
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(validate(settings), indent=2) + "\n")
    os.replace(tmp, path)


@dataclass(frozen=True)
class PhaseInfo:
    phase: str
    since: datetime | None
    next_phase: str | None
    next_change_at: datetime | None
    trading_day: bool


def _at(day: date, t: time) -> datetime:
    return datetime.combine(day, t, tzinfo=mc.ET)


def _day_events(day: date, s: dict) -> list[tuple[datetime, str]]:
    events: list[tuple[datetime, str]] = []
    bell = timedelta(seconds=s["bell_seconds"])
    if mc.is_trading_day(day):
        opened = _at(day, mc.OPEN)
        if bell:
            events.append((opened, "opening_bell"))
        events.append((opened + bell, "market"))
        closed = _at(day, mc.close_time(day))
        if bell:
            events.append((closed, "closing_bell"))
        if s["recap_minutes"]:
            events.append((closed + bell, "recap"))
        start = closed + bell + timedelta(minutes=s["recap_minutes"])
        if mc.close_time(day) == mc.EARLY_CLOSE:
            events.append((start, "idle_off"))
            start = max(start, _at(day, _parse_hhmm("evening_start", s["evening_start"])))
    elif s["non_trading_days"] == "evening":
        start = _at(day, _parse_hhmm("evening_start", s["evening_start"]))
    else:
        return events
    events.append((start, "evening"))
    lights_out = _at(start.date(), _parse_hhmm("lights_out", s["lights_out"]))
    if lights_out < start:
        lights_out = _at(start.date() + timedelta(days=1), lights_out.timetz())
    events.append((lights_out, "night"))
    return events


def phase_at(now: datetime, s: dict) -> PhaseInfo:
    now = now.astimezone(mc.ET)
    today = now.date()
    events: list[tuple[datetime, str]] = []
    for offset in range(-3, 9):  # covers the longest market closure (4-day weekends)
        events.extend(_day_events(today + timedelta(days=offset), s))
    events.sort(key=lambda e: e[0])
    timeline: list[tuple[datetime, str]] = []
    for ev in events:
        if not timeline or timeline[-1][1] != ev[1]:
            timeline.append(ev)
    current, since, nxt = "night", None, None
    for when, ph in timeline:
        if when <= now:
            current, since = ph, when
        else:
            nxt = (when, ph)
            break
    return PhaseInfo(phase=current, since=since,
                     next_phase=nxt[1] if nxt else None,
                     next_change_at=nxt[0] if nxt else None,
                     trading_day=mc.is_trading_day(today))
