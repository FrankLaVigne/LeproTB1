"""Schedule screen for the lamp TUI — edits the workshop's daily schedule.

Opened with `c` from the main screen. Reads/writes the same /api/schedule
the web cockpit's Schedule tab uses, sending only the fields that changed.
Design: docs/superpowers/specs/2026-10-23-daily-schedule-design.md
"""

from __future__ import annotations

from datetime import datetime

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, SelectionList, Static, Switch

PHASE_NAMES = {
    "opening_bell": "opening bell", "market": "market", "closing_bell": "closing bell",
    "recap": "close recap", "idle_off": "off until evening", "evening": "evening",
    "night": "night",
}

_TEXT_FIELDS = ("symbol", "lights_out", "evening_start")
_INT_FIELDS = ("bell_seconds", "recap_minutes", "evening_rotate_minutes")


def next_change_text(schedule: dict | None) -> str:
    """'next: closing bell Tue 16:00' from a schedule summary, or ''."""
    if not schedule or not schedule.get("next_change_at"):
        return ""
    when = datetime.fromisoformat(schedule["next_change_at"])
    name = PHASE_NAMES.get(schedule.get("next_phase"), schedule.get("next_phase") or "?")
    return f"next: {name} {when:%a %H:%M}"


def _row(label: str, widget) -> Horizontal:
    return Horizontal(Label(label, classes="sched-label"), widget, classes="sched-row")


class ScheduleScreen(ModalScreen[bool]):
    """Modal form over every schedule setting. Dismisses True after a save."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    DEFAULT_CSS = """
    ScheduleScreen { align: center middle; }
    #sched-box { width: 80; max-height: 95%; border: round $accent; padding: 0 1;
                 background: $surface; }
    .sched-row { height: auto; margin: 0 0 1 0; }
    .sched-label { width: 30; padding: 1 1 0 0; }
    .sched-row Input, .sched-row Select { width: 1fr; }
    #evening_motions { height: 10; }
    #sched-buttons { height: auto; align: right middle; }
    #sched-status { margin: 1 0; }
    """

    def __init__(self, api, **kwargs):
        super().__init__(**kwargs)
        self.api = api
        self.loaded: dict = {}

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="sched-box"):
            yield Static("📅 Daily schedule", id="sched-title")
            yield Static("", id="sched-status")
            yield _row("Schedule on", Switch(id="enabled"))
            yield _row("Stock", Input(id="symbol"))
            yield _row("Check price every", Select(
                [("10 s", 10), ("30 s", 30), ("1 min", 60), ("5 min", 300)],
                id="interval", allow_blank=False))
            yield _row("Opening bell motion", Select([("Random", "random")], id="opening_motion", allow_blank=False))
            yield _row("Closing bell motion", Select([("Random", "random")], id="closing_motion", allow_blank=False))
            yield _row("Bell length (s, 0 = off)", Input(id="bell_seconds", type="integer"))
            yield _row("Close recap (min, 0 = off)", Input(id="recap_minutes", type="integer"))
            yield _row("New animation every (min)",
                       Input(id="evening_rotate_minutes", type="integer"))
            yield _row("Use all motions", Switch(id="all_motions"))
            yield SelectionList[str](id="evening_motions")
            yield _row("Lights out (HH:MM)", Input(id="lights_out"))
            yield _row("Weekends & holidays", Select(
                [("Evening animations", "evening"), ("Off all day", "off")],
                id="non_trading_days", allow_blank=False))
            yield _row("Evening starts (HH:MM)", Input(id="evening_start"))
            with Horizontal(id="sched-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="save", variant="success")

    async def on_mount(self) -> None:
        try:
            data = await self.api.get_schedule()
            motions = await self.api.get_motions()
        except Exception as e:  # noqa: BLE001
            self.app.notify(f"could not load schedule: {e}", severity="error")
            self.dismiss(False)
            return
        if "settings" not in data:
            self.app.notify(data.get("error") or "schedule unavailable", severity="error")
            self.dismiss(False)
            return
        self.loaded = data["settings"]
        self._fill(self.loaded, motions)
        status = data.get("status") or {}
        phase = PHASE_NAMES.get(status.get("phase"), status.get("phase") or "?")
        self.query_one("#sched-status", Static).update(
            f"now: {phase}   {next_change_text(status)}")

    def _fill(self, s: dict, motions: list[dict]) -> None:
        ordered = sorted(motions, key=lambda m: (not m.get("name"), m.get("name") or m["id"]))
        options = [("Random", "random")] + [(m.get("name") or m["id"], m["id"]) for m in ordered]
        ids = {m["id"] for m in motions}
        for field in ("opening_motion", "closing_motion"):
            sel = self.query_one(f"#{field}", Select)
            sel.set_options(options)
            sel.value = s[field] if s[field] in ids else "random"
        self.query_one("#enabled", Switch).value = bool(s["enabled"])
        for field in _TEXT_FIELDS + _INT_FIELDS:
            self.query_one(f"#{field}", Input).value = str(s[field])
        self.query_one("#interval", Select).value = s["interval"]
        self.query_one("#non_trading_days", Select).value = s["non_trading_days"]
        all_on = s["evening_motions"] == "all"
        self.query_one("#all_motions", Switch).value = all_on
        chosen = set() if all_on else set(s["evening_motions"])
        picker = self.query_one("#evening_motions", SelectionList)
        picker.add_options([(m.get("name") or m["id"], m["id"], m["id"] in chosen)
                            for m in ordered])
        picker.disabled = all_on

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "all_motions":
            self.query_one("#evening_motions", SelectionList).disabled = event.value

    def _read(self) -> dict:
        out: dict = {"enabled": self.query_one("#enabled", Switch).value}
        for field in _TEXT_FIELDS:
            out[field] = self.query_one(f"#{field}", Input).value.strip()
        for field in _INT_FIELDS:
            raw = self.query_one(f"#{field}", Input).value.strip()
            out[field] = int(raw) if raw.lstrip("-").isdigit() else raw  # server explains bad input
        for field in ("interval", "non_trading_days", "opening_motion", "closing_motion"):
            out[field] = self.query_one(f"#{field}", Select).value
        if self.query_one("#all_motions", Switch).value:
            out["evening_motions"] = "all"
        else:
            out["evening_motions"] = list(self.query_one("#evening_motions", SelectionList).selected)
        return out

    async def action_save(self) -> None:
        values = self._read()
        changes = {k: v for k, v in values.items() if self.loaded.get(k) != v}
        if not changes:
            self.dismiss(False)
            return
        if changes.get("evening_motions") == []:
            self.app.notify("pick at least one motion, or use all motions", severity="error")
            return
        result = await self.api.put_schedule(changes)
        if "settings" not in result:
            self.app.notify(result.get("error") or "save failed", severity="error")
            return
        self.app.notify("schedule saved")
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            await self.action_save()
        else:
            self.action_cancel()
