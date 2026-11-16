from __future__ import annotations

import asyncio

from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import SETTINGS, configure_logging
from handlers.admin import admin_callback_handler, admin_command
from handlers.admin_input import admin_input_handler
from handlers.broadcast import broadcast_callback_handler
from handlers.sponsor import sponsor_callback_handler
from handlers.start import start_command
from handlers.user import handle_user_callback

logger = configure_logging()


def register_application_handlers(application) -> None:
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(
        CallbackQueryHandler(
            handle_user_callback,
            pattern=r"^(crypto|dollar|gold|oil|back_main|refresh_.*|check_membership)$",
        )
    )
    application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern=r"^admin_"))
    application.add_handler(CallbackQueryHandler(sponsor_callback_handler, pattern=r"^sponsor_"))
    application.add_handler(CallbackQueryHandler(broadcast_callback_handler, pattern=r"^broadcast_"))
    application.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, admin_input_handler),
        group=1,
    )


async def setup_application() -> None:
    if not SETTINGS.bot_token:
        logger.warning("BOT_TOKEN is not configured. Bot cannot start until it is set in the .env file.")
        return

    application = ApplicationBuilder().token(SETTINGS.bot_token).build()
    register_application_handlers(application)

    logger.info("Starting Dideban Telegram bot")
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Future()


def main() -> None:
    try:
        asyncio.run(setup_application())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot shutdown requested")
    except Exception as exc:  # pragma: no cover - safety for local startup
        logger.exception("Bot crashed: %s", exc)
        raise


if __name__ == "__main__":
    main()
