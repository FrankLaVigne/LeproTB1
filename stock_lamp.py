#!/usr/bin/env python3
"""Track a stock ticker and color the lamp green on uptick / red on downtick."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

import yfinance as yf

from lepro import LeproClient, load_config

# --- types & constants ---------------------------------------------------------

Command = tuple[str, tuple[int, int, int]]  # ("animate" | "solid", (r, g, b))

GREEN = (0, 255, 0)
RED = (255, 0, 0)
YELLOW = (255, 255, 0)  # solid yellow = fetch failure / "I don't know"


def decide_command(prev_price: float | None,
                   now: float | None,
                   last_command: Command | None) -> Command | None:
    """Return the next lamp Command, or None if nothing should be published.

    - `now is None` (fetch failed) -> solid yellow (deduped if already yellow)
    - `prev_price is None`         -> None (baseline, first successful sample)
    - `now > prev_price`           -> animate green (deduped if already)
    - `now < prev_price`           -> animate red   (deduped if already)
    - `now == prev_price`, last was animate -> solid in that color (calm down)
    - `now == prev_price`, last was solid or None -> None
    """
    if now is None:
        desired: Command = ("solid", YELLOW)
        return None if desired == last_command else desired
    if prev_price is None:
        return None
    if now > prev_price:
        desired = ("animate", GREEN)
    elif now < prev_price:
        desired = ("animate", RED)
    elif last_command is not None and last_command[0] == "animate":
        desired = ("solid", last_command[1])
    else:
        return None
    return None if desired == last_command else desired


async def apply_command(client, cmd: Command) -> None:
    """Dispatch a Command to the lamp: animate -> breath effect, solid -> set_color."""
    kind, color = cmd
    if kind == "animate":
        await client.set_effect("breath", speed=50, color=color)
    else:  # "solid"
        await client.set_color(*color)


def _status_line(prev: float | None,
                 now: float | None,
                 cmd: Command | None,
                 last_command: Command | None) -> str:
    """Render the human-readable suffix shown after the timestamp + symbol."""
    if now is None:
        return "warn: fetch failed" + (
            " (lamp → yellow)" if cmd is not None else " (already yellow)"
        )
    if prev is None:
        return f"${now:.2f}  (first sample, baseline set)"
    if cmd is not None:
        kind, color = cmd
        verb = "pulsing" if kind == "animate" else "holding"
        name = "green" if color == GREEN else ("red" if color == RED else "yellow")
        arrow = "↑" if color == GREEN else ("↓" if color == RED else "·")
        return f"${now:.2f}  {arrow} {verb} {name}"
    if now == prev:
        return f"${now:.2f}  · (no change)"
    # Same-direction tick deduped.
    arrow = "↑" if now > prev else "↓"
    name = "green" if last_command is not None and last_command[1] == GREEN else "red"
    return f"${now:.2f}  {arrow} (already pulsing {name})"


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
    """Poll `symbol` every `interval` seconds; drive the lamp via decide_command."""
    prev: float | None = None
    last_command: Command | None = None
    while True:
        now = await asyncio.to_thread(fetch_fn, symbol)
        cmd = decide_command(prev, now, last_command)

        suffix = _status_line(prev, now, cmd, last_command)
        print(f"{_ts()}  {symbol}  {suffix}")

        if cmd is not None:
            try:
                await apply_command(client, cmd)
                last_command = cmd
            except Exception as e:  # noqa: BLE001  — log and retry next tick
                print(f"warn: lamp publish failed: {e}")

        if now is not None:
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
