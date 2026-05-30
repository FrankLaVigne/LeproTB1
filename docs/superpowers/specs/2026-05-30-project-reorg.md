# Project Reorganization — package layout

**Date:** 2026-05-30
**Status:** Approved by user via brainstorm (full package: lepro/ + cli/ + web/ + mcp/; `app.py` preserved as `web/legacy.py`; reorg before UI redesign).

## Goal

Move from 8 loose `.py` files at the project root to a proper Python package
layout, grouped by role:

- `lepro/` — the shared `LeproClient` library (used by everything else)
- `cli/` — terminal scripts (`cli`, `stock_lamp`)
- `web/` — the aiohttp UI (`server`, `ticker`, `clock`, `static/`, plus
  `legacy` for `app.py`)
- `mcp/` — the FastMCP server

Sets up the directory structure cleanly **before** the UI redesign lands, so
new files (`web/static/cockpit.css`, `web/static/cockpit.js`, etc.) drop into
the right place from day one.

## Approach

Full restructure with import path migration. No backward-compat shims — this
is a personal project; clean cut is better than carrying parallel paths.
Launch commands become `python -m <package>.<module>` (no install required;
no `pyproject.toml` for now). The `python -m` form works from any cwd because
the packages live at the repo root.

## New layout

```
lepro/
  __init__.py              # re-exports LeproClient, LeproError, load_config
  client.py                # was lepro.py (the LeproClient + helpers)
  client_key.pem           # MQTT static key (lives with the code that uses it)
cli/
  __init__.py
  main.py                  # was cli.py
  stock_lamp.py            # was stock_lamp.py
web/
  __init__.py
  server.py                # was workshop.py (the cockpit web server)
  ticker.py                # was ticker.py (TickerSession; only imported by server)
  clock.py                 # was clock.py (ClockSession; only imported by server)
  legacy.py                # was app.py (older single-page demo, kept for reference)
  static/                  # was static/ — lamp-utils.js etc.
mcp/
  __init__.py
  server.py                # was mcp_server.py
presets/                   # unchanged — preset library (user data)
docs/                      # unchanged
tests/                     # unchanged (but imports update)
certs/                     # unchanged (runtime-downloaded per-account TLS certs)
config.json                # unchanged (gitignored credentials)
README.md                  # unchanged path; updated content
pytest.ini                 # unchanged
requirements.txt           # unchanged
```

## Path migration (every move spelled out)

| Old | New |
|---|---|
| `lepro.py` | `lepro/client.py` |
| `client_key.pem` | `lepro/client_key.pem` |
| `cli.py` | `cli/main.py` |
| `stock_lamp.py` | `cli/stock_lamp.py` |
| `workshop.py` | `web/server.py` |
| `app.py` | `web/legacy.py` |
| `ticker.py` | `web/ticker.py` |
| `clock.py` | `web/clock.py` |
| `static/lamp-utils.js` | `web/static/lamp-utils.js` |
| `mcp_server.py` | `mcp/server.py` |

Each directory gets a one-line `__init__.py` (mostly empty, except for
`lepro/__init__.py` which re-exports the common names).

### `lepro/__init__.py`

```python
"""Lepro TB1 lamp-control package — cloud client + utilities."""

from .client import LeproClient, LeproError, load_config

__all__ = ["LeproClient", "LeproError", "load_config"]
```

This means downstream code can keep saying `from lepro import LeproClient`
even though the actual class lives in `lepro/client.py`.

## Import-path migration

### Inside the new package

- `cli/main.py`: `from lepro import LeproClient, load_config` ← unchanged thanks to the re-export.
- `cli/stock_lamp.py`: same.
- `web/server.py`: `from lepro import LeproClient, load_config, LeproError` (was `from lepro import ...` at root — keeps working).
- `web/server.py`: `from web import ticker as _ticker_mod` (was `import ticker as _ticker_mod`).
- `web/server.py`: `from web import clock as _clock_mod` (was `import clock as _clock_mod`).
- `web/ticker.py`: `from web.server import build_d50_from_leds` (was `from workshop import ...`) — local import inside the function, as today.
- `web/clock.py`: `from web.server import apply_lamp_rotation, build_d50_from_leds` (was `from workshop import ...`) — local import inside `_tick_once`, as today.
- `mcp/server.py`: `from lepro import LeproClient` (was unchanged at root).

### Tests

- `import workshop` → `from web import server as workshop` (keep the local alias so the tests' assertion text doesn't change everywhere).
- `import ticker` → `from web import ticker`.
- `import clock` → `from web import clock`.

Most test files have one `import workshop` near the top; renaming to `from
web import server as workshop` lets the rest of the file remain identical.

