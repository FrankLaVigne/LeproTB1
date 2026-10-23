"""listen_forever must keep retrying until MQTT is back, not stall after one failure."""

import asyncio
import json
from types import SimpleNamespace

import aiomqtt
import pytest

import lepro.client as client_mod
from lepro.client import LeproClient


class _FakeMqtt:
    """Stands in for aiomqtt.Client.

    Mirrors the real failure mode: a client whose __aenter__ failed never
    resolves its disconnect future, so iterating its messages blocks forever.
    """

    script: list = []  # per-instance behavior: "fail" | "drop" | "deliver"

    def __init__(self, **_kw):
        self._mode = _FakeMqtt.script.pop(0)
        self._entered = False

    async def __aenter__(self):
        if self._mode == "fail":
            raise OSError(-3, "Temporary failure in name resolution")
        self._entered = True
        return self

    async def __aexit__(self, *_exc):
        return None

    async def subscribe(self, _topic):
        return None

    @property
    def messages(self):
        return self._iter()

    async def _iter(self):
        if not self._entered:
            await asyncio.Event().wait()  # hangs forever, like real aiomqtt
        if self._mode == "drop":
            raise aiomqtt.MqttError("Disconnected during message iteration")
        yield SimpleNamespace(topic=SimpleNamespace(value="le/123/prp/rpt"),
                              payload=json.dumps({"d": {"d1": 1}}).encode())
        await asyncio.Event().wait()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(client_mod.aiomqtt, "Client", _FakeMqtt)
    c = LeproClient("a@example.com", "pw")
    c._mqtt_info = {"host": "broker", "port": 8883}
    return c


async def test_listen_forever_recovers_after_repeated_reconnect_failures(client):
    # Initial connection drops; the next two reconnects fail (DNS); third works.
    _FakeMqtt.script = ["drop", "fail", "fail", "deliver"]
    await client.connect_mqtt()

    got = asyncio.Event()

    async def on_update(did, d):
        got.set()

    task = asyncio.create_task(client.listen_forever(on_update=on_update, backoff=0))
    try:
        await asyncio.wait_for(got.wait(), timeout=2)
    finally:
        task.cancel()
    assert client.state["123"] == {"d1": 1}


async def test_failed_connect_does_not_leave_a_dead_client(client):
    _FakeMqtt.script = ["fail"]
    with pytest.raises(OSError):
        await client.connect_mqtt()
    with pytest.raises(client_mod.LeproError, match="not connected"):
        await client._publish("123", {"d1": 1})
