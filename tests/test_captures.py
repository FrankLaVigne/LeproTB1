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
