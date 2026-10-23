"""NYSE trading-day calendar — checked against NYSE's published holiday dates."""

from datetime import date, time

import pytest

from web import market_calendar as mc


def _d(*days):
    return {date(*d) for d in days}


def test_holidays_2025():
    assert mc.nyse_holidays(2025) == _d(
        (2025, 1, 1), (2025, 1, 20), (2025, 2, 17), (2025, 4, 18), (2025, 5, 26),
        (2025, 6, 19), (2025, 7, 4), (2025, 9, 1), (2025, 11, 27), (2025, 12, 25))


def test_holidays_2026():
    assert mc.nyse_holidays(2026) == _d(
        (2026, 1, 1), (2026, 1, 19), (2026, 2, 16), (2026, 4, 3), (2026, 5, 25),
        (2026, 6, 19), (2026, 7, 3), (2026, 9, 7), (2026, 11, 26), (2026, 12, 25))


def test_holidays_2027():
    assert mc.nyse_holidays(2027) == _d(
        (2027, 1, 1), (2027, 1, 18), (2027, 2, 15), (2027, 3, 26), (2027, 5, 31),
        (2027, 6, 18), (2027, 7, 5), (2027, 9, 6), (2027, 11, 25), (2027, 12, 24))


def test_new_year_on_saturday_not_observed():
    # 2022-01-01 was a Saturday; NYSE did not close on Friday 2021-12-31.
    assert date(2021, 12, 31) not in mc.nyse_holidays(2021)
    assert mc.is_trading_day(date(2021, 12, 31))


def test_weekends_not_trading():
    assert not mc.is_trading_day(date(2026, 10, 24))
    assert not mc.is_trading_day(date(2026, 10, 25))


def test_holiday_not_trading():
    assert not mc.is_trading_day(date(2026, 11, 26))


def test_regular_day_trading():
    assert mc.is_trading_day(date(2026, 10, 23))


@pytest.mark.parametrize("d", [date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24),
                               date(2026, 11, 27), date(2026, 12, 24)])
def test_early_closes(d):
    assert mc.close_time(d) == time(13, 0)


@pytest.mark.parametrize("d", [date(2026, 7, 2), date(2026, 10, 23)])
def test_regular_closes(d):
    assert mc.close_time(d) == time(16, 0)


def test_constants():
    assert mc.OPEN == time(9, 30)
    assert str(mc.ET) == "America/New_York"
