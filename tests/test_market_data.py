from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from handlers import user
from services import crypto, dollar, gold, oil
from services.cache import AsyncTTLCache, CachedValue
from services.market_data import MarketDataError, parse_finite_number, response_json_object


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeAsyncClient:
    def __init__(self, route):
        self.route = route

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, **kwargs):
        return await self.route(url, **kwargs)


def _mock_client(monkeypatch, module, route):
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: FakeAsyncClient(route))


def _settings(monkeypatch, **values):
    defaults = {
        "cache_ttl": 30,
        "navasan_api_key": "test-key",
        "navasan_dollar_price_unit": "IRR",
        "navasan_gold_price_unit": "IRR",
        "twelve_data_api_key": "test-key",
        "oil_wti_symbol": "WTI/USD",
        "oil_brent_symbol": "BRENT/USD",
    }
    defaults.update(values)
    monkeypatch.setattr(crypto, "SETTINGS", SimpleNamespace(**defaults))
    monkeypatch.setattr(dollar, "SETTINGS", SimpleNamespace(**defaults))
    monkeypatch.setattr(gold, "SETTINGS", SimpleNamespace(**defaults))
    monkeypatch.setattr(oil, "SETTINGS", SimpleNamespace(**defaults))


@pytest.mark.parametrize("value", [None, True, "NaN", "inf", "-inf", "not a price"])
def test_market_data_rejects_invalid_numbers(value):
    with pytest.raises(ValueError):
        parse_finite_number(value, positive=True)


def test_market_data_accepts_finite_numeric_values_and_rejects_zero_price():
    assert parse_finite_number("12.5", positive=True) == 12.5
    with pytest.raises(ValueError):
        parse_finite_number(0, positive=True)


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse({"price": 1}, status_code=503),
        FakeResponse(ValueError("invalid json")),
        FakeResponse("<html>error</html>"),
        FakeResponse({"status": "error", "message": "failed"}),
    ],
)
def test_response_json_object_rejects_http_and_invalid_payloads(response):
    with pytest.raises(MarketDataError):
        response_json_object(response)


@pytest.mark.asyncio
async def test_cache_expiry_preserves_bounded_stale_value_and_clear(monkeypatch):
    cache = AsyncTTLCache(default_ttl=2)
    import services.cache as cache_module

    current_time = [100.0]
    monkeypatch.setattr(cache_module.time, "monotonic", lambda: current_time[0])
    await cache.set("quote", {"price": 9}, ttl=1)
    current_time[0] = 103.0
    assert await cache.get("quote") is None
    stale = await cache.get_stale("quote", 5)
    assert stale == CachedValue({"price": 9}, True, 3.0)
    await cache.clear()
    assert await cache.get_stale("quote", 5) is None


def _valid_crypto_payload(symbol):
    return {
        "symbol": f"{symbol}USDT",
        "lastPrice": "123.45",
        "priceChangePercent": "-1.25",
        "closeTime": "1700000000000",
    }


@pytest.mark.asyncio
async def test_crypto_keeps_valid_symbols_and_marks_partial_failure(monkeypatch):
    _settings(monkeypatch)
    monkeypatch.setattr(crypto, "CACHE", AsyncTTLCache(default_ttl=30))

    async def route(url, *, params):
        if params["symbol"] == "ETHUSDT":
            raise httpx.ReadTimeout("timeout")
        return FakeResponse(_valid_crypto_payload(params["symbol"][:-4]))

    _mock_client(monkeypatch, crypto, route)
    prices = await crypto.fetch_crypto_prices(force_refresh=True)

    assert "BTC" in prices
    assert "ETH" not in prices
    assert prices["_meta"]["is_partial"] is True
    assert prices["_meta"]["failed_symbols"] == ["ETH"]
    assert (await crypto.CACHE.get(crypto._CACHE_KEY)) is not None


@pytest.mark.asyncio
async def test_crypto_all_failed_does_not_cache_empty_result(monkeypatch):
    _settings(monkeypatch)
    monkeypatch.setattr(crypto, "CACHE", AsyncTTLCache(default_ttl=30))

    async def route(url, **kwargs):
        raise httpx.ConnectError("connection failed")

    _mock_client(monkeypatch, crypto, route)
    with pytest.raises(MarketDataError):
        await crypto.fetch_crypto_prices(force_refresh=True)
    assert await crypto.CACHE.get(crypto._CACHE_KEY) is None


