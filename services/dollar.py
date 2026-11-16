from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx

from config import SETTINGS
from services.cache import AsyncTTLCache
from services.market_data import (
    MarketDataError,
    log_fetch_failure,
    mark_fresh,
    mark_stale,
    parse_finite_number,
    response_json_object,
    utc_timestamp,
)

CACHE = AsyncTTLCache(default_ttl=SETTINGS.cache_ttl)
STALE_IF_ERROR_SECONDS = 5 * 60
_CACHE_KEY = "dollar:navasan"
logger = logging.getLogger("dideban")
_ALIASES = {
    "buy": ("buy", "usd_buy"),
    "sell": ("sell", "usd_sell"),
}


def _extract_price(data: Mapping[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        if key in data:
            return parse_finite_number(data[key], positive=True)
    raise MarketDataError("Navasan response is missing a required dollar price.")


def _stale_dollar(cached: dict[str, Any], age: float) -> dict[str, Any]:
    result = mark_stale(cached, age)
    result["stale_age_seconds"] = int(age)
    return result


async def get_dollar_data(*, force_refresh: bool = False) -> dict[str, Any]:
    api_key = SETTINGS.navasan_api_key
    if not api_key:
        raise MarketDataError("Navasan API key is not configured.")
    source_unit = getattr(SETTINGS, "navasan_dollar_price_unit", None)
    if source_unit not in {"IRR", "TOMAN"}:
        raise MarketDataError("Navasan dollar price unit is not confirmed.")

    if not force_refresh:
        cached = await CACHE.get(_CACHE_KEY)
        if cached is not None:
            return cached

    async with CACHE.key_lock(_CACHE_KEY):
        if not force_refresh:
            cached = await CACHE.get(_CACHE_KEY)
            if cached is not None:
                return cached
        stale = await CACHE.get_stale(_CACHE_KEY, STALE_IF_ERROR_SECONDS)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://api.navasan.tech/latest",
                    headers={"X-API-Key": api_key},
                )
            payload = response_json_object(response)
            price = payload.get("usd") or payload.get("dollar")
            if not isinstance(price, Mapping):
                raise MarketDataError("Navasan response is missing the USD price object.")
            buy_source = _extract_price(price, _ALIASES["buy"])
            sell_source = _extract_price(price, _ALIASES["sell"])
            buy_rial = buy_source if source_unit == "IRR" else buy_source * 10
            sell_rial = sell_source if source_unit == "IRR" else sell_source * 10
            buy_toman = buy_source / 10 if source_unit == "IRR" else buy_source
            sell_toman = sell_source / 10 if source_unit == "IRR" else sell_source
            data = mark_fresh(
                {
                    "buy_rial": buy_rial,
                    "sell_rial": sell_rial,
                    "buy_toman": buy_toman,
                    "sell_toman": sell_toman,
                    "updated_at": utc_timestamp(),
                    "source": "Navasan",
                    "currency_unit": source_unit,
                }
            )
        except (httpx.HTTPError, OSError, MarketDataError, ValueError, TypeError) as exc:
            log_fetch_failure(logger, "Navasan dollar", exc)
            if stale is not None and isinstance(stale.value, dict):
                logger.warning("Navasan dollar request failed; returning bounded stale quote.")
                return _stale_dollar(stale.value, stale.age_seconds)
            raise MarketDataError("No valid dollar quote is available.") from exc

        await CACHE.set(_CACHE_KEY, data)
        return data
