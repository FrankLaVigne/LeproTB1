"""Lepro Cloud client — control Lepro Wi-Fi lights (e.g. TB1) programmatically.

Lepro's Wi-Fi lights are NOT Tuya devices; they talk to Lepro's own cloud.
Control therefore routes through Lepro's servers (internet required), but this
client runs entirely on your own machine — no phone app or Home Assistant needed.

Flow (reverse-engineered, mirrors the official app + the lepro_led HA integration):
  1. POST  /user/login                         -> bearer token
  2. GET   /user/profile                        -> uid + MQTT host/port + cert URLs
  3. download per-account root CA + client cert (auth'd); private key is the
     bundled client_key.pem shipped with the app
  4. GET   /family/list/timestamp/{ts}          -> family id (fid)
  5. GET   /v3/device/list/fid/{fid}/timestamp/{ts} -> devices (did, series, ...)
  6. MQTT over TLS: publish control to le/{did}/prp/set, read state from le/{did}/prp/#

Field semantics (the "d" dict):
  d1  power (0/1)
  d2  mode  (0=white/CCT, 1=RGB, 2=segmented/effect, 3=special effect)
  d3  brightness 0..1000 (white & B-series RGB modes)
  d4  color temperature 0..1000 (0=2700K warm, 1000=6500K cool)
  d5  RGB as HSV hex: HHHH SSSS VVVV (hue 0..360, sat/val 0..1000)
  d52 brightness 0..1000 (segmented/strip mode)
  d50 segmented color+effect string
"""

from __future__ import annotations

import asyncio
import colorsys
import hashlib
import json
import logging
import math
import os
import random
import ssl
import time
from dataclasses import dataclass, field

import aiohttp
import aiomqtt

_LOG = logging.getLogger("lepro")

# Note: there is no separate "us" host — North American accounts use "na".
REGIONS = {
    "eu": "api-eu-iot.lepro.com",
    "na": "api-na-iot.lepro.com",
    "fe": "api-fe-iot.lepro.com",
}

# App identity headers — the API rejects requests that don't look like the app.
_APP_VERSION = "1.0.9.202"
_BASE_HEADERS = {
    "App-Version": _APP_VERSION,
    "Device-Model": "lepro-py",
    "Device-System": "custom",
    "GMT": "+0",
    "Platform": "2",
    "Screen-Size": "1536*2048",
    "User-Agent": f"LE/{_APP_VERSION} (lepro-py)",
}

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_KEY = os.path.join(_HERE, "client_key.pem")
_CERT_DIR = os.path.join(_HERE, "certs")
# Reuse a cached token for this long before re-running /user/login, which the
# API throttles aggressively (code -905). Token itself lives longer server-side.
_SESSION_TTL = 1800

# Models that use the B-series protocol (d2=1/d5 HSV for RGB, d3/d4 for white).
# Note: "TB1" matches "B1", so the table lamp is treated as a B-series device.
_B_SERIES_TOKENS = ("B1", "B2", "B3", "BC1", "BP1", "T1", "SE1")


def _is_b_series(series: str) -> bool:
    s = (series or "").upper()
    return any(tok in s for tok in _B_SERIES_TOKENS)


# d50-family effects (firmware runs them); names map to a payload "tail".
_D50_EFFECTS = ("solid", "breath", "gradient", "clockwise", "counterclockwise", "circular")
# d60 "special" effects; each maps to a 7-char prefix.
_D60_SPECIAL = {
    "flash": "2000064",
    "wave_1": "2010064", "wave_2": "2020064", "wave_3": "2030064", "wave_4": "2040064",
    "laser_1": "2050064", "laser_2": "2060064", "laser_3": "2070064", "laser_4": "2080064",
}
EFFECTS = list(_D50_EFFECTS) + list(_D60_SPECIAL)


def _speed_to_hex(speed: int) -> str:
    """Encode a 0-100 speed into the 4-hex-char form the d50 effects expect."""
    s = max(0, min(100, int(speed)))
    if s <= 0:
        return "1000"
    raw = int(round(-117.41 * math.log(s + 1) + 597.75))
    return f"0{raw:03X}"


