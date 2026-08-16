"""Inline keyboards — minimal, two-column where it helps, always with a way back."""
from __future__ import annotations

from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..db.models import User, WeeklyPlanDB
from ..domain.models import SLOTS_PER_DAY, WEEKDAY_KEYS, WeeklyPlan
from ..domain.persian import jalali_day_month, jalali_short, to_fa_digits, week_label
from . import texts as T
from .texts import AssignCB, DayCB, FileCB, ListCB, Nav, PlanCB, SlotCB, StudentCB, WeekCB


def advisor_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ برنامه جدید", callback_data=Nav(to="new"))
    kb.button(text="📂 برنامه‌های قبلی", callback_data=Nav(to="history"))
    kb.button(text="📝 پیش‌نویس‌ها", callback_data=Nav(to="drafts"))
    kb.button(text="👨‍🎓 دانش‌آموزان", callback_data=Nav(to="students"))
    kb.adjust(2, 2)
    return kb.as_markup()


def student_menu(has_plan: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if has_plan:
        kb.button(text="📅 برنامه این هفته", callback_data=Nav(to="my_last"))
        kb.button(text="📆 برنامه‌های قبلی", callback_data=Nav(to="my_history"))
        kb.adjust(1, 1)
    else:
        kb.button(text="🔄 بررسی مجدد", callback_data=Nav(to="my_last"))
    return kb.as_markup()


def students_list(
    students: list[User], page: int, total: int, page_size: int, *, mode: str = "pick"
) -> InlineKeyboardMarkup:
    """mode='pick' → choose a student for a new plan; mode='card' → manage roster."""
    kb = InlineKeyboardBuilder()
    action = "pick" if mode == "pick" else "card"
    for s in students:
        dot = "🟢" if s.telegram_id else "🟡"
        label = f"{dot} {s.full_name}"
        if s.grade:
            label += f" · {s.grade}"
        kb.button(
            text=label[:34],
            callback_data=StudentCB(action=action, student_id=s.id, mode=mode),
        )
    kb.adjust(*([1] * len(students)))

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀️ قبلی",
                callback_data=StudentCB(action="page", page=page - 1, mode=mode).pack(),
            )
        )
    if (page + 1) * page_size < total:
        nav.append(
            InlineKeyboardButton(
                text="بعدی ▶️",
                callback_data=StudentCB(action="page", page=page + 1, mode=mode).pack(),
            )
        )
    if nav:
        kb.row(*nav)
    kb.row(
        InlineKeyboardButton(
            text="➕ افزودن دانش‌آموز",
            callback_data=StudentCB(action="add", mode=mode).pack(),
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="🔎 جستجو", callback_data=StudentCB(action="search", mode=mode).pack()
        ),
        InlineKeyboardButton(text="⬅️ بازگشت", callback_data=Nav(to="menu").pack()),
    )
    return kb.as_markup()


def no_students(mode: str = "card") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text="➕ افزودن دانش‌آموز", callback_data=StudentCB(action="add", mode=mode)
    )
    kb.button(text="⬅️ بازگشت", callback_data=Nav(to="menu"))
    kb.adjust(1, 1)
    return kb.as_markup()


def student_card(student: User) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text="➕ برنامه جدید برای او",
        callback_data=StudentCB(action="pick", student_id=student.id, mode="card"),
    )
    kb.adjust(1)
    if not student.telegram_id:
        kb.row(
            InlineKeyboardButton(
                text="🔗 لینک دعوت",
                callback_data=StudentCB(action="invite", student_id=student.id).pack(),
            )
        )
    kb.row(
        InlineKeyboardButton(
            text="📂 برنامه‌های او",
            callback_data=ListCB(kind="student", ref=student.id).pack(),
        ),
        InlineKeyboardButton(
            text="🗑 حذف",
            callback_data=StudentCB(action="ask_del", student_id=student.id).pack(),
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="⬅️ فهرست دانش‌آموزان", callback_data=Nav(to="students").pack()
        )
    )
    return kb.as_markup()


