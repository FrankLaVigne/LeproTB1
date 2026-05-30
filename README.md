# LeproTB1

Programmatic control for the **Lepro TB1 AI Smart Table Lamp** (and other Lepro
Wi-Fi lights) — from a CLI or a small self-hosted web page.

## Why this exists

Lepro's Wi-Fi lights are **not** Tuya devices. They speak Lepro's own cloud
protocol, so the usual local tools (`tinytuya`, Tuya-based Home Assistant
integrations, ESPHome reflashing) don't apply. There's also no official local
API or web UI — only the phone app and Alexa/Google.

This project talks to **Lepro's cloud** the same way the app does: a REST login
to fetch a token + per-account MQTT certificates, then **MQTT over TLS** to send
control commands and read state. Control routes through Lepro's servers
(internet required), but everything here runs on your own machine — no phone app
or Home Assistant needed.

The protocol was reverse-engineered with help from the excellent
[`Sanji78/lepro_led`](https://github.com/Sanji78/lepro_led) Home Assistant
integration.

> **If you don't provide an API, we will figure it out on our own.**
>
> ![Can't stop the signal, Mal — Mr. Universe, Serenity (2005)](assets/cant-stop-the-signal.gif)

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.json.example config.json   # then edit with your Lepro account
```

`config.json` (git-ignored) or the `LEPRO_ACCOUNT` / `LEPRO_PASSWORD` /
`LEPRO_REGION` environment variables provide credentials. Regions: `na`, `eu`, `fe`.

## Project layout

```
lepro/        package — the cloud client (LeproClient) + utilities
cli/          terminal scripts: main, stock_lamp, play_preset
web/          aiohttp UI: server (cockpit), ticker, clock, legacy, static/
mcphost/      FastMCP host (named mcphost/ so it doesn't shadow PyPI's mcp)
presets/      preset library (data)
docs/         protocol notes, calibration, plans & specs
tests/        pytest suite
```

Every runnable script is a Python module: launch with `python -m <pkg>.<mod>`.

## CLI

```bash
.venv/bin/python -m cli.main discover          # list devices + their ids
.venv/bin/python -m cli.main state             # dump live state of the first device
.venv/bin/python -m cli.main on
.venv/bin/python -m cli.main off
.venv/bin/python -m cli.main bright 40         # 40% brightness
.venv/bin/python -m cli.main color 255 0 120   # RGB
.venv/bin/python -m cli.main white 3000 60     # 3000K @ 60%
.venv/bin/python -m cli.main raw '{"d1":1,"d2":1,"d5":"00F003E803E8"}'
```

Add `--did <id>` to target a specific light (default: the first one discovered).

## Web front end

```bash
.venv/bin/python -m web.server        # serves on 0.0.0.0:8081
```

Open `http://<your-vm-ip>:8081`. Five tabs: Presets / DIY / Ticker / State /
Clock. Override the bind address with `LEPRO_WORKSHOP_HOST` /
`LEPRO_WORKSHOP_PORT`.

The legacy single-page demo from earlier in the project lives at
`web/legacy.py` (`python -m web.legacy`) — preserved for reference; not
recommended for daily use.

## MCP server

Expose the lamp to AI agents (OpenClaw, Claude Desktop/Code, any MCP client) over
the network:

```bash
# add "mcp_token": "<random>" to config.json, then:
.venv/bin/python -m mcphost.server        # streamable-HTTP on 0.0.0.0:8765
```

Clients connect to `http://<vm-ip>:8765/mcp` with header
`Authorization: Bearer <mcp_token>`. Tools: `list_lights`, `list_effects`,
`set_power`, `set_brightness`, `set_color`, `set_white`, `set_effect`,
`set_segments`, `play_animation`, `stop_animation`, `get_state`, `send_raw`.

Override bind with `LEPRO_MCP_HOST` / `LEPRO_MCP_PORT`. Without a token the server
refuses to bind a non-loopback address.

**Recommended:** run the server under a *dedicated second Lepro account* shared
into the lamp's Home, so it doesn't fight your phone for the single session.

### Effects on the TB1
Effect/segment payloads were reverse-engineered from Lepro bulbs/strips. To
confirm what the TB1 actually uses, run `python -m cli.main capture`, trigger each effect in
the app, and adjust the catalog to the logged `d50`/`d60` values. Segment groups
are currently capped at 9 until the d50 count-field width is verified this way.

## Stock tracker

Color the lamp green on every uptick and red on every downtick of a single
stock, polled live:

```bash
.venv/bin/python -m cli.stock_lamp IBM
.venv/bin/python -m cli.stock_lamp 7203.T --interval 10   # Toyota on Tokyo
.venv/bin/python -m cli.stock_lamp BBVA.MC --interval 60  # BBVA on Madrid
```

The ticker uses Yahoo Finance's suffix convention (no suffix = US listings;
`.T` = Tokyo; `.MC` = Madrid; etc.). `--interval` is in seconds, minimum 5,
default 30. Ctrl-C to stop.

First sample establishes the baseline (no color change). After that, each
poll is compared to the previous poll:

- price went up → lamp **pulses green** (breath animation)
- price went down → lamp **pulses red** (breath animation)
- price unchanged → on the *first* flat poll after a tick, the lamp calms
  down to a **solid color** (whichever direction it was last pulsing);
  subsequent flat polls publish nothing
- repeated same-direction ticks are deduplicated, so the pulse doesn't
  visibly restart on each one
- fetch failed → lamp goes **solid yellow** ("I don't know"); when the next
  poll succeeds, the lamp recovers automatically based on the price change
  since the last successful poll

In short: pulse means "something is moving"; solid green/red means "calm";
yellow means "I can't see the price right now"; the color tells you the
most recent direction (or the failure state).

## Preset workshop

A web UI for browsing the captured preset library, recoloring a chosen preset
via a color-combo picker, naming the variant, previewing it on the lamp, and
saving the result as a new `presets/*.json`.

```bash
.venv/bin/python -m web.server        # serves on 0.0.0.0:8081
```

Open `http://<vm-ip>:8081` in a browser. Left column lists every preset with a
palette preview; click one to load it as the base. Right column has a Variant
name input, a Color Combo (N round swatches matching the base's distinct
palette colors — click each to pick a new hex), and disabled Speed / Brightness
sliders (decode pending — see `docs/D50_FORMAT.md`). Preview pushes the recolored
animation to the lamp live; Save writes a new file under `presets/`.

LAN-only — no auth. Override the bind address with `LEPRO_WORKSHOP_HOST` /
`LEPRO_WORKSHOP_PORT`. Coexists with the MCP server (8765).

The workshop now also includes a **DIY editor** at `http://<vm-ip>:8081/diy`,
mimicking the Lepro app's DIY screen — a clickable 3-ring SVG canvas (48 app-
matched segments or 196 per-LED resolution via toggle), Draw/Fill/Erase/Back
tools, color picker with quick-pick swatches, the six confirmed motion effects
(Steady/Breathe/Gradient/Leftward/Rightward/Circle), speed and brightness
sliders, and Save (which writes a single-frame preset into `presets/`). Every
stroke updates the lamp live via the cloud, with client-side 100 ms throttling
to coalesce drag movements.

