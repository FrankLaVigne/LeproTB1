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
