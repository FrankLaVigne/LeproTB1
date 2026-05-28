#!/usr/bin/env python3
"""Track a stock ticker and color the lamp green on uptick / red on downtick."""

from __future__ import annotations

import yfinance as yf


def decide_color(prev: float | None, now: float) -> tuple[int, int, int] | None:
    """Return the color the lamp should display, or None if no change should be sent.

    - prev is None  -> None (first sample, just establish baseline)
    - now > prev    -> (0, 255, 0)  green
    - now < prev    -> (255, 0, 0)  red
    - now == prev   -> None (no publish)
    """
    if prev is None or now == prev:
        return None
    return (0, 255, 0) if now > prev else (255, 0, 0)


def fetch_price(symbol: str) -> float | None:
    """Return the latest known price for `symbol`, or None on any error.

    Synchronous; the async loop wraps this in `asyncio.to_thread`.
    """
    try:
        price = yf.Ticker(symbol).fast_info["last_price"]
        return float(price) if price is not None else None
    except Exception:  # noqa: BLE001  — any yfinance / network error -> None
        return None
