import asyncio
import logging

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    PollAnswerHandler,
    filters,
)

import database as db
import handlers as h
from config import BOT_TOKEN
from webapp import start_webapp

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)


def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", h.cmd_start))
    app.add_handler(CommandHandler("menu", h.cmd_menu))
    app.add_handler(CommandHandler("quiz", h.cmd_quiz))
    app.add_handler(CommandHandler("help", h.cmd_help))
    app.add_handler(CommandHandler("cancel", h.cmd_cancel))
    app.add_handler(CommandHandler("admin", h.cmd_admin))

    app.add_handler(CallbackQueryHandler(h.on_menu, pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(h.on_subject, pattern=r"^subj:"))
    app.add_handler(CallbackQueryHandler(h.on_begin, pattern=r"^begin:"))
    app.add_handler(CallbackQueryHandler(h.on_top, pattern=r"^top:"))
    app.add_handler(CallbackQueryHandler(h.on_timer, pattern=r"^timer:"))
    app.add_handler(CallbackQueryHandler(h.on_admin, pattern=r"^admin:"))
    app.add_handler(CallbackQueryHandler(h.on_control, pattern=r"^ctl:"))

    app.add_handler(PollAnswerHandler(h.on_poll_answer))
    app.add_handler(MessageHandler(filters.Document.ALL, h.on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, h.on_text))

    return app


async def main() -> None:
    db.init_db()

    app = build_app()

    web_runner = await start_webapp(host="0.0.0.0", port=8080)

    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=["message", "callback_query", "poll_answer"])

    log.info("Bot va WebApp ishga tushdi...")

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await web_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
