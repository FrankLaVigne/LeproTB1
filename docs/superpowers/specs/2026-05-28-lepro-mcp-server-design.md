# Lepro MCP Server — Design Spec

**Date:** 2026-05-28
**Status:** Approved (pending implementation plan)
**Repo:** git@github.com:FrankLaVigne/LeproTB1.git

## Goal

Expose the existing Lepro lamp-control capability (the `LeproClient` in
`lepro.py`) as a **networked MCP server** so that AI agents — primarily
**OpenClaw** running on a separate server, but also Claude Desktop/Code and any
MCP-compatible client — can control the TB1 over the network, including running
animations like the official app.

Two original requests converge here:
- "Connect this to OpenClaw" → OpenClaw connects to the MCP server over HTTP.
- "Make an MCP" → the server is a standard MCP server, reusable by any MCP client.

If OpenClaw turns out not to speak MCP, the existing `cli.py` remains a
shell-out fallback (no extra work required).

## Non-goals

- No changes to the reverse-engineered protocol in `lepro.py`'s core (login,
  MQTT, cached session) beyond **adding** effect/segment/animation methods.
- No music-sync support (most cloud-fragile; explicitly out of scope).
- No new configuration system — extend the existing `config.json` / `LEPRO_*`
  env-var scheme.
- No OAuth; access control is a single shared bearer token.

## Architecture

One long-lived process on the VM (where `config.json` + certs already live):

```
OpenClaw (separate server) ──HTTP + Bearer token──► mcp_server.py (VM :8765)
                                                          │ reuses
                                                          ▼
                                                    LeproClient ──MQTT/TLS──► Lepro cloud ──► TB1
```

- **New file:** `mcp_server.py`. The core `lepro.py` gains animation methods but
  its existing behavior is untouched.
- **Transport:** MCP streamable-HTTP via the `mcp` Python SDK, served by uvicorn.
- **Single client object:** the server owns one `LeproClient`, logged in at
  startup (using the cached-session path), with a persistent MQTT connection and
  the existing auto-reconnecting `listen_forever` background task.

## Components

### 1. `mcp_server.py`
- Builds an MCP server, registers the tools below, wraps the ASGI app with a
  bearer-token middleware, and runs it under uvicorn.
- Owns the `LeproClient` lifecycle (startup login, shutdown cleanup) and the
  `AnimationPlayer` registry (one per device).

### 2. Additions to `lepro.py`
Pure-ish command builders + an animation runner, mirroring the proven payload
formats from the `Sanji78/lepro_led` reference integration:
- `set_effect(name, speed=50, did=None)` — `d50` effects (`solid`, `breath`,
  `gradient`, `clockwise`, `counterclockwise`, `circular`) and `d60` special
  effects (`flash`, `wave_1..4`, `laser_1..4`). Includes the speed-hex encoder.
- `EFFECTS` catalog constant (names + which family) for `list_effects`.
- `set_segments(colors, did=None)` — `colors` = list of up to 25 `[r,g,b]`
  groups; builds the grouped `d50` string (compress identical adjacent groups,
  pad/truncate to 25 segments).
- `AnimationPlayer` — async task that plays a list of frames on a device.

### 3. `AnimationPlayer`
- `frames`: list of `{color?: [r,g,b], segments?: [[r,g,b],...], brightness?: int,
  duration_ms: int}`. At least one of color/segments/brightness per frame.
- `play(frames, repeat)`: runs an asyncio task publishing each frame then
  sleeping `duration_ms`; `repeat` loops indefinitely (or N times).
- `stop()`: cancels the task.
- One player per `did`; a new `play` replaces a running one.
- Validation: `duration_ms >= 80` (floor, to avoid hammering MQTT); reasonable
  max frame count.

## MCP Tools (the agent-facing interface)

