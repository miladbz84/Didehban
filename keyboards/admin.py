from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def admin_main_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📢 مدیریت اسپانسرها", callback_data="admin_sponsors")],
        [InlineKeyboardButton("👥 مدیریت ادمین‌ها", callback_data="admin_admins")],
        [InlineKeyboardButton("📨 ارسال پیام همگانی", callback_data="broadcast_start")],
        [InlineKeyboardButton("👤 مدیریت کاربران", callback_data="admin_users")],
        [InlineKeyboardButton("📊 آمار", callback_data="admin_stats")],
        [InlineKeyboardButton("🐞 گزارش خطاها", callback_data="admin_errors")],
        [InlineKeyboardButton("📋 لاگ فعالیت‌ها", callback_data="admin_logs")],
        [InlineKeyboardButton("⚙️ تنظیمات", callback_data="admin_settings")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def sponsor_admin_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("➕ افزودن اسپانسر", callback_data="sponsor_add")],
        [InlineKeyboardButton("🗑 حذف اسپانسر", callback_data="sponsor_delete")],
        [InlineKeyboardButton("🔄 فعال/غیرفعال کردن", callback_data="sponsor_toggle")],
        [InlineKeyboardButton("📋 لیست اسپانسرها", callback_data="sponsor_list")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")],
    ]
    return InlineKeyboardMarkup(keyboard)


def admin_management_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("➕ افزودن ادمین", callback_data="admin_add")],
        [InlineKeyboardButton("🗑 حذف ادمین", callback_data="admin_remove")],
        [InlineKeyboardButton("📋 لیست ادمین‌ها", callback_data="admin_list")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")],
    ]
    return InlineKeyboardMarkup(keyboard)


def admin_cancel_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("لغو", callback_data="admin_cancel")]]
    )


def sponsor_cancel_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("لغو", callback_data="sponsor_cancel")]]
    )


def broadcast_confirmation_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ تأیید ارسال", callback_data="broadcast_confirm")],
            [InlineKeyboardButton("❌ لغو", callback_data="broadcast_cancel")],
        ]
    )
