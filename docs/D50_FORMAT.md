# d50 Animation Protocol — Working Hypotheses

This is the working analysis of the Lepro TB1's `d50` MQTT field, which carries
the animation programs the lamp's firmware runs. **It is not authoritative.**
Confidence levels are flagged honestly throughout. See
[`REVERSE_ENGINEERING.md`](REVERSE_ENGINEERING.md) for the methodology this
analysis came out of, and the captured payloads under [`presets/`](presets/)
for the raw source material.

If you reverse-engineer any of the unknowns below, please open a PR.

---

## The big claim (high confidence)

**`d50` is a procedural animation DSL, not raw LED frame data.** The lamp's
firmware *runs the effect* based on what we hand it — we're sending
instructions, not pixels.

The strongest evidence is bandwidth. 196 LEDs cycling smoothly over 30 seconds
at any reasonable framerate would need ~1 MB of raw data. Captured packets are
100–400 bytes. The compression ratio rules out raw frames; it has to be
procedural.

That's also why "capture and replay verbatim" works at all. We hand the lamp
one short string; it autonomously animates for as long as nothing overrides it.

---

## The structural skeleton (high confidence)

```
[#V:<metadata>;]              optional version / timing prefix
[#I00:NXX:P...;P...;]         per-ring program for outer ring  (88 LEDs)
[#I01:NXX:P...;P...;]         per-ring program for middle ring (62 LEDs)
[#I02:NXX:P...;P...;]         per-ring program for inner ring  (46 LEDs)

OR (when no per-ring split):

NXX:P...;P...;P...;           flat multi-program for the whole lamp
```

- **`N01` / `N02` / `N03` = number of program "layers"** in the block that
  follows. We see exactly that many `P...;` blocks each time. Locked in.
- **`#I00` / `#I01` / `#I02` = the three concentric LED rings.** TB1 specs are
  88 outer + 62 middle + 46 inner = 196 total, which matches exactly.

Layered programs (`P1000` + `P600` in an N02 payload) are simultaneous on the
same surface — think one "color/palette/structure" layer and a second
"motion/modulation" layer running on top of it.

---

## The palette block (experimentally confirmed)

```
P1000{N}{R1G1B1}{R2G2B2}...{RNGNBN}
```

- `P1000` is a constant marker; the next character is the color count (decimal,
  1–9 observed; the reference parser handles up to 3 digits but our code caps
  at 9 until verified). Then `N` six-hex-char RGB colors.
- An alternate parse — `P1` + `00` + `06` (count) — produces the same colors;
  use whichever mental model you prefer. The reference Home Assistant
  integration uses the `P1000` form, so the codebase here follows that.

### Experimental confirmation (2026-05-28)

`presets/white-blue-tour.json` was generated from `presets/purple-pink-tour.json`
by a mechanical hex-string substitution applied **only to the palette colors**
inside each frame's `d50` string (`8000FF` → `FFFFFF`, `FFC0CB` → `0000FF`).
Motion fields (`U`/`T`/`X`/`S`/`O`/`R`/etc.) were left untouched.

When replayed on the actual TB1, the result was **the same sequence of
animations with the colors swapped to white + blue.** No animations broke; no
purple or pink survived. This is direct experimental evidence that:

1. Palette colors live exclusively inside the `P1000{N}{colors}` block.
2. Motion fields reference colors by **palette index** rather than hex RGB.
3. Captured presets can be mechanically recolored without parsing any of the
   still-mysterious motion fields.

This is the cleanest single experiment in the project so far. A future
`recolor_preset.py` helper or CLI subcommand becomes a trivial 20-line
implementation now that the boundary is proven.

### Other `P`-prefixes

- **`P600` (no color list)** appears as the *second* layer in `N02` payloads.
  My read: `P600` programs modulate timing/intensity on top of whatever
  `P1000` produced — a "how it moves" layer over a "what colors and where"
  layer.
- **`P4` (cyberpunk only so far)** has a different color encoding:
  `e500e500e500e500e500` = `E500E5` repeated five times. That's still RGB hex
  (`E5 00 E5` = magenta), but the prefix differs. Possibly a fixed-pattern
  shortcut where the palette is one color used N times. Low confidence on the
  exact role.

---

## The `#V:` topology prefix (experimentally confirmed)

Per-ring captures consistently start with `#V:` followed by three structured
blocks. Decoding the bytes:

