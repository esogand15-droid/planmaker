"""Tiny UI-safety helpers shared by every handler module.

Two Telegram realities break handlers that look correct on paper:

1. **A callback query may be answered only once.** Panels routinely re-render a
   screen by calling another handler, and both answer the same query. Telegram
   then rejects the second call with *"Query is too old and response timeout
   expired"* / *"Query ID is invalid"* — the exception escaped to the error
   middleware and the user saw «❌ انجام این کار با مشکل مواجه شد» *after a
   successful action*. `answer()` below is idempotent: it swallows exactly
   those expected failures and keeps the log quiet.

2. **`callback_query.message` can be an `InaccessibleMessage`** (older than 48
   hours, or the bot was restarted with old buttons on screen). Calling
   `edit_text`/`answer` on it raises `TypeError` inside aiogram's type
   validation, which no `try/except` around the *edit* can recover from.
   `edit_or_send()` always has a way to reach the user.

Nothing here talks to the database, so it is safe to import from anywhere.
"""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

log = logging.getLogger(__name__)

#: substrings Telegram returns when a callback answer is refused for a benign
#: reason (already answered, expired, message too old). Never user-visible.
_BENIGN_ANSWER_ERRORS = (
    "query is too old",
    "response timeout expired",
    "query_id is invalid",
    "query is invalid",
    "message is not modified",
    "message to edit not found",
    "message can't be edited",
    "bot can't edit message",
    "replied message not found",
)

#: substrings that mean "this button can no longer do anything"
_BENIGN_EDIT_ERRORS = _BENIGN_ANSWER_ERRORS + (
    "message author not found",
    "have no rights",
)


def _is_benign(exc: BaseException, markers: tuple[str, ...]) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in markers)


async def answer(
    cq: CallbackQuery,
    text: str | None = None,
    *,
    show_alert: bool = False,
) -> bool:
    """Answer a callback query exactly once; never raise.

    Returns True when Telegram accepted the answer.
    """
    if cq is None:
        return False
    try:
        await cq.answer(text, show_alert=show_alert)
        return True
    except Exception as exc:  # TelegramBadRequest / TelegramAPIError / TypeError
        if _is_benign(exc, _BENIGN_ANSWER_ERRORS):
            log.debug("callback answer ignored (%s)", exc)
        else:
            log.warning("could not answer callback query: %s", exc)
        return False


def _chat_and_bot(cq: CallbackQuery) -> tuple[int | None, Bot | None]:
    message = cq.message
    chat_id = getattr(message, "chat", None)
    return (chat_id.id if chat_id else None), cq.bot


async def edit_or_send(
    cq: CallbackQuery,
    text: str,
    reply_markup=None,
    *,
    parse_mode: str | None = "HTML",
    disable_web_page_preview: bool | None = None,
) -> Message | None:
    """Edit the message behind a callback; if that is impossible, send a new one.

    Handles, in order:
      * identical content / non-editable message → send a fresh message;
      * `InaccessibleMessage` (button older than 48 h) → send a fresh message;
      * deleted chat / blocked bot → give up quietly and return None.
    """
    message = cq.message
    if isinstance(message, Message) and message.chat is not None:
        try:
            return await message.edit_text(
                text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )
        except Exception as exc:
            if not _is_benign(exc, _BENIGN_EDIT_ERRORS):
                log.debug("edit_text failed (%s) — sending a new message", exc)

    chat_id, bot = _chat_and_bot(cq)
    if chat_id is None or bot is None:
        log.warning("cannot reach the user for this callback (no chat id)")
        return None
    try:
        return await bot.send_message(
            chat_id,
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
    except TelegramAPIError as exc:
        log.warning("could not send a message to chat %s: %s", chat_id, exc)
        return None


async def send_document_safe(
    cq: CallbackQuery,
    document,
    *,
    caption: str | None = None,
    parse_mode: str | None = "HTML",
) -> Message | None:
    """Send a document into the callback's chat, surviving inaccessible messages."""
    message = cq.message
    if isinstance(message, Message) and message.chat is not None:
        try:
            return await message.answer_document(
                document, caption=caption, parse_mode=parse_mode
            )
        except Exception as exc:
            log.debug("answer_document failed (%s) — trying send_document", exc)

    chat_id, bot = _chat_and_bot(cq)
    if chat_id is None or bot is None:
        return None
    try:
        return await bot.send_document(
            chat_id, document, caption=caption, parse_mode=parse_mode
        )
    except TelegramAPIError as exc:
        log.warning("could not send a document to chat %s: %s", chat_id, exc)
        return None


async def send_photo_safe(
    cq: CallbackQuery,
    photo,
    *,
    caption: str | None = None,
    parse_mode: str | None = "HTML",
) -> Message | None:
    """Send a photo into the callback's chat, surviving inaccessible messages."""
    message = cq.message
    if isinstance(message, Message) and message.chat is not None:
        try:
            return await message.answer_photo(
                photo, caption=caption, parse_mode=parse_mode
            )
        except Exception as exc:
            log.debug("answer_photo failed (%s) — trying send_photo", exc)

    chat_id, bot = _chat_and_bot(cq)
    if chat_id is None or bot is None:
        return None
    try:
        return await bot.send_photo(
            chat_id, photo, caption=caption, parse_mode=parse_mode
        )
    except TelegramAPIError as exc:
        log.warning("could not send a photo to chat %s: %s", chat_id, exc)
        return None


async def send_text_safe(
    cq: CallbackQuery,
    text: str,
    *,
    reply_markup=None,
    parse_mode: str | None = "HTML",
    disable_web_page_preview: bool | None = None,
) -> Message | None:
    """`cq.message.answer(...)` with a fallback to `bot.send_message(...)`.

    Replies to a message that no longer exists (chat history cleared, bot
    restarted, button older than 48 h) raise instead of degrading; a rendered
    plan or an invite link must still reach the advisor, so this never raises.
    `disable_web_page_preview` matters for invite links: a `t.me` preview would
    otherwise offer a "join" button straight into the student's own chat.
    """
    message = cq.message
    if isinstance(message, Message) and message.chat is not None:
        try:
            return await message.answer(
                text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )
        except Exception as exc:
            log.debug("message.answer failed (%s) — trying send_message", exc)

    chat_id, bot = _chat_and_bot(cq)
    if chat_id is None or bot is None:
        return None
    try:
        return await bot.send_message(
            chat_id,
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
    except TelegramAPIError as exc:
        log.warning("could not send a message to chat %s: %s", chat_id, exc)
        return None


def is_inaccessible(message) -> bool:
    """True when aiogram cannot use this message object at all."""
    return isinstance(message, InaccessibleMessage) or message is None
