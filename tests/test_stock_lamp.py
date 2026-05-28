"""Tests for stock_lamp."""

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
