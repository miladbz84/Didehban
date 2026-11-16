from __future__ import annotations

import asyncio

import pytest

from services.cache import AsyncTTLCache


@pytest.mark.asyncio
async def test_cache_sets_and_expire_values():
    cache = AsyncTTLCache(default_ttl=1)
    await cache.set("alpha", {"value": 7})
    assert await cache.get("alpha") == {"value": 7}

    await asyncio.sleep(1.1)
    assert await cache.get("alpha") is None


@pytest.mark.asyncio
async def test_cache_get_or_set_works():
    cache = AsyncTTLCache(default_ttl=2)
    value = await cache.get_or_set("beta", lambda: {"count": 2})
    assert value == {"count": 2}
