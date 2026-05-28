import asyncio
import json
import pytest
import lepro


def test_frame_to_payload_color_b_series():
    d = lepro._frame_to_payload({"color": [255, 0, 0], "duration_ms": 100}, b_series=True)
    assert d["d2"] == 1 and "d5" in d  # B-series uses HSV d5


def test_frame_to_payload_segments():
    d = lepro._frame_to_payload({"segments": [[255, 0, 0]], "duration_ms": 100}, b_series=True)
    assert d["d2"] == 2 and d["d50"].startswith("N01:")


def test_frame_to_payload_brightness_only():
    d = lepro._frame_to_payload({"brightness": 50, "duration_ms": 100}, b_series=True)
    assert d["d3"] == 500  # b-series brightness field


def test_frame_to_payload_duration_floor():
    with pytest.raises(ValueError):
        lepro._frame_to_payload({"color": [1, 2, 3], "duration_ms": 10}, b_series=True)


def test_frame_to_payload_requires_content():
    with pytest.raises(ValueError):
        lepro._frame_to_payload({"duration_ms": 100}, b_series=True)


class _FakeMQTT:
    def __init__(self):
        self.published = []

    async def publish(self, topic, payload):
        self.published.append((topic, json.loads(payload)["d"]))


def _client():
    c = lepro.LeproClient.__new__(lepro.LeproClient)
    c._mqtt = _FakeMQTT()
    c.devices = [lepro.Device(did="111", fid="f", name="lamp", series="TB1")]
    return c


@pytest.mark.asyncio
async def test_animation_plays_frames_then_stops():
    c = _client()
    player = lepro.AnimationPlayer(c, c.devices[0])
    await player.play([
        {"color": [255, 0, 0], "duration_ms": 80},
        {"color": [0, 255, 0], "duration_ms": 80},
    ], repeat=False)
    await asyncio.sleep(0.3)  # let it run through both frames
    await player.stop()
    assert len(c._mqtt.published) >= 2


@pytest.mark.asyncio
async def test_animation_stop_cancels_repeat():
    c = _client()
    player = lepro.AnimationPlayer(c, c.devices[0])
    await player.play([{"color": [255, 0, 0], "duration_ms": 80}], repeat=True)
    await asyncio.sleep(0.25)
    await player.stop()
    count_after_stop = len(c._mqtt.published)
    await asyncio.sleep(0.25)
    assert len(c._mqtt.published) == count_after_stop  # no more frames after stop
