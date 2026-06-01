"""Capture-from-UI flow for the Animations tab.

A free-form, one-click-per-capture model: user clicks Capture in the UI,
server starts polling the lamp's d50 field via MQTT (the listen task that
already populates ``_client.state[did]`` is running on the workshop server),
collecting distinct d50 values. Auto-stops on idle gap or hard cap.

See ``docs/superpowers/specs/2026-06-01-capture-ui-design.md`` for the
working model, the MQTT-session-fight risk (validated 2026-06-01), and the
rationale for the timing constants.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional


def dedup_consecutive(frames: list) -> list:
    """Drop consecutive duplicates from a frame list.

    Non-adjacent duplicates are preserved — the Lepro AI cycles through
    frames in multi-frame presets and may revisit the same frame later
    in the sequence. We only collapse a frame that is identical to the
    one IMMEDIATELY before it.
    """
    out: list = []
    for frame in frames:
        if out and out[-1] == frame:
            continue
        out.append(frame)
    return out


def auto_capture_name(now: datetime, existing_names: set[str]) -> str:
    """Default preset name for a capture: ``capture-YYYY-MM-DD-HHMM-N``.

    ``N`` is a 1-based tie-breaker that walks up until the candidate name
    is not in ``existing_names``. We bucket on the minute (no seconds),
    so back-to-back captures within the same minute collide and N
    increments; captures a minute apart get distinct base names and start
    at N=1 again.

    ``now`` is taken as a parameter (not computed via ``datetime.now()``
    inside) so tests can pin the clock.
    """
    base = now.strftime("capture-%Y-%m-%d-%H%M")
    n = 1
    while True:
        candidate = f"{base}-{n}"
        if candidate not in existing_names:
            return candidate
        n += 1


def build_capture_preset(frames: list, name: str) -> dict:
    """Assemble the preset JSON for a UI-captured animation.

    Single-frame captures get a ``payload`` key matching the existing
    DIY-save shape. Multi-frame captures get a ``frames`` list matching
    the existing Lepro-AI-capture shape. Both shapes are consumed by
    ``web/animations.py`` and the Presets tab without special-casing.
    """
    if not frames:
        raise ValueError("cannot build preset from zero frames")

    common = {
        "name": name,
        "description": f"Captured via the Animations tab UI on {date.today().isoformat()}.",
        "captured": date.today().isoformat(),
        "prompt": "captured via UI",
    }
    if len(frames) == 1:
        return {**common, "payload": {"d1": 1, "d2": 2, "d50": frames[0]}}
    return {**common, "frames": [{"d1": 1, "d2": 2, "d50": d} for d in frames]}


_IDLE_TIMEOUT_S = 6.0   # auto-stop after this many seconds without a new frame
_HARD_CAP_S = 90.0      # absolute cap regardless of activity


class CaptureSession:
    """One in-flight capture window: polls the lamp's d50 over MQTT,
    collects distinct frames, auto-stops on idle gap or hard cap.

    The polling loop lives in ``start()`` / ``_run()`` (added in Task 5).
    State + snapshot here are exercised by the routes synchronously.
    """

    def __init__(self, client, baseline_d50: Optional[str],
                 idle_timeout: float = _IDLE_TIMEOUT_S,
                 hard_cap: float = _HARD_CAP_S):
        self._client = client
        self._baseline_d50 = baseline_d50
        self._idle_timeout = idle_timeout
        self._hard_cap = hard_cap
        self._frames: list = []
        self._started_at: Optional[datetime] = None
        self._last_frame_at: Optional[datetime] = None
        self._task = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    @property
    def frames(self) -> list:
        return list(self._frames)

    def record_frame(self, d50: Optional[str]) -> None:
        """Record one polled d50. Drops baseline matches, adjacent dups,
        empty / None values. Updates the last-frame timestamp on accept."""
        if not d50:
            return
        if d50 == self._baseline_d50:
            return
        if self._frames and self._frames[-1] == d50:
            return
        self._frames.append(d50)
        self._last_frame_at = datetime.now()

    def snapshot(self) -> dict:
        """Return JSON-serialisable state for /api/captures/state."""
        auto_stop = None
        default_name = None
        if self._started_at is not None:
            hard_cap_at = self._started_at.timestamp() + self._hard_cap
            if self._last_frame_at is not None:
                idle_at = self._last_frame_at.timestamp() + self._idle_timeout
                auto_stop_ts = min(hard_cap_at, idle_at)
            else:
                auto_stop_ts = hard_cap_at
            auto_stop = datetime.fromtimestamp(auto_stop_ts).isoformat(timespec="seconds")
            default_name = auto_capture_name(self._started_at, existing_names=set())
        return {
            "running": self.running,
            "started_at": self._started_at.isoformat(timespec="seconds") if self._started_at else None,
            "frame_count": self.frame_count,
            "auto_stop_at": auto_stop,
            "default_name": default_name,
        }