def _effect_tail(effect: str, sp: str) -> str:
    """Return the d50 trailing segment encoding the named effect at speed-hex `sp`."""
    return {
        "solid": "000640000E1",
        "breath": f"000640000E4{sp}0000{sp}1664",
        "gradient": f"100640000E3{sp}C2O6{sp}",
        "clockwise": f"00164{sp}E1",
        "counterclockwise": f"00264{sp}E1",
        "circular": f"100640000E1C2O6{sp}",
    }[effect]


def _build_d50(segment_colors, effect: str = "solid", speed: int = 50) -> str:
    """Build the grouped d50 string from up to 25 RGB segments + an effect."""
    groups: list[list] = []
    for col in segment_colors:
        col = tuple(int(c) for c in col)
        if groups and groups[-1][0] == col:
            groups[-1][1] += 1
        else:
            groups.append([col, 1])
    total = sum(g[1] for g in groups)
    if total < 25:
        groups[-1][1] += 25 - total
    while sum(g[1] for g in groups) > 25:
        excess = sum(g[1] for g in groups) - 25
        if groups[-1][1] > excess:
            groups[-1][1] -= excess
        else:
            groups.pop()
    num = len(groups)
    colors_str = "".join(f"{r:02X}{g:02X}{b:02X}" for (r, g, b), _ in groups)
    lengths_str = "".join(f"{cnt:04X}" for _, cnt in groups)
    tail = _effect_tail(effect, _speed_to_hex(speed))
    return f"N01:P1000{num}{colors_str}F21000{num}{lengths_str}U3V3{tail};"


def _build_effect_payload(name: str, speed: int = 50, color=(255, 255, 255),
                          pct: int | None = None) -> dict:
    """Build the MQTT 'd' payload for a named effect (d50 family or d60 special)."""
    if name in _D60_SPECIAL:
        sens = max(0, min(0x63, round(max(0, min(100, speed)) * 0x63 / 100)))
        d = {"d1": 1, "d2": 3, "d60": f"{_D60_SPECIAL[name]}{sens:02X}0000"}
    elif name in _D50_EFFECTS:
        d = {"d1": 1, "d2": 2, "d50": _build_d50([color] * 25, name, speed)}
    else:
        raise ValueError(f"unknown effect {name!r}; see lepro.EFFECTS")
    if pct is not None:
        d["d52"] = max(0, min(1000, int(round(pct * 10))))
    return d


@dataclass
class Device:
    did: str
    fid: str
    name: str
    series: str
    raw: dict = field(default_factory=dict)

    @property
    def is_b_series(self) -> bool:
        return _is_b_series(self.series)


class LeproError(RuntimeError):
    pass


class AuthError(LeproError):
    """Token rejected / expired — triggers a fresh login."""


