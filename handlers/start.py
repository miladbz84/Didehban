from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from database import get_db
from services.sponsor import MembershipCheckError, get_active_sponsors, verify_membership
from keyboards.user import main_menu, sponsor_verification_keyboard

logger = logging.getLogger("dideban")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None:
        return

    user = update.effective_user
    db = get_db()
    db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        is_active=True,
    )

    sponsors = get_active_sponsors()
    if not sponsors:
        await update.message.reply_text("🏠 منوی اصلی", reply_markup=main_menu())
        return

    missing = []
    for sponsor in sponsors:
        try:
            is_member = await verify_membership(context.bot, user.id, sponsor)
        except MembershipCheckError:
            logger.warning("Unable to verify required channel membership for user %s.", user.id)
            await update.message.reply_text(
                "⚠️ در بررسی عضویت خطایی رخ داد. لطفاً دوباره تلاش کنید.",
                reply_markup=sponsor_verification_keyboard(sponsors),
            )
            return
        if not is_member:
            missing.append(sponsor)
    if missing:
        await update.message.reply_text(
            "❌ هنوز در همه کانال‌های اسپانسر عضو نشده‌اید.",
            reply_markup=sponsor_verification_keyboard(sponsors),
        )
    else:
        await update.message.reply_text("✅ عضویت شما تأیید شد.")
        await update.message.reply_text("🏠 منوی اصلی", reply_markup=main_menu())
