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

from datetime import datetime


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
