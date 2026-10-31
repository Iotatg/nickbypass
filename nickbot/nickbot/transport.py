"""Transport layer.

The bot logic in ``handlers.py`` never imports a Telegram framework. It only
sees ``Event`` (something that arrived) and ``Sent`` (something we posted).
Two thin subclasses adapt the frameworks we support:

* ``PTBEvent``  - python-telegram-bot, HTTP Bot API, token only.
* ``KuriEvent`` - Kurigram (MTProto), needs api_id + api_hash as well.

That split is why the same commands work on both, and why switching backends
is a config line rather than a rewrite.
"""

from __future__ import annotations

import html
from typing import Any, Iterable, List, Optional, Sequence, Tuple

# A keyboard is a list of rows; each button is (label, action) where the action
# is either "cb:<payload>" for a callback or "url:<link>" for an open button.
Rows = Sequence[Sequence[Tuple[str, str]]]

NO_PREVIEW = object()


def escape(text: Any) -> str:
    return html.escape(str(text), quote=False)


class Sent:
    """A message we already posted, so it can be edited in place."""

    async def edit(self, text: str, keyboard: Rows = ()) -> None:  # pragma: no cover
        raise NotImplementedError


class Event:
    """One incoming update, framework-free."""

    uid: int = 0
    chat_id: int = 0
    text: str = ""
    args: List[str] = []
    username: str = ""
    first_name: str = ""
    reply_to_uid: Optional[int] = None
    callback_data: str = ""
    is_callback: bool = False
    rt: Any = None

    # ------------------------------------------------------------------ output

    async def reply(self, text: str, keyboard: Rows = ()) -> Sent:  # pragma: no cover
        raise NotImplementedError

    async def answer(self, text: str = "", alert: bool = False) -> None:
        """Acknowledge a button press (no-op outside callbacks)."""

    async def edit_self(self, text: str, keyboard: Rows = ()) -> None:
        """Edit the message the button is attached to."""

    async def clear_buttons(self) -> None:
        """Remove the keyboard from the message the button is attached to."""

    async def send(self, chat_id: int, text: str) -> None:  # pragma: no cover
        raise NotImplementedError

    async def typing(self) -> None:
        """Best-effort 'typing…' indicator."""


# --------------------------------------------------------------------- helpers


def rows_from(buttons: Iterable[Tuple[str, str]]) -> List[List[Tuple[str, str]]]:
    return [list(buttons)] if buttons else []


def _split(action: str) -> Tuple[str, str]:
    kind, _, payload = (action or "").partition(":")
    return (kind or "cb"), payload


# ------------------------------------------------------------------------- PTB


class PTBSent(Sent):
    def __init__(self, message: Any) -> None:
        self._m = message

    async def edit(self, text: str, keyboard: Rows = ()) -> None:
        from telegram.constants import ParseMode
        from telegram import LinkPreviewOptions

        await self._m.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=_ptb_kb(keyboard),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )


def _ptb_kb(keyboard: Rows):
    if not keyboard:
        return None
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    out = []
    for row in keyboard:
        line = []
        for label, action in row:
            kind, payload = _split(action)
            if kind == "url":
                line.append(InlineKeyboardButton(label, url=payload))
            else:
                line.append(InlineKeyboardButton(label, callback_data=payload))
        out.append(line)
    return InlineKeyboardMarkup(out)


