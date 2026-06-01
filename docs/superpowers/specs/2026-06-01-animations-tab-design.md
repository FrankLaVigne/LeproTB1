# Animations Tab — Design Spec

**Date:** 2026-06-01
**Status:** Approved via brainstorm (sibling tab to Presets, algorithmic dedup with manual override, v1 = view + recolor + save, capture deferred to v2).

## Goal

Add an **Animations** tab to the cockpit that shows the deduped catalog of
motion patterns derived from `presets/*.json`. Each row is one underlying
animation; clicking a row lets you pick new colors and save the result as a
new preset. The existing Presets tab stays as the "ready to play" library of
named color variants.

## Background

The Lepro app's AI generates multi-frame animations on each prompt. Looking
at our 6 captured presets, several almost certainly share the same underlying
motion (the user repeated captures across sessions, expecting palette
variation only). My naïve "strip palette, hash everything else" fingerprint
shows all 6 as unique because the AI introduces frame-level variation even
when the underlying motion is the same.

**User mental model: the user sees each preset as ONE discrete animation
in the Lepro app**, even though our capture reveals it's a multi-frame
sequence with internal variation. The Lepro AI does extra work behind the
scenes; we don't need to expose that as a primary axis. The Animations tab's
job is "tell me which of my captured animations are the same as each other
underneath the colors" — the cross-preset grouping. We surface per-preset
frame-stats as a small subtitle on each row (informational, not the
headline), and we lean on the manual-merge action as the escape hatch when
the algorithmic grouping inevitably misfires.

## Approach

A new file `web/animations.py` with pure dedup + fingerprint functions. New
routes on `web/server.py` for listing, renaming, and saving. A new
`_PANEL_ANIMATIONS` HTML constant + the 5th tab in the shell. Per-frame
fingerprint uses the first ~40 chars of the palette-stripped d50 (the
"structural intent" of that frame). Per-preset signature = join of frame
fingerprints, hashed. Presets with the same hash form one animation group.

Manual overrides go in `animations.json` at the project root (gitignored,
like `config.json`). Lets the user rename a group, or declare one group as
an alias of another (manual merge for false negatives).

## Pure functions

```python
# web/animations.py

def frame_fingerprint(d50: str) -> str:
    """First ~40 chars of the palette-stripped d50.

    Strips out P1000{N}{N×6 hex colors} → P1000{N}{C × N×6}. The rest
    (effect tail, group lengths, mode bytes) stays. Truncate to capture
    the 'structural intent' without late-frame randomness.
    """

def preset_signature(preset: dict) -> str:
    """One signature per preset. For multi-frame: join frame fingerprints
    with '|'. For single-frame: just that one fingerprint."""

def per_preset_frame_stats(preset: dict) -> dict:
    """Return {total: N, unique: K} — the number of distinct frame
    fingerprints in this preset's frame sequence. For single-frame
    presets always {total: 1, unique: 1}."""

def group_presets(presets_dir: Path) -> list[Animation]:
    """Scan every *.json in the dir, compute signatures, group by sig,
    return one Animation per group with:
      - id: the signature hash (8-char hex)
      - name: from overrides if present, else from the first preset's stem
      - members: [{preset_name, palette, frame_stats}, ...]
      - default_palette: palette of the first member
    """

def apply_overrides(groups: list[Animation], overrides: dict) -> list[Animation]:
    """Merge two groups into one if one's id has alias_of: <other_id> in
    overrides. Rename groups whose id has a 'name' override."""
```

The Animation dataclass:

```python
@dataclass
class Animation:
    id: str                          # 8-char hex signature hash
    name: str                        # display name (overridable)
    members: list[PresetMember]      # presets that map to this motion
    default_palette: list[str]       # 6-hex colors, from first member

@dataclass
class PresetMember:
    name: str                        # filename stem (e.g., "cyberpunk")
    palette: list[str]               # the colors this variant uses
    frame_stats: dict                # {total: 35, unique: 8}
```

## Routes

### `GET /animations`
Page route — returns the shell-wrapped `_PANEL_ANIMATIONS` HTML.

### `GET /api/animations`
```json
{
  "animations": [
    {
      "id": "a1b2c3d4",
      "name": "Tour",
      "members": [
        {"name": "purple-pink-tour", "palette": ["8000FF", "FF00FF"], "frame_stats": {"total": 35, "unique": 8}},
        {"name": "white-blue-tour",  "palette": ["FFFFFF", "0000FF"], "frame_stats": {"total": 35, "unique": 8}}
      ],
      "default_palette": ["8000FF", "FF00FF"]
    },
    ...
  ]
}
```

### `POST /api/animations/{id}/rename`
Body: `{"name": "Tour"}`. Writes to `animations.json` under
`{"<id>": {"name": "Tour"}}`. Returns updated animation.

### `POST /api/animations/{id}/merge_into/{other_id}`
Body: empty. Writes `{"<id>": {"alias_of": "<other_id>"}}` to
`animations.json`. The two groups become one in subsequent listings.

### `POST /api/animations/{id}/save`
Body: `{"name": "my-variant", "palette": ["FF0000", "00FF00"]}`.
- Recolors the animation's default preset by mapping each palette slot to the
  user's chosen color (we already have this code in the existing Presets
  preview/save path; reuse).