@pytest.mark.asyncio
async def test_crypto_uses_stale_quote_and_retains_its_age(monkeypatch):
    _settings(monkeypatch)
    monkeypatch.setattr(crypto, "CACHE", AsyncTTLCache(default_ttl=30))
    old = {"BTC": {"symbol": "BTC", "price": 100.0, "change_24h": 1.0, "updated_at": "old"}}
    crypto.CACHE.get_stale = AsyncMock(return_value=CachedValue(old, True, 20.0))

    async def route(url, **kwargs):
        raise httpx.ConnectError("offline")

    _mock_client(monkeypatch, crypto, route)
    result = await crypto.fetch_crypto_prices(force_refresh=True)
    assert result["BTC"]["is_stale"] is True
    assert result["BTC"]["stale_age_seconds"] == 20
    assert result["_meta"]["is_stale"] is True


@pytest.mark.asyncio
async def test_crypto_does_not_serve_stale_quote_past_maximum_age(monkeypatch):
    _settings(monkeypatch)
    monkeypatch.setattr(crypto, "CACHE", AsyncTTLCache(default_ttl=30))
    old = {"BTC": {"symbol": "BTC", "price": 100.0, "change_24h": 1.0, "updated_at": "old"}}
    monkeypatch.setattr(
        crypto.CACHE,
        "get_stale",
        AsyncMock(return_value=CachedValue(old, True, crypto.STALE_IF_ERROR_SECONDS + 1)),
    )

    async def route(url, **kwargs):
        raise httpx.ConnectError("offline")

    _mock_client(monkeypatch, crypto, route)
    with pytest.raises(MarketDataError):
        await crypto.fetch_crypto_prices(force_refresh=True)


@pytest.mark.asyncio
async def test_market_handler_refreshes_and_ignores_crypto_metadata(monkeypatch):
    class Query:
        data = "refresh_crypto"
        from_user = SimpleNamespace(id=42)
        edits = []

        async def answer(self):
            return None

        async def edit_message_text(self, text, reply_markup=None):
            self.edits.append(text)

    monkeypatch.setattr(user, "_update_user_activity", lambda user_id: None)
    monkeypatch.setattr(user, "get_active_sponsors", lambda: [])
    fetch = AsyncMock(
        return_value={
            "BTC": {
                "price": 123.0,
                "change_24h": 1.0,
                "updated_at": "now",
            },
            "_meta": {
                "is_partial": True,
                "failed_symbols": ["ETH"],
                "is_stale": False,
            },
        }
    )
    monkeypatch.setattr(user, "fetch_crypto_prices", fetch)
    query = Query()
    await user.handle_user_callback(
        SimpleNamespace(callback_query=query),
        SimpleNamespace(bot=SimpleNamespace()),
    )

    fetch.assert_awaited_once_with(force_refresh=True)
    rendered = query.edits[-1]
    assert "BTC" in rendered
    assert "ETH" in rendered
    assert "_meta" not in rendered


@pytest.mark.asyncio
async def test_oil_rejects_dash_without_caching_but_keeps_other_symbol(monkeypatch):
    _settings(monkeypatch)
    monkeypatch.setattr(oil, "CACHE", AsyncTTLCache(default_ttl=30))

    async def route(url, *, params):
        price = "-" if params["symbol"] == "WTI/USD" else "82.4"
        return FakeResponse({"price": price})

    _mock_client(monkeypatch, oil, route)
    result = await oil.get_oil_data(force_refresh=True)

    assert "WTI/USD" not in result
    assert result["BRENT/USD"] == 82.4
    assert result["_meta"]["is_partial"] is True
    assert result["_meta"]["failed_symbols"] == ["WTI/USD"]


@pytest.mark.asyncio
async def test_oil_all_invalid_quotes_raise_and_do_not_cache(monkeypatch):
    _settings(monkeypatch)
    monkeypatch.setattr(oil, "CACHE", AsyncTTLCache(default_ttl=30))

    async def route(url, *, params):
        return FakeResponse({"price": "NaN"})

    _mock_client(monkeypatch, oil, route)
    with pytest.raises(MarketDataError):
        await oil.get_oil_data(force_refresh=True)
    assert await oil.CACHE.get(oil._CACHE_KEY) is None