class PTBEvent(Event):
    def __init__(self, update: Any, context: Any, rt: Any) -> None:
        self._u = update
        self._c = context
        self.rt = rt
        msg = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        self.uid = int(user.id) if user else 0
        self.chat_id = int(chat.id) if chat else 0
        self.username = (getattr(user, "username", "") or "") if user else ""
        self.first_name = (getattr(user, "first_name", "") or "") if user else ""
        self.text = (getattr(msg, "text", "") or "") if msg else ""
        self.args = list(context.args or [])
        self.is_callback = update.callback_query is not None
        if self.is_callback:
            self.callback_data = update.callback_query.data or ""
            self._target = update.callback_query.message
        else:
            self._target = msg
        rt_msg = getattr(msg, "reply_to_message", None)
        if rt_msg and getattr(rt_msg, "from_user", None):
            self.reply_to_uid = int(rt_msg.from_user.id)

    async def reply(self, text: str, keyboard: Rows = ()) -> Sent:
        from telegram.constants import ParseMode
        from telegram import LinkPreviewOptions

        sent = await self._target.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=_ptb_kb(keyboard),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return PTBSent(sent)

    async def answer(self, text: str = "", alert: bool = False) -> None:
        if self.is_callback:
            await self._u.callback_query.answer(text or None, show_alert=alert)

    async def edit_self(self, text: str, keyboard: Rows = ()) -> None:
        from telegram.constants import ParseMode
        from telegram import LinkPreviewOptions

        await self._target.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=_ptb_kb(keyboard),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    async def clear_buttons(self) -> None:
        if self.is_callback:
            await self._target.edit_reply_markup(reply_markup=None)

    async def send(self, chat_id: int, text: str) -> None:
        from telegram.constants import ParseMode

        await self._c.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)

    async def typing(self) -> None:
        try:
            from telegram.constants import ChatAction

            await self._c.bot.send_chat_action(self.chat_id, ChatAction.TYPING)
        except Exception:
            pass


# --------------------------------------------------------------------- Kurigram


class KuriSent(Sent):
    def __init__(self, client: Any, message: Any) -> None:
        self._c = client
        self._m = message

    async def edit(self, text: str, keyboard: Rows = ()) -> None:
        await self._m.edit_text(
            text,
            parse_mode="html",
            reply_markup=_kuri_kb(keyboard),
            link_preview_options=_kuri_preview(),
        )


def _kuri_preview():
    from pyrogram.types import LinkPreviewOptions

    return LinkPreviewOptions(is_disabled=True)


def _kuri_kb(keyboard: Rows):
    if not keyboard:
        return None
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    out = []
    for row in keyboard:
        line = []
        for label, action in row:
            kind, payload = _split(action)
            if kind == "url":
                line.append(InlineKeyboardButton(label, url=payload))
            else:
                line.append(InlineKeyboardButton(label, callback_data=payload))
        out.append(line)
    return InlineKeyboardMarkup(out)


class KuriEvent(Event):
    def __init__(self, client: Any, update: Any, rt: Any, is_callback: bool = False) -> None:
        self._c = client
        self._u = update
        self.rt = rt
        self.is_callback = is_callback
        msg = update.message if is_callback else update
        user = update.from_user
        self.uid = int(user.id) if user else 0
        self.username = (getattr(user, "username", "") or "") if user else ""
        self.first_name = (getattr(user, "first_name", "") or "") if user else ""
        self.chat_id = int(msg.chat.id) if getattr(msg, "chat", None) else self.uid
        self._target = msg
        if is_callback:
            self.callback_data = str(update.data or "")
            self.text = ""
            self.args = []
        else:
            self.text = msg.text or msg.caption or ""
            parts = self.text.split()
            self.args = parts[1:] if parts and parts[0].startswith("/") else []
            rt_msg = getattr(msg, "reply_to_message", None)
            if rt_msg and getattr(rt_msg, "from_user", None):
                self.reply_to_uid = int(rt_msg.from_user.id)

    async def reply(self, text: str, keyboard: Rows = ()) -> Sent:
        sent = await self._target.reply_text(
            text,
            parse_mode="html",
            reply_markup=_kuri_kb(keyboard),
            link_preview_options=_kuri_preview(),
        )
        return KuriSent(self._c, sent)

    async def answer(self, text: str = "", alert: bool = False) -> None:
        if self.is_callback:
            await self._u.answer(text or None, show_alert=alert)

    async def edit_self(self, text: str, keyboard: Rows = ()) -> None:
        await self._target.edit_text(
            text,
            parse_mode="html",
            reply_markup=_kuri_kb(keyboard),
            link_preview_options=_kuri_preview(),
        )

    async def clear_buttons(self) -> None:
        if self.is_callback:
            await self._target.edit_reply_markup(reply_markup=None)

    async def send(self, chat_id: int, text: str) -> None:
        await self._c.send_message(chat_id, text, parse_mode="html")

    async def typing(self) -> None:
        try:
            from pyrogram.enums import ChatAction

            await self._c.send_chat_action(self.chat_id, ChatAction.TYPING)
        except Exception:
            pass
