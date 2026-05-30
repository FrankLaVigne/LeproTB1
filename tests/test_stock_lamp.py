"""Tests for stock_lamp."""

import argparse
import asyncio
from itertools import chain, repeat

import pytest

from cli import stock_lamp


# --- decide_command tests ------------------------------------------------------

G = (0, 255, 0)
R = (255, 0, 0)
Y = (255, 255, 0)
ANIMATE_G = ("animate", G)
ANIMATE_R = ("animate", R)
SOLID_G = ("solid", G)
SOLID_R = ("solid", R)
SOLID_Y = ("solid", Y)


def test_decide_command_baseline_returns_none():
    assert stock_lamp.decide_command(None, 100.0, None) is None


def test_decide_command_uptick_from_cold_animates_green():
    assert stock_lamp.decide_command(100.0, 100.5, None) == ANIMATE_G


def test_decide_command_uptick_dedup_when_already_animating_green():
    assert stock_lamp.decide_command(100.0, 100.5, ANIMATE_G) is None


def test_decide_command_downtick_from_cold_animates_red():
    assert stock_lamp.decide_command(100.0, 99.5, None) == ANIMATE_R


def test_decide_command_downtick_dedup_when_already_animating_red():
    assert stock_lamp.decide_command(100.0, 99.5, ANIMATE_R) is None


def test_decide_command_downtick_after_animate_green_animates_red():
    assert stock_lamp.decide_command(100.0, 99.5, ANIMATE_G) == ANIMATE_R


def test_decide_command_flat_after_animate_green_calms_to_solid_green():
    assert stock_lamp.decide_command(100.0, 100.0, ANIMATE_G) == SOLID_G


def test_decide_command_flat_after_animate_red_calms_to_solid_red():
    assert stock_lamp.decide_command(100.0, 100.0, ANIMATE_R) == SOLID_R


def test_decide_command_flat_after_solid_returns_none():
    assert stock_lamp.decide_command(100.0, 100.0, SOLID_G) is None


def test_decide_command_flat_from_cold_returns_none():
    assert stock_lamp.decide_command(100.0, 100.0, None) is None


def test_decide_command_uptick_after_solid_red_animates_green():
    assert stock_lamp.decide_command(100.0, 100.5, SOLID_R) == ANIMATE_G


def test_decide_command_fetch_failure_from_cold_goes_yellow():
    assert stock_lamp.decide_command(None, None, None) == SOLID_Y


def test_decide_command_fetch_failure_from_running_goes_yellow():
    assert stock_lamp.decide_command(100.0, None, ANIMATE_G) == SOLID_Y


def test_decide_command_fetch_failure_dedup_when_already_yellow():
    assert stock_lamp.decide_command(100.0, None, SOLID_Y) is None


def test_decide_command_recovery_with_uptick_after_yellow():
    # prev_price was 100.0, price moved during outage, first recovered poll is 100.5
    assert stock_lamp.decide_command(100.0, 100.5, SOLID_Y) == ANIMATE_G


def test_decide_command_recovery_flat_after_yellow_stays_silent():
    # prev was 100.0, came back at exactly 100.0 → no real tick, lamp stays yellow
    assert stock_lamp.decide_command(100.0, 100.0, SOLID_Y) is None


class _FakeClient:
    """LeproClient stand-in that records both set_color and set_effect calls."""

    def __init__(self):
        # Each entry is ("solid", (r, g, b)) or ("animate", (r, g, b)).
        self.calls: list[tuple[str, tuple[int, int, int]]] = []

    async def set_color(self, r: int, g: int, b: int, pct=None, did=None):
        self.calls.append(("solid", (r, g, b)))

    async def set_effect(self, name: str, speed: int = 50,
                         color: tuple[int, int, int] = (255, 255, 255),
                         pct=None, did=None):
        self.calls.append(("animate", color))


@pytest.mark.asyncio
async def test_run_publishes_green_on_uptick_red_on_downtick():
    # Sequence: baseline 100, up to 100.5, flat, down to 99, flat. After that, repeat 99.
    prices = chain([100.0, 100.5, 100.5, 99.0, 99.0], repeat(99.0))

    def fake_fetch(_symbol):
        return next(prices)

    client = _FakeClient()
    task = asyncio.create_task(stock_lamp.run("X", 0.02, client, fake_fetch))
    await asyncio.sleep(0.4)  # ~20 cycles at 0.02s; plenty for 5 prices.
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Baseline 100, up→animate G, flat→solid G, down→animate R, flat→solid R.
    # After that prices stay at 99.0 → no further calls.
    G = (0, 255, 0)
    R = (255, 0, 0)
    assert client.calls == [("animate", G), ("solid", G), ("animate", R), ("solid", R)]


@pytest.mark.asyncio
async def test_run_publishes_yellow_then_recovers():
    # First call fails -> yellow. Second call 100 -> baseline (last_command stays yellow).
    # Third call 100.5 -> animate green. Subsequent flat 100.5 -> solid green.
    seq = chain([None, 100.0, 100.5, 100.5, 100.5], repeat(100.5))

    def fake_fetch(_):
        return next(seq)

    client = _FakeClient()
    task = asyncio.create_task(stock_lamp.run("X", 0.02, client, fake_fetch))
    await asyncio.sleep(0.5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    G = (0, 255, 0)
    Y = (255, 255, 0)
    # First call -> yellow. Baseline poll (100.0) does NOT publish (prev was None,
    # we still update prev to 100.0 — see run logic). Uptick to 100.5 -> animate G.
    # Then flat -> solid G. Stop here; further flats publish nothing.
    assert client.calls == [("solid", Y), ("animate", G), ("solid", G)]


def test_interval_accepts_floor():
    assert stock_lamp._interval("5") == 5


def test_interval_accepts_larger():
    assert stock_lamp._interval("30") == 30


def test_interval_rejects_below_floor():
    with pytest.raises(argparse.ArgumentTypeError):
        stock_lamp._interval("4")


def test_interval_rejects_non_integer():
    with pytest.raises(argparse.ArgumentTypeError):
        stock_lamp._interval("nope")


# --- _status_line tests --------------------------------------------------------


def test_status_line_baseline():
    assert stock_lamp._status_line(None, 100.0, None, None) == "$100.00  (first sample, baseline set)"


def test_status_line_uptick_publishing():
    assert stock_lamp._status_line(100.0, 100.5, ANIMATE_G, None) == "$100.50  ↑ pulsing green"


def test_status_line_uptick_deduped():
    assert stock_lamp._status_line(100.0, 100.5, None, ANIMATE_G) == "$100.50  ↑ (already pulsing green)"


def test_status_line_flat_no_change():
    assert stock_lamp._status_line(100.0, 100.0, None, SOLID_G) == "$100.00  · (no change)"


def test_status_line_fetch_failure_publishing_yellow():
    assert stock_lamp._status_line(100.0, None, SOLID_Y, ANIMATE_G) == "warn: fetch failed (lamp → yellow)"


def test_status_line_fetch_failure_already_yellow():
    assert stock_lamp._status_line(100.0, None, None, SOLID_Y) == "warn: fetch failed (already yellow)"


def test_status_line_solid_green_calmdown():
    assert stock_lamp._status_line(100.0, 100.0, SOLID_G, ANIMATE_G) == "$100.00  · holding green"


def test_status_line_solid_red_calmdown():
    assert stock_lamp._status_line(100.0, 100.0, SOLID_R, ANIMATE_R) == "$100.00  · holding red"
