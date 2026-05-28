import lepro


def test_speed_to_hex_zero_is_1000():
    assert lepro._speed_to_hex(0) == "1000"


def test_speed_to_hex_is_four_hex_chars():
    for s in (1, 25, 50, 100):
        h = lepro._speed_to_hex(s)
        assert len(h) == 4
        int(h, 16)  # parses as hex


def test_speed_to_hex_clamps_over_100():
    assert lepro._speed_to_hex(500) == lepro._speed_to_hex(100)


def test_effects_catalog_contains_known_names():
    for name in ("solid", "breath", "gradient", "clockwise", "flash", "wave_1", "laser_4"):
        assert name in lepro.EFFECTS
