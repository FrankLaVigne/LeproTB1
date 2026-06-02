"""LampApi — thin async HTTP wrapper over the workshop server's API.

Used by cli/tui.py. Deliberately contains no Textual code so it can be tested
with aiohttp's TestServer and swapped for a stub in the TUI's own tests.
"""

from __future__ import annotations

import aiohttp

TOTAL_LEDS = 196

_GET_TIMEOUT = aiohttp.ClientTimeout(total=3)
_POST_TIMEOUT = aiohttp.ClientTimeout(total=5)


class LampApi:
    """Talks to the workshop server (web/server.py) over HTTP.

    The aiohttp session is created lazily inside the running event loop (the
    Textual app's loop) and must be closed with ``await close()`` from that
    same loop — the TUI does this in App.on_unmount.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def _ensure(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    # --- reads ----------------------------------------------------------------

    async def get_leds(self) -> dict:
        """GET /api/lamp/leds — the TUI's single polling endpoint."""
        s = await self._ensure()
        async with s.get(f"{self.base_url}/api/lamp/leds", timeout=_GET_TIMEOUT) as r:
            return await r.json()

    # --- writes ---------------------------------------------------------------

    async def set_power(self, on: bool) -> dict:
        return await self._post("/api/power", {"on": bool(on)})

    async def set_brightness(self, pct: int) -> dict:
        """pct is 0..100; the web API takes 0..1000."""
        return await self._post("/api/brightness", {"value": int(pct) * 10})

    async def stop_all(self) -> dict:
        return await self._post("/api/stop", {})

    async def fill(self, color: str) -> dict:
        """Paint every LED the same color (Steady, mid speed)."""
        return await self._post("/api/diy/paint", {
            "leds": [color] * TOTAL_LEDS, "effect": "Steady", "speed": 50,
        })

    async def _post(self, path: str, body: dict) -> dict:
        """POST and return the JSON body, even on error statuses (409 mutex
        conflicts, 400 validation) — the TUI surfaces these as notifications
        rather than crashing."""
        s = await self._ensure()
        async with s.post(f"{self.base_url}{path}", json=body,
                          timeout=_POST_TIMEOUT) as r:
            try:
                return await r.json()
            except aiohttp.ContentTypeError:
                return {"ok": False, "error": f"HTTP {r.status}"}
