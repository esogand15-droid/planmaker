"""Centralised UI strings and callback factories (no hard-coded text in handlers)."""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData

from ..domain.models import WEEKDAY_FA, WEEKDAY_KEYS

HEADER = "📋 برنامه هفتگی · رتبه لند"


class Nav(CallbackData, prefix="n"):
    """Simple navigation actions: menu, back, cancel, noop."""

    to: str


class StudentCB(CallbackData, prefix="st"):
    # pick | card | page | search | add | edit | connect | invite | revoke
    # | setid | thisweek | ask_del | del
    action: str
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


class AdminCB(CallbackData, prefix="ad"):
    """Admin panel navigation. Every handler re-checks ADMIN_IDS server-side."""

    action: str          # home | advisors | students | system | bot | db | storage
                         # | stats | audit | settings | view | suspend | ask_* | do_*
    ref: int = 0
    page: int = 0
    arg: str = ""


ADMIN_MENU = (
    "╭────────────────────────────╮\n"
    "  🛠 <b>پنل مدیریت رتبه لند</b>\n"
    "╰────────────────────────────╯\n\n"
    "نسخه {version} · محیط: {env}\n"
    "👥 مشاوران: {advisors} · 👨‍🎓 دانش‌آموزان: {students} · 📋 برنامه‌ها: {plans}"
)
ADMIN_ONLY = "⛔️ این بخش فقط برای مدیر سیستم است."
ADMIN_ADVISORS = "👥 <b>مدیریت مشاوران</b> — {count} مشاور"
ADMIN_NO_ADVISORS = (
    "👥 <b>مدیریت مشاوران</b>\n\nهنوز مشاوری ثبت نشده است.\n"
    "افزودن مشاور با دستور مدیریتی زیر انجام می‌شود:\n"
    "<code>python -m tools.manage add-advisor \"نام\" --telegram-id &lt;ID&gt;</code>"
)
ADMIN_ADVISOR_CARD = (
    "👨‍🏫 <b>مشاور: {name}</b>\n\n"
    "وضعیت: {status}\n"
    "شناسه تلگرام: <code>{telegram}</code>\n\n"
    "👨‍🎓 دانش‌آموزان: {students}\n"
    "📅 برنامه‌ها: {plans}\n"
    "📝 پیش‌نویس‌ها: {drafts}\n"
    "📤 ارسال‌شده: {sent}\n"
    "🗓 برنامه‌های این هفته: {this_week}\n\n"
    "آخرین فعالیت: {last_seen}"
)
ADMIN_STUDENTS = "👨‍🎓 <b>مدیریت دانش‌آموزان</b> — {count} دانش‌آموز"
ADMIN_NO_STUDENTS = "👨‍🎓 <b>مدیریت دانش‌آموزان</b>\n\nهنوز دانش‌آموزی ثبت نشده است."
ADMIN_STUDENT_CARD = (
    "👨‍🎓 <b>دانش‌آموز: {name}</b>\n\n"
    "{grade_line}"
    "وضعیت حساب: {status}\n"
    "اتصال تلگرام: {connection}\n"
    "شناسه تلگرام: <code>{telegram}</code>\n"
    "مشاور: {advisor}\n\n"
    "📅 برنامه‌ها: {plans}\n"
    "📝 پیش‌نویس‌ها: {drafts}\n"
    "🗓 تاریخ ثبت: {created}\n"
    "آخرین فعالیت: {last_seen}"
)
ADMIN_SYSTEM = (
    "❤️ <b>سلامت سیستم</b>\n\n"
    "🤖 ربات: {bot}\n"
    "🗄 پایگاه داده: {db}\n"
    "⚡ حافظه موقت: {redis}\n"
    "🎨 موتور رندر: {renderer}\n"
    "🌐 مرورگر رندر: {chromium}\n"
    "🔤 پشتیبانی فارسی: {raqm}\n"
    "💾 فضای ذخیره‌سازی: {storage}\n\n"
    "⏱ زمان پاسخ پایگاه داده: {db_latency}\n"
    "🧵 تولیدهای در جریان: {inflight}\n"
    "⏳ مدت فعالیت: {uptime}"
)
ADMIN_BOT = (
    "🤖 <b>وضعیت ربات</b>\n\n"
    "وضعیت: {status}\n"
    "روش اتصال: {mode}\n"
    "مدت فعالیت: {uptime}\n"
    "نسخه: {version}\n"
    "نسخه قالب: {template}\n"
    "موتور رندر: {renderer}\n"
    "پشتیبان رندر: {fallback}"
)
ADMIN_DB = (
    "🗄 <b>مدیریت پایگاه داده</b>\n\n"
    "وضعیت اتصال: {status}\n\n"
    "👥 کاربران: {users}\n"
    "🧑‍🏫 مشاوران: {advisors}\n"
    "👨‍🎓 دانش‌آموزان: {students}\n"
    "📋 برنامه‌ها: {plans}\n"
    "📝 پیش‌نویس‌ها: {drafts}\n"
    "🗂 نسخه‌های ثبت‌شده: {files}\n"
    "🧩 فعالیت‌های ثبت‌شده: {activities}\n\n"
    "⏱ زمان پاسخ: {latency}"
)
ADMIN_STORAGE = (
    "📁 <b>مدیریت فایل‌ها</b>\n\n"
    "مسیر ذخیره‌سازی:\n<code>{path}</code>\n"
    "وضعیت فضا: {mounted}\n\n"
    "🖼 تصویر (PNG): {png}\n"
    "📄 پی‌دی‌اف (PDF): {pdf}\n"
    "📦 مجموع: {total} فایل · {size}\n"
    "🗑 فایل‌های بدون رکورد: {orphans}"
)
ADMIN_STATS = (
    "📊 <b>آمار و گزارش‌ها</b>\n\n"
    "👥 مشاوران: {advisors}\n"
    "👨‍🎓 دانش‌آموزان: {students}\n"
    "📋 برنامه‌ها: {plans}\n"
    "📝 پیش‌نویس‌ها: {drafts}\n"
    "📤 ارسال‌شده: {sent}\n"
    "🎨 تولیدشده: {generated}\n\n"
    "<b>بازه‌های زمانی</b>\n"
    "امروز: {today}\n"
    "این هفته: {week}\n"
    "این ماه: {month}\n"
    "از ابتدا: {all_time}\n\n"
    "🔗 دعوت‌های صادرشده: {invites}\n"
    "🛡 دعوت‌های مسدودشده: {blocked}"
)
ADMIN_AUDIT = "🧾 <b>گزارش فعالیت‌ها</b> — صفحه {page} از {pages}"
ADMIN_SETTINGS = (
    "⚙️ <b>تنظیمات مدیریت</b>\n"
    "<i>این مقادیر از متغیرهای محیطی خوانده می‌شوند</i>\n\n"
    "محیط اجرا: {env}\n"
    "منطقه زمانی: {tz}\n"
    "موتور تولید: {backend}\n"
    "مقیاس چاپ: {scale} · کیفیت: {dpi} نقطه بر اینچ\n"
    "تعداد تولید هم‌زمان: {concurrency}\n"
    "مدت نگهداری: {retention}\n"
    "تعداد مدیران: {admins}\n"
    "مسیر ذخیره‌سازی:\n<code>{storage}</code>"
)
ADMIN_PLANS = "📋 <b>مدیریت برنامه‌ها</b> — {count} برنامه"
ADMIN_PLAN_CARD = (
    "📋 <b>برنامه هفتگی</b>\n\n"
    "👨‍🎓 دانش‌آموز: {student}\n"
    "👨‍🏫 مشاور: {advisor}\n"
    "📅 هفته: {week}\n"
    "وضعیت: {status}\n"
    "نسخه: {version}\n"
    "🧩 فعالیت‌ها: {activities} · 📝 تکالیف: {assignments}"
)
ADMIN_CONNECTION = (
    "🔗 <b>مدیریت اتصال</b>\n\n"
    "دانش‌آموز: <b>{name}</b>\n"
    "وضعیت: {status}\n"
    "شناسه تلگرام: <code>{telegram}</code>\n"
    "لینک دعوت فعال: {invite}"
)
ADMIN_TRANSFER_PICK = (
    "🔄 <b>تغییر مشاور</b>\n\n"
    "دانش‌آموز: <b>{name}</b>\n"
    "مشاور فعلی: {current}\n\n"
    "مشاور جدید را انتخاب کنید:"
)
ADMIN_TRANSFER_CONFIRM = (
    "⚠️ <b>تغییر مشاور</b>\n\n"
    "دانش‌آموز: <b>{name}</b>\n"
    "از: {old}\n"
    "به: <b>{new}</b>\n\n"
    "تأیید می‌کنید؟"
)
ADMIN_TRANSFER_DONE = "✅ مشاور «{name}» به «{new}» تغییر کرد."
ADMIN_DELETE_ADVISOR = (
    "⚠️ <b>حذف مشاور</b>\n\n"
    "مشاور: <b>{name}</b>\n"
    "👨‍🎓 دانش‌آموزان: {students}\n"
    "📅 برنامه‌ها: {plans}\n\n"
    "{note}"
)
ADMIN_ADVISOR_HAS_STUDENTS = (
    "دانش‌آموزان این مشاور چه شوند؟\n"
    "• <b>انتقال</b>: دانش‌آموزان و برنامه‌ها به مشاور دیگری منتقل می‌شوند.\n"
    "• <b>بدون مشاور</b>: دانش‌آموزان می‌مانند ولی برنامه‌های این مشاور حذف می‌شود."
)
ADMIN_ADVISOR_NO_STUDENTS = "این مشاور دانش‌آموزی ندارد و حذف او بی‌خطر است."
ADMIN_PICK_TARGET_ADVISOR = "مشاور مقصد برای انتقال دانش‌آموزان را انتخاب کنید:"
ADMIN_ADVISOR_DELETED = "✅ مشاور «{name}» حذف شد. ({detail})"
ADMIN_STUDENT_DELETED = "✅ دانش‌آموز «{name}» و همه داده‌هایش حذف شد."
ADMIN_PLAN_DELETED = "✅ برنامه حذف شد. ({files} فایل پاک شد)"
ADMIN_DELETE_FAILED = "❌ عملیات حذف انجام نشد. هیچ تغییری ثبت نشد."
ADMIN_SELF_ACTION = "⚠️ این عملیات روی مدیر اصلی مجاز نیست."
ADMIN_EDIT_ADVISOR_PROMPT = (
    "✏️ <b>ویرایش مشاور</b>\n\n"
    "مقدار فعلی:\n<code>{current}</code>\n\n"
    "نام جدید را بفرستید (برای تغییر شناسه تلگرام: <code>نام | 123456789</code>)"
)
ADMIN_EDIT_STUDENT_PROMPT = (
    "✏️ <b>ویرایش دانش‌آموز</b>\n\n"
    "مقدار فعلی:\n<code>{current}</code>\n\n"
    "مقدار جدید را بفرستید: <code>نام | پایه</code>"
)
ADMIN_UPDATED = "✅ اطلاعات «{name}» به‌روزرسانی شد."
ADMIN_UNLINKED = "🔓 اتصال تلگرام «{name}» قطع شد."
ADMIN_SEARCH_PROMPT = "🔎 نام یا شناسه تلگرام را بفرستید:"
ADMIN_SEARCH_EMPTY = "نتیجه‌ای پیدا نشد."
ADMIN_CONFIRM = "⚠️ آیا مطمئن هستید؟\n\n{what}\n\nاین عملیات قابل بازگشت نیست."
ADMIN_SUSPENDED = "🔒 حساب «{name}» غیرفعال شد."
ADMIN_ACTIVATED = "🔓 حساب «{name}» فعال شد."
ADMIN_CLEANUP_DONE = "🧹 {files} فایل بدون رکورد پاک شد."
ADMIN_HEALTH_OK = "✅ همه سرویس‌ها سالم هستند."