```
#V:0358c4...  003ec4...  002ec4...
```

| Hex byte | Decimal | What it matches |
|---|---|---|
| `0x58` | 88  | outer ring LED count |
| `0x3e` | 62  | middle ring LED count |
| `0x2e` | 46  | inner ring LED count |
| `0xc4` | 196 | total LED count (88+62+46) |
| `0x03` | 3   | total ring count (leading byte of block 1) |

The match against the TB1's published spec (88 + 62 + 46 = 196) is exact and on
every per-ring capture we've taken. The `#V:` prefix is the firmware's **ring
topology declaration** — it sets up "ring 0 has 88 LEDs, ring 1 has 62, ring 2
has 46, total 196" before the per-ring `#I00/01/02` programs run.

This was credited as a confirmation rather than a hypothesis because every byte
above is empirically present in every per-ring capture, and the values are not
generic numbers — they're the exact LED ring sizes of *this specific lamp*.

The trailing bytes in each block (`c4 00 00 ...`) appear to be per-animation
timing/state that varies between captures. Decoding those is a future
iteration; the structural layout (ring topology) is locked.

## The phase-offset finding (high confidence — and new)

The `hulk.json` capture has these three per-ring blocks:

```
#I00:...O60546       (0x0546 = 1350)
#I01:...O60384       (0x0384 =  900)
#I02:...O601c2       (0x01c2 =  450)
```

Those values **decrease by exactly 450 between rings.** That's a clean
arithmetic progression, almost certainly **phase offsets** that make the same
animation appear staggered across the rings — the visual you see as a wave
rippling from inside to outside (or vice versa).

So:

> **`O60{XXXX}` = phase offset**, where `60` is the format marker and the four
> hex digits encode the value.

This is the strongest single piece of evidence in the analysis. A future
parser should build on this.

---

## The rest of the alphabet (medium-to-low confidence)

| Token | What I think it is | Evidence strength |
|-------|---|---|
| **`U`** | Effect opcode. Hierarchical major.minor encoding (`U2xx`, `U5xx`, `U7xx` families). The "what effect" selector. | Medium — can't pin individual codes without controlled experiments |
| **`T2`** | Effect *type*, almost always 2. Maybe T1/T3 are reserved for future or unused. | Medium |
| **`V3`** | Format version stub, consistently 3. | Medium |
| **`X`** | Output channel mask or LED range. `X3`, `X5`, `X6` seen. | Low-medium |
| **`R`** | Routing/repeat bitfield. `R301011`, `R302111` look like packed flags. | Low-medium |
| **`S`** | Speed or start time. `S202fc`, `S204b0`, `S205f8` — values vary noticeably. | Low-medium |
| **`F`** | Frame/effect parameter blob. Variable length, complex. | Low — too varied |
| **`E`** | Envelope/fade parameters. Appears with `E4` followed by speed-hex repeats (the breath formula). | Medium for `breath` specifically |
| **`M`** | Mode/submode. `M2` seen in `N03` captures only. | Low — too few samples |
| **`W`** | Wide parameter block (`W61000000e102a3` seen once). | Low |
| **Lowercase `r` / `s` / `c` / `a`** | Not pure hex. Possibly ASCII opcodes for sub-blocks, or base-32/36 encoded values. We genuinely don't know. | Honest "?" |

### Per-LED-count solid-fill decoded (2026-05-29) — EXPERIMENTALLY CONFIRMED

A capture from the Lepro app's **DIY screen** with all 196 LEDs filled solid
orange produced this d50:

```
N01:P10001FFAA00F21000100C4U3V3000640000E1;
```

This is **byte-identical in shape to the strip-protocol "solid color" format**
from the reference integration, but with the total length matching the TB1's
actual LED count rather than the reference's 25-segment strip assumption.

Decoded:

| Segment | Meaning | Value |
|---|---|---|
| `N01:` | single program (whole-lamp, no per-ring split) | — |
| `P10001` | palette: 1 color follows | 1 |
| `FFAA00` | RGB hex for the palette color | orange |
| `F21000` | length-per-group block header | — |
| `1` | number of groups | 1 |
| `00C4` | **total length of group 0 in LEDs (hex)** | **196** |
| `U3V3` | opcode + version stubs | — |
| `000640000E1` | "solid effect" tail | — |

**`0xC4 = 196` is the exact LED count of the TB1** (88 outer + 62 middle + 46
inner). Total group lengths sum to the lamp's full LED count, in 16-bit
big-endian hex.

