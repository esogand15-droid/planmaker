"""Centralised UI strings and callback factories (no hard-coded text in handlers)."""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData

from ..domain.models import WEEKDAY_FA, WEEKDAY_KEYS

BRAND = "رتبه لند"
HEADER = "📋 برنامه هفتگی · رتبه لند"


class Nav(CallbackData, prefix="n"):
    """Simple navigation actions: menu, back, cancel, noop."""

    to: str


class StudentCB(CallbackData, prefix="st"):
    action: str          # pick | card | page | search | add | invite | ask_del | del
    student_id: int = 0
    page: int = 0
    mode: str = "pick"   # pick = choose for a plan, card = manage the student


class WeekCB(CallbackData, prefix="wk"):
    action: str          # this | next | custom | pick
    student_id: int = 0
    offset: int = 0


class PlanCB(CallbackData, prefix="p"):
    action: str          # open | days | preview | confirm | generate | send | ...
    plan_id: int
    page: int = 0


class DayCB(CallbackData, prefix="d"):
    action: str          # open | clear | copy | done | next
    plan_id: int
    day: str
    arg: str = ""


class SlotCB(CallbackData, prefix="s"):
    action: str          # edit | clear
    plan_id: int
    day: str
    slot: int


class ListCB(CallbackData, prefix="l"):
    """Paginated lists: history / drafts / a student's own plans."""

    kind: str            # history | drafts | mine | student
    page: int = 0
    ref: int = 0         # student id when kind == "student"


class FileCB(CallbackData, prefix="f"):
    action: str          # get
    file_id: int
    kind: str = "png"    # png | pdf


class AssignCB(CallbackData, prefix="a"):
    action: str          # open | add | clear | done
    plan_id: int
    index: int = 0


MAIN_MENU = (
    f"<b>{HEADER}</b>\n\n"
    "سامانه برنامه‌ریزی هفتگی مشاوران.\n"
    "برنامه را همین‌جا وارد کنید؛ تصویر و PDF نهایی خودکار ساخته می‌شود."
)

STUDENT_MENU = "👋 سلام {name}!\n\n📚 برنامه‌های شما"

CHOOSE_STUDENT = "👨‍🎓 <b>انتخاب دانش‌آموز</b>\n\nدانش‌آموز مورد نظر را انتخاب کنید."
NO_STUDENTS = (
    "👨‍🎓 <b>دانش‌آموزان</b>\n\n"
    "هنوز دانش‌آموزی ثبت نکرده‌اید.\n"
    "با دکمهٔ زیر، خودتان دانش‌آموز جدید اضافه کنید."
)
STUDENTS_TITLE = (
    "👨‍🎓 <b>دانش‌آموزان شما</b>\n\n"
    "تعداد: {count}\n"
    "🟢 متصل به ربات · 🟡 در انتظار اتصال"
)
ADD_STUDENT_PROMPT = (
    "➕ <b>دانش‌آموز جدید</b>\n\n"
    "نام و نام خانوادگی را بفرستید.\n"
    "اگر خواستید پایه/رشته را هم اضافه کنید، با <code>|</code> جدا کنید:\n\n"
    "<code>علی رضایی</code>\n"
    "<code>علی رضایی | دوازدهم تجربی</code>"
)
STUDENT_CREATED = (
    "✅ <b>{name}</b> اضافه شد.\n\n"
    "برای اینکه برنامه‌ها مستقیم به تلگرام او برود، لینک زیر را برایش بفرستید؛ "
    "با یک‌بار باز کردن، حسابش وصل می‌شود:\n\n"
    "{link}\n\n"
    "بدون این کار هم می‌توانید همین حالا برایش برنامه بسازید و فایل را دستی بفرستید."
)
STUDENT_CARD = (
    "👤 <b>{name}</b>\n"
    "{grade_line}"
    "وضعیت: {status}\n"
    "📅 برنامه‌ها: {plans}"
)
STUDENT_STATUS_CONNECTED = "🟢 متصل به ربات"
STUDENT_STATUS_PENDING = "🟡 در انتظار اتصال (لینک دعوت را بفرستید)"
INVITE_TEXT = (
    "🔗 <b>لینک دعوت {name}</b>\n\n"
    "{link}\n\n"
    "این لینک را برای دانش‌آموز بفرستید. با باز کردن آن، حسابش به شما وصل می‌شود "
    "و برنامه‌ها مستقیم برایش ارسال خواهد شد."
)
INVITE_ALREADY_CONNECTED = "این دانش‌آموز از قبل به ربات متصل است."
STUDENT_REMOVED = "🗑 دانش‌آموز از فهرست شما حذف شد. (برنامه‌های قبلی حذف نشدند)"
CONFIRM_REMOVE_STUDENT = (
    "حذف <b>{name}</b> از فهرست شما؟\n"
    "برنامه‌های ساخته‌شده باقی می‌مانند، فقط دیگر در فهرست شما دیده نمی‌شود."
)
STUDENT_WELCOME_LINKED = (
    "🎉 خوش آمدی <b>{name}</b>!\n\n"
    "حساب شما به مشاورتان وصل شد. از این پس برنامه هفتگی مستقیم همین‌جا برایتان می‌آید."
)
INVITE_INVALID = (
    "این لینک دعوت معتبر نیست یا قبلاً استفاده شده است.\n"
    "از مشاور خود یک لینک تازه بخواهید."
)
NOT_REGISTERED = (
    "👋 سلام!\n\n"
    "این ربات مخصوص دانش‌آموزان و مشاوران مؤسسه <b>رتبه لند</b> است.\n"
    "برای استفاده، از مشاور خود بخواهید لینک دعوت شما را بفرستد."
)
UNKNOWN_ACTION = "این دکمه دیگر معتبر نیست. لطفاً از /start دوباره شروع کنید."
SEARCH_PROMPT = "🔎 نام دانش‌آموز را بنویسید:"
CHOOSE_WEEK = (
    "📅 <b>انتخاب هفته</b>\n\n"
    "دانش‌آموز: {student}\n\n"
    "هفته پیش‌فرض از شنبه تا جمعه است."
)
CUSTOM_WEEK_PROMPT = (
    "تاریخ شنبهٔ هفته را به شمسی بنویسید:\n"
    "<code>1405/05/25</code>"
)
INVALID_DATE = "⚠️ تاریخ نامعتبر است. نمونه درست: <code>1405/05/25</code>"