@pytest.mark.asyncio
async def test_oil_uses_bounded_stale_quotes_after_source_failures(monkeypatch):
    _settings(monkeypatch)
    monkeypatch.setattr(oil, "CACHE", AsyncTTLCache(default_ttl=30))
    old = {
        "WTI/USD": 70.0,
        "BRENT/USD": 80.0,
        "updated_at": "previous",
        "quote_updated_at": {},
        "quote_stale_age_seconds": {"WTI/USD": 40},
    }
    await oil.CACHE.set(oil._CACHE_KEY, old)
    monkeypatch.setattr(
        oil.CACHE,
        "get_stale",
        AsyncMock(return_value=CachedValue(old, True, 30.0)),
    )

    async def route(url, **kwargs):
        raise httpx.ConnectError("offline")

    _mock_client(monkeypatch, oil, route)
    result = await oil.get_oil_data(force_refresh=True)

    assert result["WTI/USD"] == 70
    assert result["BRENT/USD"] == 80
    assert result["_meta"]["is_stale"] is True
    assert result["quote_stale_age_seconds"]["WTI/USD"] == 70
    assert await oil.CACHE.get(oil._CACHE_KEY) == old


@pytest.mark.asyncio
async def test_dollar_rejects_invalid_refresh_and_uses_stale_without_leaking_key(
    monkeypatch, caplog
):
    secret = "test-secret-do-not-log"
    _settings(monkeypatch, navasan_api_key=secret)
    monkeypatch.setattr(dollar, "CACHE", AsyncTTLCache(default_ttl=30))
    old = {"buy_toman": 10, "sell_toman": 11, "updated_at": "yesterday"}
    await dollar.CACHE.set(dollar._CACHE_KEY, old)
    dollar.CACHE.get_stale = AsyncMock(return_value=CachedValue(old, True, 90.0))

    async def route(url, **kwargs):
        return FakeResponse({"usd": {"buy": "NaN", "sell": "12"}}, status_code=200)

    _mock_client(monkeypatch, dollar, route)
    result = await dollar.get_dollar_data(force_refresh=True)

    assert result["buy_toman"] == 10
    assert result["is_stale"] is True
    assert secret not in caplog.text
    assert await dollar.CACHE.get(dollar._CACHE_KEY) == old


@pytest.mark.asyncio
async def test_dollar_preserves_rial_to_toman_conversion(monkeypatch):
    _settings(monkeypatch)
    monkeypatch.setattr(dollar, "CACHE", AsyncTTLCache(default_ttl=30))

    async def route(url, **kwargs):
        return FakeResponse({"usd": {"buy": "500000", "sell": "510000"}})

    _mock_client(monkeypatch, dollar, route)
    result = await dollar.get_dollar_data(force_refresh=True)
    assert result["buy_rial"] == 500000
    assert result["buy_toman"] == 50000
    assert result["sell_toman"] == 51000


@pytest.mark.asyncio
async def test_gold_missing_or_invalid_price_is_not_cached(monkeypatch):
    _settings(monkeypatch)
    monkeypatch.setattr(gold, "CACHE", AsyncTTLCache(default_ttl=30))

    async def route(url, **kwargs):
        return FakeResponse({"gold": {"price": "Infinity"}})

    _mock_client(monkeypatch, gold, route)
    with pytest.raises(MarketDataError):
        await gold.get_gold_data(force_refresh=True)
    assert await gold.CACHE.get(gold._CACHE_KEY) is None