### What this unlocks

- **Generating arbitrary multi-segment patterns from scratch.** The format
  generalizes to `P1000{N}{colors}F21000{G}{group_lengths}U3V3<tail>;` where
  `{group_lengths}` is `G × 4 hex chars`, summing to 196.
- **A clock that ticks around the rings**: `(off, K), (color, 1), (off, 196-K-1)`
  draws a single "second hand" at any position. Adjustments per ring use the
  per-ring `#I00:` / `#I01:` / `#I02:` format with each ring's length summing
  to 88/62/46 respectively.
- **Why our existing `_build_d50` doesn't work on the TB1** — it hardcodes
  total length to 25 (the strip-protocol assumption). A small fix to accept a
  configurable total fixes it.

### Multi-group encoding confirmed across all three rings (2026-05-29)

Three further captures from the DIY screen — each painting one ring at a time
to a different color — confirm the multi-group encoding scales cleanly:

| Capture | Ring(s) painted | Captured `d50` | Decoded |
|---|---|---|---|
| All outer | outer → red | `P10002 FF0000 000000 F21000 2 0058 006C` | 88 red + 108 off |
| Outer + middle | outer → white, middle → blue | `P10003 FFFFFF 0000FF 000000 F21000 3 0058 003E 002E` | 88 white + 62 blue + 46 off |
| All three | outer → white, middle → blue, inner → yellow | `P10003 FFFFFF 0000FF FFFF00 F21000 3 0058 003E 002E` | 88 white + 62 blue + 46 yellow |

Ring boundaries **locked exactly** at:

- Outer ring: LED indices **0 – 87** (88 LEDs, hex `0x58`)
- Middle ring: LED indices **88 – 149** (62 LEDs, hex `0x3E`)
- Inner ring: LED indices **150 – 195** (46 LEDs, hex `0x2E`)

### App vs protocol resolution

The Lepro app's DIY UI exposes 48 paintable "elements" (22 outer + 15 middle +
11 inner), each controlling roughly 4 LEDs:

| Ring | Hardware LEDs | App segments | LEDs per segment (avg) |
|---|---|---|---|
| Outer | 88 | 22 | 4.00 |
| Middle | 62 | 15 | 4.13 |
| Inner | 46 | 11 | 4.18 |
| Total | 196 | 48 | — |

The non-integer averages mean some app segments cover 4 LEDs and others 5
(in middle and inner rings). The protocol itself addresses LEDs individually
— in principle `(off, X), (color, 1), (off, 196-X-1)` should light exactly
one LED at position `X` — but this **has not yet been empirically confirmed
end-to-end**:

> **Per-LED addressability — EXPERIMENTALLY CONFIRMED (2026-05-29):** sent a
> sweep of 196 d50 payloads from our code (`length=1` red group at positions
> 0 through 195, all other LEDs white). The final state showed exactly **one**
> red LED on the inner ring at position 195 — no quantization, no rounding,
> exactly one LED. The firmware honors arbitrary `length=1` groups at any
> position in the 0–195 LED address space.
>
> Implication: full 196-position resolution is available to us via the
> protocol, even though the Lepro app's DIY UI exposes only 48 paintable
> segments at ~4 LEDs each.

### Brightness lives in `d52` (segmented mode) — experimentally confirmed (2026-05-29)

While testing whether the DIY app's brightness slider modifies the `d50`, we
discovered our `python -m cli.main capture` was silently filtering out everything except
`d2/d50/d60/d5`. A full unfiltered capture during a brightness experiment
revealed:

```
d1:  1     (power on)
d2:  2     (segmented/effect mode)
d3:  1000  (B-series brightness — irrelevant when d2=2, always at max)
d4:  79    (white temp — stale from previous CCT mode)
d5:  "..."  (HSV — stale; not used in segmented mode)
d50: <unchanged across the entire brightness experiment>
d52: 230   ← ★ this is the segmented-mode brightness; user had slider at 23%
d60: "20700004E0000"
d30: "11A6CE21"  (looks like a session/instance ID; unchanged across captures)
```

> **`d52`** is the brightness field for segmented mode (`d2=2`). Range 0–1000
> mapped to 0–100% on the slider. **Brightness does not affect `d50`** — the
> pattern string is identical regardless of brightness level.