A **Stock Ticker** page is available at `http://<vm-ip>:8081/ticker` — assign up
to three Yahoo Finance symbols (one per concentric ring), pick a poll interval
(10s / 30s / 60s / 5m), and Start. Each ring shows its symbol's most recent
direction as a solid color (green ↑, red ↓, yellow on fetch failure, white
baseline, off if no symbol), and every tick triggers a 5-second whole-lamp
breathe flash in the new color. Stop powers the lamp off. While the ticker is
running, the DIY paint endpoint and the workshop preview endpoint return HTTP
409 — power, brightness, and saves stay available. When any ring is in a
sustained directional move (3 consecutive same-direction ticks totalling ≥
0.5%), it earns a **⚡ FAST** badge in the page and the whole lamp switches from
Steady to Breathe (per-ring colors still visible) until the streak ends.

A **Clock** page is available at `http://<vm-ip>:8081/clock` — turns the lamp
into a three-handed analog clock with the outer ring showing seconds (88
LEDs), middle showing minutes (62), and inner showing hours (46). One bright
LED per ring marks the current position, drifting smoothly between marks as
the next-finer unit ticks. Per-ring colors are configurable from the page
(default: red seconds / green minutes / blue hours); the hour ring has a
12h / 24h toggle. Updates every second. Like the ticker, while the clock is
running the DIY paint and workshop preview endpoints return HTTP 409;
brightness and saves stay available. Stop leaves the last frame on the lamp
(use the power button to turn it off).

