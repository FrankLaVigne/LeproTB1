"""Tests for stock_lamp."""

import argparse
import asyncio
from itertools import chain, repeat

import pytest

import stock_lamp


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
    """LeproClient stand-in that records set_color calls."""

    def __init__(self):
        self.calls: list[tuple[int, int, int]] = []

    async def set_color(self, r: int, g: int, b: int, pct=None, did=None):
        self.calls.append((r, g, b))


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

    # First sample → no call. Up → green. Flat → no call. Down → red. Flat → no call.
    assert client.calls == [(0, 255, 0), (255, 0, 0)]


@pytest.mark.asyncio
async def test_run_tolerates_fetch_failure_and_continues():
    # First call fails (None), second call succeeds and becomes baseline, third is uptick.
    seq = chain([None, 100.0, 100.5], repeat(100.5))

    def fake_fetch(_):
        return next(seq)

    client = _FakeClient()
    task = asyncio.create_task(stock_lamp.run("X", 0.02, client, fake_fetch))
    await asyncio.sleep(0.3)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Failed first call → skipped. Baseline 100. Uptick to 100.5 → green. Flat → no call.
    assert client.calls == [(0, 255, 0)]


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