@pytest.mark.asyncio
async def test_gold_preserves_rial_to_toman_conversion_and_missing_change(monkeypatch):
    _settings(monkeypatch)
    monkeypatch.setattr(gold, "CACHE", AsyncTTLCache(default_ttl=30))

    async def route(url, **kwargs):
        return FakeResponse({"gold": {"price": "2500000"}})

    _mock_client(monkeypatch, gold, route)
    result = await gold.get_gold_data(force_refresh=True)
    assert result["price_rial"] == 2500000
    assert result["price_toman"] == 250000
    assert result["change"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "unit_setting", "cache_key", "payload", "call_service", "expected"),
    [
        (
            dollar,
            "navasan_dollar_price_unit",
            "dollar:navasan",
            {"usd": {"buy": "50000", "sell": "51000"}},
            dollar.get_dollar_data,
            {"buy_rial": 500000, "buy_toman": 50000, "sell_toman": 51000},
        ),
        (
            gold,
            "navasan_gold_price_unit",
            "gold:navasan",
            {"gold": {"price": "250000"}},
            gold.get_gold_data,
            {"price_rial": 2500000, "price_toman": 250000},
        ),
    ],
)
async def test_navasan_toman_source_is_not_divided_again(
    monkeypatch, service, unit_setting, cache_key, payload, call_service, expected
):
    _settings(monkeypatch, **{unit_setting: "TOMAN"})
    monkeypatch.setattr(service, "CACHE", AsyncTTLCache(default_ttl=30))

    async def route(url, **kwargs):
        return FakeResponse(payload)

    _mock_client(monkeypatch, service, route)
    result = await call_service(force_refresh=True)

    for field, value in expected.items():
        assert result[field] == value
    assert result["currency_unit"] == "TOMAN"
    assert await service.CACHE.get(cache_key) == result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "unit_setting", "call_service", "cache_key"),
    [
        (dollar, "navasan_dollar_price_unit", dollar.get_dollar_data, "dollar:navasan"),
        (gold, "navasan_gold_price_unit", gold.get_gold_data, "gold:navasan"),
    ],
)
@pytest.mark.parametrize("unit", [None, "", "UNKNOWN", "MISSING"])
async def test_navasan_unit_must_be_explicitly_confirmed(
    monkeypatch, service, unit_setting, call_service, cache_key, unit
):
    _settings(monkeypatch, **{unit_setting: unit})
    if unit == "MISSING":
        delattr(service.SETTINGS, unit_setting)
    monkeypatch.setattr(service, "CACHE", AsyncTTLCache(default_ttl=30))

    async def unexpected_request(url, **kwargs):
        pytest.fail("Unconfirmed source units must prevent an API request.")

    _mock_client(monkeypatch, service, unexpected_request)
    with pytest.raises(MarketDataError, match="unit is not confirmed"):
        await call_service(force_refresh=True)
    assert await service.CACHE.get(cache_key) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "call_service", "cached"),
    [
        (
            crypto,
            crypto.fetch_crypto_prices,
            {"BTC": {"symbol": "BTC", "price": 100.0, "change_24h": 0.0, "updated_at": "old"}},
        ),
        (
            dollar,
            dollar.get_dollar_data,
            {"buy_toman": 10.0, "sell_toman": 11.0, "updated_at": "old"},
        ),
        (
            gold,
            gold.get_gold_data,
            {"price_toman": 10.0, "updated_at": "old"},
        ),
        (
            oil,
            oil.get_oil_data,
            {"WTI/USD": 70.0, "updated_at": "old", "quote_stale_age_seconds": {}},
        ),
    ],
)
@pytest.mark.parametrize("at_limit", [True, False])
async def test_market_specific_stale_fallback_limits(
    monkeypatch, module, call_service, cached, at_limit
):
    _settings(monkeypatch)
    monkeypatch.setattr(module, "CACHE", AsyncTTLCache(default_ttl=30))
    max_age = module.STALE_IF_ERROR_SECONDS
    age = max_age
    if not at_limit:
        age += 1
    module.CACHE.get_stale = AsyncMock(
        side_effect=lambda key, max_age: (
            CachedValue(cached, True, age) if age <= max_age else None
        )
    )

    async def route(url, **kwargs):
        raise httpx.ConnectError("offline")

    _mock_client(monkeypatch, module, route)

    if at_limit:
        result = await call_service(force_refresh=True)
        if module is crypto:
            assert result["BTC"]["is_stale"] is True
            assert result["BTC"]["stale_age_seconds"] == int(age)
        elif module is oil:
            assert result["_meta"]["is_stale"] is True
            assert result["quote_stale_age_seconds"]["WTI/USD"] == int(age)
        else:
            assert result["is_stale"] is True
            assert result["stale_age_seconds"] == int(age)
    else:
        with pytest.raises(MarketDataError):
            await call_service(force_refresh=True)

    module.CACHE.get_stale.assert_awaited_once_with(module._CACHE_KEY, max_age)


def test_market_specific_stale_fallback_budgets():
    assert crypto.STALE_IF_ERROR_SECONDS == 2 * 60
    assert dollar.STALE_IF_ERROR_SECONDS == 5 * 60
    assert gold.STALE_IF_ERROR_SECONDS == 10 * 60
    assert oil.STALE_IF_ERROR_SECONDS == 30 * 60
