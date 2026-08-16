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

from ...config import settings
from ...db.models import Role, User
from ...domain.persian import jalali_short, to_fa_digits
from ...security import is_admin
from ...services.admin_service import AdminService, uptime
from ...services.plan_manager import PlanManager
from ...services.render_queue import RenderQueue
from .. import keyboards as kb
from .. import texts as T
from ..texts import AdminCB

log = logging.getLogger(__name__)
router = Router(name="admin")

PAGE = 6
DOT = {True: "🟢", False: "🔴"}


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
    cq: CallbackQuery, state: FSMContext, user: User | None = None
) -> None:
    _guard(cq, user)
    await state.clear()
    from app import __version__

    await _edit(
        cq,
        T.ADMIN_MENU.format(version=__version__, env=settings.environment),
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
            f"{DOT[advisor.is_active]} <b>{advisor.full_name}</b>\n"
            f"   👨‍🎓 {fa(students)} دانش‌آموز · 📅 {fa(plans)} برنامه"
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
    status = "🟢 فعال" if advisor.is_active else "🔒 غیرفعال"
    last = data["last_seen"]
    await _edit(
        cq,
        T.ADMIN_ADVISOR_CARD.format(
            name=advisor.full_name,
            status_line=f"وضعیت: {status} · نقش: {advisor.role.value}\n",
            telegram=advisor.telegram_id or "—",
            students=fa(data["students"]),
            plans=fa(data["plans"]),
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
    plans = await manager.plans.history(advisor_id=callback_data.ref, limit=PAGE)
    if not plans:
        await cq.answer("برنامه‌ای ثبت نشده است.", show_alert=True)
        return
    lines = ["📅 <b>برنامه‌های اخیر</b>", ""]
    for plan in plans:
        lines.append(
            f"• {plan.student.full_name} — {jalali_short(plan.week_start)} "
            f"({plan.status.value})"
        )
    await _edit(cq, "\n".join(lines), kb.admin_back("advisor", callback_data.ref))
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "ask_suspend"))
async def admin_ask_suspend(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    advisor = await AdminService(session).s.get(User, callback_data.ref)
    if advisor is None:
        await cq.answer("پیدا نشد.", show_alert=True)
        return
    what = (
        f"فعال‌سازی حساب «{advisor.full_name}»"
        if not advisor.is_active
        else f"غیرفعال‌سازی «{advisor.full_name}» — تا زمان فعال‌سازی مجدد "
        "نمی‌تواند برنامه بسازد یا ارسال کند."
    )
    await _edit(
        cq, T.ADMIN_CONFIRM.format(what=what),
        kb.admin_confirm("do_suspend", advisor.id),
    )
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
        await cq.answer("حساب مدیر را نمی‌توان غیرفعال کرد.", show_alert=True)
        return
    target.is_active = not target.is_active
    await manager.audit.log(
        "advisor.activated" if target.is_active else "advisor.suspended",
        actor_id=user.id if user else None, student_id=None, detail=target.full_name,
    )
    await cq.answer(
        (T.ADMIN_ACTIVATED if target.is_active else T.ADMIN_SUSPENDED).format(
            name=target.full_name
        ),
        show_alert=True,
    )
    await admin_advisor_card(cq, AdminCB(action="advisor", ref=target.id), session, user)


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
        await _edit(cq, "👨‍🎓 <b>دانش‌آموزان</b>\n\nهنوز دانش‌آموزی ثبت نشده است.",
                    kb.admin_back())
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
    advisors = "، ".join(a.full_name for a in data["advisors"]) or "—"
    status = (
        T.STUDENT_STATUS_CONNECTED if student.telegram_id else T.STUDENT_STATUS_PENDING
    )
    if not student.is_active:
        status += " · 🔒 غیرفعال"
    await _edit(
        cq,
        T.ADMIN_STUDENT_CARD.format(
            name=student.full_name,
            grade_line=f"📚 {student.grade}\n" if student.grade else "",
            status=status,
            advisor=advisors,
            plans=fa(data["plans"]),
            created=student.created_at.strftime("%Y-%m-%d") if student.created_at else "—",
        ),
        kb.admin_student_card(student),
    )
    await cq.answer()


@router.callback_query(AdminCB.filter(F.action == "ask_suspend_student"))
async def admin_ask_suspend_student(
    cq: CallbackQuery, callback_data: AdminCB, session: AsyncSession,
    user: User | None = None,
) -> None:
    _guard(cq, user)
    student = await AdminService(session).s.get(User, callback_data.ref)
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
            bot="🟢 Online",
            db=f"{DOT[health['db']]} " + ("Connected" if health["db"] else "Down"),
            redis="🟢 Connected" if health["redis"] else "⚪️ Optional (memory)",
            renderer=f"🟢 {health['renderer']}",
            chromium="🟢 Available" if health["chromium"] else "⚪️ Fallback (Pillow)",
            raqm="🟢 Available" if health["raqm"] else "🔴 Missing",
            storage="🟢 Available" if health["storage"] else "🔴 Missing",
            health="🟢 OK" if health["db"] and health["storage"] else "🔴 Degraded",
            db_latency=f"{health['db_latency_ms']:.1f} ms",
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
            status="🟢 Online",
            mode="Long Polling (single instance)",
            uptime=uptime(),
            version=__version__,
            template=renderer.layout.version,
            renderer=renderer.signature,
            fallback=fallback.signature if fallback else "—",
            started=f"{uptime()} پیش",
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
            status="🟢 Connected",
            users=fa(stats["users"]),
            advisors=fa(stats["advisors"]),
            students=fa(stats["students"]),
            plans=fa(stats["plans"]),
            drafts=fa(stats["drafts"]),
            files=fa(stats["files"]),
            latency=f"{stats['latency_ms']:.1f} ms",
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
            mounted="✅ در دسترس" if report.mounted else "🔴 موجود نیست",
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
            what=f"پاک‌سازی {fa(len(report.orphans))} فایل بدون رکورد در دیتابیس"
        ),
        kb.admin_confirm("do_cleanup", 0),
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
    await cq.answer(T.ADMIN_CLEANUP_DONE.format(plans=fa(0), files=fa(removed)),
                    show_alert=True)
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
        )
        + f"\n\n🔗 دعوت‌های صادرشده: {fa(stats['invites_issued'])}"
        + f"\n🛡 دعوت‌های مسدودشده: {fa(stats['invites_blocked'])}",
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
        await _edit(cq, "📋 <b>Audit Logs</b>\n\nرویدادی ثبت نشده است.", kb.admin_back())
        await cq.answer()
        return
    lines = [T.ADMIN_AUDIT.format(page=fa(callback_data.page + 1)), ""]
    for entry in rows:
        lines.append(
            f"<code>{entry.at:%m-%d %H:%M}</code> · <b>{entry.action}</b>\n"
            f"   actor={entry.actor_id or '—'} plan={entry.plan_id or '—'} "
            f"student={entry.student_id or '—'}"
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
            dpi=settings.pdf_dpi,
            concurrency=fa(settings.render_concurrency),
            retention=(f"{fa(settings.retention_days)} روز"
                       if settings.retention_days else "نامحدود"),
            admins=fa(len(settings.admin_ids)),
            storage=settings.storage_root,
        ),
        kb.admin_back("settings"),
    )
    await cq.answer()
