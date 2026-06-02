"""Tests for web.captures — the capture-from-UI flow's pure helpers + session."""

import json
from datetime import datetime

import pytest

from web import captures


# --- dedup_consecutive ------------------------------------------------------


def test_dedup_consecutive_drops_adjacent_duplicates():
    # "A A B" -> "A B" — the second A is dropped because it's adjacent.
    assert captures.dedup_consecutive(["A", "A", "B"]) == ["A", "B"]


def test_dedup_consecutive_keeps_non_adjacent_duplicates():
    # "A B A" -> "A B A" — the third entry is the SAME as the first, but
    # B sits between them, so it's NOT a consecutive duplicate.
    # This matters because the Lepro AI cycles through frames in a multi-
    # frame preset and may revisit the same frame later in the sequence.
    assert captures.dedup_consecutive(["A", "B", "A"]) == ["A", "B", "A"]


def test_dedup_consecutive_empty():
    assert captures.dedup_consecutive([]) == []


def test_dedup_consecutive_single_entry():
    assert captures.dedup_consecutive(["X"]) == ["X"]


def test_dedup_consecutive_all_same():
    # "A A A A" -> "A".
    assert captures.dedup_consecutive(["A", "A", "A", "A"]) == ["A"]


# --- auto_capture_name ------------------------------------------------------


def test_auto_capture_name_first_use():
    now = datetime(2026, 5, 29, 14, 37, 22)  # seconds ignored
    name = captures.auto_capture_name(now, set())
    assert name == "capture-2026-05-29-1437-1"


def test_auto_capture_name_pads_single_digit_month_day_and_minute():
    now = datetime(2026, 1, 3, 7, 5, 0)
    name = captures.auto_capture_name(now, set())
    assert name == "capture-2026-01-03-0705-1"


def test_auto_capture_name_increments_on_collision():
    now = datetime(2026, 5, 29, 14, 37, 0)
    existing = {"capture-2026-05-29-1437-1"}
    name = captures.auto_capture_name(now, existing)
    assert name == "capture-2026-05-29-1437-2"


def test_auto_capture_name_walks_past_multiple_collisions():
    now = datetime(2026, 5, 29, 14, 37, 0)
    existing = {f"capture-2026-05-29-1437-{i}" for i in range(1, 6)}
    name = captures.auto_capture_name(now, existing)
    assert name == "capture-2026-05-29-1437-6"


def test_auto_capture_name_ignores_unrelated_existing_names():
    # Other names in the library shouldn't affect the counter.
    now = datetime(2026, 5, 29, 14, 37, 0)
    existing = {"snowfall", "tour-blue", "capture-2026-05-28-0900-3"}
    name = captures.auto_capture_name(now, existing)
    assert name == "capture-2026-05-29-1437-1"


# --- build_capture_preset ---------------------------------------------------


def test_build_capture_preset_single_frame_uses_payload_shape():
    frames = ["N01:P10001FFFFFFF21000100C4U3V3000640000E1;"]
    preset = captures.build_capture_preset(frames, name="my-capture")
    assert preset["name"] == "my-capture"
    assert "payload" in preset
    assert preset["payload"]["d50"] == frames[0]
    assert preset["payload"]["d1"] == 1
    assert preset["payload"]["d2"] == 2
    assert "frames" not in preset


def test_build_capture_preset_multi_frame_uses_frames_shape():
    frames = [
        "N01:P10001FFFFFFF21000100C4U3V3000640000E1;",
        "N01:P10001FF0000F21000100C4U3V3000640000E1;",
        "N01:P10001" + "00FF00" + "F21000100C4U3V3000640000E1;",
    ]
    preset = captures.build_capture_preset(frames, name="my-capture")
    assert preset["name"] == "my-capture"
    assert "frames" in preset
    assert len(preset["frames"]) == 3
    for i, f in enumerate(preset["frames"]):
        assert f["d50"] == frames[i]
        assert f["d1"] == 1
        assert f["d2"] == 2
    assert "payload" not in preset


def test_build_capture_preset_includes_captured_date_and_prompt():
    frames = ["N01:P10001FFFFFFF21000100C4U3V3000640000E1;"]
    preset = captures.build_capture_preset(frames, name="my-capture")
    assert "captured" in preset
    # ISO YYYY-MM-DD shape
    assert len(preset["captured"]) == 10 and preset["captured"][4] == "-"
    assert preset["prompt"] == "captured via UI"
    assert preset["description"].startswith("Captured")


