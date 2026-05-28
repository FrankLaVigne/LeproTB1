import mcp_server
import mcp_server as _mcp_server  # explicit alias so we can manipulate module globals


def test_guarded_tool_exposes_real_params():
    ts = {t.name: t for t in mcp_server.mcp._tool_manager.list_tools()}
    props = ts["set_color"].parameters["properties"]
    assert {"r", "g", "b"} <= props.keys()
    assert "kwargs" not in props


def test_all_expected_tools_present():
    names = {t.name for t in mcp_server.mcp._tool_manager.list_tools()}
    assert names == {
        "list_lights", "list_effects", "set_power", "set_brightness", "set_color",
        "set_white", "set_effect", "set_segments", "play_animation", "stop_animation",
        "get_state", "send_raw",
    }


def test_player_for_returns_same_instance_for_same_did():
    """Repeated _player_for calls reuse the per-device AnimationPlayer."""
    import lepro

    # Build a minimal LeproClient stand-in with one device
    class _FakeMQTT:
        async def publish(self, topic, payload):
            pass

    fake_client = lepro.LeproClient.__new__(lepro.LeproClient)
    fake_client._mqtt = _FakeMQTT()
    fake_client.devices = [lepro.Device(did="111", fid="f", name="lamp", series="TB1")]

    saved_client = _mcp_server._client
    saved_players = dict(_mcp_server._players)
    _mcp_server._client = fake_client
    _mcp_server._players.clear()
    try:
        p1 = _mcp_server._player_for(None)
        p2 = _mcp_server._player_for(None)
        p3 = _mcp_server._player_for("111")
        assert p1 is p2 is p3
    finally:
        _mcp_server._client = saved_client
        _mcp_server._players.clear()
        _mcp_server._players.update(saved_players)


def test_main_refuses_non_loopback_without_token(monkeypatch):
    """Fail-safe: binding 0.0.0.0 (or any non-loopback) without a token must exit."""
    import pytest
    monkeypatch.setattr(_mcp_server, "load_config", lambda: {
        "account": "a", "password": "b", "region": "na",
        "mcp_token": None, "mcp_host": "0.0.0.0", "mcp_port": 8765,
    })
    with pytest.raises(SystemExit):
        _mcp_server.main()


def test_main_allows_loopback_without_token(monkeypatch):
    """Loopback bind without a token is allowed (uvicorn.run is mocked out)."""
    called = {}
    monkeypatch.setattr(_mcp_server, "load_config", lambda: {
        "account": "a", "password": "b", "region": "na",
        "mcp_token": None, "mcp_host": "127.0.0.1", "mcp_port": 8765,
    })
    monkeypatch.setattr(_mcp_server.uvicorn, "run", lambda app, **kw: called.setdefault("ran", True))
    _mcp_server.main()
    assert called.get("ran") is True
