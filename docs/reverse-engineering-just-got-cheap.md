# Reverse Engineering Just Got Cheap

I bought a smart lamp last week. 196 individually addressable LEDs arranged in
three concentric rings, controlled exclusively by a phone app talking to a
cloud. No documentation. No public API. The vendor's "smart" claim ends at
their app's UI.

I now have a Python client, a web UI, an MCP server, a multi-symbol stock
ticker that drives the rings, and a clickable per-LED painting canvas. The
protocol — including a 100-character wire format for arbitrary per-LED patterns
with six built-in motion effects — is documented in my repo. Total time
investment: parts of three evenings.

A decade ago this would have been a graduate-level reverse engineering project.
The reason it now fits in a weekend isn't that I got smarter. The activation
energy for reverse engineering collapsed, and almost nobody is pricing that in
yet.

## The old shape of the work

Reverse engineering a closed protocol used to filter for a very specific kind
of person. You needed to be fluent in five or six disjoint disciplines at once:
packet capture (Wireshark, mitmproxy, sometimes a rogue access point),
TLS interception (certificate pinning, system trust stores, occasionally Frida
to bypass app-level pinning), protocol decoding (length-prefixed framing,
endianness, variable-width integers, mystery checksums), application
disassembly (Ghidra, Hopper, IDA, reading hex offsets like sentences),
JavaScript deobfuscation if it was a web target, and a willingness to spend
two hundred hours on one device.

That filter is what kept things closed. Vendors didn't open their APIs because
they didn't have to. The cost of someone forking their ecosystem was too high
to bother defending against.

The filter doesn't really exist anymore.

## What changed in the middle

People talk about AI-assisted coding making the average developer more
productive. That's true, but it's the boring version of the story. The
interesting version is that AI demolished the *middle steps* of reverse
engineering — the smart-but-tedious work that used to be the bottleneck.

The hardest parts of decoding a captured payload were never the parts at the
boundary. Capturing packets is easy; that's just plumbing. Verifying a
hypothesis is easy; you send a payload and watch the lamp. The hard part was
the middle: staring at 40 hex dumps and pattern-matching for hours, trying to
spot which bytes covary with which UI actions, building up a mental model of
the framing, holding seventeen partial hypotheses in your head and updating
them one capture at a time.

That's exactly the work LLMs are good at. They will hold seventeen partial
hypotheses without complaint, take a new capture and re-rank them, surface the
two most likely framings, and walk you through testing each. They do not get
bored. They do not lose the plot at 11pm. The bottleneck used to be "is anyone
willing to spend a week of evenings on this." The bottleneck now is "do you
have an afternoon and curiosity."

## A concrete example

The lamp's per-LED protocol is a 100-character string in a field called `d50`.
Its structure looks like:

```
N01:P1000{N}{colors}F21000{G}{lengths}U3V3<effect_tail>;
```

`{N}` is a digit (palette size), `{colors}` is concatenated six-hex RGB,
`{G}` is the number of run-length groups, `{lengths}` is concatenated
four-hex-character big-endian run lengths that must sum to `0xC4` (196).
The effect tail picks among Steady, Breathe, Gradient, Leftward, Rightward,
Circle, each with a 4-character speed slot encoded as
`round(-117.41 × ln(speed+1) + 597.75)`.

Working that out the old way would have been: capture, stare, hypothesize,
disassemble the Android app, find the encoder, prove it. Maybe a month if you
were focused.

The way I actually did it: capture twelve payloads from the official app while
clicking specific buttons, ask Claude to enumerate possible framings, test the
top three by sending crafted payloads to the lamp, watch the rings light up,
narrow further. Four evening sessions to fully decoded. The hardest piece — the
log-scale speed encoding — fell when I realized one of my captures had been
labeled with the wrong speed value. The model caught the inconsistency I'd
missed.

I did not touch a disassembler. I did not bypass TLS pinning. I did not write
custom protocol parsers. The work that used to dominate reverse engineering
just isn't on the critical path anymore.

## The asymmetry

Here's the part that I think the IoT industry has not absorbed yet.

The reverse engineer's marginal cost-per-protocol is dropping fast — call it
10× in the last two years. The vendor's defensive cost is dropping much more
slowly, and a lot of their defensive levers (TLS pinning, signed payloads,
hardware attestation, server-side feature gating) are expensive *per device*
in ways that don't compose. A $30 smart bulb cannot economically ship hardware
attestation. Adding cert pinning to a phone app slows down a hobbyist by maybe
a day. Adding server-side validation means re-architecting the cloud.

Meanwhile, the cost of *not* having an open API compounds in the other
direction. Every closed device taxes its own ecosystem: someone wanting Home
Assistant support pays the reverse-engineering tax once, then publishes the
client, then everyone else pays zero. The closed-vendor surcharge is real,
short-lived, and exclusively borne by the first person motivated enough to
break the protocol. Once one Python client exists, the official app is no
longer the only path. Custom dashboards show up. WLED reflash guides appear.
The vendor still owns the hardware but loses the ecosystem.

That's the dynamic that actually moves vendors, not shame. Sonos, Philips Hue
in their early days, Yamaha receivers — these companies published their APIs
*because they wanted to shape* how integrations happened. The dumb vendors just
get reverse-engineered around. The smart ones notice that "open is defensive"
and ship a public REST endpoint so they at least control the surface.

## Compounding

The other thing the industry hasn't priced in is that reverse-engineering
patterns *compose across vendors*. A huge fraction of the consumer IoT space
shares a small set of building blocks: TLS-MQTT to AWS IoT, length-prefixed
length-encoded payloads, BLE provisioning over Nordic chips, embedded webviews
calling Tuya or similar cloud platforms. Once a hobbyist has cracked one
vendor's framing, the same patterns let them crack the next vendor's faster.

This means the cost-per-protocol curve isn't just dropping for any single
person; it's dropping across the whole hobbyist community at once. Each open
client lowers the activation energy for the next one. The reverse engineer's
marginal hour gets cheaper faster than any individual vendor's defensive hour
gets cheaper. That's an asymmetry that compounds.

## What I think happens next

Three predictions, holding rough confidence.

Vendors in the high-volume consumer-IoT bottom-half will not respond rationally.
They will keep shipping closed APIs and watching hobbyists fork their
ecosystems within weeks of launch. The dumb ones will issue DMCA notices that
get ignored.

Vendors in the prosumer middle — smart-home gear above $200, semi-professional
A/V, mid-tier networking equipment — will gradually shift to "official APIs as
ecosystem moat." Publishing their protocol becomes a feature on the box. Some
already do this; expect more.

The cost of reverse engineering a typical consumer IoT device will keep
falling. The current ceiling — "a hobbyist with curiosity, a $40 router for
packet capture, and an LLM" — will sink to "a hobbyist with curiosity" as
captures become easier to feed in and tooling assembles itself around the
workflow. The activation energy approaches zero.

The interesting result is that the closed/open distinction stops being about
secrecy and starts being about *whose API surface gets to define the
integration*. The vendor either ships one or one gets reverse-engineered into
existence. Those are the only two options.

The smart lamp on my desk is a small data point. Most of the closed-API
ecosystem is going to learn this lesson the slow way.

---

*Project notes and the full decoded protocol live in this repo:
[D50_FORMAT.md](D50_FORMAT.md), [REVERSE_ENGINEERING.md](REVERSE_ENGINEERING.md).
The companion piece walking through the actual decoding work-by-work is at
[article.md](article.md).*
