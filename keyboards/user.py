from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("💎 کریپتو", callback_data="crypto")],
        [InlineKeyboardButton("💵 دلار", callback_data="dollar")],
        [InlineKeyboardButton("🥇 طلا", callback_data="gold")],
        [InlineKeyboardButton("🛢️ نفت", callback_data="oil")],
    ]
    return InlineKeyboardMarkup(keyboard)


def market_section_menu(asset: str) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🔄 بروزرسانی", callback_data=f"refresh_{asset}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


def sponsor_verification_keyboard(channels: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for channel in channels:
        username = channel.get("username") or channel.get("title") or str(channel.get("channel_id"))
        link = f"https://t.me/{username.lstrip('@')}" if str(username).startswith("@") or username and not username.startswith("http") else None
        if link:
            rows.append([InlineKeyboardButton("📢 عضویت در کانال", url=link)])
    rows.append([InlineKeyboardButton("✅ بررسی عضویت", callback_data="check_membership")])
    return InlineKeyboardMarkup(rows)
