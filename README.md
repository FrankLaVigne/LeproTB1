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

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.json.example config.json   # then edit with your Lepro account
```

`config.json` (git-ignored) or the `LEPRO_ACCOUNT` / `LEPRO_PASSWORD` /
`LEPRO_REGION` environment variables provide credentials. Regions: `us`, `eu`,
`na`, `fe`.

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
and gives you device selection, on/off, a brightness slider, a colour picker,
and a white-temperature slider. Override the bind address with `LEPRO_HOST` /
`LEPRO_PORT`.

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
| `d4`  | colour temperature 0–1000 (0 = 2700 K warm, 1000 = 6500 K cool) |
| `d5`  | RGB as HSV hex `HHHHSSSSVVVV` (hue 0–360, sat/val 0–1000) |
| `d52` | brightness 0–1000 (segmented/strip mode) |
| `d50` | segmented colour + effect string |
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
