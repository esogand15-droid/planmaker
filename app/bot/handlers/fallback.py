"""Last-resort router: answers callbacks that no handler claimed.

Telegram shows an endless spinner when a callback query is never answered, so a
missing or outdated handler looks like a frozen bot. This router is included
last: if execution reaches it, the button had no owner — we log it loudly (so it
gets fixed) and give the user a clear way out instead of a hang.
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from ...db.models import Role, User
from .. import keyboards as kb
from .. import texts as T

log = logging.getLogger(__name__)
router = Router(name="fallback")


@router.callback_query()
async def unhandled_callback(cq: CallbackQuery, user: User | None = None) -> None:
    log.error(
        "unhandled callback data=%r from tg=%s — a button has no handler",
        cq.data, cq.from_user.id if cq.from_user else None,
    )
    await cq.answer(T.UNKNOWN_ACTION, show_alert=True)
    if cq.message:
        markup = (
            kb.advisor_menu()
            if user is not None and user.role in (Role.ADVISOR, Role.ADMIN)
            else None
        )
        if markup is not None:
            await cq.message.answer(T.MAIN_MENU, reply_markup=markup, parse_mode="HTML")