SLOT_PROMPT = (
    "✏️ <b>فعالیت شماره {slot}</b> — {day}\n\n"
    "اطلاعات را در یک پیام بفرستید (هر بخش با <code>|</code> جدا شود):\n"
    "<code>درس | مبحث | کار | زمان</code>\n\n"
    "نمونه:\n<code>زیست | گوارش | ۴۰ تست | ۹۰ دقیقه</code>\n\n"
    "نوشتن همه بخش‌ها لازم نیست."
)
SLOT_SAVED = "✅ ذخیره شد."
ASSIGN_PROMPT = (
    "📝 <b>تکالیف</b>\n\n"
    "هر تکلیف را در یک خط بنویسید و یک‌جا بفرستید:\n"
    "<code>مرور فصل ۲ زیست\nحل ۵۰ تست ریاضی\nتحلیل آزمون</code>"
)
ASSIGN_SAVED = "✅ تکالیف ذخیره شد."

CONFIRM_TEMPLATE = (
    "📋 <b>خلاصه برنامه</b>\n\n"
    "👨‍🎓 دانش‌آموز: {student}\n"
    "📅 هفته: {week}\n"
    "📚 روزهای دارای برنامه: {filled_days} از ۷\n"
    "📝 تعداد فعالیت: {activities}\n"
    "📌 تکالیف: {assignments} مورد\n"
    "🧩 نسخه: {version}\n\n"
    "برنامه تولید شود؟"
)
GENERATING = "⏳ در حال تولید برنامه…"
GENERATED = (
    "✅ <b>برنامه هفتگی آماده شد</b>\n\n"
    "👨‍🎓 {student}\n📅 {week}\n🧩 نسخه {version}"
)
SEND_CONFIRM = (
    "آیا برنامه برای دانش‌آموز ارسال شود؟\n\n"
    "👨‍🎓 {student}\n📅 {week}"
)
SENT_OK = "✅ برنامه با موفقیت برای دانش‌آموز ارسال شد."
STUDENT_NO_TELEGRAM = (
    "⚠️ این دانش‌آموز هنوز در ربات ثبت‌نام نکرده است.\n"
    "پس از /start کردن ربات توسط دانش‌آموز، ارسال ممکن می‌شود."
)
STUDENT_NEW_PLAN = "📬 برنامه هفتگی جدید شما رسید!\n\n📅 {week}"
STUDENT_NO_PLAN = "هنوز برنامه‌ای برای شما ثبت نشده است."

NOT_READY = "⚠️ برنامه هنوز قابل تولید نیست.\n\n{problems}"
GENERIC_ERROR = (
    "❌ انجام این کار با مشکل مواجه شد.\n"
    "لطفاً دوباره تلاش کنید؛ اگر تکرار شد با پشتیبانی تماس بگیرید."
)
ACCESS_DENIED = "⛔️ دسترسی به این بخش برای شما مجاز نیست."
DRAFTS_EMPTY = "پیش‌نویسی وجود ندارد."
HISTORY_EMPTY = "برنامه‌ای ثبت نشده است."
DELETED = "🗑 برنامه حذف شد."
COPIED_WEEK = "📋 {count} فعالیت از هفته قبل کپی شد."
NO_PREVIOUS_WEEK = "برنامه‌ای برای هفته‌های قبل این دانش‌آموز پیدا نشد."
DISCARD_CONFIRM = "تغییرات این مرحله ذخیره نشود؟"

SLOT_EMPTY = "خالی"
DAY_TITLE = "📅 <b>{day}</b> — {date}\n\nروی هر ردیف بزنید تا ویرایش شود."


def day_fa(key: str) -> str:
    return WEEKDAY_FA[key]


def next_weekday(key: str) -> str | None:
    i = WEEKDAY_KEYS.index(key)
    return WEEKDAY_KEYS[i + 1] if i + 1 < len(WEEKDAY_KEYS) else None
