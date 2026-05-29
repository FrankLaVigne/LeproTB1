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

### Open: the DIY app exposes segments, not LEDs

The Lepro app's DIY screen presents the lamp as a smaller number of paintable
arcs per ring (visually ~15-20 outer, ~12 middle, ~9 inner). Per-LED control
(196 individually-addressable LEDs) likely exists at a deeper level in the
firmware (LightGPM AI uses it) but isn't exposed by the app. The
`F21000{G}{lengths}` format above could still encode 196 individual groups of
length 1 each — but the app won't let the user *paint* at that resolution.
Practical resolution from app captures: ring-segment level, which is plenty
for a clock face.

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