| Tool | Args | Returns |
|------|------|---------|
| `list_lights` | — | `{ok, lights:[{did,name,series}]}` |
| `list_effects` | — | `{ok, effects:[names], speed_range:[0,100]}` |
| `set_power` | `on: bool`, `did?` | `{ok, did, applied}` |
| `set_brightness` | `pct: int(0–100)`, `did?` | `{ok, did, applied}` |
| `set_color` | `r,g,b: int(0–255)`, `pct?`, `did?` | `{ok, did, applied}` |
| `set_white` | `kelvin: int(2700–6500)`, `pct?`, `did?` | `{ok, did, applied}` |
| `set_effect` | `name: str`, `speed?: int(0–100)`, `did?` | `{ok, did, applied}` |
| `set_segments` | `colors: [[r,g,b],...]`, `did?` | `{ok, did, applied}` |
| `play_animation` | `frames: [...]`, `repeat?: bool\|int`, `did?` | `{ok, did, frames, repeat}` |
| `stop_animation` | `did?` | `{ok, did}` |
| `get_state` | `did?` | `{ok, did, state}` (best-effort) |
| `send_raw` | `d: object`, `did?` | `{ok, did, sent}` (escape hatch) |

`did` is optional everywhere; omitted = the first/only light. Every tool returns
structured JSON; failures return `{ok: false, error: "..."}` rather than raising,
so the agent can read and react.

## Auth, config, transport

- **Bearer token:** ASGI middleware checks `Authorization: Bearer <token>` on
  every request. Token from `LEPRO_MCP_TOKEN` env or `config.json` `mcp_token`.
  Missing/invalid → HTTP 401.
- **Fail-safe:** if no token is configured AND the bind host is not loopback, the
  server refuses to start (prevents accidentally exposing an open lamp endpoint).
- **Bind:** default `0.0.0.0:8765`; override `LEPRO_MCP_HOST` / `LEPRO_MCP_PORT`
  (or `config.json` `mcp_host`/`mcp_port`).
- **Dependencies:** add `mcp` and `uvicorn` to `requirements.txt`.

## Lifecycle & error handling

- **Startup:** construct `LeproClient`, `await login()` (cached-session path),
  `await connect_mqtt()`, start `listen_forever`. Load devices once.
- **Single-session reality:** the server holds the account's one session, so
  while the phone app is active the broker may drop the server's MQTT connection
  (control publishes still succeed; live `get_state` is best-effort). Documented
  prominently. Recommended deployment uses a **dedicated second Lepro account**
  shared into the lamp's Home, so the phone and server don't collide.
- **Errors:** tools catch `LeproError`/`AuthError` and return `{ok:false,error}`.
  Expired token → transparent re-auth via the existing `AuthError` path.
- **Animation cleanup:** `stop_animation` and server shutdown cancel running
  `AnimationPlayer` tasks cleanly.

## TB1 effect verification (discovery step)

Effect/segment payload formats were reverse-engineered from Lepro *bulbs and
strips*; the TB1 may differ. A **capture** mode subscribes to the lamp's MQTT
report topic (`le/{did}/prp/#`) while the user triggers each effect in the app,
logging the exact `d50`/`d60` the TB1 emits — giving ground-truth payloads to
encode. Works best from the dedicated second account (stays subscribed while the
app drives the lamp). This is an implementation task that confirms/corrects the
`set_effect`/`set_segments` builders before they're trusted.

## Testing

- **Unit (no hardware/network):** payload builders are deterministic — assert
  `set_effect("breath",50)` → expected `d50`; `set_segments([...])` → expected
  grouped string; color/white/brightness mappings; bearer middleware (401 vs
  pass-through); animation frame validation (duration floor, bad frames).
- **Integration (manual, real lamp):** checklist exercising each MCP tool against
  the TB1, including the capture step.
- **MCP smoke:** start server, connect an MCP client with the token, confirm
  `list_lights` / `list_effects` and one control call succeed; confirm a request
  without the token gets 401.

## File plan

- `mcp_server.py` — new; MCP server, tools, bearer middleware, uvicorn runner.
- `lepro.py` — add `set_effect`, `set_segments`, `EFFECTS`, `AnimationPlayer`.
- `requirements.txt` — add `mcp`, `uvicorn`.
- `config.json.example` / `README.md` — document `mcp_token`, host/port, the
  bearer requirement, and the second-account recommendation.
- `tests/` — unit tests for builders + middleware.
- `capture.py` (or a `cli.py capture` subcommand) — effect-capture helper.

## Open questions for implementation

- Confirm the exact `mcp` SDK API for mounting streamable-HTTP under custom ASGI
  middleware (SDK version pin).
- Validate which named effects the TB1 actually honors (capture step) and adjust
  the catalog to what works.