def confirm_remove_student(student_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text="🗑 بله، حذف شود",
        callback_data=StudentCB(action="del", student_id=student_id),
    )
    kb.button(
        text="❌ انصراف",
        callback_data=StudentCB(action="card", student_id=student_id, mode="card"),
    )
    kb.adjust(1, 1)
    return kb.as_markup()


def student_created(student: User) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text="➕ ساخت برنامه برای او",
        callback_data=StudentCB(action="pick", student_id=student.id, mode="card"),
    )
    kb.button(text="➕ دانش‌آموز بعدی", callback_data=StudentCB(action="add", mode="card"))
    kb.button(text="⬅️ فهرست دانش‌آموزان", callback_data=Nav(to="students"))
    kb.adjust(1, 1, 1)
    return kb.as_markup()


def week_choices(student_id: int, this_saturday: date) -> InlineKeyboardMarkup:
    from datetime import timedelta

    kb = InlineKeyboardBuilder()
    kb.button(
        text=f"این هفته ({jalali_day_month(this_saturday)})",
        callback_data=WeekCB(action="pick", student_id=student_id, offset=0),
    )
    nxt = this_saturday + timedelta(days=7)
    kb.button(
        text=f"هفته بعد ({jalali_day_month(nxt)})",
        callback_data=WeekCB(action="pick", student_id=student_id, offset=1),
    )
    kb.button(
        text="🗓 تاریخ دلخواه",
        callback_data=WeekCB(action="custom", student_id=student_id),
    )
    kb.button(text="⬅️ بازگشت", callback_data=Nav(to="new"))
    kb.adjust(1, 1, 1, 1)
    return kb.as_markup()


def days_overview(plan_id: int, domain: WeeklyPlan) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for day in domain.days:
        filled = day.filled_count
        mark = f"{to_fa_digits(str(filled))}/{to_fa_digits(str(SLOTS_PER_DAY))}" if filled else "—"
        kb.button(
            text=f"{day.fa_name}  ·  {mark}",
            callback_data=DayCB(action="open", plan_id=plan_id, day=day.weekday),
        )
    kb.adjust(2, 2, 2, 1)
    kb.row(
        InlineKeyboardButton(
            text="📝 تکالیف", callback_data=AssignCB(action="open", plan_id=plan_id).pack()
        ),
        InlineKeyboardButton(
            text="📋 کپی از هفته قبل",
            callback_data=PlanCB(action="copyweek", plan_id=plan_id).pack(),
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="👀 پیش‌نمایش", callback_data=PlanCB(action="preview", plan_id=plan_id).pack()
        ),
        InlineKeyboardButton(
            text="✅ تولید برنامه", callback_data=PlanCB(action="confirm", plan_id=plan_id).pack()
        ),
    )
    kb.row(InlineKeyboardButton(text="⬅️ منو", callback_data=Nav(to="menu").pack()))
    return kb.as_markup()


def day_editor(plan_id: int, domain: WeeklyPlan, weekday: str) -> InlineKeyboardMarkup:
    day = domain.day(weekday)
    kb = InlineKeyboardBuilder()
    digits = "۱۲۳۴۵۶۷۸"
    for i in range(SLOTS_PER_DAY):
        activity = day.slot(i)
        label = activity.summary() if activity else T.SLOT_EMPTY
        if len(label) > 28:
            label = label[:27] + "…"
        kb.button(
            text=f"{digits[i]} · {label}",
            callback_data=SlotCB(action="edit", plan_id=plan_id, day=weekday, slot=i),
        )
    kb.adjust(*([1] * SLOTS_PER_DAY))
    kb.row(
        InlineKeyboardButton(
            text="🧹 پاک‌کردن روز",
            callback_data=DayCB(action="clear", plan_id=plan_id, day=weekday).pack(),
        ),
        InlineKeyboardButton(
            text="📋 کپی به روز دیگر",
            callback_data=DayCB(action="copy", plan_id=plan_id, day=weekday).pack(),
        ),
    )
    nxt = T.next_weekday(weekday)
    row = [
        InlineKeyboardButton(
            text="✅ تکمیل این روز",
            callback_data=PlanCB(action="days", plan_id=plan_id).pack(),
        )
    ]
    if nxt:
        row.append(
            InlineKeyboardButton(
                text=f"➡️ {T.day_fa(nxt)}",
                callback_data=DayCB(action="open", plan_id=plan_id, day=nxt).pack(),
            )
        )
    kb.row(*row)
    return kb.as_markup()


