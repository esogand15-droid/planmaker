# راهنمای استقرار — ربات برنامه‌ریز هفتگی رتبه لند

نسخه **۱.۴.۰** · پایگاه داده **PostgreSQL** · حالت اجرا **long polling** (بدون وب‌هوک)

این فایل فقط برای استقرار و رفع مشکل است؛ کد ربات هیچ وابستگی‌ای به آن ندارد.

---

## ۰) قبل از هر چیز: متغیرهای محیطی

الگوی کامل در `.env.example` است. حداقل‌های لازم برای بالا آمدن ربات:

| متغیر | مثال | توضیح |
|---|---|---|
| `BOT_TOKEN` | `1234567890:AA...` | از @BotFather — اجباری |
| `DATABASE_URL` | `postgresql://user:pass@host:5432/db` | اجباری. `postgres://` و آرگومان‌هایی مثل `?sslmode=require` خودکار پذیرفته و نرمال‌سازی می‌شوند |
| `ADMIN_IDS` | `123456789` | شناسه عددی تلگرام مدیر (چند مقدار با کاما). شناسه خود را با `/id` از ربات بگیرید |
| `ENVIRONMENT` | `production` | در حالت production استفاده از SQLite رد می‌شود |
| `RUN_MIGRATIONS_ON_START` | `true` | **پیش‌فرض true** — مهاجرت خودکار هنگام شروع |
| `STORAGE_ROOT` | `/data/generated` | فایل‌های تولیدشده (PNG/PDF) |
| `BACKUP_DIR` | `/data/backups` | آرشیوهای بکاپ |

> ⚠️ اگر `ADMIN_IDS` خالی باشد و در دیتابیس هم کاربری با نقش `ADMIN` نباشد،
> هیچ‌کس پنل مدیریت را نخواهد دید. این متغیر همیشه بر نقش دیتابیس اولویت دارد.

---

## ۱) چرا قبلاً «❌ انجام این کار با مشکل مواجه شد» می‌گرفتید

لاگ واقعی این بود:

```
asyncpg.exceptions.UndefinedTableError: relation "users" does not exist
```

یعنی ربات به یک پایگاه داده **خالی** (بدون جدول) وصل شده بود؛ `alembic upgrade head`
هیچ‌وقت اجرا نشده بود و هر `/start` با خطای عمومی پاسخ داده می‌شد.

اصلاحات انجام‌شده (لایه‌به‌لایه، تا این خطا دیگر تکرار نشود):

1. **مهاجرت خودکار هنگام استارت** — `app/bot/main.py::bootstrap_schema()`:
   `alembic upgrade head` → بررسی جدول‌ها → در صورت نیاز ساخت از ORM → گزارش
   `revision/head`. اگر دیتابیس همچنان خراب بماند، ربات با کد `2` خارج می‌شود و
   **ترافیک نمی‌پذیرد** (به‌جای پاسخ غلط به کاربر).
2. **`RUN_MIGRATIONS_ON_START=true` به‌صورت پیش‌فرض** در `Dockerfile` و
   `docker-entrypoint.sh`.
3. **قفل مهاجرت (advisory lock)** — اگر چند نمونه هم‌زمان استارت بزنند، فقط یکی
   مهاجرت را اجرا می‌کند (`migration_lock()` در `app/services/backup.py`).
4. **پیام درست به کاربر** — اگر باز هم جدولی موجود نباشد، به‌جای «با مشکل مواجه شد»
   پیام راهنمای آماده‌سازی پایگاه داده نمایش داده می‌شود
   (`app/bot/middlewares.py::_is_missing_schema`).
5. **ابزار تشخیص**: `python -m tools.manage doctor`

---

## ۲) استقرار روی Railway

```bash
railway init
railway add --plugin postgresql
railway variables --set "BOT_TOKEN=<token>" \
                --set "ADMIN_IDS=<your id>" \
                --set "ENVIRONMENT=production" \
                --set "STORAGE_ROOT=/data/generated" \
                --set "BACKUP_DIR=/data/backups" \
                --set "BACKUP_AUTO_ENABLED=true"
railway up
```

`railway.toml` دو مرحله دارد:

* `preDeployCommand = "alembic upgrade head"` — قبل از گرفتن ترافیک
* `startCommand = "./docker-entrypoint.sh bot"` — که خودش هم یک‌بار مهاجرت می‌کند

اجرای دوباره‌ی مهاجرت عمدی است: `alembic upgrade head` بی‌ضرر (idempotent) است و
همین لایه دوم، استقرار اولی را که `preDeploy` در آن اجرا نشده نجات می‌دهد.

**Volume**: یک volume به مسیر `/data` وصل کنید تا فایل‌های تولیدشده و آرشیوهای
بکاپ بعد از هر استقرار از بین نروند.

**PORT**: ربات polling است و پورت ورودی ندارد؛ اگر Railway پورت تزریق کرد،
endpoint سلامت روی `GET /health` بالا می‌آید (`app/bot/health.py`).

