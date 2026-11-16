from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping
from typing import Any

import httpx


class MarketDataError(RuntimeError):
    """Raised when a market-data source cannot provide a valid quote."""


def parse_finite_number(value: Any, *, positive: bool = False) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError("Expected a finite numeric value.")
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError("Expected a finite numeric value.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Expected a finite numeric value.") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError("Numeric value is outside the accepted range.")
    return number


def response_json_object(response: httpx.Response) -> dict[str, Any]:
    if not 200 <= response.status_code < 300:
        raise MarketDataError(f"Market-data source returned HTTP {response.status_code}.")
    try:
        payload = response.json()
    except (ValueError, TypeError) as exc:
        raise MarketDataError("Market-data source returned invalid JSON.") from exc
    if not isinstance(payload, Mapping):
        raise MarketDataError("Market-data source returned an unexpected JSON structure.")
    if "status" in payload and str(payload["status"]).lower() == "error":
        raise MarketDataError("Market-data source reported an error.")
    if "code" in payload and str(payload["code"]).lower() not in {"0", "200", "ok"}:
        raise MarketDataError("Market-data source reported an error.")
    if "message" in payload and not any(
        key in payload for key in ("price", "symbol", "usd", "dollar", "gold", "18ayar", "gold_18")
    ):
        raise MarketDataError("Market-data source returned an error message.")
    return dict(payload)


def utc_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def mark_stale(data: dict[str, Any], age_seconds: float) -> dict[str, Any]:
    result = dict(data)
    result["is_stale"] = True
    result["stale_age_seconds"] = int(age_seconds)
    return result


def mark_fresh(data: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    result["is_stale"] = False
    result["stale_age_seconds"] = 0
    return result


def log_fetch_failure(logger, service: str, exc: Exception, *, symbol: str | None = None) -> None:
    if symbol is None:
        logger.warning("%s quote fetch failed (%s).", service, type(exc).__name__)
    else:
        logger.warning(
            "%s quote fetch failed for %s (%s).",
            service,
            symbol,
            type(exc).__name__,
        )
