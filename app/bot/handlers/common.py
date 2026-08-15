"""Common entry points: /start, /help, /cancel and role routing."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import Role, User
from ...repositories.repositories import PlanRepository
from .. import keyboards as kb
from .. import texts as T

router = Router(name="common")


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, user: User, session: AsyncSession) -> None:
    await state.clear()
    if user.role in (Role.ADVISOR, Role.ADMIN):
        await message.answer(T.MAIN_MENU, reply_markup=kb.advisor_menu(), parse_mode="HTML")
        return
    latest = await PlanRepository(session).latest_for_student(user.id)
    await message.answer(
        T.STUDENT_MENU.format(name=user.full_name),
        reply_markup=kb.student_menu(latest is not None),
    )


@router.message(Command("help"))
async def help_cmd(message: Message, user: User) -> None:
    if user.role in (Role.ADVISOR, Role.ADMIN):
        await message.answer(
            "راهنما:\n"
            "/start — منوی اصلی\n"
            "/quick — قالب ورود سریع فعالیت\n"
            "/cancel — لغو مرحله جاری\n\n"
            "هر برنامه به‌صورت خودکار به‌عنوان پیش‌نویس ذخیره می‌شود؛ "
            "می‌توانید هر زمان از «پیش‌نویس‌ها» ادامه دهید."
        )
    else:
        await message.answer("برای دریافت برنامه هفتگی از منوی /start استفاده کنید.")


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext, user: User) -> None:
    await state.clear()
    markup = kb.advisor_menu() if user.role in (Role.ADVISOR, Role.ADMIN) else None
    await message.answer("لغو شد.", reply_markup=markup)


@router.callback_query(F.data == "noop")
async def noop(cq: CallbackQuery) -> None:
    await cq.answer()