---

## ۳) استقرار با Docker

```bash
cp .env.example .env      # ویرایش مقادیر
docker compose up -d --build
docker compose logs -f bot
```

سرویس‌ها: `postgres:16` + `redis:7` (اختیاری، فقط برای FSM) + `bot`.

اجرای دستی یک آرشیو بکاپ روی کانتینر در حال اجرا:

```bash
docker compose exec bot ./docker-entrypoint.sh backup
docker compose exec bot ./docker-entrypoint.sh backup --status
docker compose exec bot python -m tools.manage doctor
```

---

## ۴) استقرار روی VPS (systemd)

```bash
git clone https://github.com/esogand15-droid/planmaker && cd planmaker
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium && python -m playwright install-deps chromium
cp .env.example .env   # ویرایش
alembic upgrade head
python -m tools.manage doctor
python -m tools.smoke_test          # بررسی کامل سلامت استقرار
```

فایل `/etc/systemd/system/rotbeland-bot.service`:

```ini
[Unit]
Description=Rotbe Land weekly planner bot
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=rotbeland
WorkingDirectory=/opt/planmaker
EnvironmentFile=/opt/planmaker/.env
ExecStart=/opt/planmaker/.venv/bin/python -m app.bot.main
Restart=always
RestartSec=5
# سخت‌گیری کم‌تر فقط در صورت نیاز به Chromium بدون sandbox
# NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rotbeland-bot
journalctl -u rotbeland-bot -f
```

---

## ۵) بکاپ‌گیری

### از داخل تلگرام
`🛠 پنل مدیریت` → `🧰 بکاپ‌گیری`:

* **🗄 بکاپ فوری و ارسال** — یک آرشیو می‌سازد و همان لحظه برای همه‌ی `ADMIN_IDS` می‌فرستد
* **📬 ارسال مجدد آخرین بکاپ** — بدون ساخت آرشیو جدید
* **🟢/🔴 بکاپ خودکار** — روشن/خاموش
* **⏰ زمان‌بندی** — ساعتی / هر N ساعت / روزانه / هفتگی + ساعت + روز هفته
* **🗂 تعداد نسخه‌ها** — نگهداری ۳ تا ۳۰ آرشیو (پاک‌سازی خودکار قدیمی‌ترها)
* **🕘 تاریخچه** — وضعیت، حجم، تعداد رکورد، موتور و خطای آخرین اجراها

تنظیمات در جدول `bot_settings` ذخیره می‌شود؛ یعنی بعد از ری‌استارت هم می‌ماند.
مقادیر `BACKUP_*` در env فقط **مقادیر اولیه** هستند.

### از خط فرمان
```bash
python -m tools.backup                 # ساخت آرشیو در BACKUP_DIR
python -m tools.backup --status        # زمان‌بندی، اجرای بعدی، تاریخچه
python -m tools.backup --list          # آرشیوهای روی دیسک
python -m tools.backup --send 123456789  # ساخت + ارسال به یک چت
python -m tools.backup --restore-info rotbeland-backup-...tar.gz
python -m tools.backup --no-pg-dump    # موتور پایتون (بدون pg_dump)
```

### موتور بکاپ
* اگر باینری `pg_dump` موجود باشد (در Dockerfile نصب شده: `postgresql-client`) از آن
  استفاده می‌شود — خروجی کاملاً استاندارد و سازگار با `pg_restore`.
* در غیر این صورت، دامپر داخلی پایتون همان کار را می‌کند (سازگار با PostgreSQL و
  SQLite). اگر نسخه‌ی سرور از `pg_dump` جدیدتر باشد، به‌طور خودکار به موتور پایتون
  برمی‌گردد.

### محتوای هر آرشیو
```
rotbeland-backup-<تاریخ>-<کد>.tar.gz
├── README.txt          راهنمای فارسی بازیابی
├── restore.sh          بازیابی یک‌خطی با psql
├── metadata.json       جزئیات + checksum (بدون رمز عبور)
├── checksums.txt       sha256 هر عضو
├── sql/dump.sql        ساخت جدول + پاک‌سازی + داده + سکونس‌ها
├── sql/schema.sql      فقط ساختار
├── sql/data_only.sql   فقط داده
├── sql/pgdump.dump     خروجی باینری pg_dump (فقط وقتی pg_dump موجود باشد)
├── json/*.json         هر جدول به‌صورت JSON
└── csv/*.csv           هر جدول به‌صورت CSV (قابل باز شدن در اکسل)
```
جدول‌های `backup_logs` و `bot_settings` عمداً در بکاپ نیستند (لاگِ خودِ بکاپ).
هیچ‌جای آرشیو DSN یا رمز عبور نوشته نمی‌شود — فقط نام پایگاه داده.

