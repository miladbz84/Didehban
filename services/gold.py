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
STALE_IF_ERROR_SECONDS = 10 * 60
_CACHE_KEY = "gold:navasan"
logger = logging.getLogger("dideban")


def _extract_gold(payload: dict[str, Any]) -> Mapping[str, Any]:
    for key in ("gold", "18ayar", "gold_18"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            return value
    raise MarketDataError("Navasan response is missing the 18-karat gold price object.")


def _extract_required_price(gold: Mapping[str, Any]) -> float:
    for key in ("price", "value"):
        if key in gold:
            return parse_finite_number(gold[key], positive=True)
    raise MarketDataError("Navasan response is missing the gold price.")


def _extract_change(gold: Mapping[str, Any]) -> float | None:
    if "change" not in gold:
        return None
    return parse_finite_number(gold["change"])


def _stale_gold(cached: dict[str, Any], age: float) -> dict[str, Any]:
    result = mark_stale(cached, age)
    result["stale_age_seconds"] = int(age)
    return result


async def get_gold_data(*, force_refresh: bool = False) -> dict[str, Any]:
    api_key = SETTINGS.navasan_api_key
    if not api_key:
        raise MarketDataError("Navasan API key is not configured.")
    source_unit = getattr(SETTINGS, "navasan_gold_price_unit", None)
    if source_unit not in {"IRR", "TOMAN"}:
        raise MarketDataError("Navasan gold price unit is not confirmed.")

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
            gold = _extract_gold(payload)
            price_source = _extract_required_price(gold)
            price_rial = price_source if source_unit == "IRR" else price_source * 10
            price_toman = price_source / 10 if source_unit == "IRR" else price_source
            data = mark_fresh(
                {
                    "price_rial": price_rial,
                    "price_toman": price_toman,
                    "change": _extract_change(gold),
                    "updated_at": utc_timestamp(),
                    "source": "Navasan",
                    "currency_unit": source_unit,
                }
            )
        except (httpx.HTTPError, OSError, MarketDataError, ValueError, TypeError) as exc:
            log_fetch_failure(logger, "Navasan gold", exc)
            if stale is not None and isinstance(stale.value, dict):
                logger.warning("Navasan gold request failed; returning bounded stale quote.")
                return _stale_gold(stale.value, stale.age_seconds)
            raise MarketDataError("No valid gold quote is available.") from exc

        await CACHE.set(_CACHE_KEY, data)
        return data
