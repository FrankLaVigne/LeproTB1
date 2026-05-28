"""Tests for stock_lamp."""

import argparse
import asyncio
from itertools import chain, repeat

import pytest

import stock_lamp


def test_decide_color_first_sample_returns_none():
    assert stock_lamp.decide_color(None, 100.0) is None


def test_decide_color_uptick_returns_green():
    assert stock_lamp.decide_color(100.0, 100.5) == (0, 255, 0)


def test_decide_color_downtick_returns_red():
    assert stock_lamp.decide_color(100.0, 99.9) == (255, 0, 0)


def test_decide_color_flat_returns_none():
    assert stock_lamp.decide_color(100.0, 100.0) is None


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
