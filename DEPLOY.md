# DEPLOY — رتبه لند (نسخه ۱.۰.۰-final)

از ZIP تا ربات آنلاین در ۱۶ گام. نیازی به دستکاری کد نیست.

---

### ۱. Extract ZIP

```bash
unzip rotbeland-bot-final-v1.0.0.zip -d rotbeland && cd rotbeland
```

### ۲. ساخت ریپازیتوری GitHub

یک ریپوی **Private** بسازید.

### ۳. Push کد

```bash
git init
git add .
git commit -m "Rotbe Land weekly planner v1.0.0-final"
git branch -M main
git remote add origin <GITHUB_REPO_URL>
git push -u origin main
```

### ۴. ساخت پروژه در Railway

[railway.app](https://railway.app) → **New Project**.

### ۵. افزودن PostgreSQL

**Add → Database → PostgreSQL** (در همان پروژه).

### ۶. اتصال GitHub

**Add → GitHub Repo** → ریپوی خود را انتخاب کنید.
تنظیمات Build خودکار از `railway.toml` خوانده می‌شود (Dockerfile).

### ۷. تنظیم `BOT_TOKEN`

از **@BotFather** توکن بگیرید → سرویس bot → **Variables**:

```
BOT_TOKEN = <توکن شما>
```

### ۸. تنظیم `DATABASE_URL`

```
DATABASE_URL = ${{Postgres.DATABASE_URL}}
```

(Reference بزنید، دستی کپی نکنید. تبدیل به درایور async خودکار انجام می‌شود.)

### ۹. تنظیم `ADMIN_IDS`

آیدی عددی تلگرام خودتان (از @userinfobot یا بعداً با `/id` در همین ربات):

```
ADMIN_IDS = 123456789
```

این متغیر **کلید پنل مدیریت** است: هر آیدی داخل آن، بعد از `/start` دکمه
«🛠 پنل مدیریت» را می‌بیند (مشاوران و دانش‌آموزان نمی‌بینند). چند مدیر را با
کاما جدا کنید: `ADMIN_IDS=111,222`.

بقیه متغیرهای پیشنهادی:

```
ENVIRONMENT   = production
STORAGE_ROOT  = /data/generated
RENDER_BACKEND= auto
TIMEZONE      = Asia/Tehran
```

### ۱۰. افزودن Volume

سرویس bot → **Settings → Volumes → Add Volume** → Mount path:

```
/data/generated
```

### ۱۱. Deploy

دکمه Deploy. ترتیب خودکار:

```
Build (deps + Chromium + verify libraqm/assets)
   ↓
preDeploy: alembic upgrade head
   ↓
./docker-entrypoint.sh bot   →   Long Polling
```

⚠️ **Replicas را روی ۱ نگه دارید** (در `railway.toml` ست شده) وگرنه تلگرام
خطای `Conflict` می‌دهد.

### ۱۲. اجرای Smoke Test

سرویس bot → **Shell**:

```bash
python -m tools.smoke_test --full
```

باید همه‌جا `✔` باشد.

### ۱۳. باز کردن تلگرام

به ربات خود بروید.

### ۱۴. `/start`

منوی مشاور باید ظاهر شود (چون آی‌دی شما در `ADMIN_IDS` است).

### ۱۵. ساخت مشاور و دانش‌آموز

**مشاور** (فقط یک‌بار، از Shell):

```bash
python -m tools.manage add-advisor "نام مشاور" --telegram-id <TELEGRAM_ID>
```

**دانش‌آموز** — کاملاً داخل ربات، بدون Shell:

```
👨‍🎓 دانش‌آموزان → ➕ افزودن دانش‌آموز
   → «علی رضایی»  یا  «علی رضایی | دوازدهم تجربی»
   → 🔗 اتصال به تلگرام → ساخت لینک دعوت
   → لینک را برای دانش‌آموز بفرستید
```

### ۱۶. پنل مدیریت و اولین برنامه

با `/start` → «🛠 پنل مدیریت» به این بخش‌ها دسترسی دارید:
مدیریت مشاوران · مدیریت دانش‌آموزان · مدیریت برنامه‌ها · مدیریت فایل‌ها ·
پایگاه داده · آمار و گزارش‌ها · گزارش فعالیت‌ها · سلامت سیستم · وضعیت ربات ·
تنظیمات مدیریت. (حذف، ویرایش، تغییر مشاور و مدیریت اتصال از همین‌جا انجام می‌شود.)

سپس اولین برنامه:

```
👨‍🎓 دانش‌آموزان → انتخاب دانش‌آموز → 📅 برنامه این هفته
   → روز → خانه → «زیست | گوارش | ۴۰ تست | ۹۰ دقیقه»
   → 📝 تکالیف → 👀 پیش‌نمایش → ✅ تولید برنامه
   → 📤 ارسال برای دانش‌آموز
```

---

## اشکال‌زدایی سریع

| نشانه | راه‌حل |
|---|---|
| `BOT_TOKEN is not set` | متغیر در Railway ست نشده |
| `TelegramConflictError` | بیش از یک Instance؛ Replicas = 1 و ربات محلی را ببندید |
| `No module named 'psycopg2'` | نسخه‌های قبل از ۱.۰.۱؛ این بسته مشکل ندارد |
| `database unreachable` | `DATABASE_URL` باید Reference به سرویس Postgres باشد |
| `chromium: False` | PDF رستر تولید می‌شود (ربات سالم است) |
| دانش‌آموز پیام نمی‌گیرد | باید لینک دعوت را باز کرده باشد (کارت او: 🟢/🟡) |
| تاریخ‌ها یک روز جلو/عقب | `TIMEZONE=Asia/Tehran` را ست کنید |

جزئیات بیشتر: `README.md` (بخش Troubleshooting) و `REVIEW.md`.
