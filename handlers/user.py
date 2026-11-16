from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from config import SETTINGS
from database import get_db
from services.crypto import fetch_crypto_prices
from services.dollar import get_dollar_data
from services.gold import get_gold_data
from services.oil import get_oil_data
from services.sponsor import get_active_sponsors, verify_membership
from keyboards.user import main_menu, market_section_menu, sponsor_verification_keyboard
from utils.formatters import format_number

logger = logging.getLogger("dideban")


def _update_user_activity(user_id: int) -> None:
    db = get_db()
    db.upsert_user(user_id=user_id, is_active=True)


async def _membership_access(bot, user_id: int) -> tuple[bool | None, list[dict]]:
    sponsors: list[dict] = []
    try:
        sponsors = get_active_sponsors()
        for sponsor in sponsors:
            if not await verify_membership(bot, user_id, sponsor):
                return False, sponsors
        return True, sponsors
    except Exception as exc:
        logger.warning(
            "Unable to verify required channel membership for user %s (%s).",
            user_id,
            type(exc).__name__,
        )
        return None, sponsors


def _stale_notice(data: dict) -> str:
    if data.get("is_stale"):
        age = int(data.get("stale_age_seconds", 0))
        return f"⚠️ دادهٔ قدیمی است (آخرین پاسخ {age} ثانیه پیش).\n"
    return ""


async def handle_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    data = query.data or ""
    user = query.from_user
    if user is None:
        return
    _update_user_activity(user.id)

    if data == "check_membership":
        is_member, sponsors = await _membership_access(context.bot, user.id)
        if is_member is True:
            await query.edit_message_text("✅ عضویت شما تأیید شد.")
            await query.message.reply_text("🏠 منوی اصلی", reply_markup=main_menu())
        elif is_member is False:
            await query.edit_message_text(
                "❌ هنوز در همه کانال‌های اسپانسر عضو نشده‌اید.",
                reply_markup=sponsor_verification_keyboard(sponsors),
            )
        else:
            await query.edit_message_text(
                "⚠️ بررسی عضویت موقتاً ناموفق بود. لطفاً دوباره تلاش کنید.",
                reply_markup=sponsor_verification_keyboard(sponsors),
            )
        return

    if data in {"crypto", "dollar", "gold", "oil"} or data.startswith("refresh_"):
        is_member, sponsors = await _membership_access(context.bot, user.id)
        if is_member is not True:
            if is_member is False:
                text = "⛔ برای استفاده از این بخش ابتدا در همه کانال‌های اسپانسر عضو شوید."
            else:
                text = "⚠️ امکان بررسی عضویت نیست. لطفاً دوباره تلاش کنید."
            await query.edit_message_text(
                text,
                reply_markup=sponsor_verification_keyboard(sponsors),
            )
            return

    if data == "back_main":
        await query.edit_message_text("🏠 منوی اصلی", reply_markup=main_menu())
        return

    refresh = data.startswith("refresh_")
    asset = data.removeprefix("refresh_") if refresh else data
    if asset not in {"crypto", "dollar", "gold", "oil"}:
        return

    await query.edit_message_text(
        "⏳ در حال بروزرسانی..." if refresh else "⏳ در حال دریافت اطلاعات...",
        reply_markup=None,
    )
    try:
        if asset == "crypto":
            prices = await fetch_crypto_prices(force_refresh=refresh)
            meta = prices.get("_meta", {})
            lines = ["💎 قیمت‌های ارزهای دیجیتال\n", _stale_notice(meta).rstrip()]
            if meta.get("is_partial"):
                failed = ", ".join(meta.get("failed_symbols", []))
                lines.append(f"⚠️ داده ناقص است؛ نمادهای ناموفق: {failed or '-'}\n")
            for symbol, payload in prices.items():
                if symbol == "_meta" or not isinstance(payload, dict):
                    continue
                change = payload["change_24h"]
                age_notice = ""
                if payload.get("is_stale"):
                    age = int(payload.get("stale_age_seconds", 0))
                    age_notice = f" ⚠️ دادهٔ قدیمی ({age} ثانیه)"
                lines.append(f"💎 {symbol}{age_notice}")
                lines.append(f"💰 قیمت: ${format_number(payload['price'], 2)}")
                lines.append(f"📈 تغییر 24 ساعت: {change:+.2f}%")
                lines.append(f"🕐 بروزرسانی: {payload['updated_at']}\n")
            lines.append("Source: Binance")
        elif asset == "dollar":
            quote = await get_dollar_data(force_refresh=refresh)
            lines = [
                "💵 دلار",
                _stale_notice(quote).rstrip(),
                f"🟢 خرید: {quote['buy_toman']:,.0f} تومان",
                f"🔴 فروش: {quote['sell_toman']:,.0f} تومان",
                f"🕐 آخرین بروزرسانی: {quote['updated_at']}",
                "Source: Navasan",
            ]
        elif asset == "gold":
            quote = await get_gold_data(force_refresh=refresh)
            change = "در دسترس نیست" if quote["change"] is None else f"{quote['change']:g}"
            lines = [
                "🥇 طلای ۱۸ عیار",
                _stale_notice(quote).rstrip(),
                f"💰 قیمت: {quote['price_toman']:,.0f} تومان",
                f"📈 تغییر: {change}",
                f"🕐 آخرین بروزرسانی: {quote['updated_at']}",
                "Source: Navasan",
            ]
        else:
            quote = await get_oil_data(force_refresh=refresh)
            meta = quote.get("_meta", {})
            wti = quote.get(SETTINGS.oil_wti_symbol)
            brent = quote.get(SETTINGS.oil_brent_symbol)
            lines = [
                "🛢️ نفت",
                _stale_notice(quote).rstrip(),
                f"WTI: ${format_number(wti, 2) if wti is not None else 'در دسترس نیست'}",
                f"Brent: ${format_number(brent, 2) if brent is not None else 'در دسترس نیست'}",
            ]
            if meta.get("is_partial"):
                lines.append("⚠️ پاسخ ناقص است؛ برخی نمادها در دسترس نیستند.")
            quote_times = meta.get("quote_updated_at", quote.get("quote_updated_at", {}))
            quote_ages = meta.get(
                "quote_stale_age_seconds",
                quote.get("quote_stale_age_seconds", {}),
            )
            for symbol in meta.get("stale_symbols", []):
                timestamp = quote_times.get(symbol)
                timestamp_text = f" (ثبت‌شده در {timestamp})" if timestamp else ""
                age = quote_ages.get(symbol)
                age_text = f" (سن: {int(age)} ثانیه)" if age is not None else ""
                lines.append(
                    f"⚠️ {symbol}: قیمت معتبر قبلی نمایش داده شده است"
                    f"{age_text}{timestamp_text}."
                )
            lines.extend(
                [
                    f"🕐 آخرین بروزرسانی: {quote.get('updated_at', '-')}",
                    "Source: Twelve Data",
                ]
            )
        await query.edit_message_text(
            "\n".join(line for line in lines if line),
            reply_markup=market_section_menu(asset),
        )
    except Exception as exc:
        logger.error(
            "%s market-data request failed in user handler (%s).",
            asset,
            type(exc).__name__,
        )
        await query.edit_message_text(
            "⚠️ دریافت قیمت ممکن نشد. لطفاً بعداً دوباره تلاش کنید.",
            reply_markup=market_section_menu(asset),
        )
