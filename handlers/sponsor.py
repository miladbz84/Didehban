from __future__ import annotations

import logging
import re
import sqlite3

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from database import get_db
from handlers.admin import ADMIN_INPUT_KEY, is_admin_authorized
from keyboards.admin import sponsor_admin_menu, sponsor_cancel_menu
from services.sponsor import (
    delete_sponsor,
    set_sponsor_active,
)

logger = logging.getLogger("dideban")
_CHANNEL_ID = re.compile(r"^-[0-9]{1,20}$")


def _sponsor_controls(sponsors) -> InlineKeyboardMarkup:
    rows = []
    for sponsor in sponsors:
        label = sponsor["title"] or sponsor["username"] or str(sponsor["channel_id"])
        status = "فعال" if sponsor["is_active"] else "غیرفعال"
        rows.append(
            [
                InlineKeyboardButton(
                    f"🔄 {label} ({status})",
                    callback_data=f"sponsor_toggle:{sponsor['channel_id']}",
                ),
                InlineKeyboardButton(
                    f"🗑 {sponsor['channel_id']}",
                    callback_data=f"sponsor_delete:{sponsor['channel_id']}",
                ),
            ]
        )
    rows.extend(sponsor_admin_menu().inline_keyboard)
    return InlineKeyboardMarkup(rows)


def _sponsor_list_text(sponsors) -> str:
    if not sponsors:
        return "📋 اسپانسری ثبت نشده است."
    lines = ["📋 فهرست اسپانسرها\n"]
    for row in sponsors:
        label = row["title"] or row["username"] or str(row["channel_id"])
        status = "فعال" if row["is_active"] else "غیرفعال"
        lines.append(f"- {label} | {row['channel_id']} | {status}")
    return "\n".join(lines)


async def sponsor_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.from_user is None:
        return
    try:
        authorized = is_admin_authorized(query.from_user.id)
    except sqlite3.Error as exc:
        logger.error("Sponsor authorization database failure (%s).", type(exc).__name__)
        await query.answer("خطا در بررسی مجوز؛ دوباره تلاش کنید.", show_alert=True)
        return
    if not authorized:
        await query.answer("دسترسی ممنوع است", show_alert=True)
        return

    await query.answer()
    data = query.data or ""
    if data == "sponsor_add":
        context.user_data[ADMIN_INPUT_KEY] = "sponsor_add_waiting"
        await query.edit_message_text(
            "📢 نام‌کاربری عمومی کانال با @ یا شناسهٔ عددی منفی کانال را ارسال کنید.",
            reply_markup=sponsor_cancel_menu(),
        )
        return
    if data == "sponsor_cancel":
        context.user_data.pop(ADMIN_INPUT_KEY, None)
        await query.edit_message_text("❌ عملیات لغو شد.", reply_markup=sponsor_admin_menu())
        return

    try:
        db = get_db()
        if data in {"sponsor_list", "sponsor_delete", "sponsor_toggle"}:
            context.user_data.pop(ADMIN_INPUT_KEY, None)
            sponsors = db.list_sponsors(active_only=None)
            await query.edit_message_text(
                _sponsor_list_text(sponsors),
                reply_markup=_sponsor_controls(sponsors),
            )
            return

        action, separator, raw_id = data.partition(":")
        if not separator or action not in {"sponsor_delete", "sponsor_toggle"}:
            await query.edit_message_text("⚠️ گزینه نامعتبر", reply_markup=sponsor_admin_menu())
            return
        if not _CHANNEL_ID.fullmatch(raw_id):
            await query.edit_message_text("⚠️ شناسهٔ کانال نامعتبر است.", reply_markup=sponsor_admin_menu())
            return
        channel_id = int(raw_id)
        current = db.get_sponsor(channel_id)
        if current is None:
            await query.edit_message_text(
                "ℹ️ اسپانسر موردنظر پیدا نشد.",
                reply_markup=sponsor_admin_menu(),
            )
            return

        if action == "sponsor_delete":
            if not delete_sponsor(channel_id, db):
                await query.edit_message_text(
                    "ℹ️ اسپانسر موردنظر حذف نشد؛ رکوردی پیدا نشد.",
                    reply_markup=sponsor_admin_menu(),
                )
                return
            result = f"✅ اسپانسر {channel_id} حذف شد."
        else:
            updated = set_sponsor_active(channel_id, not bool(current["is_active"]), db)
            if updated is None:
                await query.edit_message_text(
                    "ℹ️ اسپانسر موردنظر پیدا نشد.",
                    reply_markup=sponsor_admin_menu(),
                )
                return
            status = "فعال" if updated["is_active"] else "غیرفعال"
            result = f"✅ وضعیت اسپانسر {channel_id} در پایگاه داده: {status}."

        sponsors = db.list_sponsors(active_only=None)
        await query.edit_message_text(
            f"{result}\n\n{_sponsor_list_text(sponsors)}",
            reply_markup=_sponsor_controls(sponsors),
        )
    except (sqlite3.Error, RuntimeError) as exc:
        logger.error("Sponsor operation failed (%s).", type(exc).__name__)
        await query.edit_message_text(
            "⚠️ عملیات اسپانسر ناموفق بود؛ پایگاه داده و دسترسی را بررسی کنید.",
            reply_markup=sponsor_admin_menu(),
        )
