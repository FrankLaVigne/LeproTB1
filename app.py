#!/usr/bin/env python3
"""Self-hosted web front end for Lepro lights.

Run:  python app.py        (then open http://<vm-ip>:8080)

Keeps a single persistent Lepro cloud login + MQTT connection, serves a small
control page, and exposes a JSON API the page calls. Bind address/port via
LEPRO_HOST / LEPRO_PORT (default 0.0.0.0:8080).
"""

import asyncio
import logging
import os

from aiohttp import web

from lepro import LeproClient, LeproError, load_config

logging.basicConfig(level=logging.INFO)

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lepro Control</title>
<style>
  :root { color-scheme: dark; }
  body { font: 16px/1.5 system-ui, sans-serif; margin: 0; background:#111; color:#eee;
         display:flex; min-height:100vh; align-items:center; justify-content:center; }
  .card { background:#1c1c1f; padding:28px; border-radius:16px; width:320px;
          box-shadow:0 8px 30px rgba(0,0,0,.5); }
  h1 { font-size:18px; margin:0 0 4px; }
  .sub { color:#888; font-size:13px; margin-bottom:20px; }
  label { display:block; font-size:13px; color:#aaa; margin:16px 0 6px; }
  input[type=range]{ width:100%; }
  input[type=color]{ width:100%; height:44px; border:none; background:none; border-radius:8px; }
  .row { display:flex; gap:10px; }
  button { flex:1; padding:12px; border:0; border-radius:10px; font-size:15px;
           cursor:pointer; background:#2a2a30; color:#eee; }
  button.on { background:#2563eb; } button.off{ background:#3a3a40; }
  select { width:100%; padding:8px; border-radius:8px; background:#2a2a30; color:#eee; border:0; }
  #status { font-size:12px; color:#777; margin-top:16px; min-height:1em; }
</style></head>
<body><div class="card">
  <h1>Lepro Control</h1>
  <div class="sub" id="devname">loading…</div>
  <select id="device"></select>
  <div class="row" style="margin-top:16px">
    <button class="on" onclick="power(true)">On</button>
    <button class="off" onclick="power(false)">Off</button>
  </div>
  <label>Brightness <span id="bval">—</span></label>
  <input type="range" id="bright" min="1" max="100" value="100"
         oninput="bval.textContent=this.value+'%'" onchange="setBright(this.value)">
  <label>Colour</label>
  <input type="color" id="color" value="#ffffff" onchange="setColor(this.value)">
  <label>White temperature</label>
  <input type="range" id="cct" min="2700" max="6500" step="100" value="4000"
         onchange="setWhite(this.value)">
  <div id="status"></div>
</div>
<script>
const $ = id => document.getElementById(id);
async function api(path, body){
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(body||{})});
  const j = await r.json();
  $('status').textContent = j.ok ? 'ok' : ('error: ' + (j.error||'?'));
  return j;
}
const did = () => $('device').value;
function power(on){ api('/api/power', {did:did(), on}); }
function setBright(v){ api('/api/brightness', {did:did(), pct:+v}); }
function setColor(hex){
  const r=parseInt(hex.substr(1,2),16),g=parseInt(hex.substr(3,2),16),b=parseInt(hex.substr(5,2),16);
  api('/api/color', {did:did(), r,g,b});
}
function setWhite(k){ api('/api/white', {did:did(), kelvin:+k}); }
async function init(){
  const r = await fetch('/api/devices'); const j = await r.json();
  const sel = $('device'); sel.innerHTML='';
  (j.devices||[]).forEach(d => {
    const o=document.createElement('option'); o.value=d.did;
    o.textContent = d.name + ' ('+d.series+')'; sel.appendChild(o);
  });
  $('devname').textContent = j.devices && j.devices.length
      ? j.devices.length+' device(s)' : 'no devices found';
}
init();
</script></body></html>"""


async def index(_req):
    return web.Response(text=PAGE, content_type="text/html")


async def devices(req):
    c: LeproClient = req.app["lepro"]
    return web.json_response({"ok": True, "devices": [
        {"did": d.did, "name": d.name, "series": d.series, "b_series": d.is_b_series}
        for d in c.devices
    ]})


def _cmd(handler):
    async def wrapped(req):
        c: LeproClient = req.app["lepro"]
        try:
            body = await req.json()
            await handler(c, body)
            return web.json_response({"ok": True})
        except (LeproError, ValueError, KeyError) as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)
    return wrapped


api_power = _cmd(lambda c, b: c.power(bool(b["on"]), b.get("did")))
api_brightness = _cmd(lambda c, b: c.set_brightness(int(b["pct"]), b.get("did")))
api_color = _cmd(lambda c, b: c.set_color(int(b["r"]), int(b["g"]), int(b["b"]),
                                          b.get("pct"), b.get("did")))
api_white = _cmd(lambda c, b: c.set_white(int(b["kelvin"]), b.get("pct"), b.get("did")))


async def _on_startup(app):
    cfg = load_config()
    if not cfg["account"] or not cfg["password"]:
        raise SystemExit("Missing credentials: create config.json or set LEPRO_ACCOUNT/LEPRO_PASSWORD.")
    client = LeproClient(cfg["account"], cfg["password"], cfg["region"])
    await client.login()
    await client.connect_mqtt()
    app["lepro"] = client
    # Background listener keeps client.state fresh and drains the MQTT queue,
    # auto-reconnecting when the broker drops us (e.g. phone app reclaims the slot).
    app["listener"] = asyncio.create_task(client.listen_forever())
    logging.info("ready: %d device(s)", len(client.devices))


async def _on_cleanup(app):
    app["listener"].cancel()
    await app["lepro"].close()


def build_app() -> web.Application:
    app = web.Application()
    app.add_routes([
        web.get("/", index),
        web.get("/api/devices", devices),
        web.post("/api/power", api_power),
        web.post("/api/brightness", api_brightness),
        web.post("/api/color", api_color),
        web.post("/api/white", api_white),
    ])
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


if __name__ == "__main__":
    host = os.environ.get("LEPRO_HOST", "0.0.0.0")
    port = int(os.environ.get("LEPRO_PORT", "8080"))
    web.run_app(build_app(), host=host, port=port)
