"""Tests for cli.tui_render — pure pixel math for the TUI visualizer.

The math tests use a 181×181 canvas (center exactly at (90, 90), radius 90.5)
so expected LED indices can be computed by hand.
"""

from cli import tui_render


SIZE = 181  # center (90, 90)


# --- led_index_at ---------------------------------------------------------------


def test_center_pixel_is_background():
    assert tui_render.led_index_at(90, 90, SIZE) is None


def test_corner_pixel_is_background():
    assert tui_render.led_index_at(0, 0, SIZE) is None


def test_top_of_outer_ring_is_led_0():
    # (90, 0): straight up from center, radius ~0.99 → outer band, angle 0.
    assert tui_render.led_index_at(90, 0, SIZE) == 0


def test_right_of_outer_ring_is_quarter_way():
    # (180, 90): 3 o'clock → 1/4 around the 88-LED outer ring = index 22.
    assert tui_render.led_index_at(180, 90, SIZE) == 22


def test_bottom_of_outer_ring_is_halfway():
    # (90, 180): 6 o'clock → index 44.
    assert tui_render.led_index_at(90, 180, SIZE) == 44


def test_left_of_outer_ring_is_three_quarters():
    # (0, 90): 9 o'clock → index 66.
    assert tui_render.led_index_at(0, 90, SIZE) == 66


def test_top_of_middle_ring_is_led_88():
    # (90, 36): radius 54/90.5 ≈ 0.60, inside the middle band (0.50–0.69).
    assert tui_render.led_index_at(90, 36, SIZE) == 88


def test_top_of_inner_ring_is_led_150():
    # (90, 56): radius 34/90.5 ≈ 0.38, inside the inner band (0.28–0.47).
    assert tui_render.led_index_at(90, 56, SIZE) == 150


def test_gap_between_inner_and_middle_is_background():
    # (90, 46): radius 44/90.5 ≈ 0.49 — in the gap between bands.
    assert tui_render.led_index_at(90, 46, SIZE) is None


# --- rings_grid -----------------------------------------------------------------


def test_rings_grid_shape():
    grid = tui_render.rings_grid(None, 21)
    assert len(grid) == 21
    assert all(len(row) == 21 for row in grid)


def test_rings_grid_none_leds_renders_dark_bands():
    grid = tui_render.rings_grid(None, SIZE)
    assert grid[0][90] == tui_render.DARK      # top of outer ring: present but dark
    assert grid[90][90] is None                # center: background


def test_rings_grid_colors_come_from_page_space_leds():
    leds = ["FF0000"] * 88 + ["00FF00"] * 62 + ["0000FF"] * 46
    grid = tui_render.rings_grid(leds, SIZE)
    assert grid[0][90] == (255, 0, 0)      # outer top
    assert grid[36][90] == (0, 255, 0)     # middle top
    assert grid[56][90] == (0, 0, 255)     # inner top


def test_rings_grid_dim_scales_colors():
    leds = ["FF0000"] * 196
    grid = tui_render.rings_grid(leds, SIZE, dim=0.25)
    assert grid[0][90] == (64, 0, 0)


def test_rings_grid_black_leds_render_dark():
    leds = ["000000"] * 196
    grid = tui_render.rings_grid(leds, SIZE)
    assert grid[0][90] == tui_render.DARK


# --- strips_rows -----------------------------------------------------------------


def test_strips_rows_ring_names_and_lengths():
    rows = tui_render.strips_rows(None)
    assert [(name, len(colors)) for name, colors in rows] == [
        ("outer", 88), ("middle", 62), ("inner", 46)]


def test_strips_rows_none_leds_are_dark():
    rows = tui_render.strips_rows(None)
    for _name, colors in rows:
        assert all(c == tui_render.DARK for c in colors)


def test_strips_rows_maps_page_space_rings():
    leds = ["FF0000"] * 88 + ["00FF00"] * 62 + ["0000FF"] * 46
    rows = dict(tui_render.strips_rows(leds))
    assert rows["outer"] == [(255, 0, 0)] * 88
    assert rows["middle"] == [(0, 255, 0)] * 62
    assert rows["inner"] == [(0, 0, 255)] * 46


def test_strips_rows_dim():
    leds = ["FF0000"] * 196
    rows = dict(tui_render.strips_rows(leds, dim=0.25))
    assert rows["outer"][0] == (64, 0, 0)
