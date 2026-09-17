"""Admin control panel — visible only to ADMIN_IDS.

Security: every handler re-checks admin authority against the live Telegram id
(`is_admin`), never against callback data. Forged callbacks from an advisor or
student are rejected before a single query runs.
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db.models import BackupSchedule, BackupStatus, BotSettings, Role, User
from ...domain.persian import (
    jalali_datetime,
    jalali_short,
    to_en_digits,
    to_fa_digits,
    week_label,
)
from ...security import is_admin
from ...services.admin_service import AdminService, uptime
from ...services.backup import (
    BackupService,
    _now_utc,
    deliver,
    human_bytes,
    next_run,
)
from ...services.restore import (
    KEY_TABLES,
    MAX_RESTORE_BYTES,
    TABLE_FA,
    RestoreError,
    RestorePlan,
    RestoreResult,
    RestoreService,
)
from ...services.deletion import DeletionService
from ...services.plan_manager import PlanManager, StudentError
from ...services.render_queue import RenderQueue
from .. import keyboards as kb
from .. import texts as T
from .. import ui
from ..texts import AdminCB

log = logging.getLogger(__name__)
router = Router(name="admin")

PAGE = 6


class AdminFlow(StatesGroup):
    add_advisor = State()
    edit_advisor = State()
    edit_student = State()
    search_advisor = State()
    search_student = State()
    #: restore from an uploaded archive: waiting for the file …
    backup_restore_file = State()
    #: … then waiting for the explicit «yes, replace everything» tap
    backup_restore_confirm = State()


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
    """Edit the panel message, or send a fresh one when editing is impossible.

    Buttons older than 48 hours arrive with an `InaccessibleMessage`, and
    `edit_text` also fails on unchanged content — both used to bubble up as
    «❌ انجام این کار با مشکل مواجه شد» even though the action had succeeded.
    """
    await ui.edit_or_send(cq, text, markup)


#: a callback query may only be answered once; panel screens are re-rendered by
#: calling each other, so every answer goes through the idempotent helper.
_answer = ui.answer


@router.callback_query(AdminCB.filter(F.action == "home"))
async def admin_home(
    cq: CallbackQuery, state: FSMContext, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    await state.clear()
    from app import __version__

    stats = await AdminService(session).db_stats()
    pending = await PlanManager(session).requests.count_pending()
    await _edit(
        cq,
        (f"🙋 <b>{fa(pending)} درخواست دسترسی در انتظار بررسی</b>\n\n" if pending else "")
        + T.ADMIN_MENU.format(
            version=__version__,
            env=settings.environment,
            advisors=fa(stats["advisors"]),
            students=fa(stats["students"]),
            plans=fa(stats["plans"]),
        ),
        kb.admin_menu(),
    )
    await _answer(cq)


# ────────────────────────────── access requests ─────────────────────────────
@router.callback_query(AdminCB.filter(F.action == "requests"))
async def admin_requests(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    """People who opened the bot without an invite, waiting for a role."""
    _guard(cq, user)
    manager = PlanManager(session)
    total = await manager.requests.count_pending()
    rows = await manager.requests.pending(PAGE, callback_data.page * PAGE)
    if not rows:
        await _edit(cq, T.ADMIN_NO_REQUESTS, kb.admin_back())
        await _answer(cq)
        return
    body = [T.ADMIN_REQUESTS.format(count=fa(total)), ""]
    for request in rows:
        handle = f"@{request.username}" if request.username else "—"
        body.append(
            f"🙋 <b>{request.full_name}</b> · {handle}\n"
            f"   <code>{request.telegram_id}</code> · "
            f"{fa(request.visits)} مراجعه"
        )
    await _edit(cq, "\n".join(body),
                kb.admin_requests(rows, callback_data.page, total, PAGE))
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "request"))
async def admin_request_card(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    request = await PlanManager(session).requests.by_id(callback_data.ref)
    if request is None:
        await _answer(cq, "درخواست پیدا نشد.", show_alert=True)
        return
    await _edit(
        cq,
        T.ADMIN_REQUEST_CARD.format(
            name=request.full_name,
            username=f"@{request.username}" if request.username else "—",
            telegram=request.telegram_id,
            visits=fa(request.visits),
            last_seen=request.last_seen_at.strftime("%Y-%m-%d %H:%M")
            if request.last_seen_at else "—",
        ),
        kb.admin_request_card(request.id),
    )
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "grant_student"))
async def admin_pick_advisor_for_request(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    candidates = await AdminService(session).advisor_candidates()
    if not candidates:
        await _answer(cq, "مشاوری برای تخصیص وجود ندارد.", show_alert=True)
        return
    await _edit(cq, T.ADMIN_PICK_TARGET_ADVISOR,
                kb.admin_pick_advisor_for_request(candidates, callback_data.ref))
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "grant"))
async def admin_grant_role(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    """Give the visitor a real account. Only reachable by an admin."""
    _guard(cq, user)
    manager = PlanManager(session)
    arg = callback_data.arg or "advisor"
    advisor_id: int | None = None
    if arg == "student":
        role = Role.STUDENT
        advisor_id = callback_data.page or None   # the int field carries the advisor
    else:
        role = Role.ADVISOR

    try:
        created = await manager.approve_request(
            user, callback_data.ref, role, advisor_id=advisor_id
        )
        await session.commit()
    except StudentError as exc:
        await _answer(cq, str(exc), show_alert=True)
        return

    role_fa = T.ROLE_FA[role.value]
    await _answer(cq, 
        T.ADMIN_REQUEST_APPROVED.format(name=created.full_name, role=role_fa),
        show_alert=True,
    )
    # tell the person right away
    try:
        await cq.bot.send_message(
            created.telegram_id,
            T.ACCESS_APPROVED_ADVISOR if role is Role.ADVISOR else T.ACCESS_APPROVED_STUDENT,
            parse_mode="HTML",
        )
    except Exception:  # pragma: no cover - they may have blocked the bot
        log.warning("could not notify approved user tg=%s", created.telegram_id)
    await admin_requests(cq, AdminCB(action="requests"), session, user)


@router.callback_query(AdminCB.filter(F.action == "reject"))
async def admin_reject_request(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    manager = PlanManager(session)
    try:
        request = await manager.reject_request(user, callback_data.ref)
        await session.commit()
    except StudentError as exc:
        await _answer(cq, str(exc), show_alert=True)
        return
    await _answer(cq, T.ADMIN_REQUEST_REJECTED.format(name=request.full_name),
                    show_alert=True)
    await admin_requests(cq, AdminCB(action="requests"), session, user)


# ── adding an advisor directly (no shell) ──
@router.callback_query(AdminCB.filter(F.action == "add_advisor"))
async def admin_add_advisor_prompt(
    cq: CallbackQuery, state: FSMContext, user: User | None = None
) -> None:
    _guard(cq, user)
    await state.set_state(AdminFlow.add_advisor)
    await _edit(cq, T.ADMIN_ADD_ADVISOR_PROMPT, kb.admin_back("advisors"))
    await _answer(cq)


@router.message(AdminFlow.add_advisor, F.text)
async def admin_add_advisor_input(
    message, state: FSMContext, session: AsyncSession, user: User | None = None
) -> None:
    if not is_admin(user, message.from_user.id):
        raise PermissionError(T.ADMIN_ONLY)
    raw = message.text.strip()
    if raw.startswith("/"):
        return
    name, _, tg_raw = raw.partition("|")
    digits = to_en_digits(tg_raw.strip())
    if not digits.isdigit():
        await message.answer(T.TG_ID_INVALID)
        return
    manager = PlanManager(session)
    try:
        advisor = await manager.create_advisor_by_telegram_id(user, name, int(digits))
    except StudentError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    await state.clear()
    await message.answer(
        T.ADMIN_ADVISOR_ADDED.format(name=advisor.full_name),
        reply_markup=kb.admin_advisor_card(advisor),
        parse_mode="HTML",
    )
    try:
        await message.bot.send_message(
            advisor.telegram_id, T.ACCESS_APPROVED_ADVISOR, parse_mode="HTML"
        )
    except Exception:  # pragma: no cover
        log.warning("could not notify new advisor tg=%s", advisor.telegram_id)


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
        await _edit(cq, T.ADMIN_NO_ADVISORS, kb.admin_no_advisors())
        await _answer(cq)
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
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "advisor"))
async def admin_advisor_card(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    data = await AdminService(session).advisor_detail(callback_data.ref)
    if not data:
        await _answer(cq, "مشاور پیدا نشد.", show_alert=True)
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
    await _answer(cq)


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
        await _answer(cq, "این مشاور دانش‌آموزی ندارد.", show_alert=True)
        return
    await _edit(
        cq, T.ADMIN_STUDENTS.format(count=fa(total)),
        kb.admin_students(students, callback_data.page, total, PAGE, ref=callback_data.ref),
    )
    await _answer(cq)


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
        await _answer(cq, "برنامه‌ای ثبت نشده است.", show_alert=True)
        return
    await _edit(
        cq, T.ADMIN_PLANS.format(count=fa(total)),
        kb.admin_plans(plans, callback_data.page, total, PAGE,
                       ref=callback_data.ref, action="advisor_plans"),
    )
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "edit_advisor"))
async def admin_edit_advisor_prompt(
    cq: CallbackQuery, callback_data: AdminCB, state: FSMContext,
    session: AsyncSession, user: User | None = None,
) -> None:
    _guard(cq, user)
    advisor = await PlanManager(session).users.by_id(callback_data.ref)
    if advisor is None:
        await _answer(cq, "مشاور پیدا نشد.", show_alert=True)
        return
    await state.set_state(AdminFlow.edit_advisor)
    await state.update_data(target=advisor.id)
    current = advisor.full_name + (f" | {advisor.telegram_id}" if advisor.telegram_id else "")
    await _edit(cq, T.ADMIN_EDIT_ADVISOR_PROMPT.format(current=current),
                kb.admin_back("advisor", advisor.id))
    await _answer(cq)


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
    await _answer(cq)


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
        await _answer(cq, "پیدا نشد.", show_alert=True)
        return
    if is_admin(advisor, advisor.telegram_id):
        await _answer(cq, T.ADMIN_SELF_ACTION, show_alert=True)
        return
    what = (
        f"فعال‌سازی حساب «{advisor.full_name}»"
        if not advisor.is_active
        else f"تعلیق «{advisor.full_name}» — تا فعال‌سازی مجدد نمی‌تواند "
             "برنامه بسازد یا ارسال کند."
    )
    await _edit(cq, T.ADMIN_CONFIRM.format(what=what),
                kb.admin_confirm("do_suspend", advisor.id))
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "do_suspend"))
async def admin_do_suspend(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    manager = PlanManager(session)
    target = await manager.users.by_id(callback_data.ref)
    if target is None:
        await _answer(cq, "پیدا نشد.", show_alert=True)
        return
    if is_admin(target, target.telegram_id):
        await _answer(cq, T.ADMIN_SELF_ACTION, show_alert=True)
        return
    target.is_active = not target.is_active
    await manager.audit.log(
        "advisor.activated" if target.is_active else "advisor.suspended",
        actor_id=user.id if user else None, detail=target.full_name,
    )
    await _answer(cq, 
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
        await _answer(cq, str(exc), show_alert=True)
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
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "del_advisor_pick"))
async def admin_pick_transfer_target(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    candidates = await AdminService(session).advisor_candidates(exclude=callback_data.ref)
    if not candidates:
        await _answer(cq, "مشاور دیگری برای انتقال وجود ندارد.", show_alert=True)
        return
    await _edit(cq, T.ADMIN_PICK_TARGET_ADVISOR,
                kb.admin_pick_advisor(candidates, callback_data.ref, "del_advisor_to"))
    await _answer(cq)


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
        await _answer(cq, str(exc), show_alert=True)
        return
    except Exception:
        log.exception("advisor deletion failed (id=%s)", callback_data.ref)
        await _answer(cq, T.ADMIN_DELETE_FAILED, show_alert=True)
        return

    detail = (
        f"{fa(report.transferred)} دانش‌آموز منتقل شد"
        if transfer else
        f"{fa(report.detached)} دانش‌آموز بدون مشاور شد · {fa(report.files)} فایل پاک شد"
    )
    await _answer(cq, T.ADMIN_ADVISOR_DELETED.format(name=report.name, detail=detail),
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
        await _answer(cq)
        return
    await _edit(cq, T.ADMIN_STUDENTS.format(count=fa(total)),
                kb.admin_students(students, callback_data.page, total, PAGE))
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "student"))
async def admin_student_card(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    data = await AdminService(session).student_detail(callback_data.ref)
    if not data:
        await _answer(cq, "دانش‌آموز پیدا نشد.", show_alert=True)
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
    await _answer(cq)


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
        await _answer(cq, "برنامه‌ای ثبت نشده است.", show_alert=True)
        return
    await _edit(cq, T.ADMIN_PLANS.format(count=fa(total)),
                kb.admin_plans(plans, callback_data.page, total, PAGE,
                               ref=callback_data.ref, action="student_plans"))
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "edit_student"))
async def admin_edit_student_prompt(
    cq: CallbackQuery, callback_data: AdminCB, state: FSMContext,
    session: AsyncSession, user: User | None = None,
) -> None:
    _guard(cq, user)
    student = await PlanManager(session).users.by_id(callback_data.ref)
    if student is None:
        await _answer(cq, "دانش‌آموز پیدا نشد.", show_alert=True)
        return
    await state.set_state(AdminFlow.edit_student)
    await state.update_data(target=student.id)
    current = student.full_name + (f" | {student.grade}" if student.grade else "")
    await _edit(cq, T.ADMIN_EDIT_STUDENT_PROMPT.format(current=current),
                kb.admin_back("student", student.id))
    await _answer(cq)


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
    await _answer(cq)


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
        await _answer(cq, "پیدا نشد.", show_alert=True)
        return
    what = ("فعال‌سازی" if not student.is_active else "غیرفعال‌سازی") + \
        f" حساب «{student.full_name}»"
    await _edit(cq, T.ADMIN_CONFIRM.format(what=what),
                kb.admin_confirm("do_suspend_student", student.id))
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "do_suspend_student"))
async def admin_do_suspend_student(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    manager = PlanManager(session)
    student = await manager.users.by_id(callback_data.ref)
    if student is None or student.role is not Role.STUDENT:
        await _answer(cq, "پیدا نشد.", show_alert=True)
        return
    student.is_active = not student.is_active
    await manager.audit.log(
        "student.activated" if student.is_active else "student.suspended",
        actor_id=user.id if user else None, student_id=student.id,
    )
    await _answer(cq, 
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
        await _answer(cq, str(exc), show_alert=True)
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
    await _answer(cq)


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
    await _answer(cq)


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
        await _answer(cq, str(exc), show_alert=True)
        return
    except Exception:
        log.exception("admin student deletion failed (id=%s)", callback_data.ref)
        await _answer(cq, T.ADMIN_DELETE_FAILED, show_alert=True)
        return
    await _answer(cq, T.ADMIN_STUDENT_DELETED.format(name=report.name), show_alert=True)
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
        await _answer(cq, "دانش‌آموز پیدا نشد.", show_alert=True)
        return
    current_ids = [a.id for a in data["advisors"]]
    candidates = await service.advisor_candidates(
        exclude=current_ids[0] if current_ids else 0
    )
    if not candidates:
        await _answer(cq, "مشاور دیگری وجود ندارد.", show_alert=True)
        return
    await _edit(
        cq,
        T.ADMIN_TRANSFER_PICK.format(
            name=data["student"].full_name,
            current="، ".join(a.full_name for a in data["advisors"]) or "بدون مشاور",
        ),
        kb.admin_pick_advisor(candidates, callback_data.ref, "transfer_to"),
    )
    await _answer(cq)


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
        await _answer(cq, "اطلاعات نامعتبر است.", show_alert=True)
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
    await _answer(cq)


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
        await _answer(cq, str(exc), show_alert=True)
        return
    await _answer(cq, 
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
        await _answer(cq, "دانش‌آموز پیدا نشد.", show_alert=True)
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
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "reissue"))
async def admin_reissue_invite(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    manager = PlanManager(session)
    student = await manager.users.by_id(callback_data.ref)
    if student is None or student.telegram_id:
        await _answer(cq, "این دانش‌آموز از قبل متصل است.", show_alert=True)
        return
    token = await manager.users.rotate_invite_token(student)
    await manager.audit.log(
        "student.invite_issued", actor_id=user.id if user else None, student_id=student.id
    )
    me = await cq.bot.me()
    link = f"https://t.me/{me.username}?start=inv_{token}"
    await ui.send_text_safe(
        cq,
        T.INVITE_READY.format(
            name=student.full_name, link=link,
            expires=jalali_short(student.invite_expires_at.date()),
        ),
        reply_markup=kb.admin_connection(student),
        disable_web_page_preview=True,
    )
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "revoke_invite"))
async def admin_revoke_invite(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    manager = PlanManager(session)
    student = await manager.users.by_id(callback_data.ref)
    if student is None:
        await _answer(cq, "پیدا نشد.", show_alert=True)
        return
    await manager.users.revoke_invite(student)
    await manager.audit.log(
        "student.invite_revoked", actor_id=user.id if user else None, student_id=student.id
    )
    await _answer(cq, T.INVITE_REVOKED, show_alert=True)
    await admin_connection(cq, AdminCB(action="connection", ref=student.id), session, user)


@router.callback_query(AdminCB.filter(F.action == "ask_unlink"))
async def admin_ask_unlink(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    student = await PlanManager(session).users.by_id(callback_data.ref)
    if student is None:
        await _answer(cq, "پیدا نشد.", show_alert=True)
        return
    await _edit(
        cq,
        T.ADMIN_CONFIRM.format(
            what=f"قطع اتصال تلگرام «{student.full_name}» — پس از این، برنامه‌ها "
                 "مستقیم برای او ارسال نمی‌شود."
        ),
        kb.admin_confirm("do_unlink", student.id),
    )
    await _answer(cq)


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
        await _answer(cq, str(exc), show_alert=True)
        return
    await _answer(cq, T.ADMIN_UNLINKED.format(name=student.full_name), show_alert=True)
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
        await _answer(cq)
        return
    await _edit(cq, T.ADMIN_PLANS.format(count=fa(total)),
                kb.admin_plans(plans, callback_data.page, total, PAGE))
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "plan"))
async def admin_plan_card(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    manager = PlanManager(session)
    plan = await manager.plans.get(callback_data.ref)
    if plan is None:
        await _answer(cq, "برنامه پیدا نشد.", show_alert=True)
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
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "ask_del_plan"))
async def admin_ask_delete_plan(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    plan = await PlanManager(session).plans.get(callback_data.ref)
    if plan is None:
        await _answer(cq, "برنامه پیدا نشد.", show_alert=True)
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
    await _answer(cq)


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
        await _answer(cq, str(exc), show_alert=True)
        return
    except Exception:
        log.exception("admin plan deletion failed (id=%s)", callback_data.ref)
        await _answer(cq, T.ADMIN_DELETE_FAILED, show_alert=True)
        return
    await _answer(cq, T.ADMIN_PLAN_DELETED.format(files=fa(report.files)), show_alert=True)
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
    await _answer(cq, T.ADMIN_HEALTH_OK if callback_data.action == "health" else None)


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
    await _answer(cq)


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
    await _answer(cq)


# ─────────────────────────────── storage ────────────────────────────────────
@router.callback_query(AdminCB.filter(F.action == "storage"))
async def admin_storage(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    queue: RenderQueue, user: User | None = None,
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
    await _answer(cq)


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
    await _answer(cq)


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
    await _answer(cq, T.ADMIN_CLEANUP_DONE.format(files=fa(removed)), show_alert=True)
    await admin_storage(cq, AdminCB(action="storage"), session, queue, user)


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
    await _answer(cq)


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
        await _answer(cq)
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
    await _answer(cq)


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
    await _answer(cq)


# ═════════════════════════════════ backups ══════════════════════════════════
def _schedule_label(row: BotSettings) -> str:
    """Persian description of the current schedule (panel + confirmations)."""
    from ...domain.models import WEEKDAY_FA, WEEKDAY_KEYS

    key = row.backup_schedule.value if isinstance(row.backup_schedule, BackupSchedule) \
        else str(row.backup_schedule)
    template = T.BACKUP_SCHEDULE_FA.get(key, T.BACKUP_SCHEDULE_FA["daily"])
    return template.format(
        n=fa(row.backup_every_hours),
        hour=fa(f"{row.backup_hour:02d}:00"),
        day=WEEKDAY_FA[WEEKDAY_KEYS[min(6, max(0, row.backup_weekday))]],
    )


def _backup_when(moment) -> str:
    return jalali_datetime(moment) if moment else "—"


def _last_backup_block(entry) -> str:
    if entry is None:
        return T.ADMIN_BACKUP_LAST_NONE
    if entry.status is not BackupStatus.OK:
        return T.ADMIN_BACKUP_LAST_FAILED.format(
            when=_backup_when(entry.at), error=(entry.error or "—")[:200]
        )
    return T.ADMIN_BACKUP_LAST_ROW.format(
        icon="✅",
        when=_backup_when(entry.at),
        filename=entry.filename or "—",
        size=human_bytes(entry.size_bytes),
        rows=fa(entry.rows),
        tables=fa(entry.tables),
        engine=entry.engine or "—",
        delivered=fa(entry.delivered),
    )


async def _render_backup_panel(
    cq: CallbackQuery, session: AsyncSession, user: User | None
) -> None:
    """One place builds the 🧰 بکاپ‌گیری screen: status + history + controls."""
    service = BackupService(session)
    status = await service.status()
    row: BotSettings = status["settings"]
    latest = status["latest"]
    on_disk = Path(latest.path) if latest and latest.path else None
    has_file = bool(on_disk and on_disk.exists())
    recipients = status["recipients"]
    shown = recipients[:4]
    recipient_text = "، ".join(f"<code>{c}</code>" for c in shown)
    if len(recipients) > len(shown):
        recipient_text += f" و {fa(len(recipients) - len(shown))} گیرنده دیگر"
    if not recipient_text:
        recipient_text = "⚠️ گیرنده‌ای تنظیم نشده (ADMIN_IDS خالی است)"

    await _edit(
        cq,
        T.ADMIN_BACKUP.format(
            status=T.BACKUP_STATUS_ON if row.backup_enabled else T.BACKUP_STATUS_OFF,
            schedule=_schedule_label(row),
            recipients=recipient_text,
            last=_last_backup_block(latest),
            archives=fa(status["archives_on_disk"]),
            size=human_bytes(status["disk_bytes"]),
            ok=fa(status["stats"]["ok"]),
            failed=fa(status["stats"]["failed"]),
            keep=fa(row.backup_keep),
            next=(
                _backup_when(status["next_run"])
                if status["next_run"]
                else "— (بکاپ خودکار خاموش است)"
            ),
            directory=settings.backup_dir,
        ),
        kb.admin_backup(row, last_ok=latest is not None
                        and latest.status is BackupStatus.OK, has_file=has_file),
    )


@router.callback_query(AdminCB.filter(F.action == "backup"))
async def admin_backup_panel(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    await _render_backup_panel(cq, session, user)
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "backup_now"))
async def admin_backup_now(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    """Take a backup right now and send the archive to the admin chats."""
    _guard(cq, user)
    await _answer(cq, "⏳ در حال تهیه بکاپ…")
    await _edit(cq, T.ADMIN_BACKUP_RUNNING, kb.admin_back("backup"))

    service = BackupService(session, cq.bot)
    try:
        artifact, entry = await service.run(
            trigger="manual", actor_id=user.id if user else None, send=True
        )
        await session.commit()
    except Exception:
        await session.rollback()
        log.exception("manual backup failed")
        await _answer(cq, T.ADMIN_BACKUP_FAILED.format(error="خطای داخلی — لاگ‌ها را ببینید"),
                      show_alert=True)
        await _render_backup_panel(cq, session, user)
        return

    if artifact is None:
        await _answer(
            cq, T.ADMIN_BACKUP_FAILED.format(error=(entry.error or "نامشخص")[:300]),
            show_alert=True,
        )
        await _render_backup_panel(cq, session, user)
        return

    await _edit(
        cq,
        T.ADMIN_BACKUP_SENT.format(
            filename=artifact.filename,
            size=human_bytes(artifact.size_bytes),
            rows=fa(artifact.rows),
            tables=fa(artifact.tables),
            engine=artifact.engine,
            seconds=fa(round(artifact.duration_ms / 1000, 1)),
            delivered=fa(entry.delivered),
        ),
        kb.admin_backup(await service.config(), last_ok=True,
                        has_file=artifact.path.exists()),
    )
    if entry.delivered == 0:
        await cq.bot.send_message(
            cq.from_user.id, T.ADMIN_BACKUP_NO_RECIPIENT, parse_mode="HTML"
        )


@router.callback_query(AdminCB.filter(F.action == "backup_send_last"))
async def admin_backup_send_last(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    """Re-send the newest archive without taking a new backup."""
    _guard(cq, user)
    service = BackupService(session)
    entry = await service.backups.latest_ok()
    if entry is None or not entry.path:
        await _answer(cq, T.ADMIN_BACKUP_FILE_MISSING, show_alert=True)
        return
    path = Path(entry.path)
    if not path.exists():
        await _answer(cq, T.ADMIN_BACKUP_FILE_MISSING, show_alert=True)
        return
    await _answer(cq, "⏳ در حال ارسال…")
    try:
        await cq.bot.send_document(
            cq.from_user.id,
            FSInputFile(path),
            caption=T.BACKUP_CAPTION_MANUAL.format(when=_backup_when(entry.at)),
            parse_mode="HTML",
        )
    except Exception as exc:
        log.warning("could not re-send the last backup: %s", exc)
        await _answer(cq, T.ADMIN_BACKUP_FILE_MISSING, show_alert=True)
        return
    await service.audit.log(
        "backup.sent", actor_id=user.id if user else None, detail=entry.filename
    )
    await cq.bot.send_message(cq.from_user.id, T.ADMIN_BACKUP_SENT_AGAIN)
    await _render_backup_panel(cq, session, user)


@router.callback_query(AdminCB.filter(F.action.in_({"backup_auto_on", "backup_auto_off"})))
async def admin_backup_toggle(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    service = BackupService(session)
    enabled = callback_data.action == "backup_auto_on"
    row = await service.set_enabled(enabled, actor_id=user.id if user else None)
    await session.commit()
    recipients = row.recipient_list()
    text = (
        T.ADMIN_BACKUP_AUTO_ON.format(
            schedule=_schedule_label(row),
            next=_backup_when(next_run(row)),
            recipients=(
                "، ".join(f"<code>{c}</code>" for c in recipients[:4])
                or "⚠️ گیرنده‌ای تنظیم نشده"
            ),
        )
        if enabled
        else T.ADMIN_BACKUP_AUTO_OFF
    )
    await _answer(cq, text, show_alert=True)
    await _render_backup_panel(cq, session, user)


@router.callback_query(AdminCB.filter(F.action == "backup_sched"))
async def admin_backup_schedule(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    from ...domain.models import WEEKDAY_FA, WEEKDAY_KEYS

    row = await BackupService(session).config()
    await _edit(
        cq,
        T.ADMIN_BACKUP_SCHED.format(
            current=_schedule_label(row),
            hour=fa(f"{row.backup_hour:02d}:00"),
            day=WEEKDAY_FA[WEEKDAY_KEYS[min(6, max(0, row.backup_weekday))]],
            interval=f"{fa(row.backup_every_hours)} ساعت",
        ),
        kb.admin_backup_schedules(
            row.backup_schedule.value if isinstance(row.backup_schedule, BackupSchedule)
            else str(row.backup_schedule)
        ),
    )
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "backup_set_sched"))
async def admin_backup_set_schedule(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    try:
        schedule = BackupSchedule(callback_data.arg)
    except ValueError:
        await _answer(cq, "مقدار نامعتبر است.", show_alert=True)
        return
    service = BackupService(session)
    row = await service.set_schedule(schedule, actor_id=user.id if user else None)
    await session.commit()
    await _answer(cq, f"✅ زمان‌بندی تغییر کرد: {_schedule_label(row)}", show_alert=True)
    await admin_backup_schedule(cq, AdminCB(action="backup_sched"), session, user)


@router.callback_query(AdminCB.filter(F.action == "backup_hour"))
async def admin_backup_hour(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    row = await BackupService(session).config()
    await _edit(
        cq, T.ADMIN_BACKUP_HOUR.format(hour=fa(f"{row.backup_hour:02d}:00")),
        kb.admin_backup_hours(row.backup_hour),
    )
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "backup_set_hour"))
async def admin_backup_set_hour(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    service = BackupService(session)
    row = await service.set_hour(callback_data.ref, actor_id=user.id if user else None)
    await session.commit()
    await _answer(cq, f"✅ ساعت بکاپ: {fa(f'{row.backup_hour:02d}:00')}", show_alert=True)
    await _render_backup_panel(cq, session, user)


@router.callback_query(AdminCB.filter(F.action == "backup_day"))
async def admin_backup_day(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    from ...domain.models import WEEKDAY_FA, WEEKDAY_KEYS

    row = await BackupService(session).config()
    await _edit(
        cq,
        T.ADMIN_BACKUP_DAY.format(
            day=WEEKDAY_FA[WEEKDAY_KEYS[min(6, max(0, row.backup_weekday))]]
        ),
        kb.admin_backup_days(row.backup_weekday),
    )
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "backup_set_day"))
async def admin_backup_set_day(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    from ...domain.models import WEEKDAY_FA, WEEKDAY_KEYS

    service = BackupService(session)
    row = await service.set_weekday(callback_data.ref, actor_id=user.id if user else None)
    await session.commit()
    day = WEEKDAY_FA[WEEKDAY_KEYS[row.backup_weekday]]
    await _answer(cq, f"✅ روز بکاپ هفتگی: {day}", show_alert=True)
    await _render_backup_panel(cq, session, user)


@router.callback_query(AdminCB.filter(F.action == "backup_interval"))
async def admin_backup_interval(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    row = await BackupService(session).config()
    await _edit(
        cq, T.ADMIN_BACKUP_INTERVAL.format(interval=fa(row.backup_every_hours)),
        kb.admin_backup_intervals(row.backup_every_hours),
    )
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "backup_set_interval"))
async def admin_backup_set_interval(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    service = BackupService(session)
    row = await service.set_interval_hours(
        callback_data.ref, actor_id=user.id if user else None
    )
    await session.commit()
    await _answer(cq, f"✅ فاصله بکاپ: هر {fa(row.backup_every_hours)} ساعت", show_alert=True)
    await _render_backup_panel(cq, session, user)


@router.callback_query(AdminCB.filter(F.action == "backup_keep"))
async def admin_backup_keep(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    row = await BackupService(session).config()
    await _edit(
        cq, T.ADMIN_BACKUP_KEEP.format(keep=fa(row.backup_keep)),
        kb.admin_backup_keep(row.backup_keep),
    )
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "backup_set_keep"))
async def admin_backup_set_keep(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    service = BackupService(session)
    row = await service.set_keep(callback_data.ref, actor_id=user.id if user else None)
    await session.commit()
    from ...services.backup import prune_backups

    removed = prune_backups(settings.backup_dir, row.backup_keep)
    await _answer(
        cq,
        f"✅ نگهداری روی {fa(row.backup_keep)} نسخه تنظیم شد."
        + (f" ({fa(len(removed))} فایل قدیمی پاک شد)" if removed else ""),
        show_alert=True,
    )
    await _render_backup_panel(cq, session, user)


@router.callback_query(AdminCB.filter(F.action == "backup_history"))
async def admin_backup_history(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    service = BackupService(session)
    rows = await service.backups.recent(PAGE)
    total = await service.backups.count()
    if not rows:
        await _edit(cq, T.ADMIN_BACKUP_NO_HISTORY, kb.admin_backup_history())
        await _answer(cq)
        return
    lines = [T.ADMIN_BACKUP_HISTORY.format(count=fa(total)), ""]
    for entry in rows:
        icon = "✅" if entry.status is BackupStatus.OK else "❌"
        kind = "خودکار" if entry.trigger == "auto" else "دستی"
        lines.append(
            f"{icon} <code>{_backup_when(entry.at)}</code> · {kind}\n"
            f"   <code>{entry.filename or '—'}</code>\n"
            f"   {human_bytes(entry.size_bytes)} · {fa(entry.rows)} رکورد · "
            f"{fa(entry.delivered)} ارسال"
            + (f"\n   ⚠️ {(entry.error or '')[:120]}" if entry.error else "")
        )
    await _edit(cq, "\n".join(lines), kb.admin_backup_history(callback_data.page))
    await _answer(cq)


# ═══════════════════════ restore from an uploaded archive ═══════════════════
#: a second restore while one is running would race the same tables; the flag is
#: per-process, and the PostgreSQL advisory lock covers the multi-replica case
_restore_running = False


def _restore_root() -> Path:
    """Where uploaded archives are staged — inside BACKUP_DIR so one volume
    holds everything an operator may need after a disaster."""
    root = Path(settings.backup_dir) / "restore"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _stage_upload(name: str) -> tuple[str, Path]:
    """Create `restore/<id>/<name>` and return (id, path).

    The id is what travels inside the confirmation button (Telegram caps
    callback_data at 64 bytes), so it has to stay short while the original file
    name — which carries the backup timestamp — is preserved on disk.
    """
    restore_id = uuid.uuid4().hex[:12]
    safe = Path(name or "backup.tar.gz").name.replace("..", "_")[:100] or "backup.tar.gz"
    folder = _restore_root() / restore_id
    folder.mkdir(parents=True, exist_ok=True)
    return restore_id, folder / safe


def _drop_staged(path: Path | str | None) -> None:
    """Remove a staged upload (and its folder) once it is no longer needed."""
    if not path:
        return
    target = Path(str(path))
    target.unlink(missing_ok=True)
    parent = target.parent
    if parent.name and parent.parent == _restore_root():
        try:
            parent.rmdir()
        except OSError:
            pass


def _delta_lines(plan: RestorePlan) -> str:
    """«current ⟵ from backup» per table, for the confirmation screen."""
    out: list[str] = []
    for delta in plan.deltas():
        now = "—" if delta.now is None else fa(delta.now)
        backup = "—" if delta.backup is None else fa(delta.backup)
        if delta.delta is None:
            icon = "❔"
            change = ""
        elif delta.delta > 0:
            icon, change = "➕", f" (+{fa(delta.delta)})"
        elif delta.delta < 0:
            icon, change = "➖", f" ({fa(delta.delta)})"
        else:
            icon, change = "🟰", ""
        out.append(T.ADMIN_RESTORE_TABLE_ROW.format(
            icon=icon, label=delta.label, now=now, backup=backup, delta=change))
    return "".join(out) or "—\n"


def _verified_lines(result: RestoreResult) -> str:
    counts = result.verified or {}
    shown = [
        f"• {TABLE_FA.get(name, name)}: {fa(value)}"
        for name, value in counts.items()
        if name in KEY_TABLES or value
    ]
    return "\n".join(shown[:12]) or "—"


def _warning_lines(warnings: list[str], template: str) -> str:
    return "".join(template.format(text=text[:200]) for text in warnings[:6])


async def _send_safety_backup(bot, result: RestoreResult, chat_ids: list[int]) -> str:
    """The pre-restore archive is the only way back — hand it to the admins."""
    safety = result.safety_backup
    if safety is None:
        return ""
    caption = T.BACKUP_CAPTION_SAFETY.format(when=jalali_datetime(_now_utc()))
    delivered: list[int] = []
    if chat_ids:
        try:
            delivered, _failed = await deliver(bot, safety, chat_ids, caption)
        except Exception as exc:  # pragma: no cover - Telegram outage
            log.warning("could not send the pre-restore backup: %s", exc)
    if delivered:
        return T.ADMIN_RESTORE_SAFETY_SENT.format(
            filename=safety.filename, size=human_bytes(safety.size_bytes))
    return T.ADMIN_RESTORE_SAFETY_KEPT.format(
        filename=safety.filename, size=human_bytes(safety.size_bytes), path=safety.path)


@router.callback_query(AdminCB.filter(F.action == "backup_restore"))
async def admin_backup_restore_prompt(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    state: FSMContext, user: User | None = None,
) -> None:
    """Step 1 — ask for the archive and explain what will be checked."""
    _guard(cq, user)
    if _restore_running:
        await _answer(cq, T.ADMIN_RESTORE_IN_PROGRESS, show_alert=True)
        return
    await state.set_state(AdminFlow.backup_restore_file)
    await state.update_data(restore_path=None)
    await _edit(
        cq,
        T.ADMIN_RESTORE_UPLOAD.format(max_size=human_bytes(MAX_RESTORE_BYTES)),
        kb.admin_backup_restore(),
    )
    await _answer(cq)


@router.callback_query(AdminCB.filter(F.action == "backup_restore_cancel"))
async def admin_backup_restore_cancel(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    state: FSMContext, user: User | None = None,
) -> None:
    _guard(cq, user)
    data = await state.get_data()
    await state.clear()
    _drop_staged(data.get("restore_path"))
    await service_audit(session, "restore.cancelled", user)
    await session.commit()
    await _answer(cq, T.ADMIN_RESTORE_CANCELLED, show_alert=True)
    await _render_backup_panel(cq, session, user)


@router.message(AdminFlow.backup_restore_file, F.document)
async def admin_backup_restore_upload(
    message: Message, state: FSMContext, session: AsyncSession,
    user: User | None = None,
) -> None:
    """Step 2 — download, validate and show exactly what would change."""
    if not is_admin(user, message.from_user.id if message.from_user else None):
        await state.clear()
        await message.answer(T.ADMIN_ONLY, parse_mode="HTML")
        return

    document = message.document
    name = (document.file_name or "backup.tar.gz").replace("/", "_").replace("..", "_")
    size = document.file_size or 0
    if size > MAX_RESTORE_BYTES:
        await message.answer(
            T.ADMIN_RESTORE_TOO_BIG.format(
                size=human_bytes(size), max_size=human_bytes(MAX_RESTORE_BYTES)),
            parse_mode="HTML",
            reply_markup=kb.admin_backup_restore(),
        )
        return

    notice = await message.answer(
        "⏳ در حال دریافت و بررسی فایل بکاپ…", parse_mode="HTML")
    restore_id, target = _stage_upload(name)
    try:
        # aiogram 3 downloads through the bot (`Bot.download` resolves the
        # file_id via getFile and streams it); there is no `Document.download`
        await cq_bot_download(message.bot, document, target)
    except Exception as exc:
        log.warning("restore upload download failed: %s", exc)
        _drop_staged(target)
        await _replace_notice(
            notice,
            T.ADMIN_RESTORE_DOWNLOAD_FAILED.format(reason=str(exc)[:200]),
            kb.admin_backup_restore(),
        )
        return

    service = RestoreService()
    try:
        plan = await service.inspect(target)
    except RestoreError as exc:
        _drop_staged(target)
        await _replace_notice(
            notice, T.ADMIN_RESTORE_BAD_FILE.format(reason=str(exc)),
            kb.admin_backup_restore())
        return
    except Exception:
        _drop_staged(target)
        log.exception("restore inspection crashed")
        await _replace_notice(
            notice, T.ADMIN_RESTORE_BAD_FILE.format(reason="خطای داخلی — لاگ‌ها را ببینید"),
            kb.admin_backup_restore())
        return

    if plan.fatal:
        # nothing will be restored, so do not leave the upload on disk either
        _drop_staged(target)
        await _replace_notice(
            notice, T.ADMIN_RESTORE_FATAL.format(reason=plan.fatal),
            kb.admin_backup_restore())
        await service_audit(session, "restore.inspected", user,
                            f"rejected {plan.path.name}: {plan.fatal[:200]}")
        await session.commit()
        return

    await state.set_state(AdminFlow.backup_restore_confirm)
    await state.update_data(restore_path=str(target), restore_id=restore_id)
    await service_audit(
        session, "restore.inspected", user,
        f"{plan.path.name} sha256={plan.sha256[:16]} rows={plan.rows} "
        f"revision={plan.revision or '—'} executor={plan.executor}",
    )
    await session.commit()

    now_rows = sum(value for key, value in plan.now_counts.items()
                   if key in KEY_TABLES)
    await _replace_notice(
        notice,
        T.ADMIN_RESTORE_SUMMARY.format(
            filename=plan.path.name,
            created=plan.created_jalali or plan.created_utc or "—",
            version=plan.app_version,
            dialect=plan.dialect,
            engine=plan.engine,
            tables=fa(plan.tables),
            rows=fa(plan.rows),
            size=human_bytes(plan.size_bytes),
            revision=plan.revision or "—",
            sha=plan.sha256[:32] + "…",
            executor=plan.executor,
            script=plan.script,
            table=_delta_lines(plan),
            warnings=_warning_lines(plan.warnings, T.ADMIN_RESTORE_WARNINGS),
            now_rows=fa(now_rows),
        ),
        kb.admin_restore_confirm(restore_id),
    )


@router.callback_query(AdminCB.filter(F.action == "backup_restore_do"))
async def admin_backup_restore_run(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    state: FSMContext, user: User | None = None,
) -> None:
    """Step 3 — wipe the live database and load the archive."""
    global _restore_running

    _guard(cq, user)
    actor_id = user.id if user else None
    telegram_id = cq.from_user.id if cq.from_user else None
    if _restore_running:
        await _answer(cq, T.ADMIN_RESTORE_IN_PROGRESS, show_alert=True)
        return

    data = await state.get_data()
    staged = Path(str(data.get("restore_path") or ""))
    if not str(staged) or not staged.exists():
        await state.clear()
        await _answer(cq, T.ADMIN_RESTORE_BAD_FILE.format(
            reason="فایل موقت روی سرور موجود نیست؛ دوباره بفرستید."), show_alert=True)
        return
    if callback_data.arg and str(data.get("restore_id") or "") != callback_data.arg:
        # the confirmation payload must match the staged upload — a stale button
        # from another upload may not silently restore the wrong archive
        await state.clear()
        _drop_staged(staged)
        await _answer(
            cq,
            T.ADMIN_RESTORE_BAD_FILE.format(
                reason="این دکمه به فایل دیگری تعلق دارد؛ لطفاً بکاپ را دوباره بفرستید."),
            show_alert=True,
        )
        return

    await _answer(cq, "⏳ بازگردانی شروع شد…")
    progress_message = await ui.edit_or_send(
        cq, T.ADMIN_RESTORE_RUNNING.format(stage="در حال بررسی و آماده‌سازی…"),
        kb.admin_back("backup"),
    )
    last_edit = time.monotonic()

    async def progress(stage: str) -> None:
        nonlocal last_edit
        if progress_message is None or time.monotonic() - last_edit < 1.5:
            return
        last_edit = time.monotonic()
        await ui.edit_or_send(
            cq, T.ADMIN_RESTORE_RUNNING.format(stage=stage), kb.admin_back("backup"))

    service = RestoreService()
    _restore_running = True
    try:
        result = await service.restore(staged, actor_id=actor_id, progress=progress)
    except RestoreError as exc:
        await ui.edit_or_send(
            cq, T.ADMIN_RESTORE_FAILED.format(reason=str(exc), safety=""),
            kb.admin_back("backup"))
        await state.clear()
        return
    except Exception as exc:
        log.exception("restore failed")
        await ui.edit_or_send(
            cq,
            T.ADMIN_RESTORE_FAILED.format(
                reason=f"{type(exc).__name__}: {str(exc)[:200]}", safety=""),
            kb.admin_back("backup"))
        await state.clear()
        return
    finally:
        _restore_running = False

    # the middleware session is bound to an engine the restore disposed — drop it
    try:
        await session.close()
    except Exception:  # pragma: no cover - defensive
        pass

    recipients = list(dict.fromkeys(
        [telegram_id, *settings.admin_ids] if telegram_id else list(settings.admin_ids)
    ))
    safety_block = await _send_safety_backup(cq.bot, result, recipients)

    if not result.ok:
        body = T.ADMIN_RESTORE_FAILED.format(
            reason=(result.error or "نامشخص")[:600], safety=safety_block)
    else:
        body = T.ADMIN_RESTORE_OK.format(
            filename=result.plan.path.name,
            created=result.plan.created_jalali or result.plan.created_utc or "—",
            duration=f"{fa(round(result.duration_ms / 1000, 1))} ثانیه",
            executor=result.executor,
            revision=result.revision_after or result.plan.revision or "—",
            migrated=" (مهاجرت‌ها اجرا شد)" if result.migrated else "",
            rows=fa(result.plan.rows),
            table=_verified_lines(result),
            warnings=_warning_lines(
                result.plan.warnings, T.ADMIN_RESTORE_WARNINGS)
            + _warning_lines(result.mismatches, T.ADMIN_RESTORE_MISMATCH),
            safety=safety_block,
        )
    await ui.edit_or_send(cq, body, kb.admin_back("backup"))
    await state.clear()
    _drop_staged(staged)
    await _render_backup_panel(cq, session, user)


async def cq_bot_download(bot, document, target: Path) -> None:
    """Stream an uploaded document to `target`, refusing anything unexpected."""
    if bot is None:
        raise RestoreError("ربات در دسترس نیست.")
    target.parent.mkdir(parents=True, exist_ok=True)
    await bot.download(document, destination=str(target))
    if not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise RestoreError("فایل دانلود شده خالی است.")


async def _replace_notice(notice, text: str, markup=None) -> None:
    """Turn the «receiving…» message into the real answer (never a new spam)."""
    if notice is None:
        return
    try:
        await notice.edit_text(text, reply_markup=markup, parse_mode="HTML")
        return
    except Exception as exc:
        log.debug("could not edit the restore notice (%s) — sending a new one", exc)
    try:
        await notice.answer(text, reply_markup=markup, parse_mode="HTML")
    except Exception as exc:  # pragma: no cover - chat went away
        log.warning("could not report the restore result: %s", exc)


async def service_audit(session: AsyncSession, action: str, user: User | None,
                        detail: str | None = None) -> None:
    """Audit helper for handlers that run outside a PlanManager."""
    from ...repositories.repositories import AuditRepository

    await AuditRepository(session).log(
        action, actor_id=user.id if user else None, detail=detail)
