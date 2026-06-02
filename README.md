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
cli/          terminal scripts: main, stock_lamp, play_preset, tui (Textual cockpit)
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

### Lamp TUI

A Textual terminal cockpit: live ring visualizer + power / brightness / fill /
stop, driven through the workshop server's HTTP API (start `web.server` first).

```bash
.venv/bin/python -m web.server     # the workshop (terminal 1)
.venv/bin/python -m cli.tui        # the TUI (terminal 2, same machine)
.venv/bin/python -m cli.tui --server http://192.168.1.50:8081   # or remote
```

Keys: `p` power · `↑`/`↓` brightness · `1`-`8` fill color · `s` stop ·
`v` rings/strips view · `d` raw d-fields · `r` refresh · `q` quit.

The visualizer decodes the lamp's reported `d50` (our N01 format), RGB mode
(`d5`), and white mode (`d4`). Official-app animations (N02/N03 formats) show
as dark rings — nothing decodes those yet.

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

## Web UI (cockpit)

```bash
.venv/bin/python -m web.server        # serves on 0.0.0.0:8081
```

Open `http://<vm-ip>:8081`. The interface is a single "cockpit" layout:

- **Left panel (always visible):** the lamp visualizer (live 3-ring SVG of
  what the lamp is currently showing), the active-mode banner (Idle / Off /
  Preset / Ticker / Clock), power on/off, brightness slider (0-100 %), and
  a collapsible diagnostics drawer with the raw d-field values.
- **Right panel:** five tabs.
  - **🎨 Presets** — browse / recolor / preview / save captured presets
    (`presets/*.json`). Click Preview to loop the preset on the lamp.
  - **✏️ DIY** — click-to-paint 3-ring SVG canvas; Draw / Fill / Erase /
    Back tools; color picker with quick-pick swatches; six confirmed motion
    effects (Steady / Breathe / Gradient / Leftward / Rightward / Circle);
    speed slider; Save to a single-frame preset.
  - **📈 Ticker** — assign up to 3 Yahoo Finance symbols to the rings;
    each ring shows its symbol's most recent direction as a solid color,
    with a 5-second whole-lamp breathe flash on every tick. Sustained
    moves earn a ⚡ FAST badge and switch the base effect to Breathe.
  - **⏰ Clock** — three-handed analog clock (outer = seconds, middle =
    minutes, inner = hours), per-ring configurable colors, 12 h / 24 h
    toggle, 1-second cadence.
  - **🎞 Animations** — the deduped catalog of motion patterns derived from
    your `presets/*.json` library. Click a row to pick new colors and save
    the result as a new preset. Useful when you've captured the same
    Lepro-AI prompt twice with different palettes and want to see they're
    the same motion underneath. Manual rename and merge available via
    `animations.json` (tracked in git, written by the tab's UI). A **🎥
    Capture** button at the top lets you grow the library from the UI:
    click it, trigger one animation in the Lepro phone app, the server
    records the d50 frames over MQTT (auto-stops on 6 s idle or 90 s cap)
    and saves the result as a new preset. A counter shows your progress
    toward the Lepro app's ~72-animation catalog.

While the ticker or clock is running, the DIY paint and the Presets-page
preview endpoint return HTTP 409. Power off stops every active driver
(ticker, clock, preset preview) before turning the lamp off. Starting
the ticker or clock — or sending a DIY paint — auto-stops the preset
preview loop so manual paints aren't overwritten.

Responsive layout: side-by-side cockpit on desktop (≥ 720 px), stacked
on phone. LAN-only — no auth. Override the bind address with
`LEPRO_WORKSHOP_HOST` / `LEPRO_WORKSHOP_PORT`.

`/state` (the old standalone state page) is gone — it was absorbed into
the left panel. The URL 302-redirects to `/` so old bookmarks still land
somewhere useful.

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
