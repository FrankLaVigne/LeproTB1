# A Playbook for Reverse-Engineering a Cloud-Controlled Smart Device

This is the methodology I followed to take a closed, cloud-only smart lamp
(the **Lepro TB1**) and end up with my own Python client, web UI, MCP server,
and a growing library of capturable effects. The same shape of process applies
to most consumer IoT products — bulbs, plugs, strips, table lamps — that talk to
a manufacturer's cloud via REST + MQTT.

I'm publishing this so other people can do the same with their own devices.
If you're working through this on a different lamp / bulb / plug, the
**six phases below** should adapt cleanly. Pull requests with what you found
on *your* device are welcome.

> **Worked example throughout:** the Lepro TB1 (a Wi-Fi RGB+IC table lamp with
> three concentric LED rings, controlled by the "Lepro" app, with an AI-driven
> lighting designer called LightGPM). The code is at this repo.

## Before you start: the local-vs-cloud fork

The first question is always: **does the device speak its own network, or only
the cloud?** If it can be controlled on your LAN, the rest of this is short.
If it can't, you're going to be talking to its cloud — which is most of this
playbook.

Quick triage:

- Bluetooth-only or Thread/Matter device → different playbook (sniff BLE GATT
  / use the Matter SDK), out of scope here.
- Wi-Fi device that *might* be a relabelled Tuya unit → check that first
  (Phase 1). If yes, you're an hour away from local control.
- Wi-Fi device that isn't Tuya → you're in cloud-RE territory; keep reading.

---

## Phase 1 — Identify what you're dealing with

The single highest-leverage thing you can do in the first hour. Two parts:

### 1a. Is it Tuya?

A huge fraction of "different" smart-home brands are relabelled **Tuya**
hardware, and Tuya devices announce themselves on your LAN over UDP — you can
detect them passively in ~20 seconds:

```bash
python3 -m venv .venv
.venv/bin/pip install tinytuya
.venv/bin/python -m tinytuya scan
```

If your device shows up, you're on the easy path: extract its local key (the
`tinytuya wizard` walks you through it) and control it directly with
`tinytuya` or via LocalTuya in Home Assistant. **Stop here.**

