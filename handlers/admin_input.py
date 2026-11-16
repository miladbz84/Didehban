from __future__ import annotations

import logging
import sqlite3

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from config import SETTINGS
from database import get_db
from handlers.admin import ADMIN_INPUT_KEY, is_admin_authorized, is_owner_authorized
from keyboards.admin import (
    admin_management_menu,
    broadcast_confirmation_menu,
    sponsor_admin_menu,
)
from services.sponsor import SponsorInputError, add_sponsor_from_input

logger = logging.getLogger("dideban")
BROADCAST_DRAFT_KEY = "broadcast_draft"


async def admin_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    action = context.user_data.get(ADMIN_INPUT_KEY)
    if not action:
        return
    try:
        authorized = is_admin_authorized(user.id)
    except sqlite3.Error as exc:
        logger.error("Admin input authorization database failure (%s).", type(exc).__name__)
        context.user_data.pop(ADMIN_INPUT_KEY, None)
        await message.reply_text("⚠️ بررسی مجوز مدیریت ناموفق بود.")
        return
    if not authorized:
        context.user_data.pop(ADMIN_INPUT_KEY, None)
        await message.reply_text("⛔ دسترسی شما به این عملیات لغو شد.")
        return

    if action in {"admin_add", "admin_remove"}:
        if not is_owner_authorized(user.id):
            context.user_data.pop(ADMIN_INPUT_KEY, None)
            await message.reply_text("⛔ فقط مالک می‌تواند ادمین‌ها را مدیریت کند.")
            return
        value = message.text or ""
        from handlers.admin import _parse_user_id

        target_id = _parse_user_id(value)
        if target_id is None:
            await message.reply_text(
                "⚠️ شناسه باید یک عدد مثبت باشد. دوباره ارسال کنید یا عملیات را لغو کنید."
            )
            return
        if target_id == SETTINGS.owner_id:
            await message.reply_text("⛔ شناسهٔ مالک برای این عملیات مجاز نیست.")
            return
        try:
            db = get_db()
            if action == "admin_add":
                if db.get_admin_role(target_id) is not None:
                    await message.reply_text(
                        "ℹ️ این کاربر از قبل ادمین است.",
                        reply_markup=admin_management_menu(),
                    )
                    context.user_data.pop(ADMIN_INPUT_KEY, None)
                    return
                db.add_admin(target_id, None)
                if db.get_admin_role(target_id) != "ADMIN":
                    raise RuntimeError("Administrator record was not persisted.")
                result = f"✅ ادمین {target_id} با موفقیت افزوده شد."
            else:
                if not db.remove_admin(target_id):
                    await message.reply_text(
                        "ℹ️ ادمینی با این شناسه پیدا نشد.",
                        reply_markup=admin_management_menu(),
                    )
                    context.user_data.pop(ADMIN_INPUT_KEY, None)
                    return
                if db.get_admin_role(target_id) is not None:
                    raise RuntimeError("Administrator record still exists after removal.")
                result = f"✅ ادمین {target_id} حذف شد."
            context.user_data.pop(ADMIN_INPUT_KEY, None)
            await message.reply_text(result, reply_markup=admin_management_menu())
        except sqlite3.IntegrityError:
            context.user_data.pop(ADMIN_INPUT_KEY, None)
            await message.reply_text(
                "ℹ️ این شناسه قبلاً ثبت شده است.",
                reply_markup=admin_management_menu(),
            )
        except (sqlite3.Error, RuntimeError) as exc:
            logger.error("Administrator database operation failed (%s).", type(exc).__name__)
            context.user_data.pop(ADMIN_INPUT_KEY, None)
            await message.reply_text("⚠️ عملیات ادمین در پایگاه داده ناموفق بود.")
        return

    if action == "broadcast_waiting":
        if message.text is not None and not message.text.strip():
            await message.reply_text("⚠️ پیام خالی قابل ارسال نیست.")
            return
        context.user_data[BROADCAST_DRAFT_KEY] = {
            "chat_id": message.chat_id,
            "message_id": message.message_id,
        }
        context.user_data.pop(ADMIN_INPUT_KEY, None)
        await message.reply_text(
            "👆 پیش‌نمایش بالا را بررسی کنید. برای ارسال به کاربران فعال، تأیید کنید.",
            reply_markup=broadcast_confirmation_menu(),
        )
        return

    if action == "sponsor_add_waiting":
        try:
            saved = await add_sponsor_from_input(
                context.bot,
                message.text or "",
                get_db(),
            )
        except SponsorInputError as exc:
            await message.reply_text(
                f"⚠️ ورودی نامعتبر: {exc}",
                reply_markup=sponsor_admin_menu(),
            )
            context.user_data.pop(ADMIN_INPUT_KEY, None)
            return
        except (TelegramError, sqlite3.Error, RuntimeError) as exc:
            logger.error("Sponsor addition failed (%s).", type(exc).__name__)
            await message.reply_text(
                "⚠️ افزودن اسپانسر انجام نشد؛ ورودی و دسترسی ربات به کانال را بررسی کنید.",
                reply_markup=sponsor_admin_menu(),
            )
            context.user_data.pop(ADMIN_INPUT_KEY, None)
            return
        context.user_data.pop(ADMIN_INPUT_KEY, None)
        label = saved.get("title") or saved.get("username") or saved["channel_id"]
        await message.reply_text(
            f"✅ اسپانسر {label} با وضعیت فعال در پایگاه داده ثبت شد.",
            reply_markup=sponsor_admin_menu(),
        )