ADVISOR_PROFILE = (
    "👨‍🏫 <b>پروفایل من</b>\n\n"
    "نام: {name}\n"
    "نقش: {role}\n"
    "شناسه تلگرام: <code>{telegram}</code>\n"
    "وضعیت: {status}\n\n"
    "👨‍🎓 دانش‌آموزان من: {students}\n"
    "📅 برنامه‌ها: {plans}\n"
    "📝 پیش‌نویس‌ها: {drafts}\n"
    "📤 ارسال‌شده: {sent}"
)
STUDENT_PROFILE = (
    "👨‍🎓 <b>پروفایل من</b>\n\n"
    "نام: {name}\n"
    "{grade_line}"
    "مشاور: {advisor}\n"
    "اتصال تلگرام: {connection}\n\n"
    "📅 برنامه‌های دریافتی: {plans}\n"
    "🗓 عضویت: {created}"
)
ROLE_FA = {"admin": "مدیر", "advisor": "مشاور", "student": "دانش‌آموز"}

STATUS_ACTIVE = "🟢 فعال"
STATUS_SUSPENDED = "🔒 غیرفعال"
STATUS_ONLINE = "🟢 آنلاین"
STATUS_CONNECTED = "🟢 متصل"
STATUS_NOT_CONNECTED = "🟡 متصل نشده"
STATUS_READY = "🟢 آماده"
STATUS_AVAILABLE = "🟢 در دسترس"
STATUS_OPTIONAL_OFF = "⚪️ غیرفعال (اختیاری)"
STATUS_FALLBACK = "⚪️ حالت جایگزین"
STATUS_MISSING = "🔴 در دسترس نیست"
MODE_POLLING = "دریافت دوره‌ای پیام‌ها"
PLAN_STATUS_FA = {
    "draft": "پیش‌نویس",
    "ready": "آماده",
    "generated": "تولیدشده",
    "sent": "ارسال‌شده",
    "archived": "بایگانی",
}
AUDIT_ACTIONS_FA = {
    "plan.created": "ایجاد برنامه",
    "plan.edited": "ویرایش برنامه",
    "plan.generated": "تولید برنامه",
    "plan.regenerated": "تولید مجدد برنامه",
    "plan.sent": "ارسال برنامه",
    "plan.deleted": "حذف برنامه",
    "plan.purged": "پاک‌سازی برنامه‌های قدیمی",
    "student.created": "ایجاد دانش‌آموز",
    "student.edited": "ویرایش دانش‌آموز",
    "student.deleted": "حذف دانش‌آموز",
    "student.detached": "جدا کردن دانش‌آموز از مشاور",
    "student.advisor_changed": "تغییر مشاور دانش‌آموز",
    "student.connected": "اتصال دانش‌آموز",
    "student.linked_manually": "اتصال دستی دانش‌آموز",
    "student.suspended": "غیرفعال کردن دانش‌آموز",
    "student.activated": "فعال کردن دانش‌آموز",
    "student.invite_issued": "صدور لینک دعوت",
    "student.invite_revoked": "ابطال لینک دعوت",
    "advisor.created": "ایجاد مشاور",
    "advisor.edited": "ویرایش مشاور",
    "advisor.deleted": "حذف مشاور",
    "advisor.suspended": "تعلیق مشاور",
    "advisor.activated": "فعال‌سازی مشاور",
    "invite.opened": "باز شدن لینک دعوت",
    "invite.accepted": "اتصال دانش‌آموز با لینک",
    "invite.rejected": "رد لینک دعوت",
    "invite.expired": "لینک دعوت منقضی",
    "invite.already_used": "لینک دعوت قبلاً استفاده شده",
    "invite.already_linked": "دانش‌آموز قبلاً متصل بوده",
    "invite.role_conflict": "تلاش نامعتبر برای استفاده از لینک دعوت",
    "telegram.unlinked": "قطع اتصال تلگرام",
    "storage.cleanup": "پاک‌سازی فایل‌ها",
}


