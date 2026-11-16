from __future__ import annotations

import logging
from typing import Any

import httpx

from config import SETTINGS
from services.cache import AsyncTTLCache
from services.market_data import (
    MarketDataError,
    log_fetch_failure,
    parse_finite_number,
    response_json_object,
    utc_timestamp,
)

CACHE = AsyncTTLCache(default_ttl=SETTINGS.cache_ttl)
STALE_IF_ERROR_SECONDS = 30 * 60
PARTIAL_CACHE_TTL_SECONDS = 5
_CACHE_KEY = "oil:twelve-data"
_META_KEY = "_meta"
logger = logging.getLogger("dideban")


def _parse_price(payload: dict[str, Any]) -> float:
    price = parse_finite_number(payload.get("price"), positive=True)
    if price > 1_000:
        raise ValueError("Oil price is outside the accepted USD-per-barrel range.")
    return price


def _stale_result(cached: dict[str, Any], age_seconds: float, failed: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    stale_symbols: list[str] = []
    stale_ages = cached.get("quote_stale_age_seconds", {})
    for symbol in (SETTINGS.oil_wti_symbol, SETTINGS.oil_brent_symbol):
        quote_age = float(stale_ages.get(symbol, 0)) + age_seconds
        if symbol in cached and quote_age <= STALE_IF_ERROR_SECONDS:
            result[symbol] = cached[symbol]
            stale_symbols.append(symbol)
    result["updated_at"] = cached.get("updated_at")
    result["source"] = "Twelve Data"
    result["quote_updated_at"] = cached.get("quote_updated_at", {})
    result["quote_stale_age_seconds"] = {
        symbol: int(float(stale_ages.get(symbol, 0)) + age_seconds)
        for symbol in stale_symbols
    }
    result["is_stale"] = True
    result["stale_age_seconds"] = int(age_seconds)
    result[_META_KEY] = {
        "is_partial": len(stale_symbols) < 2,
        "is_stale": True,
        "stale_age_seconds": int(age_seconds),
        "failed_symbols": failed,
        "stale_symbols": stale_symbols,
        "quote_updated_at": result["quote_updated_at"],
        "quote_stale_age_seconds": result["quote_stale_age_seconds"],
    }
    return result


async def get_oil_data(*, force_refresh: bool = False) -> dict[str, Any]:
    api_key = SETTINGS.twelve_data_api_key
    if not api_key:
        raise MarketDataError("Twelve Data API key is not configured.")

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
        symbols = (SETTINGS.oil_wti_symbol, SETTINGS.oil_brent_symbol)
        results: dict[str, Any] = {}
        failed: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                for symbol in symbols:
                    try:
                        response = await client.get(
                            "https://api.twelvedata.com/price",
                            params={"symbol": symbol, "apikey": api_key},
                        )
                        payload = response_json_object(response)
                        results[symbol] = _parse_price(payload)
                    except (httpx.HTTPError, MarketDataError, ValueError, TypeError) as exc:
                        log_fetch_failure(logger, "Twelve Data", exc, symbol=symbol)
                        failed.append(symbol)
        except (httpx.HTTPError, OSError) as exc:
            log_fetch_failure(logger, "Twelve Data", exc)
            failed.extend(symbol for symbol in symbols if symbol not in failed)

        if not results:
            if stale is not None and isinstance(stale.value, dict):
                logger.warning("All Twelve Data oil quotes failed; returning bounded stale quotes.")
                stale_result = _stale_result(stale.value, stale.age_seconds, failed)
                if any(
                    symbol in stale_result for symbol in symbols
                ):
                    return stale_result
            raise MarketDataError("No valid oil prices are available.")

        stale_symbols: list[str] = []
        quote_updated_at = {}
        quote_stale_ages = {}
        if stale is not None and isinstance(stale.value, dict):
            quote_updated_at.update(stale.value.get("quote_updated_at", {}))
            quote_stale_ages.update(stale.value.get("quote_stale_age_seconds", {}))
            for symbol in failed:
                if symbol in stale.value:
                    quote_age = float(quote_stale_ages.get(symbol, 0)) + stale.age_seconds
                    if quote_age <= STALE_IF_ERROR_SECONDS:
                        results[symbol] = stale.value[symbol]
                        stale_symbols.append(symbol)
                        quote_stale_ages[symbol] = int(quote_age)
        now = utc_timestamp()
        for symbol in results:
            if symbol not in stale_symbols:
                quote_updated_at[symbol] = now
                quote_stale_ages.pop(symbol, None)
        data = {
            **results,
            "updated_at": now,
            "quote_updated_at": quote_updated_at,
            "quote_stale_age_seconds": quote_stale_ages,
            "source": "Twelve Data",
            "is_stale": False,
            "stale_age_seconds": 0,
            _META_KEY: {
                "is_partial": bool(failed),
                "is_stale": False,
                "stale_age_seconds": 0,
                "failed_symbols": failed,
                "stale_symbols": stale_symbols,
                "quote_updated_at": quote_updated_at,
                "quote_stale_age_seconds": quote_stale_ages,
            },
        }
        await CACHE.set(
            _CACHE_KEY,
            data,
            ttl=PARTIAL_CACHE_TTL_SECONDS if failed else None,
        )
        return data