def test_build_capture_preset_empty_frames_raises():
    with pytest.raises(ValueError):
        captures.build_capture_preset([], name="anything")


# --- CaptureSession state ---------------------------------------------------


def test_capture_session_initial_snapshot_not_running():
    sess = captures.CaptureSession(client=None, baseline_d50="N01:base;")
    snap = sess.snapshot()
    assert snap["running"] is False
    assert snap["started_at"] is None
    assert snap["frame_count"] == 0
    assert snap["auto_stop_at"] is None
    assert snap["default_name"] is None


def test_capture_session_frame_count_reflects_record_frame():
    sess = captures.CaptureSession(client=None, baseline_d50="X")
    sess.record_frame("first")
    sess.record_frame("second")
    assert sess.frame_count == 2
    assert sess.frames == ["first", "second"]


def test_capture_session_record_frame_dedups_adjacent():
    sess = captures.CaptureSession(client=None, baseline_d50="X")
    sess.record_frame("A")
    sess.record_frame("A")  # adjacent duplicate, dropped
    sess.record_frame("B")
    sess.record_frame("A")  # non-adjacent, kept
    assert sess.frames == ["A", "B", "A"]


def test_capture_session_record_frame_ignores_baseline():
    # If the lamp echoes the baseline d50 (because nothing has changed yet),
    # don't record it as a frame.
    sess = captures.CaptureSession(client=None, baseline_d50="BASE")
    sess.record_frame("BASE")
    sess.record_frame("BASE")
    assert sess.frames == []


def test_capture_session_record_frame_ignores_none_and_empty():
    sess = captures.CaptureSession(client=None, baseline_d50="X")
    sess.record_frame(None)
    sess.record_frame("")
    assert sess.frames == []


def test_capture_session_running_reflects_task_state():
    sess = captures.CaptureSession(client=None, baseline_d50=None)
    assert sess.running is False


# --- CaptureSession async loop ----------------------------------------------


import asyncio


class _FakeClient:
    """Lets us prime the lamp's reported state for capture tests."""

    def __init__(self, did: str = "abc"):
        self.state = {did: {"d50": None}}
        self.did = did

    def set_d50(self, d50):
        self.state[self.did]["d50"] = d50


@pytest.mark.asyncio
async def test_tick_once_records_new_d50():
    client = _FakeClient()
    sess = captures.CaptureSession(client=client, baseline_d50=None)
    client.set_d50("frame-a")
    sess._tick_once()
    assert sess.frames == ["frame-a"]


@pytest.mark.asyncio
async def test_tick_once_ignores_baseline():
    client = _FakeClient()
    sess = captures.CaptureSession(client=client, baseline_d50="base")
    client.set_d50("base")
    sess._tick_once()
    assert sess.frames == []


@pytest.mark.asyncio
async def test_tick_once_appends_only_distinct_values():
    client = _FakeClient()
    sess = captures.CaptureSession(client=client, baseline_d50=None)
    for d50 in ["a", "a", "b", "b", "c"]:
        client.set_d50(d50)
        sess._tick_once()
    assert sess.frames == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_start_then_stop_lifecycle():
    client = _FakeClient()
    sess = captures.CaptureSession(client=client, baseline_d50=None)
    assert sess.running is False
    await sess.start()
    assert sess.running is True
    await sess.stop()
    assert sess.running is False


@pytest.mark.asyncio
async def test_idle_timeout_fires_when_no_frames():
    # With a tiny idle_timeout we can verify auto-stop without sleeping forever.
    client = _FakeClient()
    sess = captures.CaptureSession(client=client, baseline_d50=None,
                                    idle_timeout=0.3, hard_cap=10.0)
    await sess.start()
    # Don't change client.set_d50 — no frames will be recorded.
    await asyncio.sleep(0.7)
    assert sess.running is False
    assert sess.frames == []


@pytest.mark.asyncio
async def test_hard_cap_fires_even_with_steady_frames():
    # Tiny hard cap + a frame stream that keeps the idle timer fresh.
    client = _FakeClient()
    sess = captures.CaptureSession(client=client, baseline_d50=None,
                                    idle_timeout=10.0, hard_cap=0.5)
    await sess.start()
    # Pump distinct frames so the idle-timeout WOULD never fire.
    for i in range(10):
        client.set_d50(f"frame-{i}")
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.3)
    assert sess.running is False


