"""Lepro lamp TUI — a Textual terminal cockpit for the workshop server.

Run:
  .venv/bin/python -m cli.tui                          # localhost:8081
  .venv/bin/python -m cli.tui --server http://pi:8081

The TUI never speaks MQTT itself — it drives the lamp through the workshop's
HTTP API, exactly like the web cockpit does. Keys: p power, ↑/↓ brightness,
s stop, 1-8 fill, v rings/strips view, d raw d-fields, r refresh, q quit.

Design: docs/superpowers/specs/2026-06-02-lamp-tui-design.md
"""

from __future__ import annotations

import argparse
import os

from rich.color import Color
from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widgets import Footer, Static

from cli import tui_render
from cli.tui_api import LampApi

DEFAULT_SERVER = os.environ.get("LEPRO_WORKSHOP_URL", "http://localhost:8081")

# Fill palette for keys 1-8.
PALETTE = [
    ("Red", "FF0000"), ("Orange", "FFAA00"), ("Yellow", "FFFF00"),
    ("Green", "00FF00"), ("Cyan", "00FFFF"), ("Blue", "0000FF"),
    ("Purple", "8000FF"), ("White", "FFFFFF"),
]

# Canvas background behind/between the rings (matches the web's #0a0a14 vibe).
_BG = (10, 10, 20)

# Brightness factor applied to the viz when the lamp is powered off
# (the TUI's equivalent of the web cockpit's .viz-dimmed class).
_OFF_DIM = 0.25


def grid_to_halfblocks(grid) -> Text:
    """Render a pixel grid (rows of RGB tuples / None) as half-block Rich text.

    Each terminal cell shows two vertically stacked pixels: the upper pixel as
    the foreground color of "▀", the lower pixel as its background color.
    """
    text = Text(no_wrap=True)
    for y in range(0, len(grid) - 1, 2):
        if y > 0:
            text.append("\n")
        for top, bottom in zip(grid[y], grid[y + 1]):
            fg = top or _BG
            bg = bottom or _BG
            text.append("▀", Style(color=Color.from_rgb(*fg),
                                   bgcolor=Color.from_rgb(*bg)))
    return text


class LampViz(Static):
    """The lamp visualizer: concentric rings (default) or unrolled strips."""

    leds = reactive(None, always_update=True)
    view = reactive("rings")
    powered = reactive(True)

    def render(self):
        if self.view == "strips":
            return self._render_strips()
        return self._render_rings()

    def _dim(self) -> float:
        return 1.0 if self.powered else _OFF_DIM

    def _render_rings(self):
        w = self.size.width or 40
        h = self.size.height or 20
        # Square pixel canvas: each terminal row is 2 pixels tall, each column
        # 1 pixel wide.
        size = min(w, h * 2)
        if size < 16:
            return Text("(terminal too small — press v for strips view)")
        grid = tui_render.rings_grid(self.leds, size, dim=self._dim())
        return grid_to_halfblocks(grid)

    def _render_strips(self):
        text = Text()
        for name, colors in tui_render.strips_rows(self.leds, dim=self._dim()):
            text.append(f"{name:<7}({len(colors)})\n", Style(bold=True))
            for rgb in colors:
                text.append("█", Style(color=Color.from_rgb(*rgb)))
            text.append("\n\n")
        return text


class StatusBar(Static):
    """Top bar: power glyph, active-mode label, brightness; plus connection meta."""

    state = reactive(None, always_update=True)
    unreachable = reactive(False)
    server_url = ""

    def render(self):
        s = self.state or {}
        glyph = "⏻ On " if s.get("power") else "⏻ Off"
        label = (s.get("active") or {}).get("label") or "—"
        pct = s.get("brightness_pct")
        if pct is None:
            bright = "brightness —"
        else:
            filled = max(0, min(10, round(pct / 10)))
            bright = f"brightness {'█' * filled}{'░' * (10 - filled)} {pct}%"
        mode = s.get("lamp_mode") or "?"
        conn = ("⚠ server unreachable" if self.unreachable
                else f"{self.server_url} · mode: {mode}")

        text = Text()
        text.append(f" {glyph}   {label}   ", Style(bold=True))
        text.append(f"{bright}\n")
        text.append(f" {conn}   fill: ", Style(dim=True))
        for i, (_name, hexcolor) in enumerate(PALETTE):
            text.append(f"{i + 1}", Style(dim=True))
            text.append("■ ", Style(color=f"#{hexcolor}"))
        return text


class FieldsPanel(Static):
    """Raw d-fields diagnostics panel, toggled with `d`."""

    fields = reactive(None, always_update=True)

    def render(self):
        f = self.fields or {}
        text = Text()
        text.append("Raw d-fields\n\n", Style(bold=True))
        if not f:
            text.append("no state yet", Style(dim=True))
            return text

        def numeric_part(key: str) -> int:
            digits = "".join(ch for ch in key if ch.isdigit())
            return int(digits) if digits else 0

        for k in sorted(f, key=numeric_part):
            v = str(f[k])
            if len(v) > 40:
                v = v[:40] + "…"
            text.append(f"{k:<6}", Style(color="cyan"))
            text.append(f"{v}\n")
        return text