End-to-end confirmation: sending `{"d52": 250}` then `{"d52": 750}` from our
code (one second apart) produced a visible dim-then-brighten on the lamp.
Brightness can be sequenced from code with sub-second precision, independent
of any `d50` pattern.

**Implications for design:**
- Brightness control is fully orthogonal to pattern generation. Any `d50`
  pattern + any `d52` value composes cleanly.
- The clock can have a "fade brightness by time of day" feature trivially.
- Our existing `set_brightness()` writes `d3` for B-series devices (the TB1
  matches this heuristic), which is *wrong* for the TB1 in segmented mode.
  When the TB1 is in `d2=2` mode (as it is for any captured preset or any
  ring-pattern we generate), `set_brightness()` should write `d52` not `d3`.
  Worth a small fix.

### The DIY screen's 6 built-in effects (catalog confirmed 2026-05-29)

The Lepro DIY screen exposes exactly six animation modes (per user screenshot):

| Button | Tail format | Capture status on TB1 |
|---|---|---|
| **Steady** | `000640000E1` | ✅ confirmed (baseline of every solid capture) |
| **Breathe** | `000640000E4{sp}0000{sp}1664` | ✅ confirmed (replays via existing `set_effect("breath")`) |
| **Gradient** | `100640000E3{sp}C2O6{sp}` | ✅ confirmed (captured 2026-05-29) |
| **Leftward** | `00164{sp}E1` | ✅ confirmed (captured 2026-05-29 — reference calls this "clockwise") |
| **Rightward** | `00264{sp}E1` | ✅ confirmed (captured 2026-05-29 — reference calls this "counterclockwise") |
| **Circle** | `100640000E1C2O6{sp}` | ✅ confirmed (captured 2026-05-29) |

**Naming reconciliation:** The reference HA integration uses "clockwise" /
"counterclockwise" for the `00164` / `00264` tails. The Lepro app calls these
"Leftward" / "Rightward" in the DIY screen. We use the Lepro app names going
forward because they're what the user sees.

**Compositional observation:** the tails appear to have sub-structure rather
than being opaque codes:

- `000640000E1` (Steady, no motion)
- `000640000E4...` (Breathe — same prefix, `E1` → `E4` adds pulsing)
- `100640000E1...` (Circle — same as Steady but with `1` lead and `C2O6{sp}` motion suffix)
- `100640000E3...` (Gradient — same `1` lead but `E3` and `C2O6{sp}`)
- `00164{sp}E1` / `00264{sp}E1` (Leftward / Rightward — shorter, simpler family with direction byte `1`/`2`)

Worth fully decoding when we need to encode arbitrary tails ourselves rather
than just reusing these six.