def audit_fa(action: str) -> str:
    return AUDIT_ACTIONS_FA.get(action, action)


ACCOUNT_SUSPENDED = (
    "🔒 حساب شما موقتاً غیرفعال شده است.\n"
    "برای پیگیری با مدیر سیستم تماس بگیرید."
)

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
    "پایه/رشته و آیدی عددی تلگرام <b>اختیاری</b> هستند و با <code>|</code> جدا می‌شوند:\n\n"
    "<code>علی رضایی</code>\n"
    "<code>علی رضایی | دوازدهم تجربی</code>\n"
    "<code>علی رضایی | دوازدهم تجربی | 123456789</code>"
)
EDIT_STUDENT_PROMPT = (
    "✏️ <b>ویرایش اطلاعات</b>\n\n"
    "اطلاعات فعلی:\n<code>{current}</code>\n\n"
    "مقدار جدید را به همین شکل بفرستید:\n<code>نام | پایه</code>"
)
STUDENT_UPDATED = "✅ اطلاعات <b>{name}</b> به‌روزرسانی شد."
NO_STUDENTS_EMPTY_STATE = (
    "👨‍🎓 <b>دانش‌آموزان شما</b>\n\n"
    "هنوز دانش‌آموزی ثبت نکرده‌اید.\n"
    "با افزودن اولین دانش‌آموز، می‌توانید برنامه هفتگی او را همین‌جا بسازید."
)
CONNECT_MENU = (
    "🔗 <b>اتصال {name} به تلگرام</b>\n\n"
    "{status}\n\n"
    "دو راه دارید:\n"
    "• <b>لینک دعوت</b> بسازید و برایش بفرستید (ساده‌ترین راه)\n"
    "• اگر آیدی عددی تلگرامش را دارید، مستقیم ثبت کنید"
)
INVITE_READY = (
    "🔗 <b>لینک اتصال آماده شد</b>\n\n"
    "این لینک را برای <b>{name}</b> بفرستید:\n\n"
    "<code>{link}</code>\n\n"
    "⏳ اعتبار تا {expires}\n"
    "🔒 یک‌بارمصرف — پس از اتصال باطل می‌شود."
)
INVITE_REVOKED = "🚫 لینک دعوت باطل شد."
SET_TG_ID_PROMPT = (
    "🔢 آیدی عددی تلگرام دانش‌آموز را بفرستید (فقط عدد):\n"
    "<code>123456789</code>\n\n"
    "دانش‌آموز می‌تواند با فرستادن <code>/id</code> به همین ربات، آیدی خود را ببیند."
)
TG_ID_INVALID = "⚠️ آیدی باید فقط عدد باشد."
TG_ID_LINKED = "✅ <b>{name}</b> به تلگرام وصل شد."
NO_PLAN_THIS_WEEK = "برای این هفته هنوز برنامه‌ای ساخته نشده است."
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
INVITE_REVOKED = "🚫 لینک دعوت باطل شد."
SET_TG_ID_PROMPT = (
    "🔢 آیدی عددی تلگرام دانش‌آموز را بفرستید (فقط عدد):\n"
    "<code>123456789</code>\n\n"
    "دانش‌آموز می‌تواند با فرستادن <code>/id</code> به همین ربات، آیدی خود را ببیند."
)
TG_ID_INVALID = "⚠️ آیدی باید فقط عدد باشد."
TG_ID_LINKED = "✅ <b>{name}</b> به تلگرام وصل شد."
NO_PLAN_THIS_WEEK = "برای این هفته هنوز برنامه‌ای ساخته نشده است."
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
STUDENT_REMOVED = "✅ دانش‌آموز «{name}» و همه داده‌هایش حذف شد."
STUDENT_REMOVE_FAILED = (
    "❌ حذف دانش‌آموز انجام نشد.\n\n"
    "هیچ تغییری ثبت نشد. لطفاً دوباره تلاش کنید."
)
CONFIRM_REMOVE_STUDENT = (
    "⚠️ <b>حذف دانش‌آموز</b>\n\n"
    "نام: <b>{name}</b>\n\n"
    "با این کار موارد زیر برای همیشه حذف می‌شوند:\n"
    "{impact}\n"
    "این عملیات قابل بازگشت نیست."
)
CONFIRM_REMOVE_STUDENT_FINAL = (
    "🗑 <b>تأیید نهایی</b>\n\n"
    "برای حذف قطعی «<b>{name}</b>» دکمه زیر را بزنید."
)
STUDENT_WELCOME_LINKED = (
    "🎉 خوش آمدی <b>{name}</b>!\n\n"
    "حساب شما به مشاورتان وصل شد. از این پس برنامه هفتگی مستقیم همین‌جا برایتان می‌آید."
)
INVITE_ROLE_CONFLICT = (
    "⚠️ این لینک دعوت برای یک <b>دانش‌آموز</b> ایجاد شده است.\n\n"
    "شما در حال حاضر با حساب {role} وارد شده‌اید و نمی‌توانید از لینک دعوت "
    "دانش‌آموز استفاده کنید.\n\n"
    "اگر این لینک را اشتباهی باز کرده‌اید جای نگرانی نیست؛ "
    "نقش و دسترسی حساب شما <b>تغییر نکرده است</b>."
)
INVITE_ALREADY_LINKED = (
    "⚠️ این دانش‌آموز قبلاً به یک حساب تلگرام متصل شده است.\n\n"
    "برای دریافت دسترسی، با مشاور یا پشتیبانی تماس بگیرید."
)
INVITE_CROSS_STUDENT = (
    "⚠️ این لینک برای حساب شما صادر نشده است.\n\n"
    "از مشاور خود بخواهید لینک مخصوص شما را بفرستد."
)
INVITE_EXPIRED = (
    "⏳ مهلت این لینک دعوت تمام شده است.\n\n"
    "از مشاور خود یک لینک تازه بخواهید."
)
INVITE_ALREADY_SELF = "✅ حساب شما قبلاً متصل شده است."
ROLE_ADMIN_FA = "مدیر"
ROLE_ADVISOR_FA = "مشاور"

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
RANGE_START_PROMPT = (
    "📅 <b>انتخاب بازه برنامه</b>\n\n"
    "<b>تاریخ شروع</b> را بفرستید:\n"
    "<code>۱۴۰۵/۰۵/۲۶</code>\n\n"
    "می‌توانید هر دو تاریخ را یکجا هم بفرستید:\n"
    "<code>۱۴۰۵/۰۵/۲۶ تا ۱۴۰۵/۰۵/۲۹</code>"
)
RANGE_END_PROMPT = (
    "🟢 <b>شروع:</b> {start}\n\n"
    "حالا <b>تاریخ پایان</b> را بفرستید:\n"
    "<code>۱۴۰۵/۰۵/۲۹</code>\n\n"
    "<i>برای برنامه یک‌روزه، همان تاریخ شروع را بفرستید.</i>"
)
RANGE_SUMMARY = (
    "📋 <b>خلاصه بازه</b>\n\n"
    "👨‍🎓 {student}\n\n"
    "🟢 شروع: {start}\n"
    "🔵 پایان: {end}\n"
    "📆 تعداد روز: {count}\n\n"
    "{days}"
)
INVALID_DATE = "⚠️ {reason}"

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