## Launch command changes

| Old | New |
|---|---|
| `python workshop.py` | `python -m web.server` |
| `python cli.py state` | `python -m cli.main state` |
| `python stock_lamp.py AAPL` | `python -m cli.stock_lamp AAPL` |
| `python mcp_server.py` | `python -m mcp.server` |
| `python app.py` | `python -m web.legacy` (rare; reference only) |

`pytest` works unchanged from the project root because pytest auto-discovers
the `tests/` directory.

### File-path resolution inside the code

A few files compute paths relative to themselves and need adjustment:

- `web/server.py` currently has `_HERE = Path(__file__).resolve().parent`.
  Today this resolves to the project root. After the move, it resolves to
  `web/`. Two consequences:
  - `_HERE / "static"` → still correct (static is now `web/static/`).
  - `_HERE / "presets"` (if it does this) needs to become
    `_HERE.parent / "presets"` because presets stays at root.

  Actually the existing code uses `_PRESETS_DIR = _HERE / "presets"`. After
  the move, this needs `_HERE.parent / "presets"`. Simple one-line fix.

- `lepro/client.py` loads `client_key.pem`. Today it's a sibling at root
  (`_HERE / "client_key.pem"`). After the move, it's a sibling in `lepro/`
  (`_HERE / "client_key.pem"`). Path expression is unchanged because the
  key moves with the code that loads it.

- `lepro/client.py` writes per-account certs to `certs/` at the project root.
  Today this is `_HERE / "certs"`. After the move, this becomes
  `_HERE.parent / "certs"`. One-line fix.

- `lepro/client.py` reads `config.json` at the project root. Today
  `_HERE / "config.json"`. After: `_HERE.parent / "config.json"`. One-line fix.

These three small path adjustments are the only runtime behaviour changes
beyond imports.

## Documentation changes

- `README.md`: update every command example (`python workshop.py` → `python
  -m web.server`, etc.) and the Files section so the layout reflects reality.
- `docs/REVERSE_ENGINEERING.md`, `docs/CALIBRATION.md`, etc.: leave as-is
  for the most part; they describe protocol, not file paths. Any specific
  `workshop.py` or `cli.py` reference gets updated to its new path.
- `docs/superpowers/specs/2026-05-30-web-ui-redesign.md`: revisit after the
  reorg lands; update all `workshop.py` → `web/server.py`, `static/*` →
  `web/static/*` mentions. This is a follow-up edit, not part of the reorg.

## Backwards compatibility

**None.** The old paths simply don't exist anymore. Any external script,
cron job, systemd unit, or shell alias that runs `python workshop.py`
breaks. The user is the only operator; they'll update their habits.

Git history preserves the file moves (we use `git mv` so blame stays intact).

## Testing

- `pytest -q` from the project root must pass after the reorg with zero
  changes to test logic (only the `import` line at the top of each test
  file changes).
- `python -c "import workshop"` no longer works; the equivalent smoke is
  `python -c "from web.server import build_app; build_app(); print('ok')"`.
- Manual smoke: `python -m web.server` and hit `/`, `/diy`, `/ticker`,
  `/state`, `/clock` — every page returns 200.
- `python -m cli.main state` — works (formerly `python cli.py state`).

## Deliberately deferred

- `pyproject.toml` + `pip install -e .` — would give us `lepro`,
  `lepro-web`, etc. as console scripts. Nice-to-have; the `python -m` form
  is fine for v1 and requires no install step. Add later if the project
  grows or moves to PyPI.
- Splitting `web/server.py` into smaller modules (now ~2000 lines and
  growing). The UI redesign will already shed ~1500 lines as the per-page
  duplicated chrome moves to the shell — wait for that to land before
  evaluating further splits.
- Renaming the project module from `lepro` to something more specific
  (`lepro-tb1` won't be the only model forever). Out of scope for this
  reorg.

## File-change summary

| Type | Count |
|---|---|
| Files moved (`git mv`) | 9 (`lepro.py`, `cli.py`, `stock_lamp.py`, `workshop.py`, `app.py`, `ticker.py`, `clock.py`, `mcp_server.py`, `static/lamp-utils.js`) |
| Files created | 5 (`lepro/__init__.py`, `cli/__init__.py`, `web/__init__.py`, `mcp/__init__.py`, plus `web/static/` being a new dir though just by virtue of the move) |
| Files modified | ~10 (each test file's one import line, README's command examples, `lepro/client.py`'s 2 path expressions, `web/server.py`'s 1 path expression) |
| Net code change | ~minimal — almost all churn is import-line edits and the path-relative-to-self adjustments |
