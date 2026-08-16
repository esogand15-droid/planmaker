# رتبه لند — سامانه برنامه‌ریزی هفتگی کنکور

ربات تلگرامی مؤسسه **رتبه لند** برای ساخت، مدیریت و تحویل **برنامه هفتگی دانش‌آموزان**.
مشاور برنامه را داخل ربات وارد می‌کند و سیستم به‌صورت خودکار همان اطلاعات را روی
**قالب گرافیکی رسمی مؤسسه** می‌نشاند و خروجی **PNG** و **PDF آماده چاپ** تحویل می‌دهد.
هیچ نرم‌افزار ادیت تصویری (InShot / Canva / Photoshop) در جریان کار نیست.

| | |
|---|---|
| Version | **1.0.0-final2** |
| Template Version | `rotbeland-weekly-v1` |
| Renderer Version | `html-chromium-1.0.0` (اصلی) · `pillow-1.0.0` (fallback) |
| Tests | **324 passed** (Renderer 26 · Bot Flow 13 · Security 36 · Deployment 19 · UX 116 · Students 55 · Admin 45 · Audit 14) |
| Stack | Python 3.12 · aiogram 3 · SQLAlchemy 2 async · PostgreSQL · Alembic · Pillow/libraqm · Playwright |

---

## فهرست