SLOT_EMPTY = "خالی"
DAY_TITLE = "📅 <b>{day}</b> — {date}\n\nروی هر ردیف بزنید تا ویرایش شود."
CONNECTION_BLOCK = (
    "🔗 <b>وضعیت اتصال:</b>\n{status}\n"
)
CONNECTION_LINK_BLOCK = (
    "\n🔗 <b>لینک دعوت:</b>\n<code>{link}</code>\n"
    "{dates}"
)
CONNECTION_STATE_LINKED = "🟢 متصل"
CONNECTION_STATE_ISSUED = "🟡 لینک دعوت صادر شده (هنوز استفاده نشده)"
CONNECTION_STATE_EXPIRED = "🔴 لینک دعوت منقضی شده"
CONNECTION_STATE_NONE = "⚪️ هنوز لینکی صادر نشده"
CONNECTION_LINK_DATES = "📅 صادر شده: {issued}\n⏳ اعتبار تا: {expires}\n"
INVITE_REGENERATE_WARNING = (
    "⚠️ یک لینک فعال برای <b>{name}</b> وجود دارد.\n\n"
    "با ساخت لینک جدید، لینک قبلی باطل می‌شود.\n"
    "آیا ادامه می‌دهید؟"
)
INVITE_SHARE = (
    "🔗 لینک اتصال <b>{name}</b>:\n\n"
    "<code>{link}</code>\n\n"
    "این پیام را برای دانش‌آموز فوروارد کنید یا لینک را کپی و ارسال کنید."
)
INVITE_COPY_HINT = (
    "📋 لینک زیر را لمس کنید تا کپی شود:\n\n<code>{link}</code>"
)


def day_fa(key: str) -> str:
    return WEEKDAY_FA[key]


def next_weekday(key: str) -> str | None:
    i = WEEKDAY_KEYS.index(key)
    return WEEKDAY_KEYS[i + 1] if i + 1 < len(WEEKDAY_KEYS) else None
