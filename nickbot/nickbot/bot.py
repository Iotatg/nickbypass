"""组装 Application。"""

from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from . import handlers as H
from .config import Config
from .handlers import Runtime


async def _post_init(app: Application) -> None:
    """启动后台任务：代理农场保温。"""
    r = app.bot_data["rt"]
    if r.engine.farm is not None:
        app.bot_data["farm_task"] = asyncio.create_task(H._farm_loop(r))


async def _post_shutdown(app: Application) -> None:
    t = app.bot_data.pop("farm_task", None)
    if t is not None:
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)


def build(cfg: Config) -> Application:
    app = (
        Application.builder()
        .token(cfg.token)
        .concurrent_updates(True)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.bot_data["rt"] = Runtime(cfg)

    app.add_handler(CommandHandler("start", H.cmd_start))
    app.add_handler(CommandHandler("help", H.cmd_help))
    app.add_handler(CommandHandler("id", H.cmd_id))
    app.add_handler(CommandHandler("mode", H.cmd_mode))
    app.add_handler(CommandHandler("proxy", H.cmd_proxy))
    app.add_handler(CommandHandler("supported", H.cmd_supported))
    app.add_handler(CommandHandler("sites", H.cmd_supported))
    app.add_handler(CommandHandler("status", H.cmd_status))
    app.add_handler(CommandHandler("bypass", H.cmd_bypass))
    app.add_handler(CommandHandler("b", H.cmd_bypass))

    app.add_handler(CommandHandler("stats", H.cmd_stats))
    app.add_handler(CommandHandler("users", H.cmd_users))
    app.add_handler(CommandHandler("ban", H.cmd_ban))
    app.add_handler(CommandHandler("unban", H.cmd_unban))
    app.add_handler(CommandHandler("broadcast", H.cmd_broadcast))

    app.add_handler(CallbackQueryHandler(H.on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, H.on_text))
    app.add_error_handler(H.on_error)
    return app


def run(cfg: Config) -> None:
    app = build(cfg)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
