"""Tests for cli.tui — the Textual app, run headless via Textual's Pilot.

The app takes an injected api object; these tests use StubApi (records calls,
returns canned state) so no server or lamp is needed.
"""

import pytest

from cli.tui import FieldsPanel, LampViz, LeproTUI, PALETTE, StatusBar


def make_state(**overrides):
    state = {
        "power": True,
        "brightness_pct": 80,
        "lamp_mode": "segmented",
        "active": {"mode": "idle", "label": "✨ Idle"},
        "leds": ["FF0000"] * 196,
        "fields": {"d1": 1, "d2": 2, "d52": 800},
        "polled_at": "2026-06-02T00:00:00+00:00",
    }
    state.update(overrides)
    return state


class StubApi:
    """Records calls; returns canned /api/lamp/leds responses."""

    base_url = "http://stub:8081"

    def __init__(self, state=None):
        self.calls = []
        self.state = state or make_state()

    async def get_leds(self):
        self.calls.append(("get_leds",))
        return dict(self.state)  # copy so optimistic lamp_state mutations don't alias the stub

    async def set_power(self, on):
        self.calls.append(("set_power", on))
        return {"ok": True}

    async def set_brightness(self, pct):
        self.calls.append(("set_brightness", pct))
        return {"ok": True}

    async def stop_all(self):
        self.calls.append(("stop_all",))
        return {"ok": True}

    async def fill(self, color):
        self.calls.append(("fill", color))
        return {"ok": True}

    async def close(self):
        self.calls.append(("close",))


class UnreachableApi(StubApi):
    async def get_leds(self):
        self.calls.append(("get_leds",))
        raise ConnectionError("server unreachable")


@pytest.mark.asyncio
async def test_app_polls_on_mount():
    api = StubApi()
    async with LeproTUI(api=api).run_test() as pilot:
        await pilot.pause()
        assert ("get_leds",) in api.calls


@pytest.mark.asyncio
async def test_state_reaches_widgets():
    api = StubApi()
    app = LeproTUI(api=api)
    async with app.run_test() as pilot:
        await pilot.pause()
        viz = app.query_one(LampViz)
        assert viz.leds == ["FF0000"] * 196
        assert viz.powered is True
        assert app.query_one(StatusBar).state["brightness_pct"] == 80


@pytest.mark.asyncio
async def test_p_toggles_power_off_when_on():
    api = StubApi()  # canned state has power=True
    async with LeproTUI(api=api).run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        assert ("set_power", False) in api.calls


@pytest.mark.asyncio
async def test_p_toggles_power_on_when_off():
    api = StubApi(state=make_state(power=False))
    async with LeproTUI(api=api).run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        assert ("set_power", True) in api.calls


@pytest.mark.asyncio
async def test_brightness_presses_debounce_to_one_post():
    api = StubApi()  # brightness_pct = 80
    app = LeproTUI(api=api)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("up", "up", "up")
        # Wait past the debounce window so the single POST fires.
        await pilot.pause(app.BRIGHTNESS_DEBOUNCE + 0.3)
        sends = [c for c in api.calls if c[0] == "set_brightness"]
        assert sends == [("set_brightness", 95)]   # 80 + 5 + 5 + 5


@pytest.mark.asyncio
async def test_brightness_clamps_to_100():
    api = StubApi(state=make_state(brightness_pct=98))
    app = LeproTUI(api=api)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("up", "up")
        await pilot.pause(app.BRIGHTNESS_DEBOUNCE + 0.3)
        sends = [c for c in api.calls if c[0] == "set_brightness"]
        assert sends == [("set_brightness", 100)]


@pytest.mark.asyncio
async def test_s_stops_everything():
    api = StubApi()
    async with LeproTUI(api=api).run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert ("stop_all",) in api.calls


@pytest.mark.asyncio
async def test_number_keys_fill_palette_colors():
    api = StubApi()
    async with LeproTUI(api=api).run_test() as pilot:
        await pilot.pause()
        await pilot.press("1")
        await pilot.pause()
        assert ("fill", PALETTE[0][1]) in api.calls   # "1" → first palette color


@pytest.mark.asyncio
async def test_v_toggles_view():
    api = StubApi()
    app = LeproTUI(api=api)
    async with app.run_test() as pilot:
        await pilot.pause()
        viz = app.query_one(LampViz)
        assert viz.view == "rings"
        await pilot.press("v")
        assert viz.view == "strips"
        await pilot.press("v")
        assert viz.view == "rings"


@pytest.mark.asyncio
async def test_d_toggles_fields_panel():
    api = StubApi()
    app = LeproTUI(api=api)
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(FieldsPanel)
        assert not panel.has_class("visible")
        await pilot.press("d")
        assert panel.has_class("visible")
        await pilot.press("d")
        assert not panel.has_class("visible")


@pytest.mark.asyncio
async def test_unreachable_server_sets_flag_not_crash():
    api = UnreachableApi()
    app = LeproTUI(api=api)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(StatusBar).unreachable is True


@pytest.mark.asyncio
async def test_api_closed_on_exit():
    api = StubApi()
    async with LeproTUI(api=api).run_test() as pilot:
        await pilot.pause()
    assert ("close",) in api.calls


@pytest.mark.asyncio
async def test_poll_does_not_clobber_pending_brightness():
    """While a debounced brightness send is pending, polls keep the optimistic value."""
    api = StubApi()  # brightness_pct = 80
    app = LeproTUI(api=api)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("up")                  # optimistic 85, debounce pending
        await app.refresh_state()                # poll returns stale 80
        bar = app.query_one(StatusBar)
        assert bar.state["brightness_pct"] == 85  # optimistic value preserved
        # let the debounce fire so the test exits cleanly
        await pilot.pause(app.BRIGHTNESS_DEBOUNCE + 0.3)


@pytest.mark.asyncio
async def test_poll_does_not_clobber_sent_brightness_until_echoed():
    """After the POST fires but before the lamp echoes, polls keep the sent value."""
    api = StubApi()  # brightness_pct = 80
    app = LeproTUI(api=api)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("up")                  # optimistic 85
        await pilot.pause(app.BRIGHTNESS_DEBOUNCE + 0.3)   # debounce fires, POST sent
        assert ("set_brightness", 85) in api.calls
        await app.refresh_state()                # poll still returns stale 80
        bar = app.query_one(StatusBar)
        assert bar.state["brightness_pct"] == 85  # sent value preserved
        # now the "lamp" catches up
        api.state = make_state(brightness_pct=85)
        await app.refresh_state()
        assert app.query_one(StatusBar).state["brightness_pct"] == 85
        # and a later, different server value is accepted again (guard released)
        api.state = make_state(brightness_pct=30)
        await app.refresh_state()
        assert app.query_one(StatusBar).state["brightness_pct"] == 30


@pytest.mark.asyncio
async def test_rejected_brightness_post_releases_guard():
    """A 409/400-rejected brightness POST must not freeze the displayed value."""

    class RejectingApi(StubApi):
        async def set_brightness(self, pct):
            self.calls.append(("set_brightness", pct))
            return {"ok": False, "error": "stock ticker is running"}

    api = RejectingApi()  # brightness_pct = 80
    app = LeproTUI(api=api)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("up")                              # optimistic 85
        await pilot.pause(app.BRIGHTNESS_DEBOUNCE + 0.3)     # POST fires, rejected
        assert ("set_brightness", 85) in api.calls
        # Server still reports the real value (80); the display must accept it.
        await app.refresh_state()
        assert app.query_one(StatusBar).state["brightness_pct"] == 80
