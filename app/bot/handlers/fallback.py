"""Last-resort router: answers events that no handler claimed.

Telegram shows an endless spinner when a callback query is never answered, so a
missing or outdated handler looks like a frozen bot. Silence is the same bug for
a plain message: an advisor who sends a file (or a voice note, or a sticker)
outside any wizard step used to get *nothing at all*, which reads as «the bot is
broken». This router is included last — if execution reaches it, the event had no
owner, so we log it loudly (so it gets fixed) and give the user a way out.
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import CallbackQuery, Message

from ...db.models import Role, User
from .. import keyboards as kb
from .. import texts as T
from .. import ui

log = logging.getLogger(__name__)
router = Router(name="fallback")


@router.callback_query()
async def unhandled_callback(cq: CallbackQuery, user: User | None = None) -> None:
    log.error(
        "unhandled callback data=%r from tg=%s — a button has no handler",
        cq.data, cq.from_user.id if cq.from_user else None,
    )
    await ui.answer(cq, T.UNKNOWN_ACTION, show_alert=True)
    if cq.message:
        markup = (
            kb.advisor_menu()
            if user is not None and user.role in (Role.ADVISOR, Role.ADMIN)
            else None
        )
        if markup is not None:
            await ui.send_text_safe(cq, T.MAIN_MENU, reply_markup=markup)


@router.message()
async def unhandled_message(message: Message, user: User | None = None) -> None:
    """A message no state/handler claimed: explain, and offer the menu."""
    kind = message.content_type
    log.error(
        "unhandled message content_type=%s from tg=%s — no handler owns this event",
        kind, message.from_user.id if message.from_user else None,
    )
    markup = (
        kb.advisor_menu_with_admin()
        if user is not None and user.role is Role.ADMIN
        else kb.advisor_menu()
        if user is not None and user.role is Role.ADVISOR
        else None
    )
    await message.answer(
        T.UNKNOWN_MESSAGE.format(kind=kind), reply_markup=markup, parse_mode="HTML"
    )
