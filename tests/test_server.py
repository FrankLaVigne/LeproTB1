import mcp_server


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