- Writes `presets/<name>.json` (same format as today's recolored saves).
- Returns `{"ok": true, "path": "presets/my-variant.json"}`.
- The new preset will appear on the Presets tab AND show up under its
  source animation's `members` list on the next /api/animations call.

### `POST /api/preview` (existing, reused)
The Play button in the Animations panel reuses the existing preview endpoint
by sending the source preset's d50. No new endpoint needed.

## Page layout

```
┌── 🎞 Animations ────────────────────────────────────────────────────┐
│                                                                     │
│  Tour                                          4 variants  ▶ Play   │
│  ⬤⬤⬤⬤  35 frames (8 unique)                                ✎ Edit   │
│                                                                     │
│   ▾ expanded view ▾                                                 │
│   Variants: purple-pink-tour, white-blue-tour, sunset-tour, ...     │
│   Pick colors:  [#] [#] [#] [#]                                     │
│   Name:  ___________________________                                │
│   [Save as new preset]    [Merge this into another animation]       │
│                                                                     │
│  Cyber-pulse                                   1 variant   ▶ Play   │
│  ⬤⬤⬤⬤⬤⬤  25 frames (~6 unique)                            ✎ Edit   │
│                                                                     │
│  Christmas-twist                               1 variant   ▶ Play   │
│  ⬤⬤  15 frames (~4 unique)                                 ✎ Edit   │
│                                                                     │
│  ...                                                                │
│                                                                     │
│ [+ Capture new from app  — CLI for v1: see README]                 │
└─────────────────────────────────────────────────────────────────────┘
```

- Each row: name + variant count + Play button. Below: tiny palette
  swatches showing the default palette + frame stats. Edit button (✎)
  toggles the expanded view.
- Expanded view: list of variant filenames, palette pickers, name input,
  Save button, Merge action.
- "Capture new from app" is a disabled-looking link or hint at the bottom
  pointing to the CLI command (v2 will make this a real button).

## Storage

### `animations.json` (new, gitignored)
```json
{
  "a1b2c3d4": {"name": "Tour"},
  "f5e4d3c2": {"name": "Cyber-pulse", "alias_of": "a1b2c3d4"}
}
```
- `name` overrides the auto-derived name.
- `alias_of` declares this group is the same as another (manual merge).
- Either or both fields optional.

### `presets/` (existing)
Unchanged. Saving from the Animations panel writes a new `presets/<name>.json`
in the same format as today's recolored saves. The Presets tab continues to
show every saved variant.

## Add `animations.json` to `.gitignore`

Mirror `config.json` — it contains user-curated metadata and shouldn't end
up in the public repo.

## Edge cases

- **Empty presets/ directory** — the panel shows "No animations yet. Capture
  some via `python -m cli.main capture --seconds 90` and they'll appear here."
- **Preset references an animation that's been merged** — the alias resolution
  happens at list-time; the source-of-truth is always the raw fingerprint hash.
- **Renaming to an existing animation's name** — allowed; names aren't unique.
  The hash is the identity.
- **Saving with a name that already exists in presets/** — return 409, same as
  today's `api_diy_save`.
- **Bad palette length** — color count must match the animation's palette
  size. If you submit fewer/more colors than the animation expects, 400.

## Testing

`tests/test_animations.py`:

- `test_frame_fingerprint_strips_palette` — verify the palette section becomes
  Cs and the rest is preserved.
- `test_frame_fingerprint_truncates_to_40` — output is exactly 40 chars (or
  less if the d50 itself is shorter).
- `test_preset_signature_single_frame` — single-frame preset has signature ==
  its one frame fingerprint.
- `test_preset_signature_multi_frame_joins_with_pipe` — N frames produce a
  pipe-joined string of N fingerprints.
- `test_per_preset_frame_stats_counts_unique` — a 5-frame preset with 3 distinct
  frame fingerprints returns `{total: 5, unique: 3}`.
- `test_group_presets_groups_by_signature` — two presets with same signature
  end up in one Animation; different signatures stay separate.
- `test_group_presets_default_palette_from_first_member` — the Animation's
  default_palette comes from the alphabetically-first member preset.
- `test_apply_overrides_merges_groups` — alias_of merges two groups into one
  in the output list.
- `test_apply_overrides_renames_group` — name override applies to the
  displayed Animation.

HTTP layer:
- `test_get_animations_returns_list` — fresh server returns the grouped data.
- `test_rename_persists_to_animations_json` — POST /rename writes the file.
- `test_save_creates_preset_with_correct_palette` — POST /save with custom
  palette produces a recolored preset file.

## Deliberately deferred (v2)

- **UI-based capture** — "Capture from app" button that starts a server-side
  capture session. ~80-150 LOC; nice but the CLI works.
- **Frame-level analysis on the Presets tab** — surface unique-frame counts
  in the Presets list too, not just Animations.
- **Cross-ring re-orchestration** — taking a 196-LED animation and adapting
  it to a smaller or larger ring topology.
- **Sequence chaining / playlists / schedules** — what I initially misread
  the ask as. Separate future feature.
- **Smarter fingerprinting** — e.g., longest-common-subsequence between
  frame sequences; semantic clustering. Try the simple per-frame-first-40
  approach first; iterate if real captures show too many false positives
  or negatives.

## File-change summary

| File | Change | Lines (est.) |
|---|---|---|
| `web/animations.py` (new) | Pure dedup/fingerprint/group/overrides | ~140 |
| `web/server.py` | 4 new routes + `_PANEL_ANIMATIONS` (HTML/JS) + 5th tab in shell | ~+320 |
| `web/static/cockpit.css` | Animation row + expanded-view styling | ~+40 |
| `tests/test_animations.py` (new) | Pure-function + HTTP tests | ~120 |
| `.gitignore` | Add `animations.json` | ~+1 |
| `README.md` | Mention the Animations tab in the cockpit section | ~+10 |

~630 LOC added; no removals.
