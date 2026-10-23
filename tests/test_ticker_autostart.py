"""Workshop server starts a configured single-stock ticker on boot."""

import asyncio

import pytest

from web import server


def test_autostart_config_reads_symbol_and_interval():
    env = {"LEPRO_TICKER_AUTOSTART": " ibm ", "LEPRO_TICKER_INTERVAL": "30"}
    assert server._ticker_autostart_config(env) == ("IBM", 30)


def test_autostart_config_interval_defaults_to_30():
    assert server._ticker_autostart_config({"LEPRO_TICKER_AUTOSTART": "IBM"}) == ("IBM", 30)


def test_autostart_config_disabled_when_unset_or_blank():
    assert server._ticker_autostart_config({}) is None
    assert server._ticker_autostart_config({"LEPRO_TICKER_AUTOSTART": "  "}) is None


@pytest.fixture
def no_session(monkeypatch):
    monkeypatch.setattr(server, "_ticker_session", None)


async def test_autostart_retries_until_first_price_fetch_succeeds(monkeypatch, no_session):
    # Network is down at boot: the first two fetches fail, the third works.
    prices = [None, None, 253.43]
    monkeypatch.setattr(server._ticker_mod, "fetch_price", lambda sym: prices.pop(0))
    started = []

    async def fake_start(self):  # don't spawn the real polling loop
        started.append(self.snapshot())

    monkeypatch.setattr(server._ticker_mod.TickerSession, "start", fake_start)

    await asyncio.wait_for(server._autostart_ticker("IBM", 30, retry_delay=0), timeout=2)

    assert len(started) == 1
    snap = started[0]
    assert (snap["interval"], snap["whole_lamp"]) == (30, True)
    assert snap["rings"]["outer"]["symbol"] == "IBM"
    assert snap["rings"]["outer"]["prev_price"] == 253.43
    assert server._ticker_session is not None
