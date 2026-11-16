from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Iterable

from telegram import Update
from telegram.error import RetryAfter, TelegramError
from telegram.ext import ContextTypes

from database import get_db
from handlers.admin import ADMIN_INPUT_KEY, is_admin_authorized
from handlers.admin_input import BROADCAST_DRAFT_KEY
from keyboards.admin import admin_cancel_menu, admin_main_menu

logger = logging.getLogger("dideban")
SEND_INTERVAL_SECONDS = 0.05
MAX_RATE_LIMIT_RETRIES = 2


async def send_broadcast(
    bot,
    users: Iterable,
    source_chat_id: int,
    source_message_id: int,
    *,
    delay: float = SEND_INTERVAL_SECONDS,
    sleep=asyncio.sleep,
) -> tuple[int, int]:
    sent = 0
    failed = 0
    for user in users:
        target_id = int(user["user_id"])
        retries = 0
        while True:
            try:
                await bot.copy_message(
                    chat_id=target_id,
                    from_chat_id=source_chat_id,
                    message_id=source_message_id,
                )
                sent += 1
                break
            except RetryAfter as exc:
                retries += 1
                if retries > MAX_RATE_LIMIT_RETRIES:
                    failed += 1
                    logger.warning("Broadcast rate limit retry exhausted for user %s.", target_id)
                    break
                retry_after = getattr(exc, "retry_after", 1)
                seconds = retry_after.total_seconds() if hasattr(retry_after, "total_seconds") else float(retry_after)
                await sleep(max(seconds, 0.1))
            except TelegramError as exc:
                failed += 1
                logger.warning(
                    "Broadcast delivery failed for user %s (%s).",
                    target_id,
                    type(exc).__name__,
                )
                break
            except Exception as exc:
                failed += 1
                logger.error(
                    "Unexpected broadcast delivery failure for user %s (%s).",
                    target_id,
                    type(exc).__name__,
                )
                break
        await sleep(delay)
    return sent, failed


async def broadcast_callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if query is None or query.from_user is None:
        return
    try:
        authorized = is_admin_authorized(query.from_user.id)
    except sqlite3.Error as exc:
        logger.error("Broadcast authorization database failure (%s).", type(exc).__name__)
        await query.answer("خطا در بررسی مجوز؛ دوباره تلاش کنید.", show_alert=True)
        return
    if not authorized:
        await query.answer("دسترسی ممنوع است", show_alert=True)
        return

    await query.answer()
    data = query.data or ""
    if data == "broadcast_start":
        context.user_data.pop(BROADCAST_DRAFT_KEY, None)
        context.user_data[ADMIN_INPUT_KEY] = "broadcast_waiting"
        await query.edit_message_text(
            "📨 پیام موردنظر را ارسال کنید تا پیش‌نمایش آن برای تأیید آماده شود.",
            reply_markup=admin_cancel_menu(),
        )
        return

    if data == "broadcast_cancel":
        context.user_data.pop(ADMIN_INPUT_KEY, None)
        context.user_data.pop(BROADCAST_DRAFT_KEY, None)
        await query.edit_message_text("❌ ارسال همگانی لغو شد.", reply_markup=admin_main_menu())
        return

    if data == "broadcast_confirm":
        draft = context.user_data.get(BROADCAST_DRAFT_KEY)
        if not isinstance(draft, dict):
            await query.edit_message_text(
                "⚠️ پیش‌نمایش منقضی شده است؛ ارسال انجام نشد.",
                reply_markup=admin_main_menu(),
            )
            return
        try:
            users = get_db().list_users(active_only=True)
        except sqlite3.Error as exc:
            logger.error("Broadcast recipient lookup failed (%s).", type(exc).__name__)
            await query.edit_message_text(
                "⚠️ دریافت فهرست کاربران ناموفق بود؛ پیامی ارسال نشد.",
                reply_markup=admin_main_menu(),
            )
            return
        if not users:
            context.user_data.pop(BROADCAST_DRAFT_KEY, None)
            await query.edit_message_text(
                "ℹ️ کاربر فعالی برای ارسال وجود ندارد.",
                reply_markup=admin_main_menu(),
            )
            return

        await query.edit_message_text("⏳ ارسال کنترل‌شدهٔ پیام همگانی آغاز شد...")
        sent, failed = await send_broadcast(
            context.bot,
            users,
            int(draft["chat_id"]),
            int(draft["message_id"]),
        )
        context.user_data.pop(BROADCAST_DRAFT_KEY, None)
        await query.edit_message_text(
            f"📨 ارسال همگانی پایان یافت.\n✅ موفق: {sent}\n❌ ناموفق: {failed}",
            reply_markup=admin_main_menu(),
        )
        return

    await query.edit_message_text("⚠️ گزینه نامعتبر", reply_markup=admin_main_menu())
