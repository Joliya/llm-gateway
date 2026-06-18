from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

from app.config import get_settings

_settings = get_settings()

# Fields that don't affect the generated output and must be excluded from the key.
_NON_SEMANTIC = {"stream", "user", "stream_options"}


def make_cache_key(model: str, params: dict[str, Any]) -> str:
    relevant = {k: v for k, v in params.items() if k not in _NON_SEMANTIC}
    blob = json.dumps({"model": model, "params": relevant}, sort_keys=True, default=str)
    return "cache:" + hashlib.sha256(blob.encode()).hexdigest()


class MemoryCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    async def get(self, key: str) -> dict | None:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            expires, value = item
            if time.monotonic() > expires:
                self._store.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: dict, ttl: int) -> None:
        with self._lock:
            self._store[key] = (time.monotonic() + ttl, value)


class RedisCache:
    def __init__(self, client) -> None:
        self._r = client

    async def get(self, key: str) -> dict | None:
        raw = await self._r.get(key)
        return json.loads(raw) if raw else None

    async def set(self, key: str, value: dict, ttl: int) -> None:
        await self._r.set(key, json.dumps(value, default=str), ex=ttl)


class _CacheProxy:
    def __init__(self) -> None:
        self.backend = MemoryCache()

    async def get(self, key: str) -> dict | None:
        return await self.backend.get(key)

    async def set(self, key: str, value: dict, ttl: int | None = None) -> None:
        await self.backend.set(key, value, ttl or _settings.cache_ttl_seconds)


response_cache = _CacheProxy()


def cache_enabled_for(alias_cache_flag: bool | None) -> bool:
    """Per-alias override wins; otherwise fall back to the global default."""
    if alias_cache_flag is not None:
        return alias_cache_flag
    return _settings.cache_enabled