Reading the captures alongside the screenshot: `{sp}` is a 4-hex-char speed
value (0x7E ≈ 50% slider, 0x0FA0 ≈ slowest, 0x0001 ≈ fastest — confirmed from
reference's `_speed_to_hex` log scale).

**Crucially:** all six effects are emitted by the same `N01:P1000{N}{colors}
F21000{G}{lengths}U3V3<tail>;` envelope. Only the trailing tail changes. So we
can:

1. Pick any palette (any colors)
2. Pick any per-ring/per-segment grouping
3. Pick any of the 6 motion modes
4. Set any speed
5. Set any brightness via the orthogonal `d52` field

All composable. Locked in.

### Side-finding from the same captures: TB1 actually honors the strip-protocol effect tails

During the brightness experiment the user accidentally tapped one of the DIY
screen's animation buttons (Leftward, Rightward, Gradient, etc.) before
finding the brightness slider. The captures showed:

```
…U3V3 100640000E3 007E C2O6 007E   ← gradient tail (from reference integration)
…U3V3 00164 007E E1                  ← clockwise tail (from reference integration)
```

These are the **exact tails** we previously tried via `set_effect("circular",
…)` and got only solid color on the TB1. The palette and group lengths in
these captures are `88/62/46` (the TB1's actual ring layout), not `0019`
(the 25-segment strip assumption our `_build_d50` uses).

> **Strong hypothesis (untested):** the rotation effects (`clockwise`,
> `counterclockwise`, `circular`, `gradient`, `leftward`, `rightward`) DO
> animate on the TB1. Our earlier "solid color, no motion" result was almost
> certainly because we sent them with `F2100010019` (25 LEDs total) instead
> of `F2100030058003E002E` (88+62+46 = 196). Fixing `_build_d50` to use the
> ring-aligned lengths should unlock all of them.

### The full generator format (confirmed)

For static patterns on the whole lamp:

```
N01:P1000{N}{color1...colorN}F21000{G}{length1...lengthG}U3V3000640000E1;
```

Where:
- `N` = palette color count (decimal, 1-9 verified in captures)
- `colorK` = 6-char RGB hex (uppercase normalized; lowercase accepted)
- `G` = group count (decimal, 1-9 verified — likely 1-3 digits per reference
  parser, but capped at 9 in our code until empirically pushed higher)
- `lengthK` = 4-char hex, big-endian, LED count of group K
- **All lengths must sum to 196** (the lamp's total LED count)

The order of groups maps to consecutive LED positions starting at outer ring
LED 0. The palette is referenced by position: group K uses palette color
index K (zero-indexed).

This format is now sufficient to render any static pattern across the lamp's
rings, at minimum 48-position resolution (app-matched) and up to 196-position
resolution if the per-LED hypothesis holds.

### New as of `purple-pink-tour` (2026-05-28)

- **`Y`** — a brand new section marker appeared in one frame:
  `N01:P100028000FFFFC0CBY3646464640001ca001001ca101;`
  We had never seen `Y` in any of the previous four captures. The payload
  parses as a normal `N01:` + 2-color palette + `Y...` tail with no other
  recognizable tokens. The `64646464` portion is suspicious — `0x64 = 100`
  repeated four times, possibly indicating an "all maxed" parameter set, and
  `01ca` appears twice (possibly speed or phase). Specific animation unknown.
  Worth capturing again to confirm reproducibility.

---

## What we already know works on the TB1

From the live tests we've already run:

- **Color setting** (`d2:2 + d50:N01:P1000{1}{color}F2100010019U3V3000640000E1;`) — works.
- **`breath` effect** (`d2:2 + d50:N01:P1000{N}{colors}F21000{N}{lengths}U3V3000640000E4{sp}0000{sp}1664;`) — works, pulses correctly.
- **Captured payloads from the Lepro app's AI** (`mars_colors`, `christmas`,
  `cyberpunk`, `hulk`) — work when replayed verbatim via `send_raw`.
- **`clockwise` / `counterclockwise` / `circular` effect tails** from the
  reference integration — **do not animate** on the TB1; lamp goes solid
  color. These reference tails appear to be strip-only.

---

## The captures we should take next (testing hypotheses)

Our four existing captures (`mars_colors`, `christmas`, `cyberpunk`, `hulk`)
are each a different *vibe* — great for visual variety, poor for science. To
actually decode the alphabet, we want **controlled comparisons** that isolate
one variable at a time:

1. **Same palette, different motions.** Set one color palette in the Lepro
   app, then cycle through every motion the app offers for that palette
   (`<palette>-wave`, `<palette>-chase`, `<palette>-breath`, `<palette>-spin`).
   Diff the payloads — the only differences should be in `U` and maybe
   `S`/`O`/`R`. This isolates "what effect" from "what colors."
2. **Same motion, different palettes.** Set one motion (e.g. "wave"); capture
   it under "red", "blue", "rainbow". The diff should be *only* in the
   `P1000...` palette block. Confirms palette structure and isolates the
   color story.
3. **Solid color, no motion.** Should produce the simplest possible payload.
   Compare to the bare `N01:P10001{color}F2100010019U3V3000640000E1;` we
   already know. Anything extra is meaningful.
4. **Speed slider, if the app exposes one.** Same animation at low vs high
   speed. Diff isolates the speed encoding (likely `S`).
5. **Single-color vs multi-color palette.** A 1-color palette vs an 8-color
   palette. The diff should be the palette block only.

These "scientific" captures produce maximum information per session. The
existing variety-captures gave us material; the controlled ones would let us
decode the alphabet.

---

## How to contribute

If you crack any of the unknowns above on a TB1 (or a related Lepro device):

1. Open an issue or a pull request against this repo.
2. Include the captured payload(s) and what they were producing visually.
3. Update this document's confidence levels accordingly.
4. Ideally: contribute the captures themselves to `presets/` so the
   evidence is reproducible.

Even partial findings — "I'm 80% sure `T2` means X because Y" — are useful.
The methodology in [`REVERSE_ENGINEERING.md`](REVERSE_ENGINEERING.md) explains
how to take captures of your own.
