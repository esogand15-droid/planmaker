"""Common entry points: /start (incl. invite deep links), /help, /cancel."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import Role, User
from ...repositories.repositories import PlanRepository
from ...services.plan_manager import PlanManager
from .. import keyboards as kb
from .. import texts as T

log = logging.getLogger(__name__)
router = Router(name="common")

INVITE_PREFIX = "inv_"


@router.message(CommandStart(deep_link=True))
async def start_with_invite(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    session: AsyncSession,
    user: User | None = None,
) -> None:
    """A student opens https://t.me/<bot>?start=inv_<token> from their advisor."""
    await state.clear()
    payload = (command.args or "").strip()

    if not payload.startswith(INVITE_PREFIX):
        await _greet(message, user, session)
        return

    token = payload[len(INVITE_PREFIX):]
    manager = PlanManager(session)
    student = await manager.claim_invite(
        token, message.from_user.id, message.from_user.username
    )
    if student is None:
        # already-registered users just get their normal menu
        if user is not None:
            await _greet(message, user, session)
        else:
            await message.answer(T.INVITE_INVALID, parse_mode="HTML")
        return

    log.info("invite claimed: student=%s tg=%s", student.id, message.from_user.id)
    await message.answer(
        T.STUDENT_WELCOME_LINKED.format(name=student.full_name), parse_mode="HTML"
    )
    await _greet(message, student, session)


@router.message(CommandStart())
async def start(
    message: Message, state: FSMContext, session: AsyncSession, user: User | None = None
) -> None:
    await state.clear()
    await _greet(message, user, session)


async def _greet(message: Message, user: User | None, session: AsyncSession) -> None:
    if user is None:
        await message.answer(T.NOT_REGISTERED, parse_mode="HTML")
        return
    if user.role in (Role.ADVISOR, Role.ADMIN):
        await message.answer(T.MAIN_MENU, reply_markup=kb.advisor_menu(), parse_mode="HTML")
        return
    latest = await PlanRepository(session).latest_for_student(user.id)
    await message.answer(
        T.STUDENT_MENU.format(name=user.full_name),
        reply_markup=kb.student_menu(latest is not None),
    )


@router.message(Command("help"))
async def help_cmd(message: Message, user: User | None = None) -> None:
    if user is not None and user.role in (Role.ADVISOR, Role.ADMIN):
        await message.answer(
            "راهنما:\n"
            "/start — منوی اصلی\n"
            "/quick — قالب ورود سریع فعالیت\n"
            "/cancel — لغو مرحله جاری\n"
            "/id — نمایش شناسه تلگرام شما\n\n"
            "افزودن دانش‌آموز: منو → 👨‍🎓 دانش‌آموزان → ➕ افزودن دانش‌آموز\n"
            "هر برنامه به‌صورت خودکار پیش‌نویس می‌شود؛ از «📝 پیش‌نویس‌ها» ادامه دهید."
        )
    else:
        await message.answer("برای دریافت برنامه هفتگی از /start استفاده کنید.")


@router.message(Command("id"))
async def whoami(message: Message, user: User | None = None) -> None:
    role = user.role.value if user else "—"
    await message.answer(
        f"🆔 شناسه تلگرام شما: <code>{message.from_user.id}</code>\nنقش: {role}",
        parse_mode="HTML",
    )


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext, user: User | None = None) -> None:
    await state.clear()
    markup = (
        kb.advisor_menu()
        if user is not None and user.role in (Role.ADVISOR, Role.ADMIN)
        else None
    )
    await message.answer("لغو شد.", reply_markup=markup)


@router.callback_query(F.data == "noop")
async def noop(cq: CallbackQuery) -> None:
    await cq.answer()