# --- HTTP layer -------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_captures_save_with_no_frames_returns_400(tmp_path, monkeypatch):
    """If a capture exists but has zero frames, save returns 400."""
    from web import server as workshop

    monkeypatch.setattr(workshop, "_PRESETS_DIR", tmp_path)
    monkeypatch.setattr(workshop, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(workshop, "_ANIMATIONS_OVERRIDES_PATH", tmp_path / "animations.json")

    sess = captures.CaptureSession(client=None, baseline_d50=None)
    # Don't start the loop — directly inject the session as if mid-capture.
    workshop._capture_session = sess

    class _Req:
        async def json(self):
            return {"name": "x"}

    try:
        resp = await workshop.api_captures_save(_Req())
        body = json.loads(resp.body.decode() if isinstance(resp.body, bytes) else resp.body)
        assert body["ok"] is False
        assert "no frames" in body["error"]
    finally:
        workshop._capture_session = None


@pytest.mark.asyncio
async def test_api_captures_save_writes_preset_and_reports_matched_animation(tmp_path, monkeypatch):
    """A capture with frames writes a file and surfaces matched_animation
    when the new preset's fingerprint matches an existing group."""
    from web import server as workshop

    monkeypatch.setattr(workshop, "_PRESETS_DIR", tmp_path)
    monkeypatch.setattr(workshop, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(workshop, "_ANIMATIONS_OVERRIDES_PATH", tmp_path / "animations.json")
    monkeypatch.setattr(workshop, "_MOTIONS_CATALOG_PATH", tmp_path / "motions.json")

    # Pre-populate an existing preset with a known d50 so the new save
    # fingerprints as a match.
    existing = {"name": "existing",
                "payload": {"d50": "N01:P10001FF0000F21000100C4U3V3000640000E1;"}}
    (tmp_path / "existing.json").write_text(json.dumps(existing))

    # Build a CaptureSession that has the same d50 in its frames.
    sess = captures.CaptureSession(client=None, baseline_d50=None)
    sess.record_frame("N01:P10001FF0000F21000100C4U3V3000640000E1;")
    workshop._capture_session = sess

    class _Req:
        async def json(self):
            return {"name": "newone"}

    try:
        resp = await workshop.api_captures_save(_Req())
        body = json.loads(resp.body.decode() if isinstance(resp.body, bytes) else resp.body)
        assert body["ok"] is True, body
        assert (tmp_path / "newone.json").exists()
        assert body["matched_animation"] is not None
        assert body["matched_animation"]["variant_count"] == 2
    finally:
        workshop._capture_session = None


@pytest.mark.asyncio
async def test_api_captures_cancel_is_idempotent(tmp_path, monkeypatch):
    """Cancel always returns ok, even with no active session."""
    from web import server as workshop
    workshop._capture_session = None  # ensure clean state
    resp = await workshop.api_captures_cancel(None)
    body = json.loads(resp.body.decode() if isinstance(resp.body, bytes) else resp.body)
    assert body["ok"] is True


def test_capture_session_snapshot_dodges_existing_default_names():
    """If presets/capture-X-1.json exists, the default_name should be -2."""
    existing = {"capture-2026-06-01-1432-1"}
    sess = captures.CaptureSession(
        client=None, baseline_d50=None,
        existing_names_provider=lambda: existing,
    )
    sess._started_at = datetime(2026, 6, 1, 14, 32, 0)
    # Add a frame so the snapshot's started_at branch is exercised.
    sess.record_frame("any-d50-value")
    snap = sess.snapshot()
    assert snap["default_name"] == "capture-2026-06-01-1432-2"


@pytest.mark.asyncio
async def test_api_captures_start_refuses_when_unsaved_frames_present(monkeypatch):
    """A stopped-but-unsaved session blocks a new start with a clear message."""
    from web import server as workshop

    # Inject a stopped session with frames into the module global.
    stale = captures.CaptureSession(client=None, baseline_d50=None)
    stale.record_frame("frame-a")  # frame_count = 1, but running = False
    workshop._capture_session = stale
    monkeypatch.setattr(workshop, "_ticker_session", None)
    monkeypatch.setattr(workshop, "_clock_session", None)
    monkeypatch.setattr(workshop, "_preview_task", None)

    try:
        resp = await workshop.api_captures_start(None)
        body = json.loads(resp.body.decode() if isinstance(resp.body, bytes) else resp.body)
        assert resp.status == 409
        assert body["ok"] is False
        assert "unsaved frames" in body["error"]
    finally:
        workshop._capture_session = None