If it doesn't show up — verify the device is powered on and on the **same
subnet** as the machine you're scanning from. A negative scan is meaningful
only if both are true. (VM networking is a real footgun here; broadcast
packets may not cross your VM's NAT.)

The TB1 returned zero devices despite being right there on `192.168.1.x` —
confirming it's *not* Tuya. Two independent signals agreeing.

### 1b. Has anyone reverse-engineered it already?

Search:

- `"<device>" home assistant integration`
- `"<device>" reverse engineer / API / SDK`
- `"<device>" github`
- The brand's community forum, often asking about Homebridge or HA integration

A pre-existing Home Assistant custom integration is the single biggest find
you can make. Even if it only covers an adjacent model, the *protocol it
implements* may apply to yours. Read its source.

For Lepro, I found [`Sanji78/lepro_led`](https://github.com/Sanji78/lepro_led)
— a HACS integration that had mapped the Lepro Cloud REST + MQTT protocol from
captured app traffic. It covers bulbs and some strips, not the TB1's full
animation language, but it was a complete map of the *auth and basic control*
layer. Days saved.

---

## Phase 2 — Establish what's documented (almost certainly: nothing)

Worth a half hour to confirm. Search for:

- `<brand> developer documentation`
- `<brand> API SDK`
- The brand's official site (developer / API / partners section)

Most consumer smart-home brands have **no public API**. Lepro confirmed it on
their own community forum: *"we currently do not have plans for the API, but
our technical team has noted your feedback."* Knowing this saves you from
hoping a future doc will appear.

Read at least one product page and one CES/IFA press release. You'll often
find product-architecture hints there that explain what protocol features
exist even if not how they encode.

---

## Phase 3 — Find the live connection (REST + MQTT, almost certainly)

Modern cloud IoT devices almost always share a shape:

1. **App logs in via HTTPS** to the manufacturer's API. Returns a bearer
   token + a "where to reach the device" hint (an MQTT broker URL, often with
   per-account TLS credentials).
2. **App + device both connect to that MQTT broker**, authenticated by client
   certificates.
3. **Control = publish to a per-device topic.** State updates = the device
   publishes back on a sibling topic.

The vocabulary you're hunting for: endpoints like `/user/login`,
`/family/list`, `/device/list`, an MQTT host like `api-xx-iot.<brand>.com` or
`mqtt.<brand>.com`, topics like `<brand>/{deviceId}/prp/set` or
`<brand>/{deviceId}/cmd`.

You can find them three ways, in increasing order of pain:

### 3a. From a reference integration (easiest)

If Phase 1b turned one up, read its source. You're looking for: the REST
endpoints, the auth flow, where the MQTT host comes from, the client-cert
bootstrap, and the publish/subscribe topic structure. For Lepro, all of this
was in `Sanji78/lepro_led/custom_components/lepro_led/` —
`const.py`, `light.py`, and the bundled `client_key.pem`.

### 3b. By sniffing the app's HTTPS traffic (moderate effort)

Point your phone's traffic through **mitmproxy** running on your laptop.
Install mitmproxy's CA on your phone. Open the app, do things, watch the
calls.

**Be warned:** many vendor apps now use **SSL pinning**, which mitmproxy alone
can't bypass. You may need **Frida-based unpinning**
([httptoolkit/frida-interception-and-unpinning](https://github.com/httptoolkit/frida-interception-and-unpinning))
or an old Android version without pinning enforcement. The Lepro app's
pinning, for instance, even Frida's automatic patcher couldn't bypass cleanly
when last attempted (2025). Budget hours, not minutes.

### 3c. By sniffing MQTT traffic from the device side (more on this in Phase 5)

Once you have *minimal* REST + MQTT auth working, you can subscribe to the
device's report topic and watch what *it* says when you control it from the
app. This is often more productive than HTTPS sniffing because the messages
are JSON and the pinning isn't a barrier.

---

## Phase 4 — Build a minimal client

Don't try to handle the full protocol upfront. Get to "on/off works" as fast
as possible, then build outward. The minimal client needs to:

1. Log in (REST) and get a bearer token.
2. Pull profile info that tells you where the MQTT broker is.
3. Download the per-account TLS certificates from the URLs in the profile
   response. The brand often bundles a shared static client *private* key
   inside the app; the per-account *cert* is downloaded at login. Both go into
   an `ssl.SSLContext`.
4. Discover devices (`/family/list` → home id → `/device/list` → device id).
5. Connect to the MQTT broker with that SSL context.
6. Publish *one* payload (typically `{"d1": 1}` for "on") to the device's
   control topic.

Confirm visually that the lamp turns on. **That's the milestone.** Everything
else builds on it.

Then — *immediately* — add **session caching**. The login endpoint will be
rate-limited (Lepro's throttled aggressively, code `-905`). Save the token +
MQTT info + downloaded certs to disk after a successful auth; on subsequent
runs reuse them until expired or rejected. Without this, you'll be throttled
every time you iterate on your code.

For Lepro, `lepro.py:LeproClient` does all of the above in ~250 lines
including reconnect handling. Look there for a concrete shape.

### Gotchas you will absolutely hit

These aren't bugs in your code; they're features of the territory:

- **Regional hostnames lie.** Lepro lists "us" in its constants, but
  `api-us-iot.lepro.com` doesn't resolve — the real US region is `na`. Test
  every regional host's DNS before trusting it.
- **Rate limiting.** Cache tokens, exponential backoff on auth failures.
- **Single-session conflict.** Most clouds enforce roughly *one active session
  per account*. The moment your script logs in, the app on your phone may get
  logged out, and vice versa. **The clean workaround is a second account
  shared into the device's "home"** (most apps support multi-member homes for
  family sharing). Phone uses account A; scripts use account B; no fight.
- **"Sign in with Google" / OAuth has no password.** Vendor APIs usually
  authenticate with email+password — they can't speak Google OAuth. Use the
  app's "Forgot password" flow to *set* a password on the social-login
  account; that creates a vendor-side password without touching your Google
  account.
- **SSL client cert is required by MQTT.** You can't skip the cert dance.
  Make sure your `ssl.SSLContext` has CA + client cert + the bundled private
  key all loaded before `connect()`.

---

## Phase 5 — Capture the rich states

This is the phase that turns "I can toggle the lamp" into "I can drive every
effect it knows about." It's also the leverage point most projects miss.

### The capture loop

1. Have your client subscribe to the device's report topic (typically
   `<brand>/{deviceId}/prp/#` or similar).
2. Open the official app on your phone.
3. Trigger an effect — pick a scene, type an AI prompt, whatever the app
   exposes.
4. The device broadcasts its new state to the broker as a series of MQTT
   messages. **Your subscriber receives them too.** Log them, deduplicate by
   payload, and save the interesting ones.

Concretely, my Lepro capture is a `cli.py capture --seconds 90` subcommand
that prints any `d50`/`d60`/`d5` field changes it sees. A 90-second window is
usually enough to capture a full effect cycle.

The single-session conflict from Phase 4 will bite you here — when you open
the app, the broker may drop your subscriber. Mitigations, in order:

- Use the second-account share to keep the script's MQTT identity separate
  from the phone's.
- If you can't share, accept that you'll get partial captures. Even one good
  packet of "real" device state is worth more than a week of guessing.

### What you'll see (the format-exploration phase)

The captured payloads will almost always be richer than what any reference
integration knew about. For the TB1, I expected the strip-protocol `d50`
strings I'd been generating; what I got back was a whole new format with
multi-program and per-ring blocks:

```
N02:P10006FF8000FFAC59...U4T2X5F20000...c2000001c20064I70384O60a8c;
P600U4T2X5F20000...
```

```
#V:0358c4000000003ec4000000002ec400000000;
#I00:N02:P10003FF0000008000FF0000U701R301011...;
#I01:N02:P10003FF0000008000FF0000U701R301011...;
#I02:N02:P10003FF0000008000FF0000U701R301011...;
```

Compare these against the simpler payloads your code generates. The gap is
the un-mapped portion of the protocol — and where you have a choice to make
in Phase 6.

---

## Phase 6 — Decode, or replay?

You have two options for using the captured states:

### Option A: Decode the format

Take the captures, diff them, hypothesize what each field means, and write a
generator that produces them from your own inputs. This is the "do it right"
path. It gives you the ability to parameterize: *"the same effect but in
blue."* But it costs weeks of work per device family, and the protocol may
evolve underneath you.

For the TB1's d50 format, I haven't done this yet (and likely won't —
there's no documentation and the format includes lowercase non-hex fields,
multi-program blocks, and per-ring blocks I'd need to disambiguate from
captures alone). Open questions remain: what do `U7`, `T2`, `X5`, `#V:1`
mean? What's the encoding of the lowercase `r0101...` blocks? What's the
relationship between `N01:` / `N02:` / `N03:` / `P4` header variants? If you
figure any of these out, please contribute.

### Option B: Capture and replay verbatim

Save the captured `d50` strings as **presets** — one JSON file per effect —
and replay them through your "raw" publish path. The lamp doesn't care that
the bytes came from the AI vs. your code; it runs them either way.

For the TB1, this is what we did. The workflow:

1. Open the Lepro app.
2. Type a prompt into LightGPM (the in-app AI lighting designer):
   `"mars colors"`, `"Christmas"`, `"cyberpunk lighting"`, `"hulk"`.
3. While the AI's effect plays, `cli.py capture` logs every distinct payload
   the lamp emits.
4. Save as `presets/<name>.json`. Multi-frame captures become a list of
   frames; single-frame captures become a single payload.
5. Replay anywhere, anytime: `play_preset.py <name>`.

**The proprietary AI becomes your preset generator.** Type a phrase once;
own the effect forever, callable from your code, the CLI, the web UI, or any
MCP-aware agent.

This is the move I'd recommend for most consumer devices with rich,
proprietary effects: don't fight the format, capture the output. Decode only
if you genuinely need parameterization.

---

## Phase 7 — Package it

Once you have a working client + a way to capture, build the things that
make it usable:

- **A library / client class.** Mine is `lepro.py`; the analog for your
  device will look similar (auth, MQTT, simple control methods).
- **A CLI** for ad-hoc commands. Mine: `cli.py on / off / bright / color /
  white / raw / capture`.
- **A web UI** for non-coders. Mine: `app.py` (aiohttp, one persistent
  connection, simple HTML page).
- **An MCP server** so AI agents can drive the device. Mine: `mcp_server.py`
  with FastMCP over streamable-HTTP and bearer-token auth.
- **A presets directory** with one JSON file per captured effect. Each
  preset's filename becomes its name; an agent or user picks effects by name.

These are commodity scaffolds; what makes them useful for your device is the
auth + MQTT layer you built in Phase 4.

---

## Worked example: the Lepro TB1, end to end

Phase-by-phase mapping for the worked example:

| Phase | Lepro TB1 specifics |
|------|---------------------|
| 1a | `tinytuya scan` → 0 devices (the lamp is on `192.168.1.170`, same subnet, was on). Not Tuya. |
| 1b | `Sanji78/lepro_led` — full REST + MQTT auth flow + simple `d50`/`d60` effect formats. |
| 2 | Lepro forum: no API planned. LightGPM page: no developer access. |
| 3a | Used the reference integration's protocol. Region `na`, not `us`. |
| 3b | Tried, hit SSL-pinning + Frida-bypass failures, abandoned. |
| 4 | `LeproClient` (~250 lines). Login → profile → cert download → MQTT. Session cached to `certs/session.json`. Power, brightness, color, white temp all working. |
| 5 | `cli.py capture --seconds 90`. Subscribed to `le/{did}/prp/#`. Triggered effects from the app. Captured payload formats not in the reference: `N02:`, `N03:`, `#V:`, `#I00/#I01/#I02:`, `P4` headers. |
| 6 | Chose **replay** over decode. Built `presets/*.json` library; currently `mars_colors`, `christmas`, `cyberpunk`, `hulk`. |
| 7 | `cli.py` + `app.py` + `mcp_server.py` + `presets/` + `play_preset.py`. |

### The TB1 `d` field reference (what we've decoded)

| Field | Meaning | Status |
|-------|---------|--------|
| `d1`  | power — `1` on, `0` off | ✅ confirmed |
| `d2`  | mode — `0` white/CCT, `1` RGB, `2` segments/effect, `3` special effect | ✅ confirmed |
| `d3`  | brightness 0–1000 (white & B-series RGB modes — **NOT** the TB1's segmented mode) | ✅ confirmed |
| `d4`  | white temperature 0–1000 (0 = 2700 K warm, 1000 = 6500 K cool) | ✅ confirmed |
| `d5`  | RGB as HSV hex `HHHHSSSSVVVV` (hue 0–360, sat/val 0–1000) | ✅ confirmed |
| `d50` | the rich animation language (see below) | ✅ confirmed |
| `d52` | brightness 0–1000 (segmented/strip mode) — **layers cleanly on top of any `d50` pattern** | ✅ confirmed end-to-end (2026-05-29) |
| `d60` | special-effect + sensitivity string | ✅ partial |
| `d30` | session/instance id (hex) — observed but role unclear | ⚠️ unknown |

**Brightness for the TB1 specifically:** the lamp lives in `d2=2` (segmented)
mode for any custom pattern or captured preset, so the relevant brightness
field is **`d52`** (not `d3`). They're independent — `d52` can be sent on its
own to fade a running pattern without re-publishing `d50`. Confirmed by
sending `{"d52": 250}` then `{"d52": 750}` from code and observing dim → bright.

> ⚠️ **Methodology lesson** (the kind that costs hours): make your capture
> tool show *every* field by default, not a filtered subset. Our `cli.py
> capture` was silently dropping `d3`/`d4`/`d52`/`d30` because we picked a
> small "interesting" set early on. We didn't realize brightness lived in
> `d52` for *weeks* because we never saw it. **If the tool can hide
> information from you, sooner or later it will.**

### The d50 format — what we know, what we don't

**Known (from the reference integration, works for setting colors and `breath` on the TB1):**

```
N01:P1000<num_groups><colors_hex>F21000<num_groups><lengths_hex>U3V3<effect_tail>;
```

Where colors are 6 hex chars each (RGB), lengths are 4 hex chars each (count
per group, hex), and `<effect_tail>` is a short code like `000640000E1` for
solid or `100640000E1C2O6<speed_hex>` for circular.

**Unknown (what the TB1 actually uses for real animations, captured from the wild):**

- `N02:` / `N03:` headers — likely indicate format version or program count.
- `P1000` / `P600` / `P4` prefixes — appear to be different "program types".
- Multi-program payloads joined by `;` — `P1000…;P600…;`.
- `#V:<hex>;` metadata prefix — possibly a version/scene-ID or transition spec.
- `#I00:`/`#I01:`/`#I02:` per-ring blocks — almost certainly the three
  concentric LED rings (88 + 62 + 46 LEDs). Each block contains its own
  `N0x:P…` program.
- Lowercase fields: `c2`, `r0101…`, `s00000001`, `a8c` — purpose unknown.
- Single-letter section markers (`U`, `T`, `X`, `M`, `O`, `V`, `R`, `S`, `W`,
  `E`, `F`) — each clearly tags a sub-block but the semantics aren't mapped.

**If you crack any of these on a TB1 or a related Lepro device, please open
a PR.**

---

## A note on the social side of reverse engineering

Three things worth saying:

1. **Stand on shoulders, then leave better shoulders.** The Sanji78
   integration saved me days. Publishing this playbook is paying it forward.
   If you used it, consider publishing your own notes on whatever device you
   investigate.
2. **Be respectful.** You're using an undocumented API. Don't hammer it, don't
   share credentials publicly, don't ship products that depend on it without
   the vendor's blessing. The vendor turned off your access tomorrow would be
   completely within their rights.
3. **Capture is a feature, not a workaround.** "Use the proprietary AI as
   your preset generator" is, philosophically, the cleanest possible
   integration with a closed system — you let it do what it's good at and own
   the output. That mindset will save you time on every IoT project you ever
   touch.

---

## Contributing

If you've used this playbook on a different device, or made progress on the
unknown TB1 d50 fields above, please open an issue or a pull request. Even a
single decoded section marker (`X5 = ?`, `T2 = ?`) would help.
