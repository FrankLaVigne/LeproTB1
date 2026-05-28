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


def test_build_d50_single_color_25_segments():
    d50 = lepro._build_d50([(255, 0, 0)] * 25, "solid")
    # one group, 25 segments (0x0019), red FF0000, solid tail
    assert d50 == "N01:P10001FF0000F2100010019U3V3000640000E1;"


def test_build_d50_two_groups():
    colors = [(255, 0, 0)] * 10 + [(0, 0, 255)] * 15
    d50 = lepro._build_d50(colors, "solid")
    assert d50.startswith("N01:P10002FF00000000FF")  # 2 groups, red then blue
    assert "F21000200" in d50  # 2 groups in the lengths block


def test_build_effect_payload_special_uses_d60():
    d = lepro._build_effect_payload("flash", speed=50)
    assert d["d1"] == 1 and d["d2"] == 3
    assert d["d60"].startswith("2000064")
    assert len(d["d60"]) == 13  # 7 prefix + 2 sens + 4 zeros


def test_build_effect_payload_d50_effect():
    d = lepro._build_effect_payload("breath", speed=50, color=(0, 255, 0))
    assert d["d1"] == 1 and d["d2"] == 2
    assert d["d50"].startswith("N01:P10001" + "00FF00")
    assert "E4" in d["d50"]  # breath marker


def test_build_effect_payload_brightness_optional():
    assert "d52" not in lepro._build_effect_payload("flash")
    assert lepro._build_effect_payload("flash", pct=50)["d52"] == 500


def test_build_effect_payload_unknown_raises():
    import pytest
    with pytest.raises(ValueError):
        lepro._build_effect_payload("nope")


import pytest


class _FakeMQTT:
    def __init__(self):
        self.published = []

    async def publish(self, topic, payload):
        self.published.append((topic, payload))


def _client_with_fake_mqtt():
    c = lepro.LeproClient.__new__(lepro.LeproClient)  # bypass __init__/network
    c._mqtt = _FakeMQTT()
    c.devices = [lepro.Device(did="111", fid="f", name="lamp", series="TB1")]
    return c


@pytest.mark.asyncio
async def test_set_effect_publishes_to_set_topic():
    import json
    c = _client_with_fake_mqtt()
    await c.set_effect("flash", speed=70)
    topic, payload = c._mqtt.published[-1]
    assert topic == "le/111/prp/set"
    d = json.loads(payload)["d"]
    assert d["d2"] == 3 and d["d60"].startswith("2000064")


@pytest.mark.asyncio
async def test_set_segments_publishes_d50():
    import json
    c = _client_with_fake_mqtt()
    await c.set_segments([(255, 0, 0), (0, 0, 255)])
    d = json.loads(c._mqtt.published[-1][1])["d"]
    assert d["d2"] == 2 and d["d50"].startswith("N01:P10002")