def copy_day_targets(plan_id: int, src: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for key in WEEKDAY_KEYS:
        if key == src:
            continue
        kb.button(
            text=T.day_fa(key),
            callback_data=DayCB(action="copyto", plan_id=plan_id, day=src, arg=key),
        )
    kb.adjust(3, 3)
    kb.row(
        InlineKeyboardButton(
            text="⬅️ بازگشت",
            callback_data=DayCB(action="open", plan_id=plan_id, day=src).pack(),
        )
    )
    return kb.as_markup()


def slot_editor(plan_id: int, weekday: str, slot: int, has_value: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if has_value:
        kb.button(
            text="🗑 خالی کردن این خانه",
            callback_data=SlotCB(action="clear", plan_id=plan_id, day=weekday, slot=slot),
        )
    kb.button(
        text="⬅️ بازگشت به روز",
        callback_data=DayCB(action="open", plan_id=plan_id, day=weekday),
    )
    kb.adjust(1, 1)
    return kb.as_markup()


def assignments_editor(plan_id: int, has_items: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if has_items:
        kb.button(text="🗑 پاک‌کردن همه", callback_data=AssignCB(action="clear", plan_id=plan_id))
    kb.button(text="⬅️ بازگشت", callback_data=PlanCB(action="days", plan_id=plan_id))
    kb.adjust(1, 1)
    return kb.as_markup()


def preview_actions(plan_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ تأیید و تولید", callback_data=PlanCB(action="confirm", plan_id=plan_id))
    kb.button(text="✏️ ویرایش", callback_data=PlanCB(action="days", plan_id=plan_id))
    kb.adjust(1, 1)
    return kb.as_markup()


def confirm_actions(plan_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ تولید برنامه", callback_data=PlanCB(action="generate", plan_id=plan_id))
    kb.button(text="✏️ ویرایش", callback_data=PlanCB(action="days", plan_id=plan_id))
    kb.adjust(1, 1)
    return kb.as_markup()


def generated_actions(plan_id: int, can_send: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📄 دریافت PDF", callback_data=PlanCB(action="pdf", plan_id=plan_id))
    kb.button(text="🖼 دریافت تصویر", callback_data=PlanCB(action="png", plan_id=plan_id))
    kb.adjust(2)
    if can_send:
        kb.row(
            InlineKeyboardButton(
                text="📤 ارسال برای دانش‌آموز",
                callback_data=PlanCB(action="ask_send", plan_id=plan_id).pack(),
            )
        )
    kb.row(
        InlineKeyboardButton(
            text="✏️ ویرایش", callback_data=PlanCB(action="days", plan_id=plan_id).pack()
        ),
        InlineKeyboardButton(
            text="🔄 تولید مجدد",
            callback_data=PlanCB(action="regenerate", plan_id=plan_id).pack(),
        ),
    )
    kb.row(InlineKeyboardButton(text="⬅️ منو", callback_data=Nav(to="menu").pack()))
    return kb.as_markup()


def send_confirm(plan_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ بله، ارسال شود", callback_data=PlanCB(action="send", plan_id=plan_id))
    kb.button(text="❌ انصراف", callback_data=PlanCB(action="open", plan_id=plan_id))
    kb.adjust(1, 1)
    return kb.as_markup()


def plan_list(
    plans: list[WeeklyPlanDB],
    page: int,
    total: int,
    page_size: int,
    *,
    kind: str = "history",
    ref: int = 0,
    student_view: bool = False,
) -> InlineKeyboardMarkup:
    """`kind` keeps pagination on the same list (history/drafts/mine/student)."""
    kb = InlineKeyboardBuilder()
    for plan in plans:
        label = week_label(plan.week_start, plan.week_end)
        who = "" if student_view or kind == "student" else f" · {plan.student.full_name}"
        mark = "📝" if plan.status.value == "draft" else "📅"
        kb.button(
            text=f"{mark} {label}{who}"[:34],
            callback_data=PlanCB(action="open", plan_id=plan.id),
        )
    kb.adjust(*([1] * len(plans)))
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀️ قبلی",
                callback_data=ListCB(kind=kind, page=page - 1, ref=ref).pack(),
            )
        )
    if (page + 1) * page_size < total:
        nav.append(
            InlineKeyboardButton(
                text="بعدی ▶️",
                callback_data=ListCB(kind=kind, page=page + 1, ref=ref).pack(),
            )
        )
    if nav:
        kb.row(*nav)
    back = (
        StudentCB(action="card", student_id=ref, mode="card").pack()
        if kind == "student" and ref
        else Nav(to="menu").pack()
    )
    kb.row(InlineKeyboardButton(text="⬅️ بازگشت", callback_data=back))
    return kb.as_markup()


def versions_list(files, plan_id: int) -> InlineKeyboardMarkup:
    """Previously generated artefacts of a plan (newest first)."""
    kb = InlineKeyboardBuilder()
    for record in files:
        version = to_fa_digits(str(record.version))
        kb.row(
            InlineKeyboardButton(
                text=f"🖼 نسخه {version}",
                callback_data=FileCB(action="get", file_id=record.id, kind="png").pack(),
            ),
            InlineKeyboardButton(
                text=f"📄 نسخه {version}",
                callback_data=FileCB(action="get", file_id=record.id, kind="pdf").pack(),
            ),
        )
    kb.row(
        InlineKeyboardButton(
            text="⬅️ بازگشت", callback_data=PlanCB(action="open", plan_id=plan_id).pack()
        )
    )
    return kb.as_markup()


def plan_card(plan: WeeklyPlanDB, *, can_edit: bool, can_send: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🖼 تصویر", callback_data=PlanCB(action="png", plan_id=plan.id))
    kb.button(text="📄 PDF", callback_data=PlanCB(action="pdf", plan_id=plan.id))
    kb.adjust(2)
    if can_edit:
        kb.row(
            InlineKeyboardButton(
                text="✏️ ویرایش", callback_data=PlanCB(action="days", plan_id=plan.id).pack()
            ),
            InlineKeyboardButton(
                text="🗑 حذف", callback_data=PlanCB(action="ask_delete", plan_id=plan.id).pack()
            ),
        )
    if can_send:
        kb.row(
            InlineKeyboardButton(
                text="📤 ارسال برای دانش‌آموز",
                callback_data=PlanCB(action="ask_send", plan_id=plan.id).pack(),
            )
        )
    if can_edit and len(plan.files) > 1:
        kb.row(
            InlineKeyboardButton(
                text="🗂 نسخه‌های قبلی",
                callback_data=PlanCB(action="versions", plan_id=plan.id).pack(),
            )
        )
    kb.row(InlineKeyboardButton(text="⬅️ بازگشت", callback_data=Nav(to="menu").pack()))
    return kb.as_markup()


def confirm_delete(plan_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 بله، حذف شود", callback_data=PlanCB(action="delete", plan_id=plan_id))
    kb.button(text="❌ انصراف", callback_data=PlanCB(action="open", plan_id=plan_id))
    kb.adjust(1, 1)
    return kb.as_markup()


def back_only(to: str = "menu") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ بازگشت", callback_data=Nav(to=to))
    return kb.as_markup()


def plan_header(plan: WeeklyPlanDB) -> str:
    return (
        f"👨‍🎓 {plan.student.full_name}\n"
        f"📅 {week_label(plan.week_start, plan.week_end)}\n"
        f"🗓 {jalali_short(plan.week_start)} تا {jalali_short(plan.week_end)}"
    )
