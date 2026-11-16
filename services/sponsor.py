from __future__ import annotations

import logging
import re
from typing import Any

from telegram.constants import ChatMemberStatus, ChatType

from database import Database, get_db

logger = logging.getLogger("dideban")


class MembershipCheckError(RuntimeError):
    """Raised when Telegram cannot provide a trustworthy membership result."""


class SponsorInputError(ValueError):
    """Raised when a sponsor identifier is not a supported Telegram channel."""


def get_active_sponsors() -> list[dict[str, Any]]:
    db = get_db()
    rows = db.fetch_all(
        "SELECT * FROM sponsors WHERE is_active = 1 ORDER BY added_at DESC"
    )
    return [dict(row) for row in rows]


def get_all_sponsors() -> list[dict[str, Any]]:
    db = get_db()
    rows = db.fetch_all("SELECT * FROM sponsors ORDER BY added_at DESC")
    return [dict(row) for row in rows]


def user_is_active(user_id: int) -> bool:
    db = get_db()
    row = db.fetch_one("SELECT is_active FROM users WHERE user_id = ?", (user_id,))
    return bool(row and row["is_active"])


async def verify_membership(bot, user_id: int, sponsor: dict[str, Any]) -> bool:
    channel_id = sponsor.get("channel_id")
    if not isinstance(channel_id, int) or isinstance(channel_id, bool):
        raise MembershipCheckError("Sponsor channel id is invalid.")
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
    except Exception as exc:
        logger.warning(
            "Telegram membership lookup failed for channel %s (%s).",
            channel_id,
            type(exc).__name__,
        )
        raise MembershipCheckError("Telegram membership lookup failed.") from exc

    status = getattr(member, "status", None)
    if status in {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.OWNER,
    }:
        return True
    if (
        status == ChatMemberStatus.RESTRICTED
        and getattr(member, "is_member", False) is True
    ):
        return True
    if status in {
        ChatMemberStatus.LEFT,
        ChatMemberStatus.BANNED,
        ChatMemberStatus.RESTRICTED,
    }:
        return False
    raise MembershipCheckError("Telegram returned an unknown membership status.")


async def add_sponsor_from_input(
    bot,
    value: str,
    db: Database | None = None,
) -> dict[str, Any]:
    normalized = value.strip()
    if re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{4,31}", normalized):
        identifier: str | int = normalized
        username = normalized
    elif re.fullmatch(r"-[0-9]{1,20}", normalized):
        identifier = int(normalized)
        if identifier >= 0 or identifier < -(2**63):
            raise SponsorInputError("Channel id is outside the supported range.")
        username = None
    else:
        raise SponsorInputError("Use a public @username or a negative numeric channel id.")

    chat = await bot.get_chat(identifier)
    channel_id = getattr(chat, "id", None)
    if getattr(chat, "type", None) not in {ChatType.CHANNEL, ChatType.SUPERGROUP}:
        raise SponsorInputError("The Telegram chat must be a channel or supergroup.")
    if (
        not isinstance(channel_id, int)
        or isinstance(channel_id, bool)
        or not -(2**63) <= channel_id < 0
    ):
        raise SponsorInputError("Telegram did not return a valid channel id.")
    bot_id = getattr(bot, "id", None)
    if not isinstance(bot_id, int) or isinstance(bot_id, bool) or bot_id <= 0:
        raise RuntimeError("Telegram bot identity is unavailable.")
    bot_member = await bot.get_chat_member(chat_id=channel_id, user_id=bot_id)
    if getattr(bot_member, "status", None) not in {
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.OWNER,
    }:
        raise RuntimeError("The bot must be an administrator in the sponsor chat.")
    chat_username = getattr(chat, "username", None)
    if chat_username:
        username = f"@{chat_username}"
    title = getattr(chat, "title", None)

    db = db or get_db()
    db.add_sponsor(
        channel_id=channel_id,
        username=username,
        title=title,
        is_active=True,
    )
    saved = db.get_sponsor(channel_id)
    if saved is None or not bool(saved["is_active"]):
        raise RuntimeError("Sponsor record was not persisted as active.")
    return dict(saved)


def set_sponsor_active(
    channel_id: int,
    is_active: bool,
    db: Database | None = None,
) -> dict[str, Any] | None:
    db = db or get_db()
    if db.get_sponsor(channel_id) is None:
        return None
    if not db.toggle_sponsor(channel_id, is_active):
        raise RuntimeError("Sponsor status update was not persisted.")
    updated = db.get_sponsor(channel_id)
    if updated is None or bool(updated["is_active"]) != is_active:
        raise RuntimeError("Sponsor status could not be verified.")
    return dict(updated)


def delete_sponsor(channel_id: int, db: Database | None = None) -> bool:
    db = db or get_db()
    if not db.delete_sponsor(channel_id):
        return False
    if db.get_sponsor(channel_id) is not None:
        raise RuntimeError("Sponsor record still exists after deletion.")
    return True