## Protocol notes

Control commands are an MQTT publish to `le/{deviceId}/prp/set` with payload
`{"id": <rand>, "t": <epoch>, "d": { ... }}`. State is reported on
`le/{deviceId}/prp/#`; query it with a publish to `le/{deviceId}/prp/get`.

The `d` dictionary fields:

| Field | Meaning |
|-------|---------|
| `d1`  | power (0/1) |
| `d2`  | mode: 0 = white/CCT, 1 = RGB, 2 = segmented/effect, 3 = special effect |
| `d3`  | brightness 0–1000 (white & B-series RGB modes) |
| `d4`  | color temperature 0–1000 (0 = 2700 K warm, 1000 = 6500 K cool) |
| `d5`  | RGB as HSV hex `HHHHSSSSVVVV` (hue 0–360, sat/val 0–1000) |
| `d52` | brightness 0–1000 (segmented/strip mode) |
| `d50` | segmented color + effect string |
| `d60` | special-effect + sensitivity string |

The **TB1** matches the `B1` model token, so this client treats it as a
B-series device (RGB via `d5`, white via `d3`/`d4`). If a command doesn't behave
as expected on your unit, run `python -m cli.main state` to see what fields it
actually reports, then use `python -m cli.main raw '{...}'` to experiment and
refine.

## Files

- `lepro/client.py` — async cloud client (`LeproClient`): login, discovery, MQTT, commands.
- `lepro/__init__.py` — re-exports `LeproClient`, `Device`, `AnimationPlayer`, etc.
- `lepro/client_key.pem` — the static MQTT client private key shipped publicly with the
  app (per-account certs are downloaded at login into `certs/`).
- `cli/main.py` — command-line interface (`python -m cli.main`).
- `cli/stock_lamp.py` — standalone CLI version of the stock ticker.
- `cli/play_preset.py` — replay a captured preset on the lamp.
- `web/server.py` — the cockpit web UI: Presets / DIY / Ticker / State / Clock at `:8081`.
- `web/ticker.py` — stock-ticker `TickerSession` (used by `web/server.py`).
- `web/clock.py` — clock-on-rings `ClockSession` (used by `web/server.py`).
- `web/legacy.py` — older single-page demo (`python -m web.legacy`), kept for reference.
- `web/static/lamp-utils.js` — shared d50 parser + page→physical rotation helpers.
- `mcphost/server.py` — networked FastMCP server with 12 lamp tools.

## Documentation (`docs/`)

- [`D50_FORMAT.md`](docs/D50_FORMAT.md) — the fully decoded per-LED `d50` protocol.
- [`REVERSE_ENGINEERING.md`](docs/REVERSE_ENGINEERING.md) — methodology playbook.
- [`CALIBRATION.md`](docs/CALIBRATION.md) — page→physical ring rotation offsets.
- [`article.md`](docs/article.md) — blog draft on the project's decoding journey.
- [`reverse-engineering-just-got-cheap.md`](docs/reverse-engineering-just-got-cheap.md) — companion essay on AI-assisted RE.
- [`superpowers/`](docs/superpowers/) — specs and implementation plans per feature.

## Disclaimer

Unofficial and not affiliated with Lepro. Uses an undocumented cloud API that
may change at any time. Use at your own risk.
