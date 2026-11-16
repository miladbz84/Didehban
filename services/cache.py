from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class CachedValue:
    value: Any
    is_stale: bool
    age_seconds: float


class AsyncTTLCache:
    """Simple in-memory cache with per-key TTL semantics."""

    def __init__(self, default_ttl: int = 30) -> None:
        self.default_ttl = max(1, default_ttl)
        self._data: dict[str, tuple[float, float, Any]] = {}
        self._lock = asyncio.Lock()
        self._key_locks: dict[str, asyncio.Lock] = {}

    async def get(self, key: str, default: Any = None) -> Any:
        async with self._lock:
            item = self._data.get(key)
            if item is None:
                return default
            _, expires_at, value = item
            if time.monotonic() >= expires_at:
                return default
            return value

    async def get_stale(self, key: str, max_age: float) -> CachedValue | None:
        async with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            stored_at, _, value = item
            now = time.monotonic()
            age = max(0.0, now - stored_at)
            if age <= max_age:
                return CachedValue(value=value, is_stale=True, age_seconds=age)
            return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> Any:
        safe_ttl = ttl if ttl is not None else self.default_ttl
        safe_ttl = max(1, safe_ttl)
        stored_at = time.monotonic()
        expires_at = stored_at + safe_ttl
        async with self._lock:
            self._data[key] = (stored_at, expires_at, value)
        return value

    def key_lock(self, key: str) -> asyncio.Lock:
        lock = self._key_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._key_locks[key] = lock
        return lock

    async def get_or_set(self, key: str, factory: Callable[[], T], ttl: int | None = None) -> T:
        cached = await self.get(key)
        if cached is not None:
            return cached
        value = factory()
        return await self.set(key, value, ttl)

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()
