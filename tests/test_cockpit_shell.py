"""Tests for the cockpit shell renderer in web.server."""

from web import server as workshop


def test_render_shell_includes_panel_html():
    panel = '<div id="my-feature">hello</div>'
    out = workshop._render_shell(active="clock", panel_html=panel, title="Clock")
    assert panel in out


def test_render_shell_includes_page_title():
    out = workshop._render_shell(active="presets", panel_html="", title="Presets")
    assert "<title>Lepro &middot; Presets</title>" in out


def test_render_shell_marks_active_tab():
    out = workshop._render_shell(active="diy", panel_html="", title="DIY")
    # The DIY tab anchor should carry class="active"; others should not.
    assert 'href="/diy" class="active"' in out
    assert 'href="/" class="active"' not in out
    assert 'href="/ticker" class="active"' not in out
    assert 'href="/clock" class="active"' not in out


def test_render_shell_links_to_all_four_tabs():
    out = workshop._render_shell(active="presets", panel_html="", title="Presets")
    for href in ('href="/"', 'href="/diy"', 'href="/ticker"', 'href="/clock"'):
        assert href in out


def test_render_shell_does_not_link_to_state():
    # State page is absorbed into the left panel; no tab for it.
    out = workshop._render_shell(active="presets", panel_html="", title="Presets")
    assert 'href="/state"' not in out


def test_render_shell_loads_cockpit_assets():
    out = workshop._render_shell(active="presets", panel_html="", title="Presets")
    assert "/static/cockpit.css" in out
    assert "/static/cockpit.js" in out


def test_render_shell_contains_left_panel_structure():
    out = workshop._render_shell(active="presets", panel_html="", title="Presets")
    # Required hooks for cockpit.js to populate.
    for hook in ('id="lamp-viz"', 'id="active-banner"',
                 'id="brightness-slider"', 'id="brightness-val"',
                 'id="pwr-on"', 'id="pwr-off"', 'id="diag-body"'):
        assert hook in out, f"missing left-panel hook: {hook}"


# --- _stop_preview helper -----------------------------------------------------


import asyncio
import pytest


def test_stop_preview_when_no_task_does_nothing():
    # Helper must be safe to call when there's no preview running.
    workshop._preview_task = None
    workshop._preview_name = None
    asyncio.run(workshop._stop_preview())
    assert workshop._preview_task is None
    assert workshop._preview_name is None


# --- api_cockpit_active -------------------------------------------------------


@pytest.mark.asyncio
async def test_cockpit_active_off_when_d1_zero():
    workshop._client = type("C", (), {"state": {"abc": {"d1": 0}}})()
    workshop._ticker_session = None
    workshop._clock_session = None
    workshop._preview_task = None
    resp = await workshop.api_cockpit_active(None)
    import json
    body = json.loads(resp.body.decode("utf-8") if isinstance(resp.body, bytes) else resp.body)
    assert body["mode"] == "off"


@pytest.mark.asyncio
async def test_cockpit_active_idle_when_d1_on_and_no_session():
    workshop._client = type("C", (), {"state": {"abc": {"d1": 1}}})()
    workshop._ticker_session = None
    workshop._clock_session = None
    workshop._preview_task = None
    resp = await workshop.api_cockpit_active(None)
    import json
    body = json.loads(resp.body.decode("utf-8") if isinstance(resp.body, bytes) else resp.body)
    assert body["mode"] == "idle"
    assert "label" in body  # always non-null label for the banner


# --- /state redirect ----------------------------------------------------------


@pytest.mark.asyncio
async def test_state_route_redirects_to_root():
    from aiohttp import web as _web
    with pytest.raises(_web.HTTPFound) as exc:
        await workshop.index_state_redirect(None)
    assert exc.value.location == "/"


# --- preview auto-stop integration -------------------------------------------


@pytest.mark.asyncio
async def test_stop_preview_cancels_running_task():
    """Helper actually cancels a running task and clears the name."""
    async def _loop():
        try:
            while True:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise

    workshop._preview_task = asyncio.create_task(_loop())
    workshop._preview_name = "fake"
    # Give the task time to actually start.
    await asyncio.sleep(0)
    assert workshop._preview_task is not None
    assert workshop._preview_task.done() is False

    await workshop._stop_preview()
    assert workshop._preview_task is None
    assert workshop._preview_name is None


def test_render_shell_links_to_animations_tab():
    out = workshop._render_shell(active="animations", panel_html="", title="Animations")
    assert 'href="/animations"' in out
    assert 'href="/animations" class="active"' in out


def test_render_shell_animations_tab_present_on_other_pages():
    # Even when not active, the Animations link must appear on every page.
    out = workshop._render_shell(active="presets", panel_html="", title="Presets")
    assert 'href="/animations"' in out


def test_render_shell_links_to_motions_tab():
    out = workshop._render_shell(active="motions", panel_html="", title="Motions")
    assert 'href="/motions"' in out
    assert 'href="/motions" class="active"' in out


def test_render_shell_motions_tab_present_on_other_pages():
    # Even when not active, the Motions link must appear on every page.
    out = workshop._render_shell(active="presets", panel_html="", title="Presets")
    assert 'href="/motions"' in out


@pytest.mark.asyncio
async def test_motions_page_renders_real_panel():
    resp = await workshop.index_motions(None)
    text = resp.text if hasattr(resp, "text") and isinstance(resp.text, str) else resp.body.decode()
    assert "motion-grid" in text          # the real panel's grid container
    assert "Motions UI coming soon" not in text    # placeholder is gone
    assert "motion-rebuild" in text       # toolbar button present
