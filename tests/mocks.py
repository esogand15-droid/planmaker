"""Test doubles for the Telegram API: no network, every call is recorded."""
from __future__ import annotations

import datetime as dt
from collections import deque
from typing import Any

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import TelegramMethod
from aiogram.types import (
    Chat,
    Document,
    Message,
    PhotoSize,
    Update,
    User as TgUser,
)

CHAT_ID = 10_000
BOT_ID = 424242


class MockedSession(BaseSession):
    """Records outgoing API calls and answers with plausible objects."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: deque[TelegramMethod[Any]] = deque()
        self.closed = False
        self._message_id = 1000

    async def close(self) -> None:
        self.closed = True

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b""

    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout=None):
        self.requests.append(method)
        name = type(method).__name__
        if name in {"SendMessage", "SendPhoto", "SendDocument", "EditMessageText"}:
            return self._message(bot, method, name)
        if name in {"AnswerCallbackQuery", "DeleteWebhook"}:
            return True
        return True

    # -- helpers -------------------------------------------------------
    def _message(self, bot: Bot, method: Any, name: str) -> Message:
        self._message_id += 1
        chat_id = getattr(method, "chat_id", CHAT_ID)
        msg = Message(
            message_id=getattr(method, "message_id", None) or self._message_id,
            date=dt.datetime.now(),
            chat=Chat(id=int(chat_id), type="private"),
            from_user=TgUser(id=BOT_ID, is_bot=True, first_name="RotbeLand"),
            text=getattr(method, "text", None) or getattr(method, "caption", None),
            photo=[PhotoSize(file_id="ph", file_unique_id="ph", width=1, height=1,
                             file_size=1)] if name == "SendPhoto" else None,
            document=Document(file_id="doc", file_unique_id="doc")
            if name == "SendDocument" else None,
        )
        return msg.as_(bot)

    # -- assertions ----------------------------------------------------
    def calls(self, name: str) -> list[Any]:
        return [r for r in self.requests if type(r).__name__ == name]

    def texts(self) -> list[str]:
        out = []
        for r in self.requests:
            value = getattr(r, "text", None) or getattr(r, "caption", None)
            if value:
                out.append(value)
        return out

    def last_markup_buttons(self) -> list[str]:
        for r in reversed(self.requests):
            markup = getattr(r, "reply_markup", None)
            if markup and getattr(markup, "inline_keyboard", None):
                return [b.text for row in markup.inline_keyboard for b in row]
        return []

    def callback_data(self) -> list[str]:
        out = []
        for r in self.requests:
            markup = getattr(r, "reply_markup", None)
            if markup and getattr(markup, "inline_keyboard", None):
                out += [b.callback_data for row in markup.inline_keyboard for b in row
                        if b.callback_data]
        return out

    def clear(self) -> None:
        self.requests.clear()


def make_bot() -> tuple[Bot, MockedSession]:
    session = MockedSession()
    bot = Bot(
        token="42:TEST",
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    return bot, session


def message_update(text: str, tg_id: int, update_id: int = 1) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=dt.datetime.now(),
            chat=Chat(id=tg_id, type="private"),
            from_user=TgUser(id=tg_id, is_bot=False, first_name="کاربر"),
            text=text,
        ),
    )


def callback_update(data: str, tg_id: int, update_id: int = 1) -> Update:
    from aiogram.types import CallbackQuery

    msg = Message(
        message_id=500,
        date=dt.datetime.now(),
        chat=Chat(id=tg_id, type="private"),
        from_user=TgUser(id=BOT_ID, is_bot=True, first_name="RotbeLand"),
        text="…",
    )
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=str(update_id),
            from_user=TgUser(id=tg_id, is_bot=False, first_name="کاربر"),
            chat_instance="ci",
            message=msg,
            data=data,
        ),
    )
