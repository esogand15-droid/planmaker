"""Student-facing flow — deliberately simpler: last plan, history, files."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db.models import Role, User
from ...domain.persian import week_label
from ...services.plan_manager import PlanManager
from ...services.render_queue import RenderQueue
from ..delivery import ensure_artifacts, input_for, remember_file_id
from .. import keyboards as kb
from .. import texts as T
from ..texts import Nav

router = Router(name="student")


@router.callback_query(Nav.filter(F.to == "my_last"))
async def my_last_plan(
    cq: CallbackQuery, user: User, session: AsyncSession, queue: RenderQueue
) -> None:
    manager = PlanManager(session)
    plan = await manager.plans.latest_for_student(user.id)
    if plan is None or not plan.image_path:
        await cq.answer(T.STUDENT_NO_PLAN, show_alert=True)
        return
    await manager.ensure_can_view_plan(user, plan)
    await ensure_artifacts(session, plan, queue)

    root = queue.service.storage_root
    png, pdf = input_for(plan, "png", root), input_for(plan, "pdf", root)
    if png is None:
        await cq.answer(T.GENERIC_ERROR, show_alert=True)
        return
    sent = await cq.message.answer_photo(
        png, caption=f"📅 {week_label(plan.week_start, plan.week_end)}"
    )
    remember_file_id(plan, "png", sent)
    if pdf is not None:
        sent_pdf = await cq.message.answer_document(pdf, caption="📄 نسخه PDF")
        remember_file_id(plan, "pdf", sent_pdf)
    await cq.answer()


@router.callback_query(Nav.filter(F.to == "my_history"))
async def my_history(cq: CallbackQuery, user: User, session: AsyncSession) -> None:
    manager = PlanManager(session)
    size = settings.plans_page_size
    plans = await manager.plans.history(
        student_id=user.id, limit=size, only_generated=True
    )
    if not plans:
        await cq.answer(T.STUDENT_NO_PLAN, show_alert=True)
        return
    total = await manager.plans.count_history(student_id=user.id, only_generated=True)
    # kind="mine" keeps pagination scoped to the student's own plans
    await cq.message.edit_text(
        "📆 برنامه‌های قبلی شما",
        reply_markup=kb.plan_list(plans, 0, total, size, kind="mine", student_view=True),
    )
    await cq.answer()


def is_student(user: User) -> bool:
    return user.role == Role.STUDENT
