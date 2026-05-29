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

## CLI

```bash
.venv/bin/python cli.py discover          # list devices + their ids
.venv/bin/python cli.py state             # dump live state of the first device
.venv/bin/python cli.py on
.venv/bin/python cli.py off
.venv/bin/python cli.py bright 40         # 40% brightness
.venv/bin/python cli.py color 255 0 120   # RGB
.venv/bin/python cli.py white 3000 60     # 3000K @ 60%
.venv/bin/python cli.py raw '{"d1":1,"d2":1,"d5":"00F003E803E8"}'
```

Add `--did <id>` to target a specific light (default: the first one discovered).

## Web front end

```bash
.venv/bin/python app.py        # serves on 0.0.0.0:8080
```

Open `http://<your-vm-ip>:8080`. It holds one persistent login + MQTT connection
and gives you device selection, on/off, a brightness slider, a color picker,
and a white-temperature slider. Override the bind address with `LEPRO_HOST` /
`LEPRO_PORT`.

## MCP server

Expose the lamp to AI agents (OpenClaw, Claude Desktop/Code, any MCP client) over
the network:

```bash
# add "mcp_token": "<random>" to config.json, then:
.venv/bin/python mcp_server.py        # streamable-HTTP on 0.0.0.0:8765
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
confirm what the TB1 actually uses, run `cli.py capture`, trigger each effect in
the app, and adjust the catalog to the logged `d50`/`d60` values. Segment groups
are currently capped at 9 until the d50 count-field width is verified this way.

## Stock tracker

Color the lamp green on every uptick and red on every downtick of a single
stock, polled live:

```bash
.venv/bin/python stock_lamp.py IBM
.venv/bin/python stock_lamp.py 7203.T --interval 10   # Toyota on Tokyo
.venv/bin/python stock_lamp.py BBVA.MC --interval 60  # BBVA on Madrid
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
.venv/bin/python workshop.py        # serves on 0.0.0.0:8081
```

Open `http://<vm-ip>:8081` in a browser. Left column lists every preset with a
palette preview; click one to load it as the base. Right column has a Variant
name input, a Color Combo (N round swatches matching the base's distinct
palette colors — click each to pick a new hex), and disabled Speed / Brightness
sliders (decode pending — see `D50_FORMAT.md`). Preview pushes the recolored
animation to the lamp live; Save writes a new file under `presets/`.

LAN-only — no auth. Override the bind address with `LEPRO_WORKSHOP_HOST` /
`LEPRO_WORKSHOP_PORT`. Coexists with `app.py` (8080) and the MCP server (8765).

The workshop now also includes a **DIY editor** at `http://<vm-ip>:8081/diy`,
mimicking the Lepro app's DIY screen — a clickable 3-ring SVG canvas (48 app-
matched segments or 196 per-LED resolution via toggle), Draw/Fill/Erase/Back
tools, color picker with quick-pick swatches, the six confirmed motion effects
(Steady/Breathe/Gradient/Leftward/Rightward/Circle), speed and brightness
sliders, and Save (which writes a single-frame preset into `presets/`). Every
stroke updates the lamp live via the cloud, with client-side 100 ms throttling
to coalesce drag movements.

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
as expected on your unit, run `cli.py state` to see what fields it actually
reports, then use `cli.py raw '{...}'` to experiment and refine.

## Files

- `lepro.py` — async cloud client (`LeproClient`): login, discovery, MQTT, commands.
- `cli.py` — command-line interface.
- `app.py` — aiohttp web front end.
- `client_key.pem` — the static MQTT client private key shipped publicly with the
  app (per-account certs are downloaded at login into `certs/`).

## Disclaimer

Unofficial and not affiliated with Lepro. Uses an undocumented cloud API that
may change at any time. Use at your own risk.
