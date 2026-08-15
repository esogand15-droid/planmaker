"""Advisor flow: new plan → student → week → days/slots → assignments →
preview → confirm → generate → deliver → send to student."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db.models import Role, User, WeeklyPlanDB
from ...domain.models import SLOTS_PER_DAY, Activity
from ...domain.persian import (
    jalali_short,
    parse_jalali,
    saturday_of,
    to_fa_digits,
    week_label,
)
from ...services.plan_manager import AccessDenied, PlanManager
from ...services.render_queue import RenderQueue
from ..delivery import ensure_artifacts, input_for, remember_file_id
from .. import keyboards as kb
from .. import texts as T
from ..states import PlanFlow
from ..texts import AssignCB, DayCB, Nav, PlanCB, SlotCB, StudentCB, WeekCB

log = logging.getLogger(__name__)
router = Router(name="advisor")


def _is_advisor(user: User) -> bool:
    return user.role in (Role.ADVISOR, Role.ADMIN)


router.message.filter(F.chat.type == "private")


# --------------------------------------------------------------- entry ------
@router.callback_query(Nav.filter(F.to == "menu"))
async def back_to_menu(cq: CallbackQuery, state: FSMContext, user: User) -> None:
    await state.clear()
    await cq.message.edit_text(
        T.MAIN_MENU, reply_markup=kb.advisor_menu(), parse_mode="HTML"
    )
    await cq.answer()


@router.callback_query(Nav.filter(F.to == "new"))
async def new_plan(
    cq: CallbackQuery, state: FSMContext, user: User, session: AsyncSession
) -> None:
    if not _is_advisor(user):
        raise AccessDenied(T.ACCESS_DENIED)
    await state.set_state(PlanFlow.select_student)
    await state.update_data(query=None)
    await _show_students(cq, session, user, page=0, query=None)


async def _show_students(
    cq: CallbackQuery, session: AsyncSession, user: User, page: int, query: str | None
) -> None:
    manager = PlanManager(session)
    size = settings.students_page_size
    students = await manager.users.students_of(
        user.id, query=query, limit=size, offset=page * size
    )
    total = await manager.users.count_students_of(user.id, query)
    if not students and not query:
        await cq.message.edit_text(T.NO_STUDENTS, reply_markup=kb.back_only())
        await cq.answer()
        return
    await cq.message.edit_text(
        T.CHOOSE_STUDENT,
        reply_markup=kb.students_list(students, page, total, size),
        parse_mode="HTML",
    )
    await cq.answer()


@router.callback_query(StudentCB.filter(F.action == "page"))
async def students_page(
    cq: CallbackQuery, callback_data: StudentCB, state: FSMContext,
    user: User, session: AsyncSession,
) -> None:
    data = await state.get_data()
    await _show_students(cq, session, user, callback_data.page, data.get("query"))


@router.callback_query(StudentCB.filter(F.action == "search"))
async def students_search(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PlanFlow.search_student)
    await cq.message.edit_text(T.SEARCH_PROMPT, reply_markup=kb.back_only("new"))
    await cq.answer()


@router.message(PlanFlow.search_student, F.text)
async def students_search_input(
    message: Message, state: FSMContext, user: User, session: AsyncSession
) -> None:
    query = message.text.strip()
    await state.update_data(query=query)
    await state.set_state(PlanFlow.select_student)
    manager = PlanManager(session)
    size = settings.students_page_size
    students = await manager.users.students_of(user.id, query=query, limit=size)
    total = await manager.users.count_students_of(user.id, query)
    if not students:
        await message.answer("نتیجه‌ای پیدا نشد.", reply_markup=kb.back_only("new"))
        return
    await message.answer(
        T.CHOOSE_STUDENT,
        reply_markup=kb.students_list(students, 0, total, size),
        parse_mode="HTML",
    )


@router.callback_query(StudentCB.filter(F.action == "pick"))
async def student_picked(
    cq: CallbackQuery, callback_data: StudentCB, state: FSMContext,
    user: User, session: AsyncSession,
) -> None:
    manager = PlanManager(session)
    student = await manager.ensure_owns_student(user, callback_data.student_id)
    await state.set_state(PlanFlow.select_week)
    await state.update_data(student_id=student.id)
    await cq.message.edit_text(
        T.CHOOSE_WEEK.format(student=student.full_name),
        reply_markup=kb.week_choices(student.id, saturday_of(date.today())),
        parse_mode="HTML",
    )
    await cq.answer()


@router.callback_query(WeekCB.filter(F.action == "pick"))
async def week_picked(
    cq: CallbackQuery, callback_data: WeekCB, state: FSMContext,
    user: User, session: AsyncSession,
) -> None:
    start = saturday_of(date.today()) + timedelta(days=7 * callback_data.offset)
    await _open_or_create(cq, state, user, session, callback_data.student_id, start)


@router.callback_query(WeekCB.filter(F.action == "custom"))
async def week_custom(cq: CallbackQuery, callback_data: WeekCB, state: FSMContext) -> None:
    await state.set_state(PlanFlow.custom_week)
    await state.update_data(student_id=callback_data.student_id)
    await cq.message.edit_text(
        T.CUSTOM_WEEK_PROMPT, reply_markup=kb.back_only("new"), parse_mode="HTML"
    )
    await cq.answer()


@router.message(PlanFlow.custom_week, F.text)
async def week_custom_input(
    message: Message, state: FSMContext, user: User, session: AsyncSession
) -> None:
    data = await state.get_data()
    try:
        start = parse_jalali(message.text)
    except ValueError:
        await message.answer(T.INVALID_DATE, parse_mode="HTML")
        return
    manager = PlanManager(session)
    plan = await manager.create_plan(user, int(data["student_id"]), start)
    await state.set_state(PlanFlow.edit_day)
    await state.update_data(plan_id=plan.id)
    await message.answer(
        _overview_text(plan, manager),
        reply_markup=kb.days_overview(plan.id, PlanManager.to_domain(plan)),
        parse_mode="HTML",
    )


async def _open_or_create(
    cq: CallbackQuery, state: FSMContext, user: User, session: AsyncSession,
    student_id: int, start: date,
) -> None:
    manager = PlanManager(session)
    plan = await manager.create_plan(user, student_id, start)
    await state.set_state(PlanFlow.edit_day)
    await state.update_data(plan_id=plan.id, student_id=student_id)
    await cq.message.edit_text(
        _overview_text(plan, manager),
        reply_markup=kb.days_overview(plan.id, PlanManager.to_domain(plan)),
        parse_mode="HTML",
    )
    await cq.answer()


def _overview_text(plan: WeeklyPlanDB, manager: PlanManager) -> str:
    domain = manager.to_domain(plan)
    return (
        f"<b>{T.HEADER}</b>\n\n"
        f"{kb.plan_header(plan)}\n\n"
        f"📚 فعالیت‌ها: {to_fa_digits(str(domain.activity_count))}"
        f" · 📝 تکالیف: {to_fa_digits(str(len(domain.assignments)))}\n"
        f"🧩 نسخه {to_fa_digits(str(plan.version))}\n\n"
        "روز مورد نظر را انتخاب کنید:"
    )


# ------------------------------------------------------------- day/slots ----
@router.callback_query(PlanCB.filter(F.action == "days"))
async def show_days(
    cq: CallbackQuery, callback_data: PlanCB, state: FSMContext,
    user: User, session: AsyncSession,
) -> None:
    manager = PlanManager(session)
    plan = await manager.get_editable(user, callback_data.plan_id)
    await state.set_state(PlanFlow.edit_day)
    await state.update_data(plan_id=plan.id)
    await _safe_edit(
        cq,
        _overview_text(plan, manager),
        kb.days_overview(plan.id, PlanManager.to_domain(plan)),
    )


@router.callback_query(DayCB.filter(F.action == "open"))
async def open_day(
    cq: CallbackQuery, callback_data: DayCB, state: FSMContext,
    user: User, session: AsyncSession,
) -> None:
    manager = PlanManager(session)
    plan = await manager.get_editable(user, callback_data.plan_id)
    domain = manager.to_domain(plan)
    day = domain.day(callback_data.day)
    await state.set_state(PlanFlow.edit_day)
    await state.update_data(plan_id=plan.id, day=callback_data.day)
    await _safe_edit(
        cq,
        T.DAY_TITLE.format(
            day=day.fa_name, date=jalali_short(day.date) if day.date else ""
        ),
        kb.day_editor(plan.id, domain, callback_data.day),
    )


@router.callback_query(DayCB.filter(F.action == "clear"))
async def clear_day(
    cq: CallbackQuery, callback_data: DayCB, user: User, session: AsyncSession
) -> None:
    manager = PlanManager(session)
    await manager.clear_day(user, callback_data.plan_id, callback_data.day)
    plan = await manager.get_editable(user, callback_data.plan_id)
    domain = manager.to_domain(plan)
    await _safe_edit(
        cq,
        T.DAY_TITLE.format(
            day=T.day_fa(callback_data.day),
            date=jalali_short(domain.day(callback_data.day).date or plan.week_start),
        ),
        kb.day_editor(plan.id, domain, callback_data.day),
    )
    await cq.answer("روز پاک شد.")


@router.callback_query(DayCB.filter(F.action == "copy"))
async def copy_day_prompt(cq: CallbackQuery, callback_data: DayCB) -> None:
    await cq.message.edit_text(
        f"کپی برنامهٔ «{T.day_fa(callback_data.day)}» به کدام روز؟",
        reply_markup=kb.copy_day_targets(callback_data.plan_id, callback_data.day),
    )
    await cq.answer()


@router.callback_query(DayCB.filter(F.action == "copyto"))
async def copy_day_apply(
    cq: CallbackQuery, callback_data: DayCB, user: User, session: AsyncSession
) -> None:
    manager = PlanManager(session)
    await manager.copy_day(user, callback_data.plan_id, callback_data.day, callback_data.arg)
    plan = await manager.get_editable(user, callback_data.plan_id)
    await _safe_edit(
        cq, _overview_text(plan, manager),
        kb.days_overview(plan.id, PlanManager.to_domain(plan)),
    )
    await cq.answer(f"به {T.day_fa(callback_data.arg)} کپی شد.")


@router.callback_query(PlanCB.filter(F.action == "copyweek"))
async def copy_previous_week(
    cq: CallbackQuery, callback_data: PlanCB, user: User, session: AsyncSession
) -> None:
    manager = PlanManager(session)
    count = await manager.copy_previous_week(user, callback_data.plan_id)
    if count == 0:
        await cq.answer(T.NO_PREVIOUS_WEEK, show_alert=True)
        return
    plan = await manager.get_editable(user, callback_data.plan_id)
    await _safe_edit(
        cq, _overview_text(plan, manager),
        kb.days_overview(plan.id, PlanManager.to_domain(plan)),
    )
    await cq.answer(T.COPIED_WEEK.format(count=to_fa_digits(str(count))))


@router.callback_query(SlotCB.filter(F.action == "edit"))
async def edit_slot(
    cq: CallbackQuery, callback_data: SlotCB, state: FSMContext,
    user: User, session: AsyncSession,
) -> None:
    manager = PlanManager(session)
    plan = await manager.get_editable(user, callback_data.plan_id)
    domain = manager.to_domain(plan)
    activity = domain.day(callback_data.day).slot(callback_data.slot)
    await state.set_state(PlanFlow.edit_slot)
    await state.update_data(
        plan_id=plan.id, day=callback_data.day, slot=callback_data.slot
    )
    current = f"\n\nمقدار فعلی:\n<code>{activity.summary()}</code>" if activity else ""
    await cq.message.edit_text(
        T.SLOT_PROMPT.format(
            slot=to_fa_digits(str(callback_data.slot + 1)), day=T.day_fa(callback_data.day)
        )
        + current,
        reply_markup=kb.slot_editor(
            plan.id, callback_data.day, callback_data.slot, activity is not None
        ),
        parse_mode="HTML",
    )
    await cq.answer()


@router.callback_query(SlotCB.filter(F.action == "clear"))
async def clear_slot(
    cq: CallbackQuery, callback_data: SlotCB, user: User, session: AsyncSession
) -> None:
    manager = PlanManager(session)
    await manager.set_slot(
        user, callback_data.plan_id, callback_data.day, callback_data.slot, None
    )
    plan = await manager.get_editable(user, callback_data.plan_id)
    domain = manager.to_domain(plan)
    await _safe_edit(
        cq,
        T.DAY_TITLE.format(
            day=T.day_fa(callback_data.day),
            date=jalali_short(domain.day(callback_data.day).date or plan.week_start),
        ),
        kb.day_editor(plan.id, domain, callback_data.day),
    )
    await cq.answer("خانه خالی شد.")


@router.message(PlanFlow.edit_slot, F.text)
async def slot_input(
    message: Message, state: FSMContext, user: User, session: AsyncSession,
    queue: RenderQueue,
) -> None:
    data = await state.get_data()
    plan_id, weekday, slot = int(data["plan_id"]), data["day"], int(data["slot"])
    activity = Activity.from_quick_entry(slot, message.text)

    manager = PlanManager(session)
    await manager.set_slot(user, plan_id, weekday, slot, activity)
    plan = await manager.get_editable(user, plan_id)
    domain = manager.to_domain(plan)

    warning = ""
    issues = [
        i for i in queue.service.renderer.validate(domain)
        if i.scope == "cell" and i.weekday == weekday and i.slot_index == slot
    ]
    if issues:
        warning = f"\n\n⚠️ {issues[0].message} — بهتر است کوتاه‌تر بنویسید."

    next_slot = slot + 1
    if next_slot < SLOTS_PER_DAY:
        await state.update_data(slot=next_slot)
        await message.answer(
            T.SLOT_SAVED + warning + "\n\n" +
            T.SLOT_PROMPT.format(
                slot=to_fa_digits(str(next_slot + 1)), day=T.day_fa(weekday)
            ),
            reply_markup=kb.slot_editor(plan_id, weekday, next_slot, False),
            parse_mode="HTML",
        )
    else:
        await state.set_state(PlanFlow.edit_day)
        await message.answer(
            T.SLOT_SAVED + warning,
            reply_markup=kb.day_editor(plan_id, domain, weekday),
        )


# ----------------------------------------------------------- assignments ----
@router.callback_query(AssignCB.filter(F.action == "open"))
async def open_assignments(
    cq: CallbackQuery, callback_data: AssignCB, state: FSMContext,
    user: User, session: AsyncSession,
) -> None:
    manager = PlanManager(session)
    plan = await manager.get_editable(user, callback_data.plan_id)
    domain = manager.to_domain(plan)
    await state.set_state(PlanFlow.edit_assignments)
    await state.update_data(plan_id=plan.id)
    current = ""
    if domain.assignments:
        listed = "\n".join(
            f"{to_fa_digits(str(i))}. {a.text}"
            for i, a in enumerate(sorted(domain.assignments, key=lambda x: x.order), 1)
        )
        current = f"\n\nتکالیف فعلی:\n{listed}"
    await cq.message.edit_text(
        T.ASSIGN_PROMPT + current,
        reply_markup=kb.assignments_editor(plan.id, bool(domain.assignments)),
        parse_mode="HTML",
    )
    await cq.answer()


@router.callback_query(AssignCB.filter(F.action == "clear"))
async def clear_assignments(
    cq: CallbackQuery, callback_data: AssignCB, user: User, session: AsyncSession
) -> None:
    manager = PlanManager(session)
    await manager.set_assignments(user, callback_data.plan_id, [])
    plan = await manager.get_editable(user, callback_data.plan_id)
    await _safe_edit(
        cq, _overview_text(plan, manager),
        kb.days_overview(plan.id, PlanManager.to_domain(plan)),
    )
    await cq.answer("تکالیف پاک شد.")


@router.message(PlanFlow.edit_assignments, F.text)
async def assignments_input(
    message: Message, state: FSMContext, user: User, session: AsyncSession,
    queue: RenderQueue,
) -> None:
    data = await state.get_data()
    plan_id = int(data["plan_id"])
    texts = [line.strip(" -•.") for line in message.text.splitlines() if line.strip()]
    manager = PlanManager(session)
    await manager.set_assignments(user, plan_id, texts)
    plan = await manager.get_editable(user, plan_id)
    domain = manager.to_domain(plan)

    warning = ""
    if any(i.scope == "assignments" for i in queue.service.renderer.validate(domain)):
        warning = "\n\n⚠️ تعداد/طول تکالیف بیش از ظرفیت قالب است؛ چند مورد را کوتاه کنید."

    await state.set_state(PlanFlow.edit_day)
    await message.answer(
        T.ASSIGN_SAVED + warning,
        reply_markup=kb.days_overview(plan.id, domain),
    )


# ------------------------------------------------- preview / generate -------
@router.callback_query(PlanCB.filter(F.action == "preview"))
async def preview(
    cq: CallbackQuery, callback_data: PlanCB, user: User,
    session: AsyncSession, queue: RenderQueue,
) -> None:
    manager = PlanManager(session)
    plan = await manager.get_editable(user, callback_data.plan_id)
    domain = manager.to_domain(plan)
    report = await queue.validate(domain)
    if report.errors:
        await cq.answer("\n".join(report.errors)[:200], show_alert=True)
        return
    await cq.answer(T.GENERATING)
    png = await queue.preview(domain)
    caption = kb.plan_header(plan)
    if report.issues:
        caption += "\n\n⚠️ " + "\n⚠️ ".join(i.human() for i in report.issues[:3])
    await cq.message.answer_photo(
        BufferedInputFile(png, filename="preview.png"),
        caption=caption,
        reply_markup=kb.preview_actions(plan.id),
    )


@router.callback_query(PlanCB.filter(F.action == "confirm"))
async def confirm(
    cq: CallbackQuery, callback_data: PlanCB, user: User,
    session: AsyncSession, queue: RenderQueue,
) -> None:
    manager = PlanManager(session)
    plan = await manager.get_editable(user, callback_data.plan_id)
    domain = manager.to_domain(plan)
    report = await queue.validate(domain)
    if not report.ok:
        problems = "\n".join(f"• {p}" for p in report.human()[:5])
        await _safe_edit(
            cq, T.NOT_READY.format(problems=problems), kb.confirm_actions(plan.id)
        )
        await cq.answer()
        return
    await _safe_edit(
        cq,
        T.CONFIRM_TEMPLATE.format(
            student=plan.student.full_name,
            week=week_label(plan.week_start, plan.week_end),
            filled_days=to_fa_digits(str(domain.filled_days)),
            activities=to_fa_digits(str(domain.activity_count)),
            assignments=to_fa_digits(str(len(domain.assignments))),
            version=to_fa_digits(str(plan.version)),
        ),
        kb.confirm_actions(plan.id),
    )
    await cq.answer()


@router.callback_query(PlanCB.filter(F.action.in_({"generate", "regenerate"})))
async def generate(
    cq: CallbackQuery, callback_data: PlanCB, state: FSMContext, user: User,
    session: AsyncSession, queue: RenderQueue,
) -> None:
    manager = PlanManager(session)
    plan = await manager.get_editable(user, callback_data.plan_id)
    domain = manager.to_domain(plan)
    force = callback_data.action == "regenerate"

    await cq.answer(T.GENERATING)
    result = await queue.generate(domain, force=force)
    await manager.plans.mark_generated(
        plan,
        image_path=str(result.png_path),
        pdf_path=str(result.pdf_path),
        plan_hash=result.plan_hash,
        template_version=result.template_version,
        renderer_version=result.renderer,
        duration_ms=result.duration_ms,
    )
    await manager.audit.log(
        "plan.generated" if not force else "plan.regenerated",
        actor_id=user.id, plan_id=plan.id, student_id=plan.student_id,
        detail=f"{result.renderer} {result.duration_ms}ms cached={result.cached}",
    )
    await state.clear()
    await _deliver(cq, plan, result.png_path, result.pdf_path, result.caption)


async def _deliver(cq: CallbackQuery, plan: WeeklyPlanDB, png, pdf, caption: str) -> None:
    photo_msg = await cq.message.answer_photo(FSInputFile(png), caption=caption)
    remember_file_id(plan, "png", photo_msg)
    pdf_msg = await cq.message.answer_document(
        FSInputFile(pdf), caption="📄 نسخه PDF (مناسب چاپ)"
    )
    remember_file_id(plan, "pdf", pdf_msg)
    await cq.message.answer(
        T.GENERATED.format(
            student=plan.student.full_name,
            week=week_label(plan.week_start, plan.week_end),
            version=to_fa_digits(str(plan.version)),
        ),
        reply_markup=kb.generated_actions(plan.id, can_send=bool(plan.student.telegram_id)),
        parse_mode="HTML",
    )


@router.callback_query(PlanCB.filter(F.action.in_({"png", "pdf"})))
async def resend_file(
    cq: CallbackQuery, callback_data: PlanCB, user: User,
    session: AsyncSession, queue: RenderQueue,
) -> None:
    manager = PlanManager(session)
    plan = await manager.get_viewable(user, callback_data.plan_id)
    kind = "png" if callback_data.action == "png" else "pdf"
    if not plan.image_path and not plan.pdf_path:
        await cq.answer("هنوز فایلی تولید نشده است.", show_alert=True)
        return

    await ensure_artifacts(session, plan, queue)  # ephemeral disk safety net
    file = input_for(plan, kind)
    if file is None:
        await cq.answer(T.GENERIC_ERROR, show_alert=True)
        return
    caption = kb.plan_header(plan) if kind == "png" else "📄 نسخه PDF"
    sent = await cq.message.answer_document(file, caption=caption)
    remember_file_id(plan, kind, sent)
    await cq.answer()


# ---------------------------------------------------------------- send ------
@router.callback_query(PlanCB.filter(F.action == "ask_send"))
async def ask_send(
    cq: CallbackQuery, callback_data: PlanCB, user: User, session: AsyncSession
) -> None:
    manager = PlanManager(session)
    plan = await manager.get_editable(user, callback_data.plan_id)
    if not plan.student.telegram_id:
        await cq.answer(T.STUDENT_NO_TELEGRAM, show_alert=True)
        return
    await _safe_edit(
        cq,
        T.SEND_CONFIRM.format(
            student=plan.student.full_name,
            week=week_label(plan.week_start, plan.week_end),
        ),
        kb.send_confirm(plan.id),
    )
    await cq.answer()


@router.callback_query(PlanCB.filter(F.action == "send"))
async def send_to_student(
    cq: CallbackQuery, callback_data: PlanCB, user: User,
    session: AsyncSession, queue: RenderQueue,
) -> None:
    manager = PlanManager(session)
    plan = await manager.get_editable(user, callback_data.plan_id)
    if not plan.image_path or not plan.pdf_path:
        await cq.answer("ابتدا برنامه را تولید کنید.", show_alert=True)
        return
    if not plan.student.telegram_id:
        await cq.answer(T.STUDENT_NO_TELEGRAM, show_alert=True)
        return

    await ensure_artifacts(session, plan, queue)
    png, pdf = input_for(plan, "png"), input_for(plan, "pdf")
    if png is None or pdf is None:
        await cq.answer(T.GENERIC_ERROR, show_alert=True)
        return

    week = week_label(plan.week_start, plan.week_end)
    sent_photo = await cq.bot.send_photo(
        plan.student.telegram_id, png, caption=T.STUDENT_NEW_PLAN.format(week=week)
    )
    remember_file_id(plan, "png", sent_photo)
    sent_pdf = await cq.bot.send_document(
        plan.student.telegram_id, pdf, caption="📄 نسخه PDF"
    )
    remember_file_id(plan, "pdf", sent_pdf)
    await manager.mark_sent(user, plan)
    await cq.message.answer(T.SENT_OK)
    await cq.answer()


# ------------------------------------------------------ history / drafts ----
@router.callback_query(Nav.filter(F.to.in_({"history", "drafts"})))
async def list_plans(
    cq: CallbackQuery, callback_data: Nav, user: User, session: AsyncSession
) -> None:
    manager = PlanManager(session)
    size = settings.plans_page_size
    if callback_data.to == "drafts":
        plans = await manager.plans.drafts_of(user.id, limit=size)
        total = len(plans)
        empty = T.DRAFTS_EMPTY
    else:
        plans = await manager.plans.history(advisor_id=user.id, limit=size)
        total = await manager.plans.count_history(advisor_id=user.id)
        empty = T.HISTORY_EMPTY
    if not plans:
        await _safe_edit(cq, empty, kb.back_only())
        await cq.answer()
        return
    title = "📝 <b>پیش‌نویس‌ها</b>" if callback_data.to == "drafts" else "📂 <b>برنامه‌های قبلی</b>"
    await _safe_edit(cq, title, kb.plan_list(plans, 0, total, size))
    await cq.answer()


@router.callback_query(PlanCB.filter(F.action == "hpage"))
async def history_page(
    cq: CallbackQuery, callback_data: PlanCB, user: User, session: AsyncSession
) -> None:
    manager = PlanManager(session)
    size = settings.plans_page_size
    plans = await manager.plans.history(
        advisor_id=user.id, limit=size, offset=callback_data.page * size
    )
    total = await manager.plans.count_history(advisor_id=user.id)
    await _safe_edit(
        cq, "📂 <b>برنامه‌های قبلی</b>", kb.plan_list(plans, callback_data.page, total, size)
    )
    await cq.answer()


@router.callback_query(PlanCB.filter(F.action == "open"))
async def open_plan(
    cq: CallbackQuery, callback_data: PlanCB, user: User, session: AsyncSession
) -> None:
    manager = PlanManager(session)
    plan = await manager.get_viewable(user, callback_data.plan_id)
    can_edit = user.role == Role.ADMIN or plan.advisor_id == user.id
    await _safe_edit(
        cq,
        f"{kb.plan_header(plan)}\n\nوضعیت: {plan.status.value} · نسخه "
        f"{to_fa_digits(str(plan.version))}",
        kb.plan_card(
            plan,
            can_edit=can_edit,
            can_send=can_edit and bool(plan.student.telegram_id) and bool(plan.image_path),
        ),
    )
    await cq.answer()


@router.callback_query(PlanCB.filter(F.action == "ask_delete"))
async def ask_delete(cq: CallbackQuery, callback_data: PlanCB) -> None:
    await cq.message.edit_text(
        "این برنامه حذف شود؟ این کار قابل بازگشت نیست.",
        reply_markup=kb.confirm_delete(callback_data.plan_id),
    )
    await cq.answer()


@router.callback_query(PlanCB.filter(F.action == "delete"))
async def delete_plan(
    cq: CallbackQuery, callback_data: PlanCB, state: FSMContext,
    user: User, session: AsyncSession,
) -> None:
    manager = PlanManager(session)
    await manager.delete_plan(user, callback_data.plan_id)
    await state.clear()
    await _safe_edit(cq, T.DELETED, kb.advisor_menu())
    await cq.answer()


# ------------------------------------------------------------- utilities ----
@router.message(Command("quick"))
async def quick_help(message: Message) -> None:
    await message.answer(
        "ورود سریع فعالیت:\n<code>زیست | گوارش | ۴۰ تست | ۹۰ دقیقه</code>\n\n"
        "کافی است داخل ویرایش هر خانه همین قالب را بفرستید.",
        parse_mode="HTML",
    )


async def _safe_edit(cq: CallbackQuery, text: str, markup) -> None:
    """edit_text fails on identical content / photo messages — fall back to answer."""
    try:
        await cq.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        await cq.message.answer(text, reply_markup=markup, parse_mode="HTML")
