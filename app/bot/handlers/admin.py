"""Admin control panel — visible only to ADMIN_IDS.

Security: every handler re-checks admin authority against the live Telegram id
(`is_admin`), never against callback data. Forged callbacks from an advisor or
student are rejected before a single query runs.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from aiogram.fsm.state import State, StatesGroup

from ...config import settings
from ...db.models import Role, User
from ...domain.persian import jalali_short, to_en_digits, to_fa_digits, week_label
from ...security import is_admin
from ...services.admin_service import AdminService, uptime
from ...services.deletion import DeletionService
from ...services.plan_manager import PlanManager, StudentError
from ...services.render_queue import RenderQueue
from .. import keyboards as kb
from .. import texts as T
from ..texts import AdminCB

log = logging.getLogger(__name__)
router = Router(name="admin")

PAGE = 6


class AdminFlow(StatesGroup):
    edit_advisor = State()
    edit_student = State()
    search_advisor = State()
    search_student = State()


def status_of(user: User) -> str:
    return T.STATUS_ACTIVE if user.is_active else T.STATUS_SUSPENDED


def _guard(cq: CallbackQuery, user: User | None) -> None:
    """Server-side authority check — callback data is never trusted."""
    if not is_admin(user, cq.from_user.id if cq.from_user else None):
        log.warning(
            "admin panel denied for tg=%s (role=%s)",
            cq.from_user.id if cq.from_user else "?",
            user.role.value if user else "none",
        )
        raise PermissionError(T.ADMIN_ONLY)


def fa(value) -> str:
    return to_fa_digits(str(value))


async def _edit(cq: CallbackQuery, text: str, markup) -> None:
    try:
        await cq.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=markup, parse_mode="HTML")


@router.callback_query(AdminCB.filter(F.action == "home"))
async def admin_home(
    cq: CallbackQuery, state: FSMContext, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    await state.clear()
    from app import __version__

    stats = await AdminService(session).db_stats()
    await _edit(
        cq,
        T.ADMIN_MENU.format(
            version=__version__,
            env=settings.environment,
            advisors=fa(stats["advisors"]),
            students=fa(stats["students"]),
            plans=fa(stats["plans"]),
        ),
        kb.admin_menu(),
    )
    await cq.answer()


# ───────────────────────────────── advisors ─────────────────────────────────
@router.callback_query(AdminCB.filter(F.action == "advisors"))
async def admin_advisors(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    service = AdminService(session)
    total = await service.count_advisors()
    rows = await service.advisors(PAGE, callback_data.page * PAGE)
    if not rows:
        await _edit(cq, T.ADMIN_NO_ADVISORS, kb.admin_back())
        await cq.answer()
        return
    body = [T.ADMIN_ADVISORS.format(count=fa(total)), ""]
    for advisor, students, plans in rows:
        body.append(
            f"{'🟢' if advisor.is_active else '🔒'} <b>{advisor.full_name}</b>\n"
            f"   👨‍🎓 {fa(students)} دانش‌آموز · 📅 {fa(plans)} برنامه · "
            f"{status_of(advisor)}"
        )
    await _edit(cq, "\n".join(body),
                kb.admin_advisors(rows, callback_data.page, total, PAGE))
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "advisor"))
async def admin_advisor_card(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    data = await AdminService(session).advisor_detail(callback_data.ref)
    if not data:
        await cq.answer("مشاور پیدا نشد.", show_alert=True)
        return
    advisor = data["advisor"]
    last = data["last_seen"]
    await _edit(
        cq,
        T.ADMIN_ADVISOR_CARD.format(
            name=advisor.full_name,
            status=status_of(advisor),
            telegram=advisor.telegram_id or "—",
            students=fa(data["students"]),
            plans=fa(data["plans"]),
            drafts=fa(data.get("drafts", 0)),
            sent=fa(data.get("sent", 0)),
            this_week=fa(data["this_week"]),
            last_seen=last.strftime("%Y-%m-%d %H:%M") if last else "—",
        ),
        kb.admin_advisor_card(advisor),
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "advisor_students"))
async def admin_advisor_students(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    students, total = await AdminService(session).students_of_advisor(
        callback_data.ref, PAGE, callback_data.page * PAGE
    )
    if not students:
        await cq.answer("این مشاور دانش‌آموزی ندارد.", show_alert=True)
        return
    await _edit(
        cq, T.ADMIN_STUDENTS.format(count=fa(total)),
        kb.admin_students(students, callback_data.page, total, PAGE, ref=callback_data.ref),
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "advisor_plans"))
async def admin_advisor_plans(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    manager = PlanManager(session)
    plans = await manager.plans.history(
        advisor_id=callback_data.ref, limit=PAGE, offset=callback_data.page * PAGE
    )
    total = await manager.plans.count_history(advisor_id=callback_data.ref)
    if not plans:
        await cq.answer("برنامه‌ای ثبت نشده است.", show_alert=True)
        return
    await _edit(
        cq, T.ADMIN_PLANS.format(count=fa(total)),
        kb.admin_plans(plans, callback_data.page, total, PAGE,
                       ref=callback_data.ref, action="advisor_plans"),
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "edit_advisor"))
async def admin_edit_advisor_prompt(
    cq: CallbackQuery, callback_data: AdminCB, state: FSMContext,
    session: AsyncSession, user: User | None = None,
) -> None:
    _guard(cq, user)
    advisor = await PlanManager(session).users.by_id(callback_data.ref)
    if advisor is None:
        await cq.answer("مشاور پیدا نشد.", show_alert=True)
        return
    await state.set_state(AdminFlow.edit_advisor)
    await state.update_data(target=advisor.id)
    current = advisor.full_name + (f" | {advisor.telegram_id}" if advisor.telegram_id else "")
    await _edit(cq, T.ADMIN_EDIT_ADVISOR_PROMPT.format(current=current),
                kb.admin_back("advisor", advisor.id))
    await cq.answer()


@router.message(AdminFlow.edit_advisor, F.text)
async def admin_edit_advisor_input(
    message, state: FSMContext, session: AsyncSession, user: User | None = None
) -> None:
    if not is_admin(user, message.from_user.id):
        raise PermissionError(T.ADMIN_ONLY)
    raw = message.text.strip()
    if raw.startswith("/"):
        return
    data = await state.get_data()
    manager = PlanManager(session)
    advisor = await manager.users.by_id(int(data["target"]))
    if advisor is None:
        await message.answer("مشاور پیدا نشد.")
        return

    name, _, tg_raw = raw.partition("|")
    name = " ".join(name.split())
    if len(name) < 2:
        await message.answer("⚠️ نام خیلی کوتاه است.")
        return
    if tg_raw.strip():
        digits = to_en_digits(tg_raw.strip())
        if not digits.isdigit():
            await message.answer(T.TG_ID_INVALID)
            return
        taken = await manager.users.by_telegram_id(int(digits))
        if taken is not None and taken.id != advisor.id:
            await message.answer("⚠️ این شناسه تلگرام قبلاً ثبت شده است.")
            return
        advisor.telegram_id = int(digits)
    advisor.full_name = name            # role is never editable from here
    await manager.audit.log(
        "advisor.edited", actor_id=user.id if user else None, detail=name
    )
    await state.clear()
    await message.answer(
        T.ADMIN_UPDATED.format(name=advisor.full_name),
        reply_markup=kb.admin_advisor_card(advisor),
        parse_mode="HTML",
    )


@router.callback_query(AdminCB.filter(F.action == "search_advisor"))
async def admin_search_advisor(
    cq: CallbackQuery, state: FSMContext, user: User | None = None
) -> None:
    _guard(cq, user)
    await state.set_state(AdminFlow.search_advisor)
    await _edit(cq, T.ADMIN_SEARCH_PROMPT, kb.admin_back("advisors"))
    await cq.answer()


@router.message(AdminFlow.search_advisor, F.text)
async def admin_search_advisor_input(
    message, state: FSMContext, session: AsyncSession, user: User | None = None
) -> None:
    if not is_admin(user, message.from_user.id):
        raise PermissionError(T.ADMIN_ONLY)
    if message.text.startswith("/"):
        return
    rows = await AdminService(session).search_advisors(message.text.strip(), PAGE)
    await state.clear()
    if not rows:
        await message.answer(T.ADMIN_SEARCH_EMPTY, reply_markup=kb.admin_back("advisors"))
        return
    await message.answer(
        T.ADMIN_ADVISORS.format(count=fa(len(rows))),
        reply_markup=kb.admin_advisors(rows, 0, len(rows), PAGE),
        parse_mode="HTML",
    )


@router.callback_query(AdminCB.filter(F.action == "ask_suspend"))
async def admin_ask_suspend(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    advisor = await PlanManager(session).users.by_id(callback_data.ref)
    if advisor is None:
        await cq.answer("پیدا نشد.", show_alert=True)
        return
    if is_admin(advisor, advisor.telegram_id):
        await cq.answer(T.ADMIN_SELF_ACTION, show_alert=True)
        return
    what = (
        f"فعال‌سازی حساب «{advisor.full_name}»"
        if not advisor.is_active
        else f"تعلیق «{advisor.full_name}» — تا فعال‌سازی مجدد نمی‌تواند "
             "برنامه بسازد یا ارسال کند."
    )
    await _edit(cq, T.ADMIN_CONFIRM.format(what=what),
                kb.admin_confirm("do_suspend", advisor.id))
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "do_suspend"))
async def admin_do_suspend(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    manager = PlanManager(session)
    target = await manager.users.by_id(callback_data.ref)
    if target is None:
        await cq.answer("پیدا نشد.", show_alert=True)
        return
    if is_admin(target, target.telegram_id):
        await cq.answer(T.ADMIN_SELF_ACTION, show_alert=True)
        return
    target.is_active = not target.is_active
    await manager.audit.log(
        "advisor.activated" if target.is_active else "advisor.suspended",
        actor_id=user.id if user else None, detail=target.full_name,
    )
    await cq.answer(
        (T.ADMIN_ACTIVATED if target.is_active else T.ADMIN_SUSPENDED).format(
            name=target.full_name
        ),
        show_alert=True,
    )
    await admin_advisor_card(cq, AdminCB(action="advisor", ref=target.id), session, user)


@router.callback_query(AdminCB.filter(F.action == "ask_del_advisor"))
async def admin_ask_delete_advisor(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    queue: RenderQueue, user: User | None = None,
) -> None:
    """Step 1 — show the impact and ask what happens to the students."""
    _guard(cq, user)
    service = DeletionService(session, queue.service.storage_root)
    try:
        report = await service.preview_advisor(user, callback_data.ref)
    except StudentError as exc:
        await cq.answer(str(exc), show_alert=True)
        return
    note = (
        T.ADMIN_ADVISOR_HAS_STUDENTS if report.students else T.ADMIN_ADVISOR_NO_STUDENTS
    )
    await _edit(
        cq,
        T.ADMIN_DELETE_ADVISOR.format(
            name=report.name, students=fa(report.students),
            plans=fa(report.plans), note=note,
        ),
        kb.admin_delete_advisor(callback_data.ref, bool(report.students)),
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "del_advisor_pick"))
async def admin_pick_transfer_target(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    candidates = await AdminService(session).advisor_candidates(exclude=callback_data.ref)
    if not candidates:
        await cq.answer("مشاور دیگری برای انتقال وجود ندارد.", show_alert=True)
        return
    await _edit(cq, T.ADMIN_PICK_TARGET_ADVISOR,
                kb.admin_pick_advisor(candidates, callback_data.ref, "del_advisor_to"))
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action.in_({"del_advisor", "del_advisor_to"})))
async def admin_delete_advisor(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    queue: RenderQueue, user: User | None = None,
) -> None:
    _guard(cq, user)
    service = DeletionService(session, queue.service.storage_root)
    transfer = callback_data.action == "del_advisor_to"
    try:
        report = await service.delete_advisor(
            user, callback_data.ref,
            strategy="transfer" if transfer else "detach",
            target_advisor_id=int(callback_data.arg) if transfer and callback_data.arg else None,
        )
        await session.commit()
    except StudentError as exc:
        await cq.answer(str(exc), show_alert=True)
        return
    except Exception:
        log.exception("advisor deletion failed (id=%s)", callback_data.ref)
        await cq.answer(T.ADMIN_DELETE_FAILED, show_alert=True)
        return

    detail = (
        f"{fa(report.transferred)} دانش‌آموز منتقل شد"
        if transfer else
        f"{fa(report.detached)} دانش‌آموز بدون مشاور شد · {fa(report.files)} فایل پاک شد"
    )
    await cq.answer(T.ADMIN_ADVISOR_DELETED.format(name=report.name, detail=detail),
                    show_alert=True)
    await admin_advisors(cq, AdminCB(action="advisors"), session, user)


# ───────────────────────────────── students ─────────────────────────────────
@router.callback_query(AdminCB.filter(F.action == "students"))
async def admin_students(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    service = AdminService(session)
    total = await service.count_students()
    students = await service.students(PAGE, callback_data.page * PAGE)
    if not students:
        await _edit(cq, T.ADMIN_NO_STUDENTS, kb.admin_back())
        await cq.answer()
        return
    await _edit(cq, T.ADMIN_STUDENTS.format(count=fa(total)),
                kb.admin_students(students, callback_data.page, total, PAGE))
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "student"))
async def admin_student_card(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    data = await AdminService(session).student_detail(callback_data.ref)
    if not data:
        await cq.answer("دانش‌آموز پیدا نشد.", show_alert=True)
        return
    student = data["student"]
    last = data.get("last_seen")
    await _edit(
        cq,
        T.ADMIN_STUDENT_CARD.format(
            name=student.full_name,
            grade_line=f"پایه/رشته: {student.grade}\n" if student.grade else "",
            status=status_of(student),
            connection=(
                T.STATUS_CONNECTED if student.telegram_id else T.STATUS_NOT_CONNECTED
            ),
            telegram=student.telegram_id or "—",
            advisor="، ".join(a.full_name for a in data["advisors"]) or "بدون مشاور",
            plans=fa(data["plans"]),
            drafts=fa(data.get("drafts", 0)),
            created=student.created_at.strftime("%Y-%m-%d") if student.created_at else "—",
            last_seen=last.strftime("%Y-%m-%d %H:%M") if last else "—",
        ),
        kb.admin_student_card(student),
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "student_plans"))
async def admin_student_plans(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    manager = PlanManager(session)
    plans = await manager.plans.history(
        student_id=callback_data.ref, limit=PAGE, offset=callback_data.page * PAGE
    )
    total = await manager.plans.count_history(student_id=callback_data.ref)
    if not plans:
        await cq.answer("برنامه‌ای ثبت نشده است.", show_alert=True)
        return
    await _edit(cq, T.ADMIN_PLANS.format(count=fa(total)),
                kb.admin_plans(plans, callback_data.page, total, PAGE,
                               ref=callback_data.ref, action="student_plans"))
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "edit_student"))
async def admin_edit_student_prompt(
    cq: CallbackQuery, callback_data: AdminCB, state: FSMContext,
    session: AsyncSession, user: User | None = None,
) -> None:
    _guard(cq, user)
    student = await PlanManager(session).users.by_id(callback_data.ref)
    if student is None:
        await cq.answer("دانش‌آموز پیدا نشد.", show_alert=True)
        return
    await state.set_state(AdminFlow.edit_student)
    await state.update_data(target=student.id)
    current = student.full_name + (f" | {student.grade}" if student.grade else "")
    await _edit(cq, T.ADMIN_EDIT_STUDENT_PROMPT.format(current=current),
                kb.admin_back("student", student.id))
    await cq.answer()


@router.message(AdminFlow.edit_student, F.text)
async def admin_edit_student_input(
    message, state: FSMContext, session: AsyncSession, user: User | None = None
) -> None:
    if not is_admin(user, message.from_user.id):
        raise PermissionError(T.ADMIN_ONLY)
    raw = message.text.strip()
    if raw.startswith("/"):
        return
    data = await state.get_data()
    manager = PlanManager(session)
    student = await manager.users.by_id(int(data["target"]))
    if student is None:
        await message.answer("دانش‌آموز پیدا نشد.")
        return
    name, _, grade = raw.partition("|")
    name = " ".join(name.split())
    if len(name) < 2:
        await message.answer("⚠️ نام خیلی کوتاه است.")
        return
    await manager.users.update_student(student, full_name=name, grade=grade.strip() or None)
    await manager.audit.log(
        "student.edited", actor_id=user.id if user else None,
        student_id=student.id, detail=name,
    )
    await state.clear()
    await message.answer(
        T.ADMIN_UPDATED.format(name=student.full_name),
        reply_markup=kb.admin_student_card(student),
        parse_mode="HTML",
    )


@router.callback_query(AdminCB.filter(F.action == "search_student"))
async def admin_search_student(
    cq: CallbackQuery, state: FSMContext, user: User | None = None
) -> None:
    _guard(cq, user)
    await state.set_state(AdminFlow.search_student)
    await _edit(cq, T.ADMIN_SEARCH_PROMPT, kb.admin_back("students"))
    await cq.answer()


@router.message(AdminFlow.search_student, F.text)
async def admin_search_student_input(
    message, state: FSMContext, session: AsyncSession, user: User | None = None
) -> None:
    if not is_admin(user, message.from_user.id):
        raise PermissionError(T.ADMIN_ONLY)
    if message.text.startswith("/"):
        return
    rows = await AdminService(session).search_students(message.text.strip(), PAGE)
    await state.clear()
    if not rows:
        await message.answer(T.ADMIN_SEARCH_EMPTY, reply_markup=kb.admin_back("students"))
        return
    await message.answer(
        T.ADMIN_STUDENTS.format(count=fa(len(rows))),
        reply_markup=kb.admin_students(rows, 0, len(rows), PAGE),
        parse_mode="HTML",
    )


@router.callback_query(AdminCB.filter(F.action == "ask_suspend_student"))
async def admin_ask_suspend_student(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    student = await PlanManager(session).users.by_id(callback_data.ref)
    if student is None:
        await cq.answer("پیدا نشد.", show_alert=True)
        return
    what = ("فعال‌سازی" if not student.is_active else "غیرفعال‌سازی") + \
        f" حساب «{student.full_name}»"
    await _edit(cq, T.ADMIN_CONFIRM.format(what=what),
                kb.admin_confirm("do_suspend_student", student.id))
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "do_suspend_student"))
async def admin_do_suspend_student(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    manager = PlanManager(session)
    student = await manager.users.by_id(callback_data.ref)
    if student is None or student.role is not Role.STUDENT:
        await cq.answer("پیدا نشد.", show_alert=True)
        return
    student.is_active = not student.is_active
    await manager.audit.log(
        "student.activated" if student.is_active else "student.suspended",
        actor_id=user.id if user else None, student_id=student.id,
    )
    await cq.answer(
        (T.ADMIN_ACTIVATED if student.is_active else T.ADMIN_SUSPENDED).format(
            name=student.full_name
        ),
        show_alert=True,
    )
    await admin_student_card(cq, AdminCB(action="student", ref=student.id), session, user)


# ── student deletion (two steps, real) ──
@router.callback_query(AdminCB.filter(F.action == "ask_del_student"))
async def admin_ask_delete_student(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    queue: RenderQueue, user: User | None = None,
) -> None:
    _guard(cq, user)
    service = DeletionService(session, queue.service.storage_root)
    try:
        report = await service.preview_student(user, callback_data.ref)
    except StudentError as exc:
        await cq.answer(str(exc), show_alert=True)
        return
    impact = "\n".join(
        line for line in [
            "• اتصال دانش‌آموز به مشاور",
            f"• {fa(report.plans)} برنامه هفتگی" if report.plans else "",
            f"• {fa(report.versions)} نسخه ثبت‌شده" if report.versions else "",
            f"• {fa(report.files)} فایل تصویر و PDF" if report.files else "",
            "• دعوت‌های فعال و حساب دانش‌آموز",
        ] if line
    )
    await _edit(
        cq,
        T.CONFIRM_REMOVE_STUDENT.format(name=report.name, impact=impact),
        kb.admin_confirm("del_student", callback_data.ref, label="➡️ ادامه"),
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "del_student"))
async def admin_confirm_delete_student(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    queue: RenderQueue, user: User | None = None,
) -> None:
    _guard(cq, user)
    service = DeletionService(session, queue.service.storage_root)
    report = await service.preview_student(user, callback_data.ref)
    await _edit(
        cq, T.CONFIRM_REMOVE_STUDENT_FINAL.format(name=report.name),
        kb.admin_confirm("del_student_final", callback_data.ref, label="🗑 حذف قطعی"),
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "del_student_final"))
async def admin_delete_student(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    queue: RenderQueue, user: User | None = None,
) -> None:
    _guard(cq, user)
    service = DeletionService(session, queue.service.storage_root)
    try:
        report = await service.delete_student(user, callback_data.ref)
        await session.commit()
    except StudentError as exc:
        await cq.answer(str(exc), show_alert=True)
        return
    except Exception:
        log.exception("admin student deletion failed (id=%s)", callback_data.ref)
        await cq.answer(T.ADMIN_DELETE_FAILED, show_alert=True)
        return
    await cq.answer(T.ADMIN_STUDENT_DELETED.format(name=report.name), show_alert=True)
    await admin_students(cq, AdminCB(action="students"), session, user)


# ── advisor transfer ──
@router.callback_query(AdminCB.filter(F.action == "transfer"))
async def admin_transfer_pick(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    service = AdminService(session)
    data = await service.student_detail(callback_data.ref)
    if not data:
        await cq.answer("دانش‌آموز پیدا نشد.", show_alert=True)
        return
    current_ids = [a.id for a in data["advisors"]]
    candidates = await service.advisor_candidates(
        exclude=current_ids[0] if current_ids else 0
    )
    if not candidates:
        await cq.answer("مشاور دیگری وجود ندارد.", show_alert=True)
        return
    await _edit(
        cq,
        T.ADMIN_TRANSFER_PICK.format(
            name=data["student"].full_name,
            current="، ".join(a.full_name for a in data["advisors"]) or "بدون مشاور",
        ),
        kb.admin_pick_advisor(candidates, callback_data.ref, "transfer_to"),
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "transfer_to"))
async def admin_transfer_confirm(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    service = AdminService(session)
    data = await service.student_detail(callback_data.ref)
    target = await PlanManager(session).users.by_id(int(callback_data.arg))
    if not data or target is None:
        await cq.answer("اطلاعات نامعتبر است.", show_alert=True)
        return
    await _edit(
        cq,
        T.ADMIN_TRANSFER_CONFIRM.format(
            name=data["student"].full_name,
            old="، ".join(a.full_name for a in data["advisors"]) or "بدون مشاور",
            new=target.full_name,
        ),
        kb.admin_confirm("do_transfer", callback_data.ref, callback_data.arg),
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "do_transfer"))
async def admin_do_transfer(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    queue: RenderQueue, user: User | None = None,
) -> None:
    _guard(cq, user)
    service = DeletionService(session, queue.service.storage_root)
    try:
        student, target = await service.transfer_student(
            user, callback_data.ref, int(callback_data.arg)
        )
    except StudentError as exc:
        await cq.answer(str(exc), show_alert=True)
        return
    await cq.answer(
        T.ADMIN_TRANSFER_DONE.format(name=student.full_name, new=target.full_name),
        show_alert=True,
    )
    await admin_student_card(cq, AdminCB(action="student", ref=student.id), session, user)


# ── telegram connection management ──
@router.callback_query(AdminCB.filter(F.action == "connection"))
async def admin_connection(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    student = await PlanManager(session).users.by_id(callback_data.ref)
    if student is None:
        await cq.answer("دانش‌آموز پیدا نشد.", show_alert=True)
        return
    await _edit(
        cq,
        T.ADMIN_CONNECTION.format(
            name=student.full_name,
            status=(T.STATUS_CONNECTED if student.telegram_id else T.STATUS_NOT_CONNECTED),
            telegram=student.telegram_id or "—",
            invite="دارد" if student.invite_token else "ندارد",
        ),
        kb.admin_connection(student),
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "reissue"))
async def admin_reissue_invite(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    manager = PlanManager(session)
    student = await manager.users.by_id(callback_data.ref)
    if student is None or student.telegram_id:
        await cq.answer("این دانش‌آموز از قبل متصل است.", show_alert=True)
        return
    token = await manager.users.rotate_invite_token(student)
    await manager.audit.log(
        "student.invite_issued", actor_id=user.id if user else None, student_id=student.id
    )
    me = await cq.bot.me()
    link = f"https://t.me/{me.username}?start=inv_{token}"
    await cq.message.answer(
        T.INVITE_READY.format(
            name=student.full_name, link=link,
            expires=jalali_short(student.invite_expires_at.date()),
        ),
        reply_markup=kb.admin_connection(student),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "revoke_invite"))
async def admin_revoke_invite(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    manager = PlanManager(session)
    student = await manager.users.by_id(callback_data.ref)
    if student is None:
        await cq.answer("پیدا نشد.", show_alert=True)
        return
    await manager.users.revoke_invite(student)
    await manager.audit.log(
        "student.invite_revoked", actor_id=user.id if user else None, student_id=student.id
    )
    await cq.answer(T.INVITE_REVOKED, show_alert=True)
    await admin_connection(cq, AdminCB(action="connection", ref=student.id), session, user)


@router.callback_query(AdminCB.filter(F.action == "ask_unlink"))
async def admin_ask_unlink(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    student = await PlanManager(session).users.by_id(callback_data.ref)
    if student is None:
        await cq.answer("پیدا نشد.", show_alert=True)
        return
    await _edit(
        cq,
        T.ADMIN_CONFIRM.format(
            what=f"قطع اتصال تلگرام «{student.full_name}» — پس از این، برنامه‌ها "
                 "مستقیم برای او ارسال نمی‌شود."
        ),
        kb.admin_confirm("do_unlink", student.id),
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "do_unlink"))
async def admin_do_unlink(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    queue: RenderQueue, user: User | None = None,
) -> None:
    _guard(cq, user)
    service = DeletionService(session, queue.service.storage_root)
    try:
        student = await service.unlink_telegram(user, callback_data.ref)
    except StudentError as exc:
        await cq.answer(str(exc), show_alert=True)
        return
    await cq.answer(T.ADMIN_UNLINKED.format(name=student.full_name), show_alert=True)
    await admin_connection(cq, AdminCB(action="connection", ref=student.id), session, user)


# ─────────────────────────────── plans ──────────────────────────────────────
@router.callback_query(AdminCB.filter(F.action == "plans"))
async def admin_plans(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    manager = PlanManager(session)
    plans = await manager.plans.history(limit=PAGE, offset=callback_data.page * PAGE)
    total = await manager.plans.count_history()
    if not plans:
        await _edit(cq, "📋 <b>مدیریت برنامه‌ها</b>\n\nهنوز برنامه‌ای ساخته نشده است.",
                    kb.admin_back())
        await cq.answer()
        return
    await _edit(cq, T.ADMIN_PLANS.format(count=fa(total)),
                kb.admin_plans(plans, callback_data.page, total, PAGE))
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "plan"))
async def admin_plan_card(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    manager = PlanManager(session)
    plan = await manager.plans.get(callback_data.ref)
    if plan is None:
        await cq.answer("برنامه پیدا نشد.", show_alert=True)
        return
    domain = PlanManager.to_domain(plan)
    await _edit(
        cq,
        T.ADMIN_PLAN_CARD.format(
            student=plan.student.full_name,
            advisor=plan.advisor.full_name if plan.advisor else "—",
            week=week_label(plan.week_start, plan.week_end),
            status=T.PLAN_STATUS_FA.get(plan.status.value, plan.status.value),
            version=fa(plan.version),
            activities=fa(domain.activity_count),
            assignments=fa(len(domain.assignments)),
        ),
        kb.admin_plan_card(plan),
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "ask_del_plan"))
async def admin_ask_delete_plan(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    plan = await PlanManager(session).plans.get(callback_data.ref)
    if plan is None:
        await cq.answer("برنامه پیدا نشد.", show_alert=True)
        return
    await _edit(
        cq,
        T.ADMIN_CONFIRM.format(
            what=f"حذف برنامه «{plan.student.full_name}» — "
                 f"{week_label(plan.week_start, plan.week_end)} "
                 f"به همراه {fa(len(plan.files))} نسخه و فایل‌های آن"
        ),
        kb.admin_confirm("del_plan", plan.id, label="🗑 حذف قطعی"),
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "del_plan"))
async def admin_delete_plan(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    queue: RenderQueue, user: User | None = None,
) -> None:
    _guard(cq, user)
    service = DeletionService(session, queue.service.storage_root)
    try:
        report = await service.delete_plan(user, callback_data.ref)
        await session.commit()
    except StudentError as exc:
        await cq.answer(str(exc), show_alert=True)
        return
    except Exception:
        log.exception("admin plan deletion failed (id=%s)", callback_data.ref)
        await cq.answer(T.ADMIN_DELETE_FAILED, show_alert=True)
        return
    await cq.answer(T.ADMIN_PLAN_DELETED.format(files=fa(report.files)), show_alert=True)
    await admin_plans(cq, AdminCB(action="plans"), session, user)


# ────────────────────────────── system / bot ────────────────────────────────
@router.callback_query(AdminCB.filter(F.action.in_({"system", "health"})))
async def admin_system(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    queue: RenderQueue, user: User | None = None,
) -> None:
    _guard(cq, user)
    health = await AdminService(session).health(queue)
    await _edit(
        cq,
        T.ADMIN_SYSTEM.format(
            bot=T.STATUS_ONLINE,
            db=T.STATUS_CONNECTED if health["db"] else T.STATUS_MISSING,
            redis=T.STATUS_CONNECTED if health["redis"] else T.STATUS_OPTIONAL_OFF,
            renderer=T.STATUS_READY,
            chromium=T.STATUS_READY if health["chromium"] else T.STATUS_FALLBACK,
            raqm=T.STATUS_AVAILABLE if health["raqm"] else T.STATUS_MISSING,
            storage=T.STATUS_AVAILABLE if health["storage"] else T.STATUS_MISSING,
            db_latency=f"{health['db_latency_ms']:.1f} میلی‌ثانیه",
            inflight=fa(health["inflight"]),
            uptime=health["uptime"],
        ),
        kb.admin_system(),
    )
    await cq.answer(T.ADMIN_HEALTH_OK if callback_data.action == "health" else None)


@router.callback_query(AdminCB.filter(F.action == "bot"))
async def admin_bot(
    cq: CallbackQuery, queue: RenderQueue, user: User | None = None
) -> None:
    _guard(cq, user)
    from app import __version__

    renderer = queue.service.renderer
    fallback = getattr(queue.service, "fallback_renderer", None)
    await _edit(
        cq,
        T.ADMIN_BOT.format(
            status=T.STATUS_ONLINE,
            mode=T.MODE_POLLING,
            uptime=uptime(),
            version=__version__,
            template=renderer.layout.version,
            renderer=renderer.signature,
            fallback=fallback.signature if fallback else "—",
        ),
        kb.admin_back("bot"),
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "db"))
async def admin_db(
    cq: CallbackQuery, session: AsyncSession, user: User | None = None
) -> None:
    _guard(cq, user)
    stats = await AdminService(session).db_stats()
    await _edit(
        cq,
        T.ADMIN_DB.format(
            status=T.STATUS_CONNECTED,
            users=fa(stats["users"]),
            advisors=fa(stats["advisors"]),
            students=fa(stats["students"]),
            plans=fa(stats["plans"]),
            drafts=fa(stats["drafts"]),
            files=fa(stats["files"]),
            activities=fa(stats["activities"]),
            latency=f"{stats['latency_ms']:.1f} میلی‌ثانیه",
        ),
        kb.admin_back("db"),
    )
    await cq.answer()


# ─────────────────────────────── storage ────────────────────────────────────
@router.callback_query(AdminCB.filter(F.action == "storage"))
async def admin_storage(
    cq: CallbackQuery, session: AsyncSession, queue: RenderQueue,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    report = await AdminService(session).storage_report(queue.service.storage_root)
    await _edit(
        cq,
        T.ADMIN_STORAGE.format(
            path=report.path,
            mounted=T.STATUS_AVAILABLE if report.mounted else T.STATUS_MISSING,
            png=fa(report.png),
            pdf=fa(report.pdf),
            total=fa(report.total),
            size=report.human_size,
            orphans=fa(len(report.orphans)),
        ),
        kb.admin_storage(len(report.orphans)),
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "ask_cleanup"))
async def admin_ask_cleanup(
    cq: CallbackQuery, session: AsyncSession, queue: RenderQueue,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    report = await AdminService(session).storage_report(queue.service.storage_root)
    await _edit(
        cq,
        T.ADMIN_CONFIRM.format(
            what=f"پاک‌سازی {fa(len(report.orphans))} فایل بدون رکورد در پایگاه داده"
        ),
        kb.admin_confirm("do_cleanup", 0, label="🧹 پاک‌سازی"),
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "do_cleanup"))
async def admin_do_cleanup(
    cq: CallbackQuery, session: AsyncSession, queue: RenderQueue,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    service = AdminService(session)
    removed = await service.delete_orphans(queue.service.storage_root)
    await PlanManager(session).audit.log(
        "storage.cleanup", actor_id=user.id if user else None, detail=f"files={removed}"
    )
    await cq.answer(T.ADMIN_CLEANUP_DONE.format(files=fa(removed)), show_alert=True)
    await admin_storage(cq, session, queue, user)


# ──────────────────────────── stats / audit ─────────────────────────────────
@router.callback_query(AdminCB.filter(F.action == "stats"))
async def admin_stats(
    cq: CallbackQuery, session: AsyncSession, user: User | None = None
) -> None:
    _guard(cq, user)
    stats = await AdminService(session).statistics()
    await _edit(
        cq,
        T.ADMIN_STATS.format(
            advisors=fa(stats["advisors"]),
            students=fa(stats["students"]),
            plans=fa(stats["plans"]),
            drafts=fa(stats["drafts"]),
            sent=fa(stats["sent"]),
            generated=fa(stats["generated"]),
            today=fa(stats["today"]),
            week=fa(stats["week"]),
            month=fa(stats["month"]),
            all_time=fa(stats["plans"]),
            invites=fa(stats["invites_issued"]),
            blocked=fa(stats["invites_blocked"]),
        ),
        kb.admin_back("stats"),
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "audit"))
async def admin_audit(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    rows, total = await AdminService(session).audit_page(PAGE, callback_data.page * PAGE)
    if not rows:
        await _edit(cq, "🧾 <b>گزارش فعالیت‌ها</b>\n\nرویدادی ثبت نشده است.",
                    kb.admin_back())
        await cq.answer()
        return
    pages = max(1, -(-total // PAGE))
    lines = [T.ADMIN_AUDIT.format(page=fa(callback_data.page + 1), pages=fa(pages)), ""]
    for entry in rows:
        who = f"کاربر #{fa(entry.actor_id)}" if entry.actor_id else "سیستم"
        lines.append(
            f"🕐 <code>{entry.at:%m-%d %H:%M}</code>\n"
            f"   <b>{T.audit_fa(entry.action)}</b> — {who}"
            + (f"\n   {entry.detail}" if entry.detail else "")
        )
    await _edit(cq, "\n".join(lines), kb.admin_audit(callback_data.page, total, PAGE))
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "settings"))
async def admin_settings(cq: CallbackQuery, user: User | None = None) -> None:
    _guard(cq, user)
    await _edit(
        cq,
        T.ADMIN_SETTINGS.format(
            env=settings.environment,
            tz=settings.timezone,
            backend=settings.render_backend,
            scale=settings.print_scale,
            dpi=fa(settings.pdf_dpi),
            concurrency=fa(settings.render_concurrency),
            retention=(f"{fa(settings.retention_days)} روز"
                       if settings.retention_days else "نامحدود"),
            admins=fa(len(settings.admin_ids)),
            storage=settings.storage_root,
        ),
        kb.admin_back("settings"),
    )
    await cq.answer()
