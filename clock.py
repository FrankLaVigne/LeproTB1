"""Lepro clock-on-rings session — pure helpers + ClockSession class.

Turns the lamp into a three-handed analog clock: outer = seconds (88 LEDs),
middle = minutes (62 LEDs), inner = hours (46 LEDs). Dot mode (one bright
LED per ring), per-ring configurable colors, 12h default with 24h toggle,
1-second cadence.
"""

from __future__ import annotations

from datetime import datetime


_VALID_MODES = ("12h", "24h")


def compute_positions(now: datetime, mode: str = "12h") -> dict:
    """Return per-ring page-space LED indices for the clock dots at ``now``.

    ``mode`` is "12h" (default) or "24h". Adds the fractional carry from
    the next-finer time unit so the minute and hour hands drift slowly
    between marks like an analog clock. Each position is `% ring_size`
    so an edge-case round-up at second 59.999... wraps to 0 cleanly.
    """
    if mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of {_VALID_MODES}; got {mode!r}")

    sec_frac = now.second + now.microsecond / 1_000_000
    outer = round(sec_frac * 88 / 60) % 88

    min_frac = now.minute + sec_frac / 60
    middle = round(min_frac * 62 / 60) % 62

    if mode == "12h":
        hour_unit = (now.hour % 12) + now.minute / 60
        hours_per_cycle = 12
    else:
        hour_unit = now.hour + now.minute / 60
        hours_per_cycle = 24
    inner = round(hour_unit * 46 / hours_per_cycle) % 46

    return {"outer": outer, "middle": middle, "inner": inner}


def build_clock_leds(positions: dict, colors: dict) -> list:
    """Return a 196-entry page-space LED array with one dot per ring.

    Positions are 0-indexed within each ring (outer 0..87, middle 0..61,
    inner 0..45). The caller is responsible for applying the lamp
    rotation and encoding via ``workshop.build_d50_from_leds`` before
    sending. Returns a fresh list (callers may mutate).
    """
    leds = [None] * 196
    leds[positions["outer"]] = colors["outer"]
    leds[88 + positions["middle"]] = colors["middle"]
    leds[150 + positions["inner"]] = colors["inner"]
    return leds