class LeproClient:
    """Async client for Lepro's cloud. Use as an async context manager."""

    def __init__(self, account: str, password: str, region: str = "na",
                 language: str = "en", key_path: str = _DEFAULT_KEY):
        if region not in REGIONS:
            raise ValueError(f"region must be one of {list(REGIONS)}")
        self.account = account
        self.password = password
        self.region = region
        self.language = language
        self.api_host = REGIONS[region]
        self.key_path = key_path

        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None
        self._mqtt: aiomqtt.Client | None = None
        self._mqtt_info: dict | None = None
        self._ssl: ssl.SSLContext | None = None
        # Stable per-account MAC + client id (the API ties sessions to these).
        h = hashlib.md5(account.encode()).hexdigest()
        self._mac = f"02:{h[0:2]}:{h[2:4]}:{h[4:6]}:{h[6:8]}:{h[8:10]}"
        self._client_id = "lepro-app-" + hashlib.sha256(account.encode()).hexdigest()[:32]

        self.devices: list[Device] = []
        self.state: dict[str, dict] = {}  # did -> latest reported "d" fields

    # ----- HTTP / REST -------------------------------------------------------

    def _headers(self) -> dict:
        h = dict(_BASE_HEADERS)
        h["Host"] = self.api_host
        h["Language"] = self.language
        h["Slanguage"] = self.language
        h["Timestamp"] = str(int(time.time()))
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
            h["Accept-Encoding"] = "gzip"
        return h

    async def _get_json(self, path: str) -> dict:
        url = f"https://{self.api_host}{path}"
        async with self._session.get(url, headers=self._headers()) as r:
            status = r.status
            try:
                data = await r.json()
            except Exception:  # noqa: BLE001
                data = {}
        code = data.get("code")
        if status == 401 or code in (401, -901, -902):
            raise AuthError(f"GET {path} -> token rejected (code={code})")
        if status != 200 or (code not in (0, None)):
            raise LeproError(
                f"GET {path} -> HTTP {status} code={code} msg={data.get('msg')!r}")
        return data

    @property
    def _session_path(self) -> str:
        return os.path.join(_CERT_DIR, "session.json")

    @property
    def _ca_path(self) -> str:
        return os.path.join(_CERT_DIR, "root_ca.pem")

    @property
    def _cert_path(self) -> str:
        return os.path.join(_CERT_DIR, "client_cert.pem")

    async def login(self) -> None:
        """Authenticate (reusing a cached token if fresh) and discover devices."""
        self._session = aiohttp.ClientSession()
        # A resumed session needs no REST calls at all: MQTT control authenticates
        # with the cached client cert, and the device list is cached too.
        if self._resume_session():
            self._build_ssl()
            return
        await self._authenticate()
        self._build_ssl()
        await self._load_devices()
        self._save_session()

    def _resume_session(self) -> bool:
        """Restore token, MQTT info, and devices from cache if recent + certs present."""
        try:
            with open(self._session_path) as f:
                s = json.load(f)
        except (OSError, ValueError):
            return False
        if s.get("account") != self.account or s.get("region") != self.region:
            return False
        if time.time() - s.get("ts", 0) > _SESSION_TTL:
            return False
        if not (os.path.exists(self._ca_path) and os.path.exists(self._cert_path)):
            return False
        self._token = s["token"]
        self._mqtt_info = s["mqtt"]
        self.devices = [Device(**d) for d in s.get("devices", [])]
        if not self.devices:
            return False
        _LOG.info("resumed cached session for %s (%d device(s))", self.account, len(self.devices))
        return True

    def _save_session(self) -> None:
        os.makedirs(_CERT_DIR, exist_ok=True)
        with open(self._session_path, "w") as f:
            json.dump({
                "account": self.account, "region": self.region,
                "token": self._token, "mqtt": self._mqtt_info,
                "ts": int(time.time()),
                "devices": [
                    {"did": d.did, "fid": d.fid, "name": d.name, "series": d.series}
                    for d in self.devices
                ],
            }, f)

    async def _authenticate(self) -> None:
        """Fresh /user/login, fetch profile + MQTT certs, cache the session."""
        payload = {
            "platform": "2", "account": self.account, "password": self.password,
            "mac": self._mac, "timestamp": str(int(time.time())),
            "language": self.language, "fcmToken": "",
        }
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        url = f"https://{self.api_host}/user/login"
        async with self._session.post(url, json=payload, headers=headers) as r:
            body = await r.json()
        if r.status != 200 or body.get("code") != 0:
            raise LeproError(
                f"login failed (HTTP {r.status}, code={body.get('code')}, "
                f"msg={body.get('msg')!r}). If the message mentions the password, "
                f"the API may expect an MD5 hash — see README."
            )
        self._token = body["data"]["token"]
        _LOG.info("logged in as %s (region=%s)", self.account, self.region)

        profile = await self._get_json("/user/profile")
        self._mqtt_info = profile["data"]["mqtt"]
        await self._download_certs()

    async def _load_devices(self) -> None:
        ts = str(int(time.time()))
        fam = await self._get_json(f"/family/list/timestamp/{ts}")
        families = fam["data"]["list"]
        if not families:
            raise LeproError("no families on this account")
        self.devices = []
        for family in families:
            fid = family["fid"]
            ts = str(int(time.time()))
            dl = await self._get_json(f"/v3/device/list/fid/{fid}/timestamp/{ts}")
            for d in dl.get("data", {}).get("list", []):
                self.devices.append(Device(
                    did=str(d["did"]), fid=str(d.get("fid", fid)),
                    name=d.get("name", "Lepro"), series=d.get("series", ""), raw=d,
                ))
        _LOG.info("found %d device(s)", len(self.devices))

    async def _download_certs(self) -> None:
        os.makedirs(_CERT_DIR, exist_ok=True)
        for url, path in ((self._mqtt_info["root"], self._ca_path),
                          (self._mqtt_info["cert"], self._cert_path)):
            async with self._session.get(url, headers=self._headers()) as r:
                if r.status != 200:
                    raise LeproError(f"cert download {url} -> HTTP {r.status}")
                with open(path, "wb") as f:
                    f.write(await r.read())

    def _build_ssl(self) -> None:
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(cafile=self._ca_path)
        ctx.load_cert_chain(certfile=self._cert_path, keyfile=self.key_path)
        self._ssl = ctx

    # ----- MQTT --------------------------------------------------------------

    async def connect_mqtt(self) -> aiomqtt.Client:
        if self._mqtt_info is None:
            raise LeproError("call login() before connect_mqtt()")
        if self._mqtt is not None:  # tear down a previous connection (reconnect)
            try:
                await self._mqtt.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._mqtt = None
        self._mqtt = aiomqtt.Client(
            hostname=self._mqtt_info["host"],
            port=int(self._mqtt_info["port"]),
            identifier=self._client_id,
            tls_context=self._ssl,
        )
        await self._mqtt.__aenter__()
        for d in self.devices:
            await self._mqtt.subscribe(f"le/{d.did}/prp/#")
        _LOG.info("MQTT connected to %s:%s", self._mqtt_info["host"], self._mqtt_info["port"])
        return self._mqtt

    async def _publish(self, did: str, d: dict) -> None:
        if self._mqtt is None:
            raise LeproError("MQTT not connected")
        payload = {"id": random.randint(0, 1_000_000_000), "t": int(time.time()), "d": d}
        await self._mqtt.publish(f"le/{did}/prp/set", json.dumps(payload))
        _LOG.debug("set %s -> %s", did, d)

    async def request_state(self, did: str,
                            keys=("d1", "d2", "d3", "d4", "d5", "d30", "d50", "d52", "d60", "online")) -> None:
        """Ask the device to report its current state on le/{did}/prp/#."""
        if self._mqtt is None:
            raise LeproError("MQTT not connected")
        await self._mqtt.publish(f"le/{did}/prp/get", json.dumps({"d": list(keys)}))

    async def listen(self, on_update=None) -> None:
        """Consume MQTT messages once, updating self.state. Returns on disconnect.

        The broker drops us when the account's connection slot is claimed elsewhere
        (e.g. the phone app), so a clean disconnect is expected, not an error.
        """
        try:
            async for msg in self._mqtt.messages:
                try:
                    parts = msg.topic.value.split("/")
                    if len(parts) < 4 or parts[0] != "le":
                        continue
                    did = parts[1]
                    body = json.loads(msg.payload.decode())
                    d = body.get("d", {})
                    if isinstance(d, dict):
                        self.state.setdefault(did, {}).update(d)
                        if on_update:
                            await on_update(did, d)
                except Exception as e:  # noqa: BLE001
                    _LOG.warning("bad MQTT message: %s", e)
        except aiomqtt.MqttError as e:
            _LOG.info("MQTT disconnected: %s", e)

    async def listen_forever(self, on_update=None, backoff: float = 3.0) -> None:
        """Listen with automatic reconnect — for long-lived consumers (the web app)."""
        while True:
            await self.listen(on_update=on_update)
            await asyncio.sleep(backoff)
            try:
                await self.connect_mqtt()
            except Exception as e:  # noqa: BLE001
                _LOG.warning("MQTT reconnect failed: %s", e)
                await asyncio.sleep(backoff)

    # ----- high-level commands ----------------------------------------------

    def _dev(self, did: str | None) -> Device:
        if did is None:
            if not self.devices:
                raise LeproError("no devices")
            return self.devices[0]
        for d in self.devices:
            if d.did == str(did):
                return d
        raise LeproError(f"unknown device {did}")

    async def power(self, on: bool, did: str | None = None) -> None:
        await self._publish(self._dev(did).did, {"d1": 1 if on else 0})

    async def set_brightness(self, pct: int, did: str | None = None) -> None:
        """Brightness as a percentage 0..100."""
        dev = self._dev(did)
        val = max(0, min(1000, int(round(pct * 10))))
        # Segmented/strip mode uses d52; B-series white/RGB uses d3.
        d = {"d1": 1, "d3": val} if dev.is_b_series else {"d1": 1, "d52": val}
        await self._publish(dev.did, d)

    async def set_white(self, kelvin: int, pct: int | None = None, did: str | None = None) -> None:
        """Tunable-white: kelvin 2700..6500, optional brightness percent."""
        dev = self._dev(did)
        k = max(2700, min(6500, int(kelvin)))
        d4 = round((k - 2700) * 1000 / (6500 - 2700))
        d = {"d1": 1, "d2": 0, "d4": d4}
        if pct is not None:
            d["d3"] = max(0, min(1000, int(round(pct * 10))))
        await self._publish(dev.did, d)

    async def set_color(self, r: int, g: int, b: int, pct: int | None = None,
                        did: str | None = None) -> None:
        """Set an RGB color (0..255 each), optional brightness percent."""
        dev = self._dev(did)
        if dev.is_b_series:
            hue, _s, _v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            hue_deg = int(round((hue * 360) % 360))
            val = 1000 if pct is None else max(0, min(1000, int(round(pct * 10))))
            d5 = f"{hue_deg:04X}{1000:04X}{val:04X}"  # HHHH SSSS(=03E8) VVVV
            await self._publish(dev.did, {"d1": 1, "d2": 1, "d5": d5})
        else:
            # Segmented: single solid color across all 25 segments.
            color = f"{r:02X}{g:02X}{b:02X}"
            d50 = f"N01:P10001{color}F210001{25:04X}U3V3000640000E1;"
            d = {"d1": 1, "d2": 2, "d50": d50}
            if pct is not None:
                d["d52"] = max(0, min(1000, int(round(pct * 10))))
            await self._publish(dev.did, d)

    async def set_effect(self, name: str, speed: int = 50, color=(255, 255, 255),
                         pct: int | None = None, did: str | None = None) -> None:
        """Run a named firmware effect (see lepro.EFFECTS)."""
        await self._publish(self._dev(did).did, _build_effect_payload(name, speed, color, pct))

    async def set_segments(self, colors, did: str | None = None) -> None:
        """Set up to 25 RGB segment-groups across the rings (solid, no motion)."""
        d = {"d1": 1, "d2": 2, "d50": _build_d50(colors, "solid")}
        await self._publish(self._dev(did).did, d)

    async def send_raw(self, d: dict, did: str | None = None) -> None:
        """Escape hatch: send an arbitrary 'd' payload (for protocol exploration)."""
        await self._publish(self._dev(did).did, d)

    # ----- lifecycle ---------------------------------------------------------

    async def close(self) -> None:
        if self._mqtt is not None:
            try:
                await self._mqtt.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._mqtt = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> "LeproClient":
        await self.login()
        await self.connect_mqtt()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()


def load_config() -> dict:
    """Credentials from config.json (next to this file) or LEPRO_* env vars."""
    cfg = {}
    path = os.path.join(_HERE, "config.json")
    if os.path.exists(path):
        with open(path) as f:
            cfg = json.load(f)
    return {
        "account": os.environ.get("LEPRO_ACCOUNT", cfg.get("account")),
        "password": os.environ.get("LEPRO_PASSWORD", cfg.get("password")),
        "region": os.environ.get("LEPRO_REGION", cfg.get("region", "us")),
    }
