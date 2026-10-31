"""Build and run the bot.

Two backends, one command table:

* **Kurigram** (MTProto) - the default when API_ID / API_HASH are present.
  Faster updates, no Bot API polling limits.
* **python-telegram-bot** (HTTP Bot API) - the fallback. Token only, nothing
  else to sign up for.

Pick explicitly with ``BACKEND=kurigram`` or ``BACKEND=ptb``; ``auto`` (the
default) takes Kurigram when the MTProto credentials exist.
"""

from __future__ import annotations

import asyncio
from typing import Callable, List, Tuple

from . import handlers as H
from .config import Config
from .handlers import Runtime

# (command, handler, menu description)
COMMANDS: List[Tuple[str, Callable, str]] = [
    ("start", H.cmd_start, "how this works"),
    ("bypass", H.cmd_bypass, "resolve a link — or just paste it"),
    ("mode", H.cmd_mode, "fast · auto · browser"),
    ("sites", H.cmd_supported, "known sites, /sites gplinks to search"),
    ("status", H.cmd_status, "engine health and cache"),
    ("ip", H.cmd_ip, "do I need a proxy here?"),
    ("id", H.cmd_id, "your Telegram ID"),
    ("stats", H.cmd_stats, "traffic and success rate"),
    ("users", H.cmd_users, "how many people use it"),
    ("broadcast", H.cmd_broadcast, "message every user"),
    ("ban", H.cmd_ban, "block a user"),
    ("unban", H.cmd_unban, "let a user back in"),
    ("proxy", H.cmd_proxy, "exit IP and local pool"),
    ("diag", H.cmd_diag, "machine, uptime, battery"),
    ("restart", H.cmd_restart, "restart the bot"),
    ("update", H.cmd_update, "pull latest code, restart"),
    ("logs", H.cmd_logs, "last log lines"),
]

ALIASES = {"help": H.cmd_help, "b": H.cmd_bypass, "supported": H.cmd_supported}


def _user_menu() -> List[Tuple[str, str]]:
    return [(c, d) for c, _, d in COMMANDS[:7]]


def _owner_menu() -> List[Tuple[str, str]]:
    return [(c, d) for c, _, d in COMMANDS]


# ------------------------------------------------------------------------- PTB


def build_ptb(cfg: Config, rt: Runtime):
    """Build the python-telegram-bot Application without starting it."""
    from telegram import BotCommand, BotCommandScopeChat
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        CommandHandler,
        MessageHandler,
        filters,
    )

    from .transport import PTBEvent

    def wrap(fn):
        async def handler(update, context):
            try:
                await fn(PTBEvent(update, context, rt))
            except Exception as exc:  # never let one bad update kill polling
                await H.report_error(exc)

        handler.__name__ = getattr(fn, "__name__", "handler")
        return handler

    async def post_init(app: Application) -> None:
        try:
            await app.bot.set_my_commands([BotCommand(c, d) for c, d in _user_menu()])
            for oid in rt.cfg.owner_ids:
                try:
                    await app.bot.set_my_commands(
                        [BotCommand(c, d) for c, d in _owner_menu()],
                        scope=BotCommandScopeChat(chat_id=oid),
                    )
                except Exception:
                    pass
        except Exception:
            pass
        if rt.engine.farm is not None:
            app.bot_data["farm_task"] = asyncio.create_task(H._farm_loop(rt))

    async def post_shutdown(app: Application) -> None:
        t = app.bot_data.pop("farm_task", None)
        if t is not None:
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)

    app = (
        Application.builder()
        .token(cfg.token)
        .concurrent_updates(True)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.bot_data["rt"] = rt

    for name, fn, _ in COMMANDS:
        app.add_handler(CommandHandler(name, wrap(fn)))
    for name, fn in ALIASES.items():
        app.add_handler(CommandHandler(name, wrap(fn)))

    app.add_handler(CallbackQueryHandler(wrap(H.on_callback)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, wrap(H.on_text)))
    return app


def run_ptb(cfg: Config, rt: Runtime) -> None:
    from telegram import Update

    app = build_ptb(cfg, rt)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


# -------------------------------------------------------------------- Kurigram


def build_kurigram(cfg: Config, rt: Runtime):
    """Build the Kurigram Client and register handlers (no connection yet)."""
    from pyrogram import Client, filters
    from pyrogram.handlers import CallbackQueryHandler, MessageHandler
    from pyrogram.types import BotCommand, BotCommandScopeChat

    from .transport import KuriEvent

    app = Client(
        "nickbot",
        api_id=cfg.api_id,
        api_hash=cfg.api_hash,
        bot_token=cfg.token,
        in_memory=True,          # bots have nothing to persist between runs
        workers=8,
    )

    def wrap(fn):
        async def handler(client, update):
            try:
                await fn(KuriEvent(client, update, rt))
            except Exception as exc:
                await H.report_error(exc)

        handler.__name__ = getattr(fn, "__name__", "handler")
        return handler

    def wrap_cb(fn):
        async def handler(client, query):
            try:
                await fn(KuriEvent(client, query, rt, is_callback=True))
            except Exception as exc:
                await H.report_error(exc)

        handler.__name__ = "callback"
        return handler

    for name, fn, _ in COMMANDS:
        app.add_handler(MessageHandler(wrap(fn), filters.command(name)))
    for name, fn in ALIASES.items():
        app.add_handler(MessageHandler(wrap(fn), filters.command(name)))

    app.add_handler(MessageHandler(wrap(H.on_text), filters.text & ~filters.regex(r"^/")))
    app.add_handler(CallbackQueryHandler(wrap_cb(H.on_callback)))
    return app


def run_kurigram(cfg: Config, rt: Runtime) -> None:
    from pyrogram import idle

    app = build_kurigram(cfg, rt)

    async def amain() -> None:
        await app.start()
        try:
            await app.set_bot_commands([BotCommand(c, d) for c, d in _user_menu()])
            for oid in rt.cfg.owner_ids:
                try:
                    await app.set_bot_commands(
                        [BotCommand(c, d) for c, d in _owner_menu()],
                        scope=BotCommandScopeChat(chat_id=oid),
                    )
                except Exception:
                    pass
        except Exception:
            pass

        task = None
        if rt.engine.farm is not None:
            task = asyncio.create_task(H._farm_loop(rt))
        try:
            await idle()
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await app.stop()

    app.run(amain())


# ----------------------------------------------------------------------- entry


def build(cfg: Config) -> Runtime:
    return Runtime(cfg)


def run(cfg: Config) -> None:
    rt = build(cfg)
    if cfg.use_kurigram():
        print("Backend: Kurigram (MTProto)")
        run_kurigram(cfg, rt)
    else:
        print("Backend: python-telegram-bot (HTTP Bot API)")
        run_ptb(cfg, rt)
