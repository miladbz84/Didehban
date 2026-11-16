from __future__ import annotations

import asyncio
import datetime as dt
import logging
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
)

CRYPTO_SYMBOLS = ["BTC", "ETH", "BNB", "SOL", "XRP", "DOGE"]
CACHE = AsyncTTLCache(default_ttl=SETTINGS.cache_ttl)
STALE_IF_ERROR_SECONDS = 2 * 60
PARTIAL_CACHE_TTL_SECONDS = 5
_CACHE_KEY = "crypto:market"
_META_KEY = "_meta"
logger = logging.getLogger("dideban")


def _parse_quote(symbol: str, payload: dict[str, Any]) -> dict[str, Any]:
    expected_symbol = f"{symbol}USDT"
    if payload.get("symbol") != expected_symbol:
        raise ValueError("Unexpected symbol in quote response.")
    price = parse_finite_number(payload.get("lastPrice"), positive=True)
    change = parse_finite_number(payload.get("priceChangePercent"))
    close_time = parse_finite_number(payload.get("closeTime"), positive=True)
    updated_at = dt.datetime.fromtimestamp(
        close_time / 1000,
        tz=dt.timezone.utc,
    ).isoformat()
    return mark_fresh(
        {
            "symbol": symbol,
            "price": price,
            "change_24h": change,
            "updated_at": updated_at,
            "source": "Binance",
        }
    )


async def _fetch_symbol(client: httpx.AsyncClient, symbol: str) -> dict[str, Any]:
    response = await client.get(
        "https://api.binance.com/api/v3/ticker/24hr",
        params={"symbol": f"{symbol}USDT"},
    )
    payload = response_json_object(response)
    return _parse_quote(symbol, payload)


def _stale_result(cached: dict[str, Any], age_seconds: float, failed: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for symbol in CRYPTO_SYMBOLS:
        quote = cached.get(symbol)
        if isinstance(quote, dict):
            quote_age = float(quote.get("stale_age_seconds", 0)) + age_seconds
            if quote_age <= STALE_IF_ERROR_SECONDS:
                result[symbol] = mark_stale(quote, quote_age)
    result[_META_KEY] = {
        "is_partial": len(result) < len(CRYPTO_SYMBOLS),
        "is_stale": True,
        "stale_age_seconds": int(age_seconds),
        "failed_symbols": failed,
    }
    return result


async def fetch_crypto_prices(*, force_refresh: bool = False) -> dict[str, dict[str, Any]]:
    """Fetch valid Binance quotes; identify partial and bounded stale results."""
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

        async with httpx.AsyncClient(timeout=8.0) as client:
            results = await asyncio.gather(
                *(_fetch_symbol(client, symbol) for symbol in CRYPTO_SYMBOLS),
                return_exceptions=True,
            )

        data: dict[str, dict[str, Any]] = {}
        failures: list[str] = []
        for symbol, result in zip(CRYPTO_SYMBOLS, results):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                log_fetch_failure(logger, "Binance", result, symbol=symbol)
                failures.append(symbol)
                continue
            data[symbol] = result

        if not data:
            if stale is not None and isinstance(stale.value, dict):
                logger.warning(
                    "All Binance symbol requests failed; returning bounded stale quotes."
                )
                stale_result = _stale_result(stale.value, stale.age_seconds, failures)
                if len(stale_result) > 1:
                    return stale_result
            raise MarketDataError("No valid cryptocurrency quotes are available.")

        if stale is not None and isinstance(stale.value, dict):
            for symbol in failures:
                old_quote = stale.value.get(symbol)
                if isinstance(old_quote, dict):
                    quote_age = float(old_quote.get("stale_age_seconds", 0)) + stale.age_seconds
                    if quote_age <= STALE_IF_ERROR_SECONDS:
                        data[symbol] = mark_stale(old_quote, quote_age)

        data[_META_KEY] = {
            "is_partial": bool(failures),
            "is_stale": False,
            "stale_age_seconds": 0,
            "failed_symbols": failures,
        }
        await CACHE.set(
            _CACHE_KEY,
            data,
            ttl=PARTIAL_CACHE_TTL_SECONDS if failures else None,
        )
        return data
