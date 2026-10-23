"""Runs the daily schedule against the lamp through an injected LampDriver.

Edge-triggered: a phase is applied once, when it begins (or at startup), so
manual control from any front-end wins until the next phase change.
Design: docs/superpowers/specs/2026-10-23-daily-schedule-design.md
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping, Protocol

from web import schedule as sch
from web.market_calendar import ET

_LOG = logging.getLogger("scheduler")

GREEN, RED, WHITE = "00FF00", "FF0000", "FFFFFF"
WARM_WHITE = "FFB46B"  # evening fallback when the motion catalog is empty

_MAX_SLEEP = 60.0       # re-check at least once a minute
_RETRY_FAILED = 30.0    # a phase that failed to apply (e.g. no first price yet)
_RETRY_CAPTURE = 60.0   # a capture blocks applying a phase
_HANDLE_PHASES = ("opening_bell", "market", "closing_bell", "evening")


class LampDriver(Protocol):
    async def start_ticker(self, symbol: str, interval: int) -> object: ...
    async def play_motion(self, motion_id: str) -> object: ...
    async def solid(self, color: str) -> None: ...
    async def power_off(self) -> None: ...
    def owns(self, handle: object) -> bool: ...
    def capture_busy(self) -> bool: ...
    def motion_ids(self) -> list[str]: ...
    def motion_name(self, motion_id: str) -> str: ...
    async def day_change(self, symbol: str) -> float | None: ...
    def last_ticker_color(self) -> str | None: ...


class Scheduler:
    def __init__(self, driver: LampDriver, path: Path, env: Mapping,
                 now: Callable[[], datetime] = lambda: datetime.now(ET),
                 sleep=asyncio.sleep, rng: random.Random | None = None):
        self._driver = driver
        self._path = Path(path)
        self._now = now
        self._sleep = sleep
        self._rng = rng or random.Random()
        self.settings = sch.load_settings(self._path, env)
        self._applied: str | None = None
        self._handle: object = None
        self._motion_id: str | None = None
        self._rotating = False
        self._rotation_due: datetime | None = None
        self._wake = asyncio.Event()

    # ----- loop ---------------------------------------------------------------

    async def run(self) -> None:
        while True:
            try:
                delay = await self.step()
            except Exception:  # noqa: BLE001 — never let the schedule die
                _LOG.exception("scheduler step failed")
                delay = _RETRY_FAILED
            await self._wait(delay)

    async def _wait(self, delay: float) -> None:
        sleeper = asyncio.ensure_future(self._sleep(delay))
        waker = asyncio.ensure_future(self._wake.wait())
        try:
            await asyncio.wait({sleeper, waker}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            sleeper.cancel()
            waker.cancel()
            self._wake.clear()

    async def step(self) -> float:
        """Apply the current phase if it changed, rotate evening motions.
        Returns seconds until the next thing worth checking."""
        now = self._now()
        info = sch.phase_at(now, self.settings)
        until_next = ((info.next_change_at - now).total_seconds()
                      if info.next_change_at else _MAX_SLEEP)
        delay = min(_MAX_SLEEP, until_next)
        if not self.settings["enabled"]:
            return max(delay, 0.001)
        if info.phase != self._applied:
            if self._driver.capture_busy():
                return max(min(delay, _RETRY_CAPTURE), 0.001)
            try:
                await self._apply(info.phase, now)
                self._applied = info.phase
            except Exception as e:  # noqa: BLE001
                _LOG.warning("could not apply %s (retrying in %.0fs): %s",
                             info.phase, _RETRY_FAILED, e)
                return max(min(delay, _RETRY_FAILED), 0.001)
        elif self._rotating and self._rotation_due and now >= self._rotation_due:
            if self._driver.owns(self._handle):
                await self._play_evening(now)
            else:
                self._rotating = False  # user took over; leave it until next phase
        if self._rotating and self._rotation_due:
            delay = min(delay, (self._rotation_due - now).total_seconds())
        return max(delay, 0.001)

    # ----- phases -------------------------------------------------------------

    async def _apply(self, phase: str, now: datetime) -> None:
        s = self.settings
        self._rotating = False
        self._handle = None
        self._motion_id = None
        if phase in ("opening_bell", "closing_bell"):
            wanted = s["opening_motion" if phase == "opening_bell" else "closing_motion"]
            ids = self._driver.motion_ids()
            mid = wanted if wanted in ids else (self._rng.choice(ids) if ids else None)
            if mid is None:
                await self._driver.solid(WARM_WHITE)
            else:
                self._handle = await self._driver.play_motion(mid)
                self._motion_id = mid
        elif phase == "market":
            self._handle = await self._driver.start_ticker(s["symbol"], s["interval"])
        elif phase == "recap":
            await self._driver.solid(await self._recap_color())
        elif phase == "evening":
            self._rotating = True
            await self._play_evening(now)
        else:  # night, idle_off
            await self._driver.power_off()
        _LOG.info("schedule: %s", phase)

    async def _recap_color(self) -> str:
        change = await self._driver.day_change(self.settings["symbol"])
        if change is None:
            last = self._driver.last_ticker_color()
            return last if last in (GREEN, RED) else WHITE
        return GREEN if change > 0 else RED if change < 0 else WHITE

    async def _play_evening(self, now: datetime) -> None:
        ids = self._driver.motion_ids()
        chosen = self.settings["evening_motions"]
        pool = ids if chosen == "all" else [i for i in chosen if i in ids]
        if not pool:
            await self._driver.solid(WARM_WHITE)
            self._rotating = False
            return
        if len(pool) > 1 and self._motion_id in pool:
            pool = [i for i in pool if i != self._motion_id]
        mid = self._rng.choice(pool)
        self._handle = await self._driver.play_motion(mid)
        self._motion_id = mid
        self._rotation_due = now + timedelta(minutes=self.settings["evening_rotate_minutes"])

    # ----- settings / status --------------------------------------------------

    async def update(self, changes: dict) -> dict:
        """Validate + save changes, then wake the loop. Raises ValueError."""
        old = self.settings
        new = sch.merge(old, changes)
        sch.save_settings(self._path, new)
        self.settings = new
        if new["enabled"] and not old["enabled"]:
            self._applied = None
        elif (self._applied == "market" and self._driver.owns(self._handle)
              and (new["symbol"], new["interval"]) != (old["symbol"], old["interval"])):
            self._applied = None  # restart the ticker with the new symbol/interval
        self._wake.set()
        return {"settings": dict(new), "status": self.status()}

    def driving(self) -> bool:
        return self._applied in _HANDLE_PHASES and self._driver.owns(self._handle)

    def status(self) -> dict:
        info = sch.phase_at(self._now(), self.settings)
        iso = lambda dt: dt.isoformat() if dt else None  # noqa: E731
        return {
            "enabled": self.settings["enabled"],
            "phase": info.phase,
            "phase_since": iso(info.since),
            "next_phase": info.next_phase,
            "next_change_at": iso(info.next_change_at),
            "trading_day": info.trading_day,
            "current_motion": (self._driver.motion_name(self._motion_id)
                               if self._motion_id and self.driving() else None),
        }
