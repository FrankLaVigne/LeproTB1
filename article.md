# How I Reverse-Engineered My Smart Lamp (and What It Taught Me About IoT)

*A beginner-friendly tour of home automation, smart-home protocols, and what
really happens when you try to control a "smart" device with your own code.*

---

I bought a Lepro TB1 — a slick AI table lamp with 196 individually addressable
LEDs, music sync, and an app full of glowing animations. It's lovely. But I'm a
developer, and the moment I set it up I wanted the thing software lets you have
but hardware rarely promises: **I wanted to control it with my own code.**

Not through the app. Not by yelling at a voice assistant. A script. A web page I
built. `lamp.on()`.

What followed was a small adventure through the plumbing of the modern smart
home — and it turned out to be the perfect crash course in how IoT actually
works. If you're new to home automation, come along. By the end you'll
understand the landscape well enough to poke at your own gadgets.

## First, a mental model: what makes a light "smart"?

A smart bulb is just a normal light with a tiny computer and a radio bolted on.
That computer needs two things to be useful: a **way to talk** (a radio) and a
**language to speak** (a protocol). Almost every consumer smart-home headache
comes from the surprising variety in those two choices.

The radios you'll meet most often:

- **Wi-Fi** — connects straight to your home router. No hub required. Power
  hungry, but simple. (This is what my lamp uses.)
- **Bluetooth (BLE)** — short range, pairs directly to your phone. Cheap, but no
  remote access unless something bridges it.
- **Zigbee / Z-Wave / Thread** — low-power mesh radios that need a *hub*. Great
  for big installations, more upfront complexity.

And the crucial architectural fork — **where does the "brain" live?**

- **Local control**: your command goes phone → device (or hub → device) on your
  own network. Fast, private, works when the internet is down.
- **Cloud control**: your command goes phone → manufacturer's servers →
  internet → back to your device. Works from anywhere, but you're renting access
  to your own lamp. If the company's servers go down — or the company goes out of
  business — your "smart" device gets dumber.

That local-vs-cloud distinction is the single most important thing to understand
in this whole space. Hold onto it.

## The Tuya plot twist (and why it matters to you)

Here's a secret that surprises most people: a huge share of "different" smart
home brands are secretly the **same product underneath**. A Chinese company
called **Tuya** makes a white-label Wi-Fi platform, and hundreds of brands slap
their logo on it. If you own a no-name smart plug or bulb, there's a real chance
it's a Tuya device wearing a costume.

Why should you care? Because Tuya devices, despite being cloud-first, can often
be controlled **locally** if you extract a secret "local key." There's a mature
ecosystem of open-source tools for exactly this — `tinytuya` in Python, the
LocalTuya plugin for Home Assistant, and so on. So step one in any "can I control
this with code?" investigation is simply: **is it a Tuya device?**

There's a beautifully simple way to find out. Tuya gadgets announce themselves on
your local network with little broadcast packets. You can listen for them:

```bash
python3 -m venv .venv
.venv/bin/pip install tinytuya
.venv/bin/python -m tinytuya scan
```

This sits quietly for ~18 seconds and prints every Tuya device it hears. If your
light shows up, congratulations — you're on the easy path, and the rest of this
article is optional reading.

I ran it. The result?

```
Scan Complete!  Found 0 devices.
```

