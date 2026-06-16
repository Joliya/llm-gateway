from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from app.config import get_settings

_settings = get_settings()


@dataclass
class _Window:
    start: float
    count: int


class MemoryRateLimiter:
    """Fixed 60-second window counters, keyed by an arbitrary scope string.

    Single-process only. For multi-instance deployments configure GW_REDIS_URL
    to use the Redis backend instead. Exposes an async interface so callers are
    backend-agnostic.
    """

    WINDOW = 60.0

    def __init__(self) -> None:
        self._windows: dict[str, _Window] = {}
        self._lock = threading.Lock()

    def _current(self, key: str) -> _Window:
        now = time.monotonic()
        w = self._windows.get(key)
        if w is None or (now - w.start) >= self.WINDOW:
            w = _Window(start=now, count=0)
            self._windows[key] = w
        return w

    async def check(self, key: str, limit: int | None, amount: int = 1) -> bool:
        """Return True if `amount` fits under `limit` for the current window,
        consuming it when it does. `limit` falsy means unlimited."""
        if not limit or limit <= 0:
            return True
        with self._lock:
            w = self._current(key)
            if w.count + amount > limit:
                return False
            w.count += amount
            return True

    async def add(self, key: str, amount: int) -> None:
        """Record usage after the fact (e.g. completion tokens) with no cap."""
        if amount <= 0:
            return
        with self._lock:
            w = self._current(key)
            w.count += amount


class RedisRateLimiter:
    """Redis-backed fixed-window limiter (INCR + EXPIRE) so limits hold across
    instances. Selected automatically when GW_REDIS_URL is set."""

    WINDOW = 60

    def __init__(self, client) -> None:
        self._r = client

    async def check(self, key: str, limit: int | None, amount: int = 1) -> bool:
        if not limit or limit <= 0:
            return True
        rkey = f"rl:{key}:{int(time.time() // self.WINDOW)}"
        new = await self._r.incrby(rkey, amount)
        if new == amount:
            await self._r.expire(rkey, self.WINDOW)
        return new <= limit

    async def add(self, key: str, amount: int) -> None:
        if amount <= 0:
            return
        rkey = f"rl:{key}:{int(time.time() // self.WINDOW)}"
        new = await self._r.incrby(rkey, amount)
        if new == amount:
            await self._r.expire(rkey, self.WINDOW)


class _LimiterProxy:
    """Holds the active backend; main.py may swap to Redis at startup."""

    def __init__(self) -> None:
        self.backend = MemoryRateLimiter()

    async def check(self, key: str, limit: int | None, amount: int = 1) -> bool:
        return await self.backend.check(key, limit, amount)

    async def add(self, key: str, amount: int) -> None:
        await self.backend.add(key, amount)


rate_limiter = _LimiterProxy()
