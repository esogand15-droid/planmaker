"""Inline keyboards — minimal, two-column where it helps, always with a way back."""
from __future__ import annotations

from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..db.models import User, WeeklyPlanDB
from ..domain.models import SLOTS_PER_DAY, WEEKDAY_KEYS, WeeklyPlan
from ..domain.persian import jalali_day_month, jalali_short, to_fa_digits, week_label
from . import texts as T
from .texts import (
    AdminCB,
    AssignCB,
    DayCB,
    FileCB,
    ListCB,
    Nav,
    PlanCB,
    SlotCB,
    StudentCB,
    WeekCB,
)


def advisor_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ برنامه جدید", callback_data=Nav(to="new"))
    kb.button(text="📂 برنامه‌های قبلی", callback_data=Nav(to="history"))
    kb.button(text="📝 پیش‌نویس‌ها", callback_data=Nav(to="drafts"))
    kb.button(text="👨‍🎓 دانش‌آموزان من", callback_data=Nav(to="students"))
    kb.button(text="👨‍🏫 پروفایل من", callback_data=Nav(to="profile"))
    kb.adjust(2, 2, 1)
    return kb.as_markup()


def student_menu(has_plan: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if has_plan:
        kb.button(text="📅 برنامه این هفته", callback_data=Nav(to="my_last"))
        kb.button(text="📆 برنامه‌های قبلی", callback_data=Nav(to="my_history"))
        kb.button(text="👨‍🎓 پروفایل من", callback_data=Nav(to="profile"))
        kb.adjust(1, 1, 1)
    else:
        kb.button(text="🔄 بررسی مجدد", callback_data=Nav(to="my_last"))
        kb.button(text="👨‍🎓 پروفایل من", callback_data=Nav(to="profile"))
        kb.adjust(1, 1)
    return kb.as_markup()


def profile_back(is_student: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ بازگشت", callback_data=Nav(to="menu" if not is_student else "student_menu"))
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


def student_card(student: User, invite_link: str | None = None) -> InlineKeyboardMarkup:
    sid = student.id
    kb = InlineKeyboardBuilder()
    if invite_link:
        kb.row(
            InlineKeyboardButton(
                text="📋 کپی لینک دعوت",
                callback_data=StudentCB(action="copylink", student_id=sid).pack()),
            InlineKeyboardButton(
                text="📤 ارسال لینک",
                callback_data=StudentCB(action="sharelink", student_id=sid).pack()),
        )
    kb.row(
        InlineKeyboardButton(
            text="📅 برنامه این هفته",
            callback_data=StudentCB(action="thisweek", student_id=sid).pack(),
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="➕ برنامه جدید",
            callback_data=StudentCB(action="pick", student_id=sid, mode="card").pack(),
        ),
        InlineKeyboardButton(
            text="📂 برنامه‌های قبلی",
            callback_data=ListCB(kind="student", ref=sid).pack(),
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="✏️ ویرایش اطلاعات",
            callback_data=StudentCB(action="edit", student_id=sid).pack(),
        ),
        InlineKeyboardButton(
            text="🔗 اتصال به تلگرام" if not student.telegram_id else "🔗 وضعیت اتصال",
            callback_data=StudentCB(action="connect", student_id=sid).pack(),
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="🗑 حذف دانش‌آموز",
            callback_data=StudentCB(action="ask_del", student_id=sid).pack(),
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="⬅️ فهرست دانش‌آموزان", callback_data=Nav(to="students").pack()
        )
    )
    return kb.as_markup()


def connect_menu(student: User) -> InlineKeyboardMarkup:
    sid = student.id
    kb = InlineKeyboardBuilder()
    if not student.telegram_id:
        if student.invite_token:
            kb.row(
                InlineKeyboardButton(
                    text="📋 کپی لینک",
                    callback_data=StudentCB(action="copylink", student_id=sid).pack()),
                InlineKeyboardButton(
                    text="📤 ارسال لینک",
                    callback_data=StudentCB(action="sharelink", student_id=sid).pack()),
            )
        kb.row(
            InlineKeyboardButton(
                text="🔄 ساخت لینک جدید" if student.invite_token else "🔗 ساخت لینک دعوت",
                callback_data=StudentCB(
                    action="ask_invite" if student.invite_token else "invite",
                    student_id=sid).pack(),
            )
        )
        kb.row(
            InlineKeyboardButton(
                text="🔢 ثبت آیدی عددی",
                callback_data=StudentCB(action="setid", student_id=sid).pack(),
            )
        )
        if student.invite_token:
            kb.row(
                InlineKeyboardButton(
                    text="🚫 ابطال لینک فعلی",
                    callback_data=StudentCB(action="revoke", student_id=sid).pack(),
                )
            )
    else:
        kb.row(
            InlineKeyboardButton(
                text="🔓 قطع اتصال",
                callback_data=StudentCB(action="unlink", student_id=sid).pack()),
        )
    kb.row(
        InlineKeyboardButton(
            text="⬅️ بازگشت",
            callback_data=StudentCB(action="card", student_id=sid, mode="card").pack(),
        )
    )
    return kb.as_markup()


def confirm_new_invite(student_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ ساخت لینک جدید",
              callback_data=StudentCB(action="invite", student_id=student_id))
    kb.button(text="❌ انصراف",
              callback_data=StudentCB(action="connect", student_id=student_id))
    kb.adjust(1, 1)
    return kb.as_markup()


def invite_ready(student: User) -> InlineKeyboardMarkup:
    sid = student.id
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="📋 کپی لینک",
            callback_data=StudentCB(action="copylink", student_id=sid).pack(),
        ),
        InlineKeyboardButton(
            text="🚫 ابطال لینک",
            callback_data=StudentCB(action="revoke", student_id=sid).pack(),
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="⬅️ بازگشت",
            callback_data=StudentCB(action="card", student_id=sid, mode="card").pack(),
        )
    )
    return kb.as_markup()


