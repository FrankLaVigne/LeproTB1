"""Schedule settings + the pure phase timeline (web/schedule.py)."""

import json
from datetime import datetime, timezone

import pytest

from web import schedule as sch
from web.market_calendar import ET


def at(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=ET)


def S(**over):
    return {**sch.DEFAULTS, **over}


def phase(now, **over):
    return sch.phase_at(now, S(**over)).phase


# --- timeline -------------------------------------------------------------

@pytest.mark.parametrize("hms,expected", [
    ((9, 29, 0), "night"), ((9, 30, 5), "opening_bell"), ((9, 30, 20), "market"),
    ((15, 59, 0), "market"), ((16, 0, 10), "closing_bell"), ((16, 0, 20), "recap"),
    ((16, 15, 20), "evening"), ((22, 29, 0), "evening"), ((22, 30, 0), "night"),
])
def test_trading_day_timeline(hms, expected):
    assert phase(at(2026, 10, 23, *hms)) == expected


def test_trading_day_next_change():
    info = sch.phase_at(at(2026, 10, 23, 12, 0), S())
    assert info.trading_day is True
    assert info.since == at(2026, 10, 23, 9, 30, 20)
    assert (info.next_phase, info.next_change_at) == ("closing_bell", at(2026, 10, 23, 16, 0))


def test_bells_disabled():
    assert phase(at(2026, 10, 23, 9, 30, 1), bell_seconds=0) == "market"
    assert phase(at(2026, 10, 23, 16, 0, 1), bell_seconds=0) == "recap"


def test_recap_disabled():
    assert phase(at(2026, 10, 23, 16, 0, 20), recap_minutes=0) == "evening"


@pytest.mark.parametrize("hms,expected", [
    ((13, 0, 10), "closing_bell"), ((13, 0, 20), "recap"),
    ((13, 15, 20), "idle_off"), ((15, 59, 0), "idle_off"), ((16, 0, 0), "evening"),
])
def test_early_close_gap(hms, expected):
    assert phase(at(2026, 11, 27, *hms)) == expected


def test_weekend_evening_mode():
    info = sch.phase_at(at(2026, 10, 24, 12, 0), S())
    assert (info.phase, info.trading_day) == ("night", False)
    assert (info.next_phase, info.next_change_at) == ("evening", at(2026, 10, 24, 16, 0))
    assert phase(at(2026, 10, 24, 16, 0)) == "evening"


def test_weekend_off_mode():
    for h in (12, 18):
        info = sch.phase_at(at(2026, 10, 24, h, 0), S(non_trading_days="off"))
        assert info.phase == "night"
        assert (info.next_phase, info.next_change_at) == ("opening_bell", at(2026, 10, 26, 9, 30))


def test_holiday_like_weekend():
    assert phase(at(2026, 11, 26, 17, 0)) == "evening"


def test_lights_out_after_midnight():
    assert phase(at(2026, 10, 23, 23, 59), lights_out="00:30") == "evening"
    assert phase(at(2026, 10, 24, 0, 29), lights_out="00:30") == "evening"
    assert phase(at(2026, 10, 24, 0, 31), lights_out="00:30") == "night"


def test_next_change_spans_long_weekend():
    info = sch.phase_at(at(2026, 9, 4, 23, 0), S(non_trading_days="off"))
    assert info.phase == "night"
    assert info.next_change_at == at(2026, 9, 8, 9, 30)


def test_dst_day():
    # 2026-11-01 is the fall-back Sunday; Monday's open is still 09:30 wall clock.
    assert phase(at(2026, 11, 2, 9, 30, 1)) == "opening_bell"
    assert phase(at(2026, 11, 2, 9, 29, 59)) == "night"


def test_now_in_utc_is_converted():
    assert sch.phase_at(datetime(2026, 10, 23, 13, 31, tzinfo=timezone.utc), S()).phase == "market"


# --- validation -----------------------------------------------------------

@pytest.mark.parametrize("field,value", [
    ("interval", 15), ("symbol", ""), ("lights_out", "15:00"), ("lights_out", "25:00"),
    ("non_trading_days", "maybe"), ("bell_seconds", -1), ("evening_motions", "x"),
    ("evening_rotate_minutes", 0), ("enabled", "yes"), ("evening_start", "4pm"),
])
def test_validate_rejects(field, value):
    with pytest.raises(ValueError) as e:
        sch.validate(S(**{field: value}))
    assert str(e.value).startswith(field)


def test_validate_rejects_unknown_key():
    with pytest.raises(ValueError, match="^foo"):
        sch.validate(S(foo=1))


def test_validate_accepts_lights_out_before_open():
    assert sch.validate(S(lights_out="08:00"))["lights_out"] == "08:00"


def test_validate_normalizes_symbol_and_motion_list():
    out = sch.validate(S(symbol=" aapl ", evening_motions=["m1", "m2"]))
    assert out["symbol"] == "AAPL" and out["evening_motions"] == ["m1", "m2"]


def test_merge_applies_changes():
    assert sch.merge(S(), {"lights_out": "23:00"})["lights_out"] == "23:00"


# --- persistence ----------------------------------------------------------

def test_load_missing_seeds_from_env(tmp_path):
    s = sch.load_settings(tmp_path / "schedule.json",
                          {"LEPRO_TICKER_AUTOSTART": " aapl ", "LEPRO_TICKER_INTERVAL": "60"})
    assert (s["symbol"], s["interval"]) == ("AAPL", 60)
    assert s["lights_out"] == "22:30"


def test_save_load_roundtrip(tmp_path):
    p = tmp_path / "schedule.json"
    sch.save_settings(p, S(lights_out="23:15"))
    assert sch.load_settings(p, {})["lights_out"] == "23:15"
    assert json.loads(p.read_text())["lights_out"] == "23:15"


def test_load_corrupt_file_uses_defaults(tmp_path, caplog):
    p = tmp_path / "schedule.json"
    p.write_text("{not json")
    assert sch.load_settings(p, {}) == sch.DEFAULTS
    assert "schedule.json" in caplog.text


def test_load_unreadable_path_uses_defaults(tmp_path):
    p = tmp_path / "schedule.json"
    p.mkdir()  # IsADirectoryError on read
    assert sch.load_settings(p, {}) == sch.DEFAULTS
