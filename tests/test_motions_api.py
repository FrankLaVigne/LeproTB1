"""Tests for the /api/motions endpoints + capture-save integration."""

import asyncio
import json

import pytest

from web import motions
from web import server as workshop


N01_SOLID = "N01:P10001FFAA00F21000100C4U3V3000640000E1;"
N02_CHRISTMAS = ("N02:P10002FF0000008000U510F2100010f01V3001640396;"
                 "P600F210001s00000001U635ca000000000002000002020000R301111;")


class _FakeDevice:
    did = "dev1"


class _FakeClient:
    def __init__(self):
        self.sent = []
        self.state = {"dev1": {"d1": 1}}

    def _dev(self, _did):
        return _FakeDevice()

    async def send_raw(self, payload, did=None):
        self.sent.append((payload, did))


class _FakeReq:
    def __init__(self, body=None, **match_info):
        self._body = body if body is not None else {}
        self.match_info = match_info

    async def json(self):
        return self._body


def _body(resp):
    return json.loads(resp.body.decode() if isinstance(resp.body, bytes) else resp.body)


@pytest.fixture
def catalog_file(tmp_path, monkeypatch):
    """Point the server at a temp catalog seeded with two motions."""
    path = tmp_path / "motions.json"
    catalog = {"motions": []}
    motions.merge_preset(catalog, {"captured": "2026-06-02",
                                   "frames": [{"d50": N01_SOLID}, {"d50": N02_CHRISTMAS}]},
                         "seed")
    motions.save_catalog(catalog, path)
    monkeypatch.setattr(workshop, "_MOTIONS_CATALOG_PATH", path)
    return path


@pytest.fixture
def quiet_lamp(monkeypatch):
    """Fake client, no sessions, no preview running."""
    client = _FakeClient()
    monkeypatch.setattr(workshop, "_client", client)
    monkeypatch.setattr(workshop, "_capture_session", None)
    monkeypatch.setattr(workshop, "_clock_session", None)
    monkeypatch.setattr(workshop, "_ticker_session", None)
    monkeypatch.setattr(workshop, "_preview_task", None)
    monkeypatch.setattr(workshop, "_preview_name", None)
    return client


async def test_get_motions(catalog_file, quiet_lamp):
    body = _body(await workshop.api_motions(None))
    assert body["total"] == 2
    assert body["named"] == 0
    assert body["motions"][0]["id"] == "motion-001"


async def test_rename_motion(catalog_file, quiet_lamp):
    resp = await workshop.api_motion_rename(
        _FakeReq({"name": "solid orange"}, id="motion-001"))
    assert _body(resp)["ok"] is True
    body = _body(await workshop.api_motions(None))
    assert body["motions"][0]["name"] == "solid orange"
    assert body["named"] == 1


async def test_rename_unknown_motion_404(catalog_file, quiet_lamp):
    resp = await workshop.api_motion_rename(
        _FakeReq({"name": "x"}, id="motion-999"))
    assert resp.status == 404


async def test_rename_requires_name(catalog_file, quiet_lamp):
    resp = await workshop.api_motion_rename(_FakeReq({}, id="motion-001"))
    assert resp.status == 400


async def test_play_recolors_and_sends(catalog_file, quiet_lamp, monkeypatch):
    sent_presets = []

    async def fake_run_preview(preset, did, client):
        sent_presets.append(preset)
        # Real _run_preview loops forever; block so the task stays not-done,
        # exactly like production.
        await asyncio.Event().wait()

    monkeypatch.setattr(workshop, "_run_preview", fake_run_preview)
    resp = await workshop.api_motion_play(
        _FakeReq({"palette": ["00FF00"]}, id="motion-001"))
    assert _body(resp)["ok"] is True
    await asyncio.sleep(0)    # let the created task run
    assert len(sent_presets) == 1
    d50 = sent_presets[0]["payload"]["d50"]
    assert "00FF00" in d50 and "FFAA00" not in d50    # recolored
    # Active mode label reflects the motion.
    active = workshop._active_mode()
    assert active["mode"] == "motion"
    assert "motion-001" in active["label"]
    # cleanup: cancel the never-ending preview task
    task = workshop._preview_task
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_play_without_palette_uses_original(catalog_file, quiet_lamp, monkeypatch):
    sent_presets = []

    async def fake_run_preview(preset, did, client):
        sent_presets.append(preset)
        # Real _run_preview loops forever; block so the task stays not-done,
        # exactly like production.
        await asyncio.Event().wait()

    monkeypatch.setattr(workshop, "_run_preview", fake_run_preview)
    resp = await workshop.api_motion_play(_FakeReq({}, id="motion-001"))
    assert _body(resp)["ok"] is True
    await asyncio.sleep(0)
    assert "FFAA00" in sent_presets[0]["payload"]["d50"]    # original colors
    # cleanup: cancel the never-ending preview task
    task = workshop._preview_task
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_play_unknown_motion_404(catalog_file, quiet_lamp):
    resp = await workshop.api_motion_play(_FakeReq({}, id="motion-999"))
    assert resp.status == 404


