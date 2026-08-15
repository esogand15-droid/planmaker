# DEPLOY — رتبه لند (GitHub → Railway → Telegram)

راهنمای کوتاه و عملی. زمان لازم: حدود ۱۰ دقیقه. نیازی به دستکاری کد نیست.

---

## Step 1 — GitHub

```bash
unzip rotbeland-bot-production-v1.0.0.zip -d rotbeland
cd rotbeland

git init
git add .
git commit -m "Initial production release"
git branch -M main
git remote add origin <GITHUB_REPO_URL>      # ریپازیتوری را Private بسازید
git push -u origin main
```

> فایل `.env` در ریپو وجود ندارد و `.gitignore` جلوی اضافه‌شدنش را می‌گیرد.
> فقط `.env.example` (بدون مقدار واقعی) منتشر می‌شود.

---

## Step 2 — Telegram Token

1. در تلگرام به **@BotFather** بروید → `/newbot` → نام و username.
2. توکن را کپی کنید. **هیچ‌جای کد قرار نمی‌گیرد** — فقط در Railway → Variables.
3. توصیه: `/setprivacy` → Enable و `/setcommands`:

```
start - منوی اصلی
quick - راهنمای ورود سریع فعالیت
cancel - لغو مرحله جاری
help - راهنما
```

---

## Step 3 — Railway Project + PostgreSQL

1. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo** → ریپوی خود را انتخاب کنید.
   (Builder خودکار از `railway.toml` خوانده می‌شود: Dockerfile.)
2. داخل همان پروژه: **Add → Database → PostgreSQL**.

ساختار پروژه در Railway:

```
Railway Project
 ├── bot  (سرویس GitHub شما)
 └── Postgres
```

---

## Step 4 — Environment Variables

در سرویس **bot** → تب **Variables**:

| Variable | مقدار |
|---|---|
| `BOT_TOKEN` | توکن @BotFather |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` ← Reference، دستی کپی نکنید |
| `ENVIRONMENT` | `production` |
| `ADMIN_IDS` | تلگرام آی‌دی عددی شما (مثلاً `123456789`) |
| `STORAGE_ROOT` | `/data/generated` |
| `RENDER_BACKEND` | `auto` |
| `REDIS_URL` | *(اختیاری)* `${{Redis.REDIS_URL}}` اگر سرویس Redis اضافه کردید |

> `postgres://` خودکار به `postgresql+asyncpg://` تبدیل می‌شود؛ نیازی به ویرایش نیست.
> Telegram ID خود را از @userinfobot بگیرید.

---

## Step 5 — Volume

سرویس bot → **Settings → Volumes → Add Volume** → Mount path:

```
/data/generated
```

بدون Volume هم ربات کار می‌کند (فایل‌ها از روی دیتابیس دوباره ساخته می‌شوند)، اما با
Volume، PNG/PDFها بین Deployها باقی می‌مانند.

---

## Step 6 — Deploy

**Deploy** را بزنید. ترتیب خودکار:

```
Build (deps + Chromium + verify libraqm/assets)
   ↓
preDeployCommand: alembic upgrade head
   ↓
./docker-entrypoint.sh bot  →  Long Polling
```

⚠️ **Replicas را روی ۱ نگه دارید** (در `railway.toml` ست شده). دو Instance هم‌زمان
باعث خطای `TelegramConflictError` می‌شود، چون Long Polling فقط یک مصرف‌کننده می‌پذیرد.

---

## Step 7 — بررسی Logs

در Deploy Logs باید ببینید:

```
✔ config: {...}          ← بدون نمایش Secret
✔ pillow/libraqm: True
✔ chromium: True
database connection established
authorized as @<your_bot>
```

---

## Step 8 — ساخت اولین مشاور

سرویس bot → **Shell** (یا `railway run` از روی سیستم خودتان):

```bash
python -m tools.manage add-advisor "نام مشاور" --telegram-id <TELEGRAM_ID>
python -m tools.manage add-student "علی رضایی" --advisor 1 --telegram-id <STUDENT_TG_ID>
python -m tools.manage list-users
```

هر آی‌دی داخل `ADMIN_IDS` در اولین `/start` خودکار نقش **admin** می‌گیرد و به همه
دانش‌آموزان دسترسی دارد؛ برای مشاوران عادی از دستور بالا استفاده کنید.

---

## Step 9 — تست در تلگرام

```
/start
 → ➕ برنامه جدید
 → انتخاب دانش‌آموز
 → انتخاب هفته
 → یک روز → یک فعالیت:  زیست | گوارش | ۴۰ تست | ۹۰ دقیقه
 → 📝 تکالیف
 → 👀 پیش‌نمایش
 → ✅ تولید برنامه   →  دریافت PNG + PDF
 → 📤 ارسال برای دانش‌آموز
```

سمت دانش‌آموز: `/start` → 📅 برنامه این هفته.

---

## Step 10 — Smoke Test

در Shell سرویس:

```bash
python -m tools.smoke_test --full
```

خروجی باید همه‌جا `✔` باشد و کد خروج `0`.

---

## اشکال‌زدایی سریع

| نشانه | راه‌حل |
|---|---|
| `BOT_TOKEN is not set` | Variable در Railway ست نشده |
| `TelegramConflictError` | بیش از یک Instance؛ Replicas = 1 و ربات محلی را ببندید |
| `database unreachable` | `DATABASE_URL` باید Reference به سرویس Postgres باشد |
| `chromium: False` | PDF رستر تولید می‌شود (ربات سالم است)؛ برای PDF برداری Build را دوباره اجرا کنید |
| دانش‌آموز پیام نمی‌گیرد | باید یک‌بار خودش `/start` کرده باشد |

جزئیات بیشتر: بخش Troubleshooting در `README.md`.
