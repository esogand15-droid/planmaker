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
from ...security import is_admin, is_admin_env
from ...services.invites import InviteOutcome
from ...domain.persian import to_fa_digits
from ...services.admin_service import AdminService
from ...services.plan_manager import PlanManager
from .. import keyboards as kb
from .. import texts as T
from ..texts import Nav

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
    result = await manager.claim_invite(
        token,
        message.from_user.id,
        message.from_user.username,
        actor=user,
        is_admin_env=is_admin_env(message.from_user.id),
    )

    if result.outcome is InviteOutcome.ROLE_CONFLICT:
        role_fa = (
            T.ROLE_ADMIN_FA
            if is_admin_env(message.from_user.id) or (user and user.role == Role.ADMIN)
            else T.ROLE_ADVISOR_FA
        )
        await message.answer(
            T.INVITE_ROLE_CONFLICT.format(role=role_fa),
            reply_markup=kb.advisor_menu(),
            parse_mode="HTML",
        )
        return

    if result.outcome is InviteOutcome.LINKED:
        await message.answer(
            T.STUDENT_WELCOME_LINKED.format(name=result.student.full_name),
            parse_mode="HTML",
        )
        await _greet(message, result.student, session)
        return

    if result.outcome is InviteOutcome.ALREADY_SELF:
        await message.answer(T.INVITE_ALREADY_SELF)
        await _greet(message, user or result.student, session)
        return

    messages = {
        InviteOutcome.ALREADY_LINKED: T.INVITE_ALREADY_LINKED,
        InviteOutcome.CROSS_STUDENT: T.INVITE_CROSS_STUDENT,
        InviteOutcome.EXPIRED: T.INVITE_EXPIRED,
        InviteOutcome.INVALID: T.INVITE_INVALID,
    }
    await message.answer(messages[result.outcome], parse_mode="HTML")
    if user is not None:  # keep an existing account inside its own world
        await _greet(message, user, session)


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
    if not user.is_active and not is_admin_env(user.telegram_id):
        await message.answer(T.ACCOUNT_SUSPENDED)
        return
    if user.role in (Role.ADVISOR, Role.ADMIN):
        admin = is_admin(user, message.from_user.id)
        await message.answer(
            T.MAIN_MENU,
            reply_markup=kb.advisor_menu_with_admin() if admin else kb.advisor_menu(),
            parse_mode="HTML",
        )
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


@router.callback_query(Nav.filter(F.to.in_({"profile", "student_menu"})))
async def profile(
    cq: CallbackQuery, callback_data: Nav, session: AsyncSession,
    user: User | None = None,
) -> None:
    """Each role sees only its own profile — no menu leakage between roles."""
    if user is None:
        await cq.answer(T.NOT_REGISTERED, show_alert=True)
        return

    manager = PlanManager(session)
    if callback_data.to == "student_menu":
        latest = await manager.plans.latest_for_student(user.id)
        await _safe_edit(
            cq, T.STUDENT_MENU.format(name=user.full_name),
            kb.student_menu(latest is not None),
        )
        await cq.answer()
        return

    if user.role in (Role.ADVISOR, Role.ADMIN):
        detail = await AdminService(session).advisor_detail(user.id)
        admin = is_admin(user, cq.from_user.id if cq.from_user else None)
        await _safe_edit(
            cq,
            T.ADVISOR_PROFILE.format(
                name=user.full_name,
                role=T.ROLE_FA.get(user.role.value, user.role.value),
                telegram=user.telegram_id or "—",
                status=T.STATUS_ACTIVE if user.is_active else T.STATUS_SUSPENDED,
                students=to_fa_digits(str(detail.get("students", 0))),
                plans=to_fa_digits(str(detail.get("plans", 0))),
                drafts=to_fa_digits(str(detail.get("drafts", 0))),
                sent=to_fa_digits(str(detail.get("sent", 0))),
            ),
            kb.advisor_menu_with_admin() if admin else kb.advisor_menu(),
        )
        await cq.answer()
        return

    advisors = await manager.users.advisors_of(user.id)
    plans = await manager.plans.count_history(student_id=user.id, only_generated=True)
    await _safe_edit(
        cq,
        T.STUDENT_PROFILE.format(
            name=user.full_name,
            grade_line=f"پایه/رشته: {user.grade}\n" if user.grade else "",
            advisor="، ".join(a.full_name for a in advisors) or "—",
            connection=T.STATUS_CONNECTED if user.telegram_id else T.STATUS_NOT_CONNECTED,
            plans=to_fa_digits(str(plans)),
            created=user.created_at.strftime("%Y-%m-%d") if user.created_at else "—",
        ),
        kb.profile_back(is_student=True),
    )
    await cq.answer()


async def _safe_edit(cq: CallbackQuery, text: str, markup) -> None:
    try:
        await cq.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data == "noop")
async def noop(cq: CallbackQuery) -> None:
    await cq.answer()