async def test_play_blocked_by_ticker(catalog_file, quiet_lamp, monkeypatch):
    class _RunningTicker:
        running = True
    monkeypatch.setattr(workshop, "_ticker_session", _RunningTicker())
    from aiohttp import web as aioweb
    with pytest.raises(aioweb.HTTPConflict):
        await workshop.api_motion_play(_FakeReq({}, id="motion-001"))


async def test_play_invalid_palette_returns_400(catalog_file, quiet_lamp):
    resp = await workshop.api_motion_play(
        _FakeReq({"palette": ["zzz"]}, id="motion-001"))
    assert resp.status == 400
    assert "6-hex" in _body(resp)["error"]


async def test_rebuild_endpoint(catalog_file, quiet_lamp):
    resp = await workshop.api_motions_rebuild(None)
    body = _body(resp)
    assert body["ok"] is True
    # Real presets merged on top of the 2 seeded motions.
    assert body["total"] > 2


async def test_capture_save_merges_into_catalog(catalog_file, quiet_lamp, monkeypatch, tmp_path):
    """api_captures_save must merge the new preset's frames into the motion catalog
    and report the merge result."""
    presets_dir = tmp_path / "presets"
    presets_dir.mkdir()
    monkeypatch.setattr(workshop, "_PRESETS_DIR", presets_dir)
    monkeypatch.setattr(workshop, "_PROJECT_ROOT", tmp_path)

    class _DoneSession:
        running = False
        # Real CaptureSession stores frames as d50 strings, not dicts.
        # N03 prefix makes this a structurally different motion from N01/N02 seeds.
        frames = ["N03:P10001ABCDEFF21000100C4U3V3000640000E1;"]

        async def stop(self):
            pass

    monkeypatch.setattr(workshop, "_capture_session", _DoneSession())
    resp = await workshop.api_captures_save(_FakeReq({"name": "new-capture"}))
    body = _body(resp)
    assert body["ok"] is True
    assert body["motions"]["new"] == 1
    assert body["motions"]["catalog_total"] == 3    # 2 seeded + 1 new
    # The catalog file was actually updated.
    catalog = motions.load_catalog(workshop._MOTIONS_CATALOG_PATH)
    assert len(catalog["motions"]) == 3


async def test_preset_preview_still_labeled_preset(catalog_file, quiet_lamp, monkeypatch):
    """api_preview must keep reporting mode='preset' after the _preview_kind change."""
    async def fake_run_preview(preset, did, client):
        # Real _run_preview loops forever; block so the task stays not-done,
        # exactly like production.
        await asyncio.Event().wait()

    monkeypatch.setattr(workshop, "_run_preview", fake_run_preview)
    monkeypatch.setattr(workshop, "_load_preset",
                        lambda name: {"name": name, "payload": {"d50": N01_SOLID}})
    monkeypatch.setattr(workshop, "apply_color_map", lambda preset, cmap: preset)
    resp = await workshop.api_preview(_FakeReq({"base_name": "christmas"}))
    assert _body(resp)["ok"] is True
    active = workshop._active_mode()
    assert active["mode"] == "preset"
    # cleanup: cancel the never-ending preview task
    task = workshop._preview_task
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_route_registration():
    app = workshop.build_app()
    routes = {(r.method, r.resource.canonical) for r in app.router.routes()
              if r.resource is not None}
    assert ("GET", "/api/motions") in routes
    assert ("POST", "/api/motions/rebuild") in routes
    assert ("POST", "/api/motions/{id}/play") in routes
    assert ("POST", "/api/motions/{id}/rename") in routes
    assert ("GET", "/motions") in routes


async def test_capture_save_succeeds_even_if_catalog_merge_fails(
        catalog_file, quiet_lamp, monkeypatch, tmp_path):
    """A catalog write failure must not fail the capture save — the preset file
    is the artifact that matters."""
    presets_dir = tmp_path / "presets"
    presets_dir.mkdir()
    monkeypatch.setattr(workshop, "_PRESETS_DIR", presets_dir)
    monkeypatch.setattr(workshop, "_PROJECT_ROOT", tmp_path)

    class _DoneSession:
        running = False
        frames = ["N03:P10001ABCDEFF21000100C4U3V3030640000R301111;"]

        async def stop(self):
            pass

    monkeypatch.setattr(workshop, "_capture_session", _DoneSession())

    def broken_save(catalog, path):
        raise OSError("disk full")

    monkeypatch.setattr(workshop._motions_mod, "save_catalog", broken_save)
    resp = await workshop.api_captures_save(_FakeReq({"name": "survives-merge-failure"}))
    body = _body(resp)
    assert body["ok"] is True                         # save still succeeded
    assert body["motions"] is None                    # merge result degraded to None
    assert (presets_dir / "survives-merge-failure.json").exists()    # preset on disk
    assert workshop._capture_session is None          # session not stranded
