#!/usr/bin/env python3
"""Replay a captured preset on the lamp.

Presets in `presets/<name>.json` may be either:
  - single-frame:  {"payload": {"d1":1, "d2":..., "d50":...}}
  - multi-frame:   {"frame_duration_ms": <ms>, "frames": [{"d2":..., "d50":...}, ...]}

The multi-frame form reprograms the lamp with each frame's `d50` (which the
firmware then animates internally) and sleeps `frame_duration_ms` between frames.
Loops forever unless --once is given.

Usage:
  python play_preset.py <name>            # loop the preset
  python play_preset.py <name> --once     # play one pass
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from lepro import LeproClient, load_config

_HERE = os.path.dirname(os.path.abspath(__file__))


def load_preset(name: str) -> dict:
    path = os.path.join(_HERE, "presets", f"{name}.json")
    with open(path) as f:
        return json.load(f)


async def play(name: str, once: bool) -> None:
    preset = load_preset(name)
    cfg = load_config()
    client = LeproClient(cfg["account"], cfg["password"], cfg["region"])
    await client.login()
    await client.connect_mqtt()
    try:
        if "frames" in preset:
            frames = preset["frames"]
            dur = preset.get("frame_duration_ms", 2500) / 1000
            print(f"playing {len(frames)} frames @ {dur:.2f}s each ({'once' if once else 'looping'}); Ctrl-C to stop")
            while True:
                for i, frame in enumerate(frames, 1):
                    d = {"d1": 1, **{k: v for k, v in frame.items() if k != "duration_ms"}}
                    await client.send_raw(d)
                    print(f"  frame {i}/{len(frames)}")
                    await asyncio.sleep(dur)
                if once:
                    break
        else:
            d = preset["payload"]
            if "d1" not in d:
                d = {"d1": 1, **d}
            await client.send_raw(d)
            print(f"sent single-frame preset {name!r}")
            await asyncio.sleep(0.5)
    finally:
        await client.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("name")
    p.add_argument("--once", action="store_true", help="play one pass instead of looping")
    args = p.parse_args()
    try:
        asyncio.run(play(args.name, args.once))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
