from __future__ import annotations

import logging
import re
import sqlite3

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import SETTINGS, get_settings
from database import get_db
from keyboards.admin import (
    admin_cancel_menu,
    admin_main_menu,
    admin_management_menu,
    sponsor_admin_menu,
)
from utils.permissions import Role, is_admin, is_owner

logger = logging.getLogger("dideban")
_POSITIVE_ID = re.compile(r"^[0-9]{1,20}$")
ADMIN_INPUT_KEY = "admin_input_action"


def _authorized_role(user_id: int | None) -> str | None:
    if user_id is None:
        return None
    owner_id = SETTINGS.owner_id
    if (
        isinstance(owner_id, int)
        and not isinstance(owner_id, bool)
        and owner_id > 0
        and owner_id == user_id
    ):
        return Role.OWNER.value
    return get_db().get_admin_role(user_id)


def _is_allowed(user_id: int | None) -> bool:
    role = _authorized_role(user_id)
    return bool(role and (is_admin(role) or is_owner(role)))


def is_admin_authorized(user_id: int | None) -> bool:
    return _is_allowed(user_id)


def is_owner_authorized(user_id: int | None) -> bool:
    owner_id = SETTINGS.owner_id
    return (
        user_id is not None
        and isinstance(owner_id, int)
        and not isinstance(owner_id, bool)
        and owner_id > 0
        and user_id == owner_id
    )


def _parse_user_id(value: str) -> int | None:
    normalized = value.strip()
    if not _POSITIVE_ID.fullmatch(normalized):
        return None
    user_id = int(normalized)
    return user_id if 0 < user_id <= 2**63 - 1 else None


def _admin_list_markup(rows) -> InlineKeyboardMarkup | None:
    buttons = [
        [InlineKeyboardButton(
            f"حذف {row['user_id']}",
            callback_data=f"admin_remove:{row['user_id']}",
        )]
        for row in rows
    ]
    if not buttons:
        return None
    buttons.append([InlineKeyboardButton("بازگشت", callback_data="admin_back")])
    return InlineKeyboardMarkup(buttons)


