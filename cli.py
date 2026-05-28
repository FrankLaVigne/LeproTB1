#!/usr/bin/env python3
"""Command-line control for Lepro lights.

Examples:
  python cli.py discover
  python cli.py state            # dump live reported fields for the first device
  python cli.py on
  python cli.py off
  python cli.py bright 40        # 40% brightness
  python cli.py color 255 0 120  # RGB
  python cli.py white 3000 60    # 3000K @ 60%
  python cli.py raw '{"d1":1,"d2":1,"d5":"00F003E803E8"}'
  # target a specific device with --did <id> (default: first device)
"""

import argparse
import asyncio
import json
import sys

from lepro import LeproClient, LeproError, load_config


async def main() -> int:
    p = argparse.ArgumentParser(description="Control Lepro lights from the CLI.")
    p.add_argument("--did", help="target device id (default: first discovered)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover", help="list devices on the account")
    sp_state = sub.add_parser("state", help="request and print live device state")
    sp_state.add_argument("--wait", type=float, default=2.0, help="seconds to collect replies")
    sub.add_parser("on")
    sub.add_parser("off")
    sp_b = sub.add_parser("bright"); sp_b.add_argument("pct", type=int)
    sp_c = sub.add_parser("color")
    sp_c.add_argument("r", type=int); sp_c.add_argument("g", type=int); sp_c.add_argument("b", type=int)
    sp_c.add_argument("--pct", type=int, default=None)
    sp_w = sub.add_parser("white")
    sp_w.add_argument("kelvin", type=int); sp_w.add_argument("pct", type=int, nargs="?", default=None)
    sp_r = sub.add_parser("raw"); sp_r.add_argument("json")
    args = p.parse_args()

    cfg = load_config()
    if not cfg["account"] or not cfg["password"]:
        print("Missing credentials. Create config.json or set LEPRO_ACCOUNT / LEPRO_PASSWORD.",
              file=sys.stderr)
        return 2

    client = LeproClient(cfg["account"], cfg["password"], cfg["region"])
    try:
        await client.login()
        if args.cmd == "discover":
            for d in client.devices:
                kind = "B-series" if d.is_b_series else "segmented/strip"
                print(f"{d.did}  {d.name!r}  series={d.series!r}  [{kind}]")
            return 0

        await client.connect_mqtt()

        if args.cmd == "state":
            await client.request_state(args.did)
            try:
                await asyncio.wait_for(client.listen(), timeout=args.wait)
            except asyncio.TimeoutError:
                pass
            did = (args.did or (client.devices[0].did if client.devices else None))
            print(json.dumps(client.state.get(str(did), {}), indent=2))
            return 0

        if args.cmd == "on":
            await client.power(True, args.did)
        elif args.cmd == "off":
            await client.power(False, args.did)
        elif args.cmd == "bright":
            await client.set_brightness(args.pct, args.did)
        elif args.cmd == "color":
            await client.set_color(args.r, args.g, args.b, args.pct, args.did)
        elif args.cmd == "white":
            await client.set_white(args.kelvin, args.pct, args.did)
        elif args.cmd == "raw":
            await client.send_raw(json.loads(args.json), args.did)
        # give the publish a moment to flush before disconnecting
        await asyncio.sleep(0.5)
        print("ok")
        return 0
    except LeproError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
