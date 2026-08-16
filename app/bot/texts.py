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
    "نسخه {version} · {env}"
)
ADMIN_ONLY = "⛔️ این بخش فقط برای مدیر سیستم است."
ADMIN_ADVISORS = "👥 <b>مشاوران</b> ({count})"
ADMIN_NO_ADVISORS = (
    "👥 <b>مشاوران</b>\n\nهنوز مشاوری ثبت نشده است.\n"
    "با دستور زیر اضافه کنید:\n"
    "<code>python -m tools.manage add-advisor \"نام\" --telegram-id &lt;ID&gt;</code>"
)
ADMIN_ADVISOR_CARD = (
    "👤 <b>{name}</b>\n"
    "{status_line}"
    "🆔 <code>{telegram}</code>\n"
    "👨‍🎓 دانش‌آموزان: {students}\n"
    "📅 برنامه‌ها: {plans} (این هفته: {this_week})\n"
    "🕐 آخرین فعالیت: {last_seen}"
)
ADMIN_STUDENTS = "👨‍🎓 <b>دانش‌آموزان</b> ({count})"
ADMIN_STUDENT_CARD = (
    "👤 <b>{name}</b>\n"
    "{grade_line}"
    "وضعیت: {status}\n"
    "مشاور: {advisor}\n"
    "📅 برنامه‌ها: {plans}\n"
    "🗓 ثبت: {created}"
)
ADMIN_SYSTEM = (
    "📊 <b>وضعیت سیستم</b>\n\n"
    "🤖 ربات        {bot}\n"
    "🗄 PostgreSQL  {db}\n"
    "⚡ Redis        {redis}\n"
    "🎨 Renderer    {renderer}\n"
    "🌐 Chromium    {chromium}\n"
    "🔤 libraqm     {raqm}\n"
    "💾 Storage     {storage}\n"
    "❤️ Health      {health}\n\n"
    "⏱ تأخیر دیتابیس: {db_latency}\n"
    "🧵 رندرهای در جریان: {inflight}\n"
    "⏳ آپ‌تایم: {uptime}"
)
ADMIN_BOT = (
    "🤖 <b>وضعیت ربات</b>\n\n"
    "وضعیت: {status}\n"
    "حالت: {mode}\n"
    "آپ‌تایم: {uptime}\n"
    "نسخه: {version}\n"
    "قالب: {template}\n"
    "رندرر: {renderer}\n"
    "Fallback: {fallback}\n"
    "آخرین راه‌اندازی: {started}"
)
ADMIN_DB = (
    "🗄 <b>دیتابیس</b>\n\n"
    "PostgreSQL {status}\n\n"
    "👥 کاربران: {users}\n"
    "🧑‍🏫 مشاوران: {advisors}\n"
    "👨‍🎓 دانش‌آموزان: {students}\n"
    "📋 برنامه‌ها: {plans}\n"
    "📝 پیش‌نویس‌ها: {drafts}\n"
    "🗂 فایل‌های ثبت‌شده: {files}\n\n"
    "⏱ تأخیر: {latency}"
)
ADMIN_STORAGE = (
    "📁 <b>فایل‌ها و Storage</b>\n\n"
    "مسیر: <code>{path}</code>\n"
    "Volume: {mounted}\n\n"
    "🖼 PNG: {png}\n"
    "📄 PDF: {pdf}\n"
    "📦 مجموع: {total} فایل · {size}\n"
    "🗑 فایل‌های یتیم: {orphans}"
)
ADMIN_STATS = (
    "📈 <b>آمار</b>\n\n"
    "👥 مشاوران: {advisors}\n"
    "👨‍🎓 دانش‌آموزان: {students}\n"
    "📋 برنامه‌ها: {plans}\n"
    "📝 پیش‌نویس‌ها: {drafts}\n"
    "📤 ارسال‌شده: {sent}\n"
    "🎨 تولیدشده: {generated}\n\n"
    "<b>بازه‌ها</b>\n"
    "امروز: {today}\n"
    "این هفته: {week}\n"
    "این ماه: {month}\n"
    "کل: {all_time}"
)
ADMIN_AUDIT = "📋 <b>Audit Logs</b> — صفحه {page}"
ADMIN_SETTINGS = (
    "⚙️ <b>تنظیمات</b> (فقط خواندنی — از Environment Variables می‌آید)\n\n"
    "محیط: {env}\n"
    "منطقه زمانی: {tz}\n"
    "رندرر: {backend}\n"
    "مقیاس چاپ: {scale} · DPI: {dpi}\n"
    "هم‌زمانی رندر: {concurrency}\n"
    "نگه‌داری: {retention}\n"
    "مدیران: {admins} نفر\n"
    "Storage: <code>{storage}</code>"
)
ADMIN_CONFIRM = "⚠️ آیا مطمئن هستید؟\n\n{what}\n\nاین عملیات قابل بازگشت نیست."
ADMIN_SUSPENDED = "🔒 حساب «{name}» غیرفعال شد."
ADMIN_ACTIVATED = "🔓 حساب «{name}» فعال شد."
ADMIN_CLEANUP_DONE = "🧹 {plans} برنامه و {files} فایل پاک شد."
ADMIN_HEALTH_OK = "✅ همه سرویس‌ها سالم هستند."
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
STUDENT_REMOVED = "🗑 دانش‌آموز از فهرست شما حذف شد. (برنامه‌های قبلی حذف نشدند)"
CONFIRM_REMOVE_STUDENT = (
    "حذف <b>{name}</b> از فهرست شما؟\n"
    "برنامه‌های ساخته‌شده باقی می‌مانند، فقط دیگر در فهرست شما دیده نمی‌شود."
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

SLOT_EMPTY = "خالی"
DAY_TITLE = "📅 <b>{day}</b> — {date}\n\nروی هر ردیف بزنید تا ویرایش شود."


def day_fa(key: str) -> str:
    return WEEKDAY_FA[key]


def next_weekday(key: str) -> str | None:
    i = WEEKDAY_KEYS.index(key)
    return WEEKDAY_KEYS[i + 1] if i + 1 < len(WEEKDAY_KEYS) else None