### بازیابی
```bash
tar -xzf rotbeland-backup-XXXXXXXX-XXXXXX-XXXX.tar.gz
cd rotbeland-backup-XXXXXXXX-XXXXXX-XXXX
export DATABASE_URL="postgresql://user:pass@host:5432/db"
./restore.sh              # یا: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/dump.sql
```
`restore.sh` در یک تراکنش اجرا می‌شود، داده‌های موجود را پاک می‌کند، داده‌ها را
بازمی‌گرداند و در انتها `alembic_version` و سکونس‌ها را تنظیم می‌کند.

### قفل‌ها
بکاپ و مهاجرت هر کدام یک **advisory lock** روی PostgreSQL می‌گیرند
(`7341001` و `7341002`) تا دو فرایند هم‌زمان روی هم نریزند. روی SQLite یا در
صورت بروز خطا، قفل به‌صورت «بدون قفل» رفتار می‌کند و هرگز جلوی بکاپ را نمی‌گیرد.

---

## ۶) بررسی سلامت

```bash
python -m tools.manage doctor    # وضعیت schema و revision
python -m tools.smoke_test       # دیتابیس + رندر + تلگرام، گزارش کامل
python -m tools.smoke_test --send-to <chat_id>   # نمونه رندر را برای شما می‌فرستد
curl -s localhost:${PORT:-8080}/health
```

CI (فایل `.github/workflows/ci.yml`) روی هر push: PostgreSQL واقعی بالا می‌آورد،
`alembic upgrade head` و `doctor` را اجرا می‌کند، یک بکاپ واقعی می‌سازد و کل
تست‌ها را اجرا می‌کند.

---

## ۷) رفع مشکل سریع

| نشانه | علت احتمالی | کار |
|---|---|---|
| `relation "users" does not exist` | دیتابیس خالی / مهاجرت اجرا نشده | `python -m tools.manage migrate` سپس `doctor`؛ `RUN_MIGRATIONS_ON_START=true` باشد |
| ربات بالا می‌آید ولی پنل مدیریت نیست | `ADMIN_IDS` خالی یا اشتباه | `/id` را به ربات بفرستید و همان عدد را در `ADMIN_IDS` بگذارید |
| «❌ انجام این کار با مشکل مواجه شد» | هر خطای پیش‌بینی‌نشده | لاگ را ببینید: `journalctl -u rotbeland-bot` یا `railway logs`. مسیر لاگ‌شده دقیقاً همان handler است |
| دکمه‌ها می‌چرخند و چیزی نمی‌آید | پاسخ‌ندادن به callback / پیام قدیمی‌تر از ۴۸ ساعت | در نسخه ۱.۴.۰ رفع شده؛ اگر تکرار شد لاگ `unhandled callback` را بفرستید |
| بکاپ خودکار نمی‌آید | خاموش بودن، یا نبودن گیرنده | پنل → بکاپ‌گیری → وضعیت؛ `BACKUP_CHAT_IDS` خالی باشد یعنی `ADMIN_IDS` |
| `pg_dump: server version mismatch` | سرور جدیدتر از کلاینت | خودکار به موتور پایتون برمی‌گردد؛ یا `postgresql-client` را به‌روز کنید |
| رندر عکس خراب/بدون شکل فارسی | نبود `libraqm` یا فونت | `apt-get install libraqm0 libfribidi0 libharfbuzz0b fonts-dejavu-core` |
| Chromium بالا نمی‌آید (کانتینر) | sandbox در محیط ریشه | در `app/rendering/html_renderer.py` آرگومان‌های `--no-sandbox --disable-dev-shm-usage` اضافه شده؛ اگر باز هم نشد `RENDER_BACKEND=pillow` |
| فایل برنامه بعد از ری‌استارت نیست | دیسک موقت | volume روی `/data`؛ در هر حال با «تولید برنامه» از دیتابیس بازسازی می‌شود |
| دو نمونه ربات هم‌زمان جواب می‌دهند | اجرای هم‌زمان polling و وب‌هوک یا دو سرویس | فقط یک نمونه اجرا شود؛ `bot.delete_webhook(drop_pending_updates=True)` هنگام استارت اجرا می‌شود |

---

## ۸) ارتقا

```bash
git pull
pip install -r requirements.txt
alembic upgrade head          # یا اجازه دهید استارت خودش انجام دهد
python -m tools.manage doctor
systemctl restart rotbeland-bot   # یا: railway up / docker compose up -d --build
```

زنجیره مهاجرت‌ها تا امروز:
`946abecd3e6c → 0b0794a592a8 → 37248bc77614 → af6d2e908bc5 → 9e411e1e97fa → c1f4b7d20a11`

`c1f4b7d20a11` همان مهاجرتی است که جدول‌های `backup_logs` و `bot_settings` را
برای بخش بکاپ‌گیری می‌سازد.

> **پیشنهاد:** قبل از هر ارتقا یک بکاپ دستی بگیرید (پنل → بکاپ‌گیری → بکاپ فوری).
