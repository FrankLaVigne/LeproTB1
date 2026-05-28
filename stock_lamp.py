#!/usr/bin/env python3
"""Track a stock ticker and color the lamp green on uptick / red on downtick."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

import yfinance as yf

from lepro import LeproClient, load_config


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


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def run(symbol: str, interval: float, client, fetch_fn=fetch_price) -> None:
    """Poll `symbol` every `interval` seconds; color the lamp on each tick."""
    prev: float | None = None
    while True:
        now = await asyncio.to_thread(fetch_fn, symbol)
        if now is None:
            print(f"{_ts()}  {symbol}  warn: fetch failed")
        else:
            color = decide_color(prev, now)
            if prev is None:
                print(f"{_ts()}  {symbol}  ${now:.2f}  (first sample, baseline set)")
            elif color is None:
                print(f"{_ts()}  {symbol}  ${now:.2f}  · (no change)")
            elif color == (0, 255, 0):
                print(f"{_ts()}  {symbol}  ${now:.2f}  ↑ GREEN")
            else:
                print(f"{_ts()}  {symbol}  ${now:.2f}  ↓ RED")

            if color is not None:
                try:
                    await client.set_color(*color)
                except Exception as e:  # noqa: BLE001  — log and retry next tick
                    print(f"warn: lamp publish failed: {e}")
            prev = now
        await asyncio.sleep(interval)


def _interval(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"interval must be an integer, got {value!r}")
    if n < 5:
        raise argparse.ArgumentTypeError(f"interval must be >= 5 (got {n})")
    return n


async def _run_main(symbol: str, interval: int) -> int:
    cfg = load_config()
    if not cfg["account"] or not cfg["password"]:
        print("Missing credentials. Create config.json or set LEPRO_ACCOUNT / LEPRO_PASSWORD.",
              file=sys.stderr)
        return 2

    # First sample up front so a bad symbol exits cleanly before we open MQTT.
    first = await asyncio.to_thread(fetch_price, symbol)
    if first is None:
        print(f"error: could not fetch price for {symbol!r} on first try", file=sys.stderr)
        return 1

    client = LeproClient(cfg["account"], cfg["password"], cfg["region"])
    await client.login()
    await client.connect_mqtt()
    try:
        await run(symbol, interval, client)
    finally:
        await client.close()
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Color the lamp green on uptick / red on downtick.")
    p.add_argument("symbol", help="Yahoo ticker, e.g. IBM, 7203.T, BBVA.MC")
    p.add_argument("--interval", type=_interval, default=30,
                   help="seconds between polls (minimum 5; default 30)")
    args = p.parse_args()
    try:
        sys.exit(asyncio.run(_run_main(args.symbol, args.interval)))
    except KeyboardInterrupt:
        print()  # newline after the ^C
        sys.exit(0)


if __name__ == "__main__":
    main()
