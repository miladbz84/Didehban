from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)


def _get_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_int(value: str | None, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_owner_id(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized.isascii() or not normalized.isdigit():
        return None
    try:
        owner_id = int(normalized)
    except ValueError:
        return None
    return owner_id if owner_id > 0 else None


def _get_navasan_price_unit(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    return normalized if normalized in {"IRR", "TOMAN"} else None


@dataclass(frozen=True)
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    owner_username: str = os.getenv("OWNER_USERNAME", "")
    owner_id: int | None = _get_owner_id(os.getenv("OWNER_ID"))
    navasan_api_key: str = os.getenv("NAVASAN_API_KEY", "")
    navasan_dollar_price_unit: str | None = _get_navasan_price_unit(
        os.getenv("NAVASAN_DOLLAR_PRICE_UNIT")
    )
    navasan_gold_price_unit: str | None = _get_navasan_price_unit(
        os.getenv("NAVASAN_GOLD_PRICE_UNIT")
    )
    twelve_data_api_key: str = os.getenv("TWELVE_DATA_API_KEY", "")
    oil_wti_symbol: str = os.getenv("OIL_WTI_SYMBOL", "WTI/USD")
    oil_brent_symbol: str = os.getenv("OIL_BRENT_SYMBOL", "BRENT/USD")
    cache_ttl: int = _get_int(os.getenv("CACHE_TTL"), 30)
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    database_path: str = str(BASE_DIR / "data" / "dideban.db")
    logs_dir: str = str(BASE_DIR / "logs")


SETTINGS = Settings()


def get_settings() -> Settings:
    return SETTINGS


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("dideban")
    logger.setLevel(getattr(logging, SETTINGS.log_level.upper(), logging.INFO))
    logger.propagate = False

    if SETTINGS.owner_id is None:
        logger.warning("OWNER_ID is missing or invalid; owner-only access is disabled.")

    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        from logging.handlers import RotatingFileHandler

        log_dir = Path(SETTINGS.logs_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "dideban.log",
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    return logger
