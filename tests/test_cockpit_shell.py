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