Zero. Even with the lamp powered on and sitting on the same network (I confirmed
its address — `192.168.1.170` — right there in my router's device list). My lamp
was **not** a Tuya device. And a quick read of the product specs explained why:
Lepro builds its *own* hardware to support its AI lighting features, explicitly
*not* using Tuya's chips.

> **Lesson:** A negative result is still a result. The scan didn't fail — it told
> me, definitively, which world I was *not* in. Good debugging is mostly about
> ruling things out.

## Following the breadcrumbs: how do you reverse-engineer a protocol?

So it's a proprietary cloud device. Now what? You have three honest options, in
increasing order of pain:

1. **Stand on someone's shoulders.** Search for "*\<your device\> Home
   Assistant*" or "*\<device\> API*" or "*\<device\> reverse engineering*."
   The home-automation community is vast and generous; someone has very likely
   sniffed the traffic already.
2. **Sniff the app yourself.** Point your phone's traffic through a tool like
   mitmproxy and watch the API calls the official app makes.
3. **Reflash the firmware.** Replace the manufacturer's software with open
   firmware like ESPHome for true local control. Powerful, but risky (you can
   brick the device) and not always possible.

I started with option 1 and struck gold: an open-source Home Assistant
integration had already mapped Lepro's cloud protocol. Reading its source code
told me everything I needed. **This is the most important skill in IoT hacking —
not cracking encryption, but knowing how to read the work of people who came
before you.**

## The protocol, demystified: REST + MQTT

Lepro's lamp speaks two languages, one after the other.

**Language one — REST, to log in and find your devices.** REST is just "make HTTP
requests like a web browser does." The app sends your email and password to a
login URL and gets back a *token* — a temporary badge that says "this person is
allowed in." Then it uses that token to ask "what devices are on this account?"

```
POST /user/login            → returns a token
GET  /user/profile          → returns where the live connection lives + certificates
GET  /family/list/...       → returns your "home"
GET  /device/list/...       → returns your lamp's unique ID
```

**Language two — MQTT, to actually control the lamp.** This is the star of the
show, and worth understanding because **MQTT is the lingua franca of IoT.**

MQTT is a lightweight messaging system built around *topics*, like chat-room
channels. Devices **subscribe** to topics they care about and **publish**
messages to topics. A central server (the "broker") routes messages between
them. It's designed for tiny, intermittent, low-power devices — exactly what a
lightbulb is.

To turn my lamp on, I publish a message to a topic named after the lamp's ID:

```
topic:   le/3689840842/prp/set
message: { "id": 12345, "t": 1716800000, "d": { "d1": 1 } }
```

That cryptic `"d"` dictionary is the device's actual control language. Decoding
it was the satisfying part:

| Field | Meaning |
|-------|---------|
| `d1`  | power — `1` on, `0` off |
| `d2`  | mode — 0 = white, 1 = color, 2 = segments/effects |
| `d3`  | brightness (0–1000) |
| `d4`  | white temperature (0 = warm, 1000 = cool) |
| `d5`  | color, encoded as hue/saturation/value in hex |

So `{"d1": 1}` is "on." `{"d2": 1, "d5": "00F003E803E8"}` is "switch to color
mode, show this hue." Once you have the table, the lamp is an open book.

One more wrinkle: the MQTT connection is secured with **TLS certificates** — not
just a username and password, but cryptographic ID cards that the app downloads
when you log in. My code downloads the same certificates and presents them to
connect. (This is good security on Lepro's part; it just means a couple extra
steps.)

## Building it

With the protocol understood, the code almost writes itself. The heart of it is a
small Python class that logs in, grabs the certificates, opens the MQTT
connection, and exposes friendly methods:

```python
async with LeproClient(account, password, region="na") as lamp:
    await lamp.power(True)              # on
    await lamp.set_brightness(40)       # 40%
    await lamp.set_color(255, 0, 120)   # hot pink
    await lamp.set_white(3000, pct=60)  # warm white at 60%
```

From there, a command-line tool and a little self-hosted web page (a browser UI
with an on/off button, a brightness slider, and a color picker) were easy to layer
on top. The full project is on GitHub.

## The gotchas — where the real learning happened

Anyone can follow a happy path. The education is in the potholes, and this
project had four great ones that you'll meet again in *any* IoT project:

**1. Names lie.** The API documentation listed a "US" server region. It doesn't
exist — the hostname doesn't even resolve. North American accounts actually use
the region code `na`. I burned a confusing ten minutes on a `Name or service not
known` error before I tested each hostname directly. *When something "should"
work but doesn't, verify your assumptions at the lowest level.*

**2. Rate limiting.** My first version logged in fresh for every single command.
The server quickly slapped me with `request is throttled`. Cloud APIs protect
themselves from hammering. The fix is the same one every real app uses: **log in
once, cache the token, and reuse it.** My script now saves its session to disk
and only re-authenticates when the cache goes stale.

**3. The single-session conflict — my favorite.** The moment my script logged in,
**the app on my phone got logged out.** Then I noticed the live connection kept
dropping. The cause: Lepro (like many IoT clouds) allows roughly *one active
session per account*. My script and my phone were fighting over the same seat.
*This is a design reality of cloud IoT, not a bug.* The clean fix is wonderfully
domestic: create a **second account**, share the device with it (most apps let
you invite family members to a "home"), and let your automation use its own
identity. Phone on account A, scripts on account B, no more squabbling.

**4. "Sign in with Google" has no password.** I'd signed into Lepro with my
Google account — which means there *is* no email/password for a script to use.
The fix isn't to reverse-engineer Google's OAuth dance; it's to use the app's
"Forgot password" flow to *set* a password on the account. (To be crystal clear:
that creates a password for the *Lepro* account, completely separate from your
Google password. Your Google account is untouched.)

> **Lesson:** Every one of these gotchas is a transferable concept — region
> config, token caching, session limits, auth flows. Learn them once on a lamp
> and you'll recognize them everywhere in software.

## The animations were a different story

With power, brightness, color, and white temperature working, I went looking for
the dynamic stuff — the chasing lights, breathing pulses, color cycles the app's
effects screen offered. The reference integration I'd been leaning on had a list
of effect names (`breath`, `clockwise`, `gradient`, `circular`…) and the encodings
to trigger each one. I wired up `set_effect("clockwise", red)`, ran it, and waited
for a red dot to chase around the rings.

The lamp went solid red.

I tried `circular` — same thing. `counterclockwise`. `breath`, though, **did**
pulse correctly. So the lamp obviously *could* run animations; it just wasn't
running the rotational ones from my code.

That sent me down the most interesting rabbit hole in the project. I needed to
see what the lamp was *actually receiving* when an effect ran. Since the protocol
is MQTT, that meant subscribing to the lamp's report topic and triggering an
effect from the *app* — letting it speak its native language while I eavesdropped.

So I wrote a `capture` mode: log in, subscribe to the lamp's report topic, dump
anything it emits. Then I opened the Lepro app, typed `"Christmas"` into its
**AI prompt** (Lepro calls it *LightGPM* — an LLM that designs a custom scene
from a phrase), and watched.

What came back was nothing like what I'd been sending. Where my code generated
short payloads like
`N01:P10001FF0000F2100010019U3V3100640000E1C2O600A2;`, the lamp was reporting
things like this:

```
N02:P10006FF8000FFAC59BF6000800000BF6000FFBF00U4T2X5F2000040201040103010501E401c2000001c20064I70384O60a8c;P600U4T2X5F20000204010601E40000000005460064I70546V3030640000O60a8c;
```

```
#V:0358c4000000003ec4000000002ec400000000;
#I00:N02:P10003FF0000008000FF0000U701R301011...;
#I01:N02:P10003FF0000008000FF0000U701R301011...;
#I02:N02:P10003FF0000008000FF0000U701R301011...;
```

A whole format I'd never seen. **Multi-program** payloads with `P1000` and `P600`
sections strung together. A `#V:` metadata header. `#I00`, `#I01`, `#I02` blocks
— almost certainly **per-ring** programs, since the TB1 has three concentric
rings (88 + 62 + 46 LEDs). Lowercase fields (`c2`, `r0`, `a8c`) that weren't
even pure hex.

The reference integration I'd been standing on had reverse-engineered the
*strip-light* protocol. The TB1 is a much richer device — addressable rings,
AI-generated scenes — and its real animation language is something the
open-source community simply hasn't mapped yet. I could spend weeks decoding
that format on my own, or…

I could just **replay it**.

## The leverage point

Here's the move that made the project click. I can't *generate* those payloads,
but I can *capture* and *replay* them — verbatim. The lamp doesn't care that the
d50 string came from the AI vs. from my script; it just runs whatever it's
handed. So the workflow flipped:

1. Open the Lepro app.
2. Type a prompt into the AI: `"mars colors"`, `"Christmas"`, `"campfire"`,
   `"aurora borealis"`.
3. While the AI's effect plays, my `capture` script logs everything the lamp
   emits.
4. Save the captured payloads as a JSON preset.
5. **Replay forever, from code or from an AI agent, no app required.**

The proprietary feature I was supposedly locked out of — the one that costs
Lepro the most to design and maintain, the LLM-driven lighting designer —
quietly became my preset generator. Type a phrase once, capture the result, and
now I have a permanent, scriptable effect I can trigger from anything that
speaks MQTT. The lock-in inverted.

Now my `presets/` directory looks like this:

```
presets/
├── mars_colors.json   # a pulsing red/orange breath
└── christmas.json     # 15-frame red-and-green sequence
```

`python play_preset.py christmas` reproduces the AI-designed Christmas
animation perfectly. And every prompt I think of adds another permanent entry
to the library.

This is the lesson I didn't see coming when I started: **sometimes the win
isn't reverse-engineering everything — it's finding the place where the system
will hand you what you need if you just hold out a bucket.**

## Should you do this? A word on ethics and expectations

A few honest caveats before you go forth:

- **This is unofficial.** I'm using an undocumented API. The manufacturer can
  change it tomorrow and my code will break. That's the deal with reverse
  engineering — you're a guest, not a tenant.
- **It still depends on the cloud.** Because this lamp is cloud-only, my
  "local" script still routes commands through Lepro's servers. It runs on *my*
  machine with no app required, which is most of what I wanted — but it's not
  truly offline. For that you'd need firmware reflashing or a device that
  supports local control in the first place.
- **Buy with this in mind.** If local, code-friendly control matters to you, it's
  worth checking *before* you buy: look for devices with documented local APIs,
  Home Assistant support, Matter compatibility, or a Tuya chip you can liberate.

## The takeaways

If you're new to home automation, here's the whole article compressed into things
worth remembering:

1. **Local vs. cloud is the defining question.** Always ask where the brain is.
2. **Check for Tuya first.** A 20-second scan can save you days.
3. **MQTT is everywhere.** Learn its publish/subscribe model and IoT stops being
   mysterious.
4. **Read other people's work.** The community has probably solved your
   problem — but they may have only solved *part* of it. The frontier of any
   device's protocol is usually wider than the open-source coverage.
5. **The errors teach the most.** Rate limits, session conflicts, and lying docs
   are features of the territory, not personal failures.
6. **Look for leverage points.** When reverse-engineering hits a wall, ask
   whether the system itself can be coaxed into generating what you need — then
   capture it. Sometimes the proprietary feature you can't reproduce is the
   exact thing that wants to be your preset generator.

My lamp now answers to a web page I wrote, a one-line command in my terminal,
and a growing library of AI-designed scenes I captured once and own forever.
It's a small thing. But understanding the machine well enough to bend it to
your will — and knowing when to let the machine do the bending for you —
that's the whole reason a lot of us got into this in the first place.

Now go find out what *your* gadgets are really saying.

---

*The complete, documented project — Python client, CLI, and web UI — is
available on GitHub. Pull requests welcome.*