class LeproTUI(App):
    """Terminal cockpit for the Lepro TB1 lamp."""

    TITLE = "Lepro TUI"

    CSS = """
    StatusBar {
        height: 3;
        background: $panel;
        padding: 0 1;
    }
    Horizontal {
        height: 1fr;
    }
    LampViz {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
    }
    FieldsPanel {
        width: 50;
        height: 1fr;
        border-left: solid $primary;
        padding: 0 1;
        display: none;
    }
    FieldsPanel.visible {
        display: block;
    }
    """

    BINDINGS = [
        Binding("p", "toggle_power", "Power"),
        Binding("up", "brightness(5)", "Bright+", key_display="↑"),
        Binding("down", "brightness(-5)", "Bright-", key_display="↓"),
        Binding("s", "stop_all", "Stop"),
        Binding("v", "toggle_view", "View"),
        Binding("d", "toggle_fields", "Fields"),
        Binding("r", "refresh_now", "Refresh"),
        Binding("q", "quit", "Quit"),
    ] + [
        Binding(str(i + 1), f"fill({i})", PALETTE[i][0], show=False)
        for i in range(len(PALETTE))
    ]

    POLL_INTERVAL = 1.0
    BRIGHTNESS_DEBOUNCE = 0.2

    def __init__(self, api: LampApi, **kwargs):
        super().__init__(**kwargs)
        self.api = api
        self.lamp_state: dict = {}
        self._pending_brightness: int | None = None
        self._brightness_timer = None
        self._sent_brightness: int | None = None

    def compose(self) -> ComposeResult:
        yield StatusBar()
        with Horizontal():
            yield LampViz()
            yield FieldsPanel()
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one(StatusBar).server_url = self.api.base_url
        self.set_interval(self.POLL_INTERVAL, self.refresh_state)
        await self.refresh_state()

    async def on_unmount(self) -> None:
        # Close the aiohttp session inside the app's event loop.
        await self.api.close()

    # --- polling ---------------------------------------------------------------

    async def refresh_state(self) -> None:
        bar = self.query_one(StatusBar)
        try:
            state = await self.api.get_leds()
        except Exception:
            # Keep the last frame; just flag the connection (web does the same).
            bar.unreachable = True
            return
        bar.unreachable = False
        # Don't let a stale poll clobber an optimistic brightness that hasn't
        # been sent / echoed yet (mirrors cockpit.js pendingBrightness guard).
        if self._pending_brightness is not None:
            state = {**state, "brightness_pct": self.lamp_state.get("brightness_pct")}
        elif self._sent_brightness is not None:
            if state.get("brightness_pct") == self._sent_brightness:
                self._sent_brightness = None    # server caught up
            else:
                state = {**state, "brightness_pct": self._sent_brightness}
        self.lamp_state = state
        bar.state = state
        viz = self.query_one(LampViz)
        viz.powered = bool(state.get("power"))
        viz.leds = state.get("leds")
        self.query_one(FieldsPanel).fields = state.get("fields")

    def burst_refresh(self) -> None:
        """Poll a few times at sub-second intervals after a user action so the
        UI catches the lamp's echo without waiting for the next 1s tick.
        Mirrors cockpit.js burstRefresh()."""
        for delay in (0.25, 0.6, 1.2):
            self.set_timer(delay, self.refresh_state)

    # --- actions ---------------------------------------------------------------

    async def action_toggle_power(self) -> None:
        on = not bool(self.lamp_state.get("power"))
        # Optimistic update — reconciled by the burst poll.
        self.lamp_state["power"] = on
        self.query_one(LampViz).powered = on
        self.query_one(StatusBar).state = dict(self.lamp_state)
        result = await self.api.set_power(on)
        self._notify_if_error(result)
        self.burst_refresh()

    def action_brightness(self, delta: int) -> None:
        current = self._pending_brightness
        if current is None:
            current = self.lamp_state.get("brightness_pct")
        if current is None:
            current = 50
        self._pending_brightness = max(0, min(100, current + delta))
        # Optimistic UI.
        self.lamp_state["brightness_pct"] = self._pending_brightness
        self.query_one(StatusBar).state = dict(self.lamp_state)
        # Debounce the HTTP send: many fast keypresses → one POST.
        if self._brightness_timer is not None:
            self._brightness_timer.stop()
        self._brightness_timer = self.set_timer(
            self.BRIGHTNESS_DEBOUNCE, self._send_brightness)

    async def _send_brightness(self) -> None:
        pct, self._pending_brightness = self._pending_brightness, None
        self._brightness_timer = None
        if pct is None:
            return
        self._sent_brightness = pct          # guard the in-flight window
        result = await self.api.set_brightness(pct)
        if isinstance(result, dict) and result.get("ok") is False:
            # Rejected (mutex conflict / validation): the lamp will never echo
            # this value, so release the guard instead of freezing the display.
            self._sent_brightness = None
        self._notify_if_error(result)
        self.burst_refresh()

    async def action_stop_all(self) -> None:
        result = await self.api.stop_all()
        self._notify_if_error(result)
        self.burst_refresh()

    async def action_fill(self, idx: int) -> None:
        name, color = PALETTE[idx]
        result = await self.api.fill(color)
        if isinstance(result, dict) and result.get("ok"):
            self.notify(f"filled {name}", timeout=2)
        else:
            self._notify_if_error(result)
        self.burst_refresh()

    def action_toggle_view(self) -> None:
        viz = self.query_one(LampViz)
        viz.view = "strips" if viz.view == "rings" else "rings"

    def action_toggle_fields(self) -> None:
        self.query_one(FieldsPanel).toggle_class("visible")

    async def action_refresh_now(self) -> None:
        await self.refresh_state()

    # --- helpers ---------------------------------------------------------------

    def _notify_if_error(self, result) -> None:
        """Surface a workshop error (mutex conflict, validation) as a toast."""
        if isinstance(result, dict) and result.get("ok") is False:
            self.notify(result.get("error") or "request failed",
                        severity="warning", timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Lepro lamp TUI (Textual)")
    parser.add_argument("--server", default=DEFAULT_SERVER,
                        help=f"workshop base URL (default: {DEFAULT_SERVER})")
    args = parser.parse_args()
    LeproTUI(api=LampApi(args.server)).run()


if __name__ == "__main__":
    main()