async def admin_command(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_message is None:
        return
    try:
        allowed = _is_allowed(update.effective_user.id)
    except sqlite3.Error as exc:
        logger.error("Admin authorization database failure (%s).", type(exc).__name__)
        await update.effective_message.reply_text("⚠️ بررسی مجوز مدیریت ناموفق بود.")
        return
    if not allowed:
        await update.effective_message.reply_text("⛔ شما اجازه دسترسی به پنل مدیریت را ندارید.")
        return

    await update.effective_message.reply_text("👑 پنل مدیریت", reply_markup=admin_main_menu())


async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.from_user is None:
        return

    user_id = query.from_user.id
    try:
        allowed = _is_allowed(user_id)
    except sqlite3.Error as exc:
        logger.error("Admin authorization database failure (%s).", type(exc).__name__)
        await query.answer("خطا در بررسی مجوز؛ دوباره تلاش کنید.", show_alert=True)
        return
    if not allowed:
        await query.answer("دسترسی ممنوع است", show_alert=True)
        return

    await query.answer()
    data = query.data or ""
    is_owner_user = is_owner_authorized(user_id)
    if data in {
        "admin_admins",
        "admin_add",
        "admin_remove",
        "admin_list",
    } or data.startswith("admin_remove:"):
        if not is_owner_user:
            await query.edit_message_text("⛔ فقط مالک می‌تواند ادمین‌ها را مدیریت کند.")
            return

    try:
        db = get_db()
        if data == "admin_back":
            context.user_data.pop(ADMIN_INPUT_KEY, None)
            await query.edit_message_text("👑 پنل مدیریت", reply_markup=admin_main_menu())
            return

        if data == "admin_cancel":
            context.user_data.pop(ADMIN_INPUT_KEY, None)
            await query.edit_message_text("❌ عملیات لغو شد.", reply_markup=admin_main_menu())
            return

        if data == "admin_sponsors":
            await query.edit_message_text("📢 مدیریت اسپانسرها", reply_markup=sponsor_admin_menu())
            return

        if data == "admin_admins":
            await query.edit_message_text("👥 مدیریت ادمین‌ها", reply_markup=admin_management_menu())
            return

        if data in {"admin_add", "admin_remove"}:
            context.user_data[ADMIN_INPUT_KEY] = data
            prompt = (
                "شناسهٔ عددی کاربر جدید را ارسال کنید."
                if data == "admin_add"
                else "شناسهٔ عددی ادمین برای حذف را ارسال کنید."
            )
            await query.edit_message_text(prompt, reply_markup=admin_cancel_menu())
            return

        if data.startswith("admin_remove:"):
            user_id_to_remove = _parse_user_id(data.partition(":")[2])
            if user_id_to_remove is None:
                await query.edit_message_text("⚠️ شناسهٔ ادمین نامعتبر است.")
                return
            if SETTINGS.owner_id == user_id_to_remove:
                await query.edit_message_text("⛔ مالک قابل حذف نیست.")
                return
            if not db.remove_admin(user_id_to_remove):
                await query.edit_message_text("ℹ️ این شناسه ادمین ثبت‌شده‌ای ندارد.")
                return
            await query.edit_message_text(
                f"✅ ادمین {user_id_to_remove} حذف شد.",
                reply_markup=admin_management_menu(),
            )
            return

        if data == "admin_list":
            admins = db.list_admins()
            if not admins:
                await query.edit_message_text(
                    "📋 ادمینی ثبت نشده است.",
                    reply_markup=admin_management_menu(),
                )
                return
            text = "👥 فهرست ادمین‌ها\n" + "\n".join(
                f"- {row['user_id']} | @{row['username']}" if row["username"]
                else f"- {row['user_id']}"
                for row in admins
            )
            await query.edit_message_text(text, reply_markup=_admin_list_markup(admins))
            return

        if data == "admin_broadcast":
            context.user_data["admin_input_action"] = "broadcast_waiting"
            await query.edit_message_text(
                "📨 پیام موردنظر برای ارسال همگانی را ارسال کنید.",
                reply_markup=admin_cancel_menu(),
            )
            return

        if data == "admin_stats":
            total_users = db.fetch_one("SELECT COUNT(*) AS total FROM users")["total"]
            active_users = db.fetch_one(
                "SELECT COUNT(*) AS total FROM users WHERE is_active = 1"
            )["total"]
            inactive_users = db.fetch_one(
                "SELECT COUNT(*) AS total FROM users WHERE is_active = 0"
            )["total"]
            sponsor_count = db.fetch_one(
                "SELECT COUNT(*) AS total FROM sponsors WHERE is_active = 1"
            )["total"]
            admin_count = len(db.list_admins())
            text = (
                "📊 آمار\n"
                f"👥 کل کاربران: {total_users}\n"
                f"🟢 کاربران فعال: {active_users}\n"
                f"🔴 کاربران غیرفعال: {inactive_users}\n"
                f"📢 تعداد اسپانسرها: {sponsor_count}\n"
                f"👑 تعداد ادمین‌ها: {admin_count}"
            )
            await query.edit_message_text(text, reply_markup=admin_main_menu())
            return

        if data == "admin_users":
            users = db.fetch_all("SELECT * FROM users ORDER BY last_seen DESC LIMIT 10")
            if not users:
                await query.edit_message_text("👤 هیچ کاربری ثبت نشده است.", reply_markup=admin_main_menu())
                return
            lines = ["👤 کاربران\n"]
            for user_row in users:
                status = "فعال" if user_row["is_active"] else "غیرفعال"
                lines.append(f"- {user_row['user_id']} | {user_row['first_name'] or '-'} | {status}")
            await query.edit_message_text("\n".join(lines), reply_markup=admin_main_menu())
            return

        if data == "admin_errors":
            errors = db.get_recent_errors(limit=5)
            if not errors:
                await query.edit_message_text("🐞 هیچ خطایی ثبت نشده است.", reply_markup=admin_main_menu())
                return
            lines = ["🐞 گزارش خطاها\n"]
            for err in errors:
                lines.append(f"#{err['id']} | {err['error_type']} | {err['message']} | {err['count']}")
            await query.edit_message_text("\n".join(lines), reply_markup=admin_main_menu())
            return

        if data == "admin_logs":
            logs = db.get_activity_logs(limit=5)
            if not logs:
                await query.edit_message_text("📋 هیچ لاگی ثبت نشده است.", reply_markup=admin_main_menu())
                return
            lines = ["📋 لاگ فعالیت‌ها"]
            for log in logs:
                lines.append(f"{log['created_at']} | {log['action']} | {log['details'] or '-'}")
            await query.edit_message_text("\n".join(lines), reply_markup=admin_main_menu())
            return

        if data == "admin_settings":
            settings = get_settings()
            text = (
                "⚙️ تنظیمات\n"
                f"🤖 توکن ربات: {'تنظیم‌شده' if settings.bot_token else 'تنظیم‌نشده'}\n"
                f"🗄 دیتابیس: {settings.database_path}\n"
                f"🕒 Cache TTL: {settings.cache_ttl}"
            )
            await query.edit_message_text(text, reply_markup=admin_main_menu())
            return

        await query.edit_message_text("⚠️ فرمان نامشخص", reply_markup=admin_main_menu())
    except sqlite3.Error as exc:
        logger.error("Admin database operation failed (%s).", type(exc).__name__)
        await query.edit_message_text("⚠️ عملیات پایگاه داده ناموفق بود؛ لطفاً دوباره تلاش کنید.")