1. [Overview](#1-overview)
2. [Features](#2-features)
3. [Architecture](#3-architecture)
4. [Requirements](#4-requirements)
5. [Local Development](#5-local-development)
6. [Environment Variables](#6-environment-variables)
7. [PostgreSQL Setup](#7-postgresql-setup)
8. [Redis Setup](#8-redis-setup)
9. [Playwright / Chromium Setup](#9-playwright--chromium-setup)
10. [Running Tests](#10-running-tests)
11. [Running the Bot](#11-running-the-bot)
12. [Alembic Migrations](#12-alembic-migrations)
13. [Docker](#13-docker)
14. [Railway Deployment](#14-railway-deployment)
15. [Telegram Bot Setup](#15-telegram-bot-setup)
16. [First Admin/Advisor Setup](#16-first-adminadvisor-setup)
17. [Storage Strategy](#17-storage-strategy)
18. [Security Model](#18-security-model)
19. [Smoke Test](#19-smoke-test)
20. [Troubleshooting](#20-troubleshooting)
21. [Template Calibration](#21-template-calibration)
22. [Known Limitations](#22-known-limitations)

---

## 1. Overview

مشاور در تلگرام: دانش‌آموز → هفته → ۷ روز × ۸ فعالیت → تکالیف → پیش‌نمایش → تولید.
سیستم خروجی نهایی را با فونت فارسی صحیح، RTL درست و مختصات دقیق روی قالب رسمی
می‌سازد و برای مشاور و (در صورت تأیید) برای خود دانش‌آموز ارسال می‌کند.

```
مشاور → ربات → PostgreSQL (منبع حقیقت) → Renderer → PNG + PDF → تلگرام → دانش‌آموز
```

## 2. Features

**مشاور**
- **مدیریت کامل دانش‌آموزان بدون مدیر سیستم و بدون Shell:**
  افزودن (`نام | پایه | آیدی اختیاری`)، ویرایش اطلاعات، حذف از فهرست،
  کارت دانش‌آموز (برنامه این هفته / برنامه جدید / برنامه‌های قبلی)،
  و اتصال به تلگرام از دو راه: **لینک دعوت یک‌بارمصرف با انقضای ۱۴ روز**
  (قابل ابطال و تمدید) یا ثبت مستقیم آیدی عددی
- ساخت برنامه هفتگی: انتخاب دانش‌آموز (جستجو + صفحه‌بندی)، انتخاب هفته (این هفته / بعد / تاریخ شمسی دلخواه)
- ورود سریع فعالیت: `زیست | گوارش | ۴۰ تست | ۹۰ دقیقه` با رفتن خودکار به خانه بعد
- تکالیف چندخطی در یک پیام
- «📋 کپی از هفته قبل»، «کپی روز به روز»، «پاک‌کردن روز»، «خالی کردن خانه»
- پیش‌نمایش با **همان رندرر نهایی**، خلاصه و تأیید، تولید، تولید مجدد
- برنامه‌های قبلی و پیش‌نویس‌ها، حذف با تأیید
- ارسال به دانش‌آموز با تأیید دو مرحله‌ای

**دانش‌آموز**
- «📅 برنامه این هفته» و «📆 برنامه‌های قبلی» با دریافت PNG/PDF

**مدیر (فقط `ADMIN_IDS`)**
- 🛠 پنل مدیریت: مشاوران (فهرست، کارت، آمار، فعال/غیرفعال)، دانش‌آموزان،
  وضعیت سیستم و ربات، دیتابیس، Storage با پاک‌سازی فایل‌های یتیم،
  آمار، Audit Logs و تنظیمات — همه با اعداد واقعی از کوئری زنده

**سیستم**
- Draft خودکار (هر ویرایش بلافاصله در دیتابیس)، مقاوم در برابر ری‌استارت
- نسخه‌بندی: ویرایش برنامهٔ تولیدشده نسخه را +۱ می‌کند و نسخه قبلی در `plan_files` می‌ماند
- تشخیص Overflow **قبل** از تولید، با پیام فارسی دقیق (روز و شماره فعالیت)
- کش خروجی بر اساس هش محتوا + نسخه قالب + نسخه رندرر
- Audit log کامل: created / edited / generated / regenerated / sent / deleted
- Rate limit، Graceful shutdown، Health endpoint، لاگ بدون افشای Secret

## 3. Architecture

```
app/
├─ bot/                     لایه تلگرام (aiogram 3)
│   ├─ handlers/common.py     /start · /help · /cancel · مسیر‌دهی نقش
│   ├─ handlers/advisor.py    کل جریان مشاور
│   ├─ handlers/student.py    نمای دانش‌آموز
│   ├─ keyboards.py           Inline Keyboardها
│   ├─ texts.py               تمام متن‌ها + CallbackData factoryها
│   ├─ states.py              FSM
│   ├─ middlewares.py         Session · User · Throttle · Error
│   ├─ delivery.py            file_id cache + بازسازی فایل گم‌شده
│   ├─ health.py              /health برای Railway
│   └─ main.py                bootstrap · preflight · graceful shutdown
├─ services/
│   ├─ plan_manager.py        Authorization + نگاشت ORM↔Domain + عملیات
│   ├─ plan_service.py        validate → render → store (+ fallback رندرر)
│   └─ render_queue.py        اجرای رندر در Thread، محدودیت هم‌زمانی، drain
├─ repositories/              تمام SQL
├─ domain/                    مدل خالص + تقویم جلالی/ارقام فارسی
├─ rendering/                 layout · fit · pillow · html · pdf · factory
├─ db/                        ORM + engine/session (pool + retry)
├─ config.py                  تنظیمات از Environment
└─ logging_config.py          لاگ با حذف خودکار Secret

assets/     فونت Vazirmatn + قالب PNG (داخل ریپو، بدون دانلود در Runtime)
config/     مختصات کالیبره‌شده قالب (JSON)
migrations/ Alembic
tools/      manage.py · smoke_test.py · calibrate.py · render_demo.py
tests/      88 تست
```

قواعد معماری: منطق کاری از رندر جداست، هیچ مختصاتی hard-code نیست، هیچ متنی داخل
هندلرها نوشته نشده، و Preview دقیقاً همان رندرر نهایی است.

## 4. Requirements

- Python 3.12+
- PostgreSQL 14+ (Production) — SQLite فقط برای تست
- Pillow با **libraqm** (`libraqm0 libfribidi0 libharfbuzz0b`)
- Chromium برای رندرر HTML (اختیاری؛ نبودش خودکار به Pillow برمی‌گردد)
- Redis (اختیاری)

## 5. Local Development

```bash
git clone <your-repo-url> rotbeland && cd rotbeland
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium      # اختیاری ولی توصیه‌شده
cp .env.example .env                                    # مقادیر را پر کنید
alembic upgrade head
python -m tools.manage add-advisor "نام مشاور" --telegram-id <TELEGRAM_ID>
python -m app.bot.main
```

## 6. Environment Variables

| متغیر | الزامی | پیش‌فرض | توضیح |
|---|---|---|---|
| `BOT_TOKEN` | ✅ | — | توکن @BotFather (`TELEGRAM_BOT_TOKEN` هم پذیرفته می‌شود) |
| `DATABASE_URL` | ✅ | — | `postgres://` و `postgresql://` خودکار به `+asyncpg` تبدیل می‌شود؛ `sslmode` حذف می‌شود |
| `ENVIRONMENT` | ➖ | `production` | در حالت production استفاده از SQLite رد می‌شود |
| `RENDER_BACKEND` | ➖ | `auto` | `auto` \| `html` \| `pillow` |
| `REDIS_URL` | ➖ | خالی | خالی = FSM در حافظه (داده‌ها در PostgreSQL هستند) |
| `ADMIN_IDS` | ➖ | خالی | لیست Telegram ID با کاما؛ نقش admin خودکار |
| `STORAGE_ROOT` | ➖ | `./generated` | محل ذخیره PNG/PDF (روی Railway = مسیر Volume) |
| `PORT` | ➖ | خالی | فعال‌سازی `/health` (Railway خودش ست می‌کند) |
| `PRINT_SCALE` | ➖ | `2.0` | مقیاس رندر چاپی |
| `PDF_DPI` | ➖ | `300` | DPI خروجی PDF رستر |
| `RENDER_CONCURRENCY` | ➖ | `2` | تعداد رندر هم‌زمان |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_RECYCLE` | ➖ | `5` / `5` / `1800` | تنظیم Pool |
| `DB_CONNECT_RETRIES` | ➖ | `10` | تلاش مجدد اتصال با Backoff |
| `STUDENTS_PAGE_SIZE` / `PLANS_PAGE_SIZE` | ➖ | `8` / `6` | صفحه‌بندی |
| `LOG_LEVEL` / `SQL_ECHO` | ➖ | `INFO` / `false` | لاگ |
| `RUN_MIGRATIONS_ON_START` | ➖ | `false` | اجرای migration در startup (برای compose/VPS) |
| `TEMPLATE` | ➖ | `template_weekly_v1` | نسخه قالب |
| `TIMEZONE` | ➖ | `Asia/Tehran` | مبنای «امروز/این هفته» (سرور Railway روی UTC است) |
| `RETENTION_DAYS` | ➖ | `0` | پیش‌فرض دستور `manage cleanup` (صفر = نگه‌داری همیشگی) |

> هیچ Secretی در سورس، تست، README یا لاگ وجود ندارد. تستِ
> `test_no_secrets_committed_in_repository` کل درخت پروژه را جاروب می‌کند.

## 7. PostgreSQL Setup

```bash
createdb rotbeland
export DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/rotbeland"
alembic upgrade head
```

- درایور async: `asyncpg` (اجباری). URLهای sync خودکار ارتقا داده می‌شوند.
- Pool: `pool_pre_ping=True` + `pool_recycle=1800` → مقاوم در برابر قطع اتصال‌های Idle در Railway.
- Startup: `wait_for_database()` با Backoff نمایی (پیش‌فرض ۱۰ تلاش) تا دیتابیس بالا بیاید.
- Shutdown: `dispose_engine()` تمام کانکشن‌ها را می‌بندد.

## 8. Redis Setup

Redis **اختیاری** است. تمام داده‌های برنامه در PostgreSQL ذخیره می‌شود؛ Redis فقط
موقعیت Wizard (FSM) را نگه می‌دارد تا با ری‌استارت، کاربر در همان مرحله بماند.
بدون Redis، کاربر فقط باید از منو دوباره وارد پیش‌نویس شود — هیچ داده‌ای گم نمی‌شود.
اگر `REDIS_URL` ست باشد ولی Redis در دسترس نباشد، سیستم با اخطار به حافظه برمی‌گردد.

## 9. Playwright / Chromium Setup

```bash
python -m playwright install --with-deps chromium
```

- `RENDER_BACKEND=auto` قبل از انتخاب بک‌اند HTML، **وجود واقعی باینری Chromium** را
  بررسی می‌کند (نه فقط import پکیج). نبودِ Chromium ⇒ خودکار Pillow.
- اگر Chromium وسط کار Crash کند، `WeeklyPlanService` همان برنامه را با Pillow می‌سازد
  (تست: `test_service_recovers_when_the_browser_backend_crashes`).
- تفاوت دو بک‌اند: HTML → PDF برداری با متن انتخاب‌شدنی؛ Pillow → PDF رستر ۳۰۰DPI روی A4.

## 10. Running Tests

```bash
python -m pytest -q                      # کل مجموعه
python -m pytest tests/test_renderer.py  # فقط رندر
python -m pytest tests/test_security.py  # فقط امنیت
```

## 11. Running the Bot

```bash
python -m app.bot.main
```

در Startup: اعتبارسنجی تنظیمات → بررسی وجود قالب/فونت‌ها → انتظار برای دیتابیس →
`getMe` → `deleteWebhook(drop_pending_updates=True)` → Long Polling.
با دریافت `SIGTERM`/`SIGINT`: توقف Polling → drain رندرهای در جریان →
بستن FSM، Session تلگرام، Pool دیتابیس و Health server.

## 12. Alembic Migrations

```bash
alembic upgrade head                      # اعمال
alembic revision --autogenerate -m "msg"  # ساخت نسخه جدید
alembic downgrade -1                      # بازگشت
```

روی Railway، migration در **preDeployCommand** اجرا می‌شود (قبل از اینکه نسخه جدید
ترافیک بگیرد) تا هرگز دو Instance هم‌زمان migrate نکنند.

## 13. Docker

```bash
docker build -t rotbeland-bot .
docker run --env-file .env -v rotbeland_data:/data/generated rotbeland-bot

# یا استک کامل (Postgres + Redis + Bot)
TELEGRAM_BOT_TOKEN=xxx docker compose up -d --build
```

ایمیج در زمان Build بررسی می‌کند: libraqm فعال باشد، هر سه فونت و فایل قالب موجود
باشند و وضعیت Chromium گزارش شود. Entrypoint: `./docker-entrypoint.sh [bot|migrate|shell|smoke]`.

## 14. Railway Deployment

> راهنمای فشرده و گام‌به‌گام برای اولین Deploy: **[DEPLOY.md](DEPLOY.md)**

```text
 1. یک ریپازیتوری خصوصی در GitHub بسازید
 2. کد را Push کنید:        git init && git add . && git commit -m "init" && git push
 3. در Railway پروژه جدید بسازید → Deploy from GitHub repo
 4. سرویس PostgreSQL اضافه کنید (Add → Database → PostgreSQL)
 5. (اختیاری) سرویس Redis اضافه کنید
 6. سرویس بات را به ریپازیتوری وصل کنید (Builder = Dockerfile، از railway.toml خوانده می‌شود)
 7. Variables را ست کنید:
        BOT_TOKEN      = <از BotFather>
        DATABASE_URL   = ${{Postgres.DATABASE_URL}}
        REDIS_URL      = ${{Redis.REDIS_URL}}        (اختیاری)
        ADMIN_IDS      = <تلگرام آی‌دی شما>
        STORAGE_ROOT   = /data/generated
        ENVIRONMENT    = production
 8. یک Volume بسازید و روی /data/generated ماونت کنید (Settings → Volumes)
 9. Deploy کنید؛ preDeployCommand خودکار `alembic upgrade head` را اجرا می‌کند
10. اولین مشاور را بسازید (Railway → Service → Shell یا `railway run`):
        python -m tools.manage add-advisor "نام مشاور" --telegram-id <ID>
11. Replicas را روی 1 نگه دارید (Long Polling فقط یک Instance می‌پذیرد)
12. Logs را ببینید: باید «authorized as @yourbot» و «database connection established» باشد
13. در تلگرام /start بزنید
14. اسموک تست: `python -m tools.smoke_test --full`
```

نکات مهم Railway:
- **numReplicas = 1** (در `railway.toml` ست شده). دو Instance ⇒ خطای `Conflict` تلگرام.
- بدون Volume، فایل‌سیستم Ephemeral است → به [Storage Strategy](#17-storage-strategy) مراجعه کنید.
- `PORT` را Railway ست می‌کند و `/health` فعال می‌شود؛ Healthcheck از `railway.toml` می‌آید.

## 15. Telegram Bot Setup

1. در @BotFather دستور `/newbot` → نام و username → توکن را در `BOT_TOKEN` بگذارید.
2. `/setprivacy` → **Enable** (بات فقط به دستورات خودش نیاز دارد).
3. توصیه: `/setcommands`
   ```
   start - منوی اصلی
   quick - راهنمای ورود سریع فعالیت
   cancel - لغو مرحله جاری
   help - راهنما
   ```
4. حالت کار: **Long Polling** (بدون نیاز به دامنه/SSL). Webhook در Startup پاک می‌شود.

## 16. First Admin/Advisor Setup

فقط **مشاورها** با خط فرمان ساخته می‌شوند؛ دانش‌آموزان را خودِ مشاور از داخل ربات
اضافه می‌کند.

```bash
python -m tools.manage add-advisor "علی مرادی" --telegram-id 123456789
python -m tools.manage list-users
python -m tools.manage list-plans --advisor 1
python -m tools.manage audit
python -m tools.manage cleanup --days 180 --dry-run   # نگه‌داری فایل‌ها
```

شناسه تلگرام هر فرد را می‌توان با فرستادن `/id` به همین ربات گرفت.

### جریان افزودن دانش‌آموز (سمت مشاور، بدون خط فرمان)

```
منو → 👨‍🎓 دانش‌آموزان → ➕ افزودن دانش‌آموز
     → «علی رضایی»  یا  «علی رضایی | دوازدهم تجربی»
     → ربات یک لینک دعوت می‌دهد:  https://t.me/<bot>?start=inv_<token>
     → مشاور لینک را برای دانش‌آموز می‌فرستد
     → دانش‌آموز لینک را باز می‌کند ⇒ حسابش به همان رکورد وصل می‌شود
```

نکته‌ها:
* لینک **یک‌بارمصرف** است و پس از استفاده باطل می‌شود (از کارت دانش‌آموز می‌توان لینک تازه گرفت).
* بدون اتصال هم می‌توان برای دانش‌آموز برنامه ساخت؛ فقط ارسال مستقیم غیرفعال است.
* اگر دانش‌آموز قبلاً ربات را باز کرده باشد، هنگام Claim رکورد تکراری‌اش ادغام می‌شود.
* افراد ناشناس (بدون دعوت) در دیتابیس ثبت نمی‌شوند و پیام راهنما می‌گیرند.

| آرگومان | توضیح |
|---|---|
| `name` | نام کامل (فارسی مجاز) |
| `--telegram-id` | عددی؛ کاربر با همین ID شناسایی می‌شود. اگر خالی باشد، کاربر بعداً با `/start` ثبت می‌شود ولی ارسال مستقیم ممکن نیست |
| `--advisor` | هنگام ساخت دانش‌آموز، او را به این مشاور تخصیص می‌دهد |

نقش‌ها: `admin` (همه‌چیز) · `advisor` (فقط دانش‌آموزان تخصیص‌یافته) · `student` (فقط برنامه‌های خودش).
هر Telegram ID داخل `ADMIN_IDS` در اولین `/start` خودکار admin می‌شود.

## 17. Storage Strategy

- خروجی‌ها در `STORAGE_ROOT/<سال>/<ماه>/<student_id>/rotbeland_weekly_plan_<sid>_<date>_v<version>_<hash>.png|pdf`
- نام فایل‌ها **ASCII-safe**؛ متن فارسی فقط در Caption.
- نوشتن **اتمیک** (`.tmp` سپس `replace`) → هرگز فایل نیمه‌کاره ارسال نمی‌شود.
- **کش**: اگر هش محتوا تغییر نکرده باشد، فایل قبلی دوباره استفاده می‌شود.
- **file_id تلگرام**: بعد از اولین ارسال ذخیره می‌شود؛ ارسال‌های بعدی بدون آپلود مجدد
  انجام می‌شود و حتی اگر فایل محلی از بین برود کار می‌کند.
- **Ephemeral FS**: اگر فایل و file_id هر دو نبودند، برنامه از روی داده‌های دیتابیس
  **دوباره رندر** می‌شود. منبع حقیقت همیشه دیتابیس است، نه فایل.
- **حذف برنامه، فایل‌هایش را هم پاک می‌کند** (فقط داخل `STORAGE_ROOT`).
- پاک‌سازی دوره‌ای: `python -m tools.manage cleanup --days 180` (با `--dry-run` برای پیش‌بینی).
- برای نگه‌داری بلندمدت روی Railway: Volume روی `/data/generated`. برای مقیاس بزرگ‌تر
  می‌توان `plan_service` را به S3/R2 وصل کرد (نقطه اتصال: `WeeklyPlanService._dir_for`).

## 18. Security Model

### یکپارچگی نقش‌ها (Role Integrity)

لینک دعوت **هرگز** نمی‌تواند نقش یک حساب موجود را تغییر دهد. ترتیب پردازش
`/start inv_...` عمداً این است:

```
شناسایی کاربر فعلی → بررسی ADMIN_IDS → بررسی نقش دیتابیس
   → اگر ADMIN یا ADVISOR بود: توقف کامل (هیچ نوشتنی انجام نمی‌شود)
   → اعتبارسنجی توکن → انقضا → مالکیت → اتصال
```

| سناریو | نتیجه |
|---|---|
| مشاور روی لینک دانش‌آموزش می‌زند | ⛔️ مسدود · نقش، آیدی و توکن دست‌نخورده · پیام اطمینان‌بخش |
| مدیر روی لینک می‌زند | ⛔️ مسدود · همچنان مدیر |
| دانش‌آموز روی لینک دانش‌آموز دیگر | ⛔️ مسدود |
| دانش‌آموزِ متصل، لینک دیگری | ⛔️ مسدود (بدون بازنویسی `telegram_id`) |
| کاربر ناشناس با لینک معتبر | ✅ اتصال به همان رکورد |
| همان دانش‌آموز، لینک تکراری | ✅ پیام «قبلاً متصل شده» + مصرف توکن |
| توکن منقضی / باطل‌شده / جعلی | ⛔️ مسدود |

همه این رویدادها Audit می‌شوند:
`invite.opened / accepted / rejected / expired / already_used / already_linked /
role_conflict` و `student.invite_issued / invite_revoked`.

هیچ مسیری در محصول نمی‌تواند `admin → advisor`، `admin → student` یا
`advisor → student` را انجام دهد (تست `test_no_flow_can_downgrade_a_role`).

### اقتدار مدیر (ADMIN_IDS)

`ADMIN_IDS` **منبع حقیقت** برای دسترسی مدیریتی است و از نقش دیتابیس بالاتر است:
اگر رکورد اشتباهاً `advisor` باشد ولی آیدی در `ADMIN_IDS` باشد، همچنان پنل مدیریت
باز می‌شود. این متغیر فقط می‌تواند **ارتقا** بدهد؛ هیچ مسیری تنزل نمی‌دهد.
هر هندلر پنل، دسترسی را دوباره با آیدی زندهٔ تلگرام بررسی می‌کند — نه با
`callback_data` (تست: مشاور روی هر ۱۸ اکشن ادمین ⇒ رد).

### تعلیق حساب (Role ≠ Status)

`role` و `is_active` جدا هستند. حساب معلق داده‌هایش را نگه می‌دارد ولی
هیچ عملیات نوشتنی (ساخت دانش‌آموز/برنامه، ویرایش، ارسال) انجام نمی‌دهد.
حساب مدیر قابل تعلیق نیست.


| موضوع | وضعیت |
|---|---|
| Authorization | `advisor → دانش‌آموزان تخصیص‌یافته` · `student → برنامه‌های خودش` · `admin → همه` |
| IDOR / callback tampering | هر ۱۹ اکشن callback با `plan_id` جعلی تست شده و رد می‌شود |
| SQL Injection | تمام کوئری‌ها پارامتری (SQLAlchemy)؛ جستجو با الگوی bind شده |
| Path traversal | فایل‌های خارج از `STORAGE_ROOT` سرو نمی‌شوند |
| نام فایل | ASCII، بدون فاصله، بدون `..`، مستقل از ورودی کاربر |
| Secret leakage | فیلتر Redaction روی کل لاگ + جاروب استاتیک ریپو |
| Rate limiting | Token bucket per-user + Cooldown ۳ ثانیه برای رندر |
| Error handling | هیچ Traceback به کاربر نمی‌رسد؛ لاگ کامل سمت سرور |

## 19. Smoke Test

```bash
python -m tools.smoke_test                 # تنظیمات، دیتابیس، migration، فونت، رندرر، تلگرام
python -m tools.smoke_test --full          # + تولید واقعی PNG/PDF
python -m tools.smoke_test --send-to 12345 # + ارسال نمونه به یک چت
```

خروجی چک‌لیستی با کد خروج ۰/۱ (مناسب CI و Railway Job).

### چک‌لیست دستی بعد از Deploy

**مشاور:** `/start` → منو → ➕ برنامه جدید → دانش‌آموز → هفته → روز → فعالیت →
تکالیف → 👀 پیش‌نمایش → ✅ تولید → دریافت PNG و PDF
**دانش‌آموز:** `/start` → 📅 برنامه این هفته → دریافت PNG/PDF
**امنیت:** با اکانت مشاور B روی دکمه‌های مشاور A (یا callback دستکاری‌شده) → باید رد شود.

## 20. Troubleshooting

| نشانه | علت / راه‌حل |
|---|---|
| `BOT_TOKEN is not set` | متغیر محیطی تنظیم نشده؛ در Railway → Variables |
| `TelegramConflictError` | بیش از یک Instance در حال Polling؛ `numReplicas=1` و Instanceهای محلی را ببندید |
| متن فارسی جدا/برعکس | Pillow بدون libraqm؛ `libraqm0 libfribidi0 libharfbuzz0b` را نصب کنید (در Dockerfile هست) |
| `Chromium is not installed` | `python -m playwright install chromium` — یا نادیده بگیرید، Pillow کار می‌کند |
| PDF بدون متن انتخاب‌شدنی | یعنی از مسیر Pillow ساخته شده (Chromium در دسترس نبوده) |
| فایل‌ها بعد از Deploy گم شده‌اند | Volume ماونت نشده؛ سیستم خودکار Re-render می‌کند ولی Volume را اضافه کنید |
| `ModuleNotFoundError: No module named 'psycopg2'` | نسخه‌های قبل از v1.0.1؛ `migrations/env.py` باید `DATABASE_URL` را به `postgresql+asyncpg://` تبدیل کند. در v1.0.1 رفع شده است |
| `database unreachable after N attempts` | `DATABASE_URL` یا دسترسی شبکه؛ `DB_CONNECT_RETRIES` را بالا ببرید |
| هشدار «متن بیش از ظرفیت سلول» | متن فعالیت را کوتاه‌تر کنید؛ سیستم عمداً فونت را بی‌نهایت کوچک نمی‌کند |
| دکمه‌ای در منو کار نمی‌کند و ساعت می‌چرخد | در v1.0.2 رفع شد: هر callback بی‌صاحب پاسخ می‌گیرد و در لاگ سرور با `unhandled callback data=…` ثبت می‌شود |
| دانش‌آموز پیام نمی‌گیرد | باید یک‌بار خودش `/start` کرده باشد تا `telegram_id` ثبت شود |

## 21. Template Calibration

```bash
python -m tools.calibrate grid    # شبکه و مختصات روی قالب → out/calibration_grid.png
python -m tools.calibrate probe   # تشخیص خطوط از پیکسل‌ها و مقایسه با config
python -m tools.calibrate fill    # پر کردن هر ۵۶ خانه (بدترین حالت)
python -m tools.calibrate nudge cells 0 -2
```

مختصات در `config/template_weekly_v1.json`. برای قالب جدید: فایل PNG را در
`assets/templates/` بگذارید، یک JSON نسخه‌دار بسازید و `TEMPLATE` را تغییر دهید؛
برنامه‌های قدیمی نسخه قالب خودشان را در دیتابیس نگه می‌دارند.

## 22. Known Limitations

- Long Polling ⇒ فقط **یک** Instance. (Webhook قابل افزودن است ولی نیاز به دامنه دارد.)
- بخش تکالیف دو خط نقطه‌چین دارد؛ بیش از ~۱۴ تکلیف بلند هشدار Overflow می‌دهد.
- نام دانش‌آموز روی خودِ قالب چاپ نمی‌شود (جای مناسبی ندارد) و در Caption/نام فایل می‌آید.
- بدون Redis، موقعیت Wizard پس از ری‌استارت از دست می‌رود (داده‌ها سالم می‌مانند).
- PDF مسیر Pillow رستر است (۳۰۰DPI)؛ برای PDF برداری Chromium لازم است.
- فاز ۳ (AI Planner و Analytics) پیاده‌سازی نشده است.

---

© رتبه لند — راهی به سوی موفقیت. فونت Vazirmatn تحت مجوز SIL OFL (`assets/fonts/Vazirmatn-OFL.txt`).