def confirm_remove_student(student_id: int, final: bool = False) -> InlineKeyboardMarkup:
    """Two-step confirmation: 'ادامه' first, then the irreversible button."""
    kb = InlineKeyboardBuilder()
    if final:
        kb.button(
            text="🗑 حذف قطعی",
            callback_data=StudentCB(action="del", student_id=student_id),
        )
    else:
        kb.button(
            text="➡️ ادامه",
            callback_data=StudentCB(action="del_confirm", student_id=student_id),
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
        text="🗓 بازه دلخواه (شروع تا پایان)",
        callback_data=WeekCB(action="custom", student_id=student_id),
    )
    kb.button(text="⬅️ بازگشت", callback_data=Nav(to="new"))
    kb.adjust(1, 1, 1, 1)
    return kb.as_markup()


def range_summary(student_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ تأیید بازه", callback_data=WeekCB(action="confirm", student_id=student_id))
    kb.button(text="✏️ اصلاح", callback_data=WeekCB(action="custom", student_id=student_id))
    kb.adjust(1, 1)
    return kb.as_markup()


def days_overview(plan_id: int, domain: WeeklyPlan) -> InlineKeyboardMarkup:
    """Only the real days of the range, in chronological order."""
    from ..domain.calendar import JalaliDate

    kb = InlineKeyboardBuilder()
    days = domain.plan_days
    for position, day in enumerate(days, start=1):
        filled = day.filled_count
        mark = f"{to_fa_digits(str(filled))}/{to_fa_digits(str(SLOTS_PER_DAY))}" if filled else "—"
        prefix = "" if domain.is_calendar_week else f"{to_fa_digits(str(position))}️⃣ "
        label = f"{prefix}{day.fa_name}"
        if not domain.is_calendar_week and day.date:
            label += f" — {JalaliDate.day_month(day.date)}"
        kb.button(
            text=f"{label}  ·  {mark}"[:34],
            callback_data=DayCB(action="open", plan_id=plan_id, day=day.weekday),
        )
    rows = [2] * (len(days) // 2) + ([1] if len(days) % 2 else [])
    kb.adjust(*(rows or [1]))
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


def copy_day_targets(plan_id: int, src: str,
                     domain: WeeklyPlan | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    keys = [d.weekday for d in domain.plan_days] if domain else list(WEEKDAY_KEYS)
    targets = [k for k in keys if k != src]
    for key in targets:
        kb.button(
            text=T.day_fa(key),
            callback_data=DayCB(action="copyto", plan_id=plan_id, day=src, arg=key),
        )
    kb.adjust(*([3] * (len(targets) // 3) + ([len(targets) % 3] if len(targets) % 3 else []) or [1]))
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


# ─────────────────────────────── admin panel ────────────────────────────────
def admin_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🙋 درخواست‌های دسترسی", callback_data=AdminCB(action="requests"))
    kb.button(text="👥 مدیریت مشاوران", callback_data=AdminCB(action="advisors"))
    kb.button(text="👨‍🎓 مدیریت دانش‌آموزان", callback_data=AdminCB(action="students"))
    kb.button(text="📋 مدیریت برنامه‌ها", callback_data=AdminCB(action="plans"))
    kb.button(text="📁 مدیریت فایل‌ها", callback_data=AdminCB(action="storage"))
    kb.button(text="🗄 پایگاه داده", callback_data=AdminCB(action="db"))
    kb.button(text="📊 آمار و گزارش‌ها", callback_data=AdminCB(action="stats"))
    kb.button(text="🧾 گزارش فعالیت‌ها", callback_data=AdminCB(action="audit"))
    kb.button(text="❤️ سلامت سیستم", callback_data=AdminCB(action="system"))
    kb.button(text="🤖 وضعیت ربات", callback_data=AdminCB(action="bot"))
    kb.button(text="⚙️ تنظیمات مدیریت", callback_data=AdminCB(action="settings"))
    kb.button(text="⬅️ پنل مشاور", callback_data=Nav(to="menu"))
    kb.adjust(1, 1, 2, 2, 2, 2, 1)
    return kb.as_markup()


def admin_back(action: str = "home", ref: int = 0) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 به‌روزرسانی", callback_data=AdminCB(action=action, ref=ref))
    kb.button(text="⬅️ پنل مدیریت", callback_data=AdminCB(action="home"))
    kb.adjust(2)
    return kb.as_markup()


def _admin_pager(kb: InlineKeyboardBuilder, action: str, page: int, total: int,
                 size: int, ref: int = 0) -> None:
    pages = max(1, -(-total // size))
    row: list[InlineKeyboardButton] = []
    if page > 0:
        row.append(InlineKeyboardButton(
            text="⬅️ قبلی",
            callback_data=AdminCB(action=action, page=page - 1, ref=ref).pack()))
    row.append(InlineKeyboardButton(
        text=f"{to_fa_digits(str(page + 1))} / {to_fa_digits(str(pages))}",
        callback_data="noop"))
    if (page + 1) * size < total:
        row.append(InlineKeyboardButton(
            text="بعدی ➡️",
            callback_data=AdminCB(action=action, page=page + 1, ref=ref).pack()))
    kb.row(*row)


def admin_requests(requests, page: int, total: int, size: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for request in requests:
        kb.row(InlineKeyboardButton(
            text=f"🙋 {request.full_name}"[:34],
            callback_data=AdminCB(action="request", ref=request.id).pack()))
    _admin_pager(kb, "requests", page, total, size)
    kb.row(InlineKeyboardButton(text="⬅️ پنل مدیریت",
                                callback_data=AdminCB(action="home").pack()))
    return kb.as_markup()


def admin_request_card(request_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text="👨‍🏫 تأیید به‌عنوان مشاور",
        callback_data=AdminCB(action="grant", ref=request_id, arg="advisor").pack()))
    kb.row(InlineKeyboardButton(
        text="👨‍🎓 تأیید به‌عنوان دانش‌آموز",
        callback_data=AdminCB(action="grant_student", ref=request_id).pack()))
    kb.row(InlineKeyboardButton(
        text="🚫 رد درخواست",
        callback_data=AdminCB(action="reject", ref=request_id).pack()))
    kb.row(InlineKeyboardButton(text="⬅️ فهرست درخواست‌ها",
                                callback_data=AdminCB(action="requests").pack()))
    return kb.as_markup()


def admin_pick_advisor_for_request(candidates, request_id: int) -> InlineKeyboardMarkup:
    """Which advisor should the approved student belong to?"""
    kb = InlineKeyboardBuilder()
    for advisor in candidates:
        kb.row(InlineKeyboardButton(
            text=f"👨‍🏫 {advisor.full_name}"[:34],
            callback_data=AdminCB(action="grant", ref=request_id,
                                  arg="student", page=advisor.id).pack()))
    kb.row(InlineKeyboardButton(text="❌ انصراف",
                                callback_data=AdminCB(action="request", ref=request_id).pack()))
    return kb.as_markup()


def admin_advisors(advisors, page: int, total: int, size: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for advisor, students, _plans in advisors:
        dot = "🟢" if advisor.is_active else "🔒"
        kb.row(InlineKeyboardButton(
            text=f"{dot} {advisor.full_name} · {to_fa_digits(str(students))} دانش‌آموز"[:34],
            callback_data=AdminCB(action="advisor", ref=advisor.id).pack()))
    _admin_pager(kb, "advisors", page, total, size)
    kb.row(InlineKeyboardButton(text="➕ افزودن مشاور",
                                callback_data=AdminCB(action="add_advisor").pack()))
    kb.row(
        InlineKeyboardButton(text="🔎 جستجوی مشاور",
                             callback_data=AdminCB(action="search_advisor").pack()),
        InlineKeyboardButton(text="⬅️ پنل مدیریت",
                             callback_data=AdminCB(action="home").pack()),
    )
    return kb.as_markup()


def admin_no_advisors() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ افزودن مشاور", callback_data=AdminCB(action="add_advisor"))
    kb.button(text="⬅️ پنل مدیریت", callback_data=AdminCB(action="home"))
    kb.adjust(1, 1)
    return kb.as_markup()


def admin_advisor_card(advisor) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="👨‍🎓 دانش‌آموزان",
            callback_data=AdminCB(action="advisor_students", ref=advisor.id).pack()),
        InlineKeyboardButton(
            text="📅 برنامه‌ها",
            callback_data=AdminCB(action="advisor_plans", ref=advisor.id).pack()),
    )
    kb.row(
        InlineKeyboardButton(
            text="✏️ ویرایش",
            callback_data=AdminCB(action="edit_advisor", ref=advisor.id).pack()),
        InlineKeyboardButton(
            text="🔓 فعال‌سازی" if not advisor.is_active else "🔒 تعلیق",
            callback_data=AdminCB(action="ask_suspend", ref=advisor.id).pack()),
    )
    kb.row(InlineKeyboardButton(
        text="🗑 حذف مشاور",
        callback_data=AdminCB(action="ask_del_advisor", ref=advisor.id).pack()))
    kb.row(InlineKeyboardButton(text="⬅️ فهرست مشاوران",
                                callback_data=AdminCB(action="advisors").pack()))
    return kb.as_markup()


def admin_delete_advisor(advisor_id: int, has_students: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if has_students:
        kb.row(InlineKeyboardButton(
            text="👤 انتقال دانش‌آموزان به مشاور دیگر",
            callback_data=AdminCB(action="del_advisor_pick", ref=advisor_id).pack()))
        kb.row(InlineKeyboardButton(
            text="📦 بدون مشاور (حذف برنامه‌های او)",
            callback_data=AdminCB(action="del_advisor", ref=advisor_id,
                                  arg="detach").pack()))
    else:
        kb.row(InlineKeyboardButton(
            text="🗑 حذف قطعی",
            callback_data=AdminCB(action="del_advisor", ref=advisor_id,
                                  arg="detach").pack()))
    kb.row(InlineKeyboardButton(
        text="❌ انصراف", callback_data=AdminCB(action="advisor", ref=advisor_id).pack()))
    return kb.as_markup()


def admin_pick_advisor(candidates, source_id: int, action: str) -> InlineKeyboardMarkup:
    """Target picker for transfers (student → advisor, or advisor deletion)."""
    kb = InlineKeyboardBuilder()
    for advisor in candidates:
        kb.row(InlineKeyboardButton(
            text=f"👨‍🏫 {advisor.full_name}"[:34],
            callback_data=AdminCB(action=action, ref=source_id,
                                  arg=str(advisor.id)).pack()))
    kb.row(InlineKeyboardButton(text="❌ انصراف",
                                callback_data=AdminCB(action="home").pack()))
    return kb.as_markup()


def admin_students(students, page: int, total: int, size: int, ref: int = 0
                   ) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for student in students:
        dot = "🟢" if student.telegram_id else "🟡"
        lock = "" if student.is_active else " 🔒"
        kb.row(InlineKeyboardButton(
            text=f"{dot} {student.full_name}{lock}"[:34],
            callback_data=AdminCB(action="student", ref=student.id).pack()))
    action = "advisor_students" if ref else "students"
    _admin_pager(kb, action, page, total, size, ref)
    back = (
        AdminCB(action="advisor", ref=ref).pack() if ref
        else AdminCB(action="home").pack()
    )
    row = [InlineKeyboardButton(text="⬅️ بازگشت", callback_data=back)]
    if not ref:
        row.insert(0, InlineKeyboardButton(
            text="🔎 جستجوی دانش‌آموز",
            callback_data=AdminCB(action="search_student").pack()))
    kb.row(*row)
    return kb.as_markup()


def admin_student_card(student) -> InlineKeyboardMarkup:
    sid = student.id
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✏️ ویرایش",
                             callback_data=AdminCB(action="edit_student", ref=sid).pack()),
        InlineKeyboardButton(text="🔄 تغییر مشاور",
                             callback_data=AdminCB(action="transfer", ref=sid).pack()),
    )
    kb.row(
        InlineKeyboardButton(text="🔗 مدیریت اتصال",
                             callback_data=AdminCB(action="connection", ref=sid).pack()),
        InlineKeyboardButton(text="📅 برنامه‌ها",
                             callback_data=AdminCB(action="student_plans", ref=sid).pack()),
    )
    kb.row(
        InlineKeyboardButton(
            text="🔓 فعال‌سازی" if not student.is_active else "🔒 غیرفعال‌سازی",
            callback_data=AdminCB(action="ask_suspend_student", ref=sid).pack()),
        InlineKeyboardButton(text="🗑 حذف",
                             callback_data=AdminCB(action="ask_del_student", ref=sid).pack()),
    )
    kb.row(InlineKeyboardButton(text="⬅️ فهرست دانش‌آموزان",
                                callback_data=AdminCB(action="students").pack()))
    return kb.as_markup()


def admin_connection(student) -> InlineKeyboardMarkup:
    sid = student.id
    kb = InlineKeyboardBuilder()
    if not student.telegram_id:
        kb.row(InlineKeyboardButton(
            text="🔗 ساخت لینک جدید",
            callback_data=AdminCB(action="reissue", ref=sid).pack()))
        if student.invite_token:
            kb.row(InlineKeyboardButton(
                text="♻️ لغو لینک قبلی",
                callback_data=AdminCB(action="revoke_invite", ref=sid).pack()))
    else:
        kb.row(InlineKeyboardButton(
            text="🔓 قطع اتصال",
            callback_data=AdminCB(action="ask_unlink", ref=sid).pack()))
    kb.row(InlineKeyboardButton(text="⬅️ بازگشت",
                                callback_data=AdminCB(action="student", ref=sid).pack()))
    return kb.as_markup()


def admin_plans(plans, page: int, total: int, size: int, ref: int = 0,
                action: str = "plans") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for plan in plans:
        label = f"📅 {week_label(plan.week_start, plan.week_end)} · {plan.student.full_name}"
        kb.row(InlineKeyboardButton(
            text=label[:34],
            callback_data=AdminCB(action="plan", ref=plan.id).pack()))
    _admin_pager(kb, action, page, total, size, ref)
    kb.row(InlineKeyboardButton(text="⬅️ پنل مدیریت",
                                callback_data=AdminCB(action="home").pack()))
    return kb.as_markup()


def admin_plan_card(plan) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if plan.image_path or plan.image_file_id:
        kb.row(
            InlineKeyboardButton(text="🖼 تصویر",
                                 callback_data=PlanCB(action="png", plan_id=plan.id).pack()),
            InlineKeyboardButton(text="📄 پی‌دی‌اف",
                                 callback_data=PlanCB(action="pdf", plan_id=plan.id).pack()),
        )
    kb.row(InlineKeyboardButton(
        text="🗑 حذف برنامه",
        callback_data=AdminCB(action="ask_del_plan", ref=plan.id).pack()))
    kb.row(InlineKeyboardButton(text="⬅️ فهرست برنامه‌ها",
                                callback_data=AdminCB(action="plans").pack()))
    return kb.as_markup()


def admin_confirm(action: str, ref: int, arg: str = "",
                  label: str = "✅ تأیید") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=label, callback_data=AdminCB(action=action, ref=ref, arg=arg))
    kb.button(text="❌ انصراف", callback_data=AdminCB(action="home"))
    kb.adjust(2)
    return kb.as_markup()


def admin_storage(orphans: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 بررسی مجدد", callback_data=AdminCB(action="storage"))
    if orphans:
        kb.button(text="🧹 پاک‌سازی فایل‌های یتیم",
                  callback_data=AdminCB(action="ask_cleanup"))
    kb.button(text="⬅️ پنل مدیریت", callback_data=AdminCB(action="home"))
    kb.adjust(1, 1, 1)
    return kb.as_markup()


def admin_system() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 به‌روزرسانی", callback_data=AdminCB(action="system"))
    kb.button(text="📋 بررسی سلامت", callback_data=AdminCB(action="health"))
    kb.button(text="⬅️ پنل مدیریت", callback_data=AdminCB(action="home"))
    kb.adjust(2, 1)
    return kb.as_markup()


def admin_audit(page: int, total: int, size: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    _admin_pager(kb, "audit", page, total, size)
    kb.row(InlineKeyboardButton(text="⬅️ پنل مدیریت",
                                callback_data=AdminCB(action="home").pack()))
    return kb.as_markup()


def advisor_menu_with_admin() -> InlineKeyboardMarkup:
    """Advisor menu plus the admin entry point (ADMIN_IDS only)."""
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ برنامه جدید", callback_data=Nav(to="new"))
    kb.button(text="📂 برنامه‌های قبلی", callback_data=Nav(to="history"))
    kb.button(text="📝 پیش‌نویس‌ها", callback_data=Nav(to="drafts"))
    kb.button(text="👨‍🎓 دانش‌آموزان من", callback_data=Nav(to="students"))
    kb.button(text="👨‍🏫 پروفایل من", callback_data=Nav(to="profile"))
    kb.button(text="🛠 پنل مدیریت", callback_data=AdminCB(action="home"))
    kb.adjust(2, 2, 2)
    return kb.as_markup()
