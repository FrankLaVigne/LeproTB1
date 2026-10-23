"""NYSE trading days and hours — rule-based, no external calendar dependency.

Covers the regular holiday set and early closes. One-off closures (national days
of mourning, weather) can't be derived from rules and are not modeled.
Design: docs/superpowers/specs/2026-10-23-daily-schedule-design.md
"""

from __future__ import annotations

from datetime import date, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OPEN = time(9, 30)
CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th (1-based) weekday (Mon=0) of a month; n=-1 means the last one."""
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Western Easter Sunday (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed(d: date) -> date:
    """Saturday holidays move to Friday, Sunday holidays to Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=None)
def nyse_holidays(year: int) -> frozenset[date]:
    days = set()
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:  # NYSE skips the Friday when Jan 1 is a Saturday
        days.add(_observed(new_year))
    days.add(_nth_weekday(year, 1, 0, 3))    # MLK Day
    days.add(_nth_weekday(year, 2, 0, 3))    # Presidents' Day
    days.add(_easter(year) - timedelta(days=2))  # Good Friday
    days.add(_nth_weekday(year, 5, 0, -1))   # Memorial Day
    days.add(_observed(date(year, 6, 19)))   # Juneteenth
    days.add(_observed(date(year, 7, 4)))    # Independence Day
    days.add(_nth_weekday(year, 9, 0, 1))    # Labor Day
    days.add(_nth_weekday(year, 11, 3, 4))   # Thanksgiving
    days.add(_observed(date(year, 12, 25)))  # Christmas
    return frozenset(d for d in days if d.year == year)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def close_time(d: date) -> time:
    """Regular-session close for a trading day (13:00 on early-close days)."""
    early = {date(d.year, 7, 3), date(d.year, 12, 24),
             _nth_weekday(d.year, 11, 3, 4) + timedelta(days=1)}
    return EARLY_CLOSE if d in early and is_trading_day(d) else CLOSE
