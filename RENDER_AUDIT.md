# ROTBE LAND — گزارش ممیزی عمیق رندرر (Template V2)

نسخه: **۱.۳.۱** · تاریخ: ۱۴۰۵/۰۵/۳۱ (2026-08-22)

---

## ۱. خلاصهٔ وضعیت

```
Renderer Audit:              PASS
Assignment Layout:           PASS
Assignment Baseline:         PASS
Assignment Overflow:         PASS
Assignment RTL:              PASS
56 Cell Audit:               PASS
Date Card Audit:             PASS
PNG Geometry:                PASS
PDF Geometry:                PASS
Preview Match:               PASS
Static Pixel Preservation:   PASS
Cache:                       PASS
Performance:                 PASS

Tests Before:  449 passed + 3 skipped
Tests After:   485 passed + 0 skipped
Failed:        0
```

* ۳۳ تست جدید (`tests/test_assignment_layout.py`) + ۳ تستی که قبلاً به‌دلیل نبود
  Chromium skip می‌شد و حالا واقعاً اجرا می‌شود.
* هیچ تست قبلی حذف یا ضعیف نشد. تنها اصلاح در تست‌ها: هیچ‌کدام؛ فقط فایل جدید
  اضافه شد.

---

## ۲. ریشهٔ باگ گزارش‌شده (نه یک x/y اشتباه)

| # | ایراد | اثر روی برگه |
|---|---|---|
| ۱ | ناحیهٔ تکالیف با مختصات **حدسی** ثبت شده بود: `x=285 y=930 w=1150 h=62` → لبهٔ راست `1435` | پنل چاپ‌شده فقط تا `x=1267` است ⇒ متن **۱۶۸px بیرون کادر**، روی موج تزئینی |
| ۲ | قرارگیری عمودی با فرمول جادویی `y = top + (band − size×1.15)/2` و لنگر `anchor="ra"` (ascender) | هیچ ارتباطی با baseline فونت نداشت؛ خط سوم از کف کادر بیرون می‌زد |
| ۳ | چیپ چاپ‌شدهٔ «تکالیف» (تا `y=936`) در هیچ محاسبه‌ای دیده نمی‌شد | متن بلند از زیر عنوان رد می‌شد |
| ۴ | بک‌اند HTML اندازهٔ فونت را **داخل مرورگر** دوباره حساب می‌کرد (`FIT_SCRIPT`) | پیش‌نمایش و خروجی نهایی می‌توانستند واگرا شوند |
| ۵ | سلول‌ها هم با `anchor="ra"` و ارتفاع تخمینی `size×line_gap` چیده می‌شدند | مرکزیت عمودی تقریبی بود، نه بر پایهٔ جوهر واقعی |
| ۶ | کش فقط `template_version` را می‌دید | تغییر مختصات در JSON کش را باطل نمی‌کرد |
| ۷ | `_issue_from_id` پس از حذف اسکریپت مرورگر کد مرده شد | تست ممیزی خودمان گرفت و حذف شد |

---

## ۳. هندسهٔ نهایی — استخراج‌شده از پیکسل‌های خود تصویر

فایل: `assets/templates/weekly_plan_v2.png` (1536×1024، بدون هیچ resize/crop)

### ناحیهٔ تکالیف

| مؤلفه | x | y | w | h |
|---|---|---|---|---|
| **Outer panel** | 268 | 916 | 1000 | 83 |
| **Title chip** (مانع) | 650 | 884 | 234 | 53 |
| **Body / Text Area** | 287 | 919 | 961 | 78 |

```
Border:            top 917 · bottom 997 · left 269 · right 1267
Top dotted line:   y = 946  (ضخامت ۲px، از x 287 تا 1247)
Bottom dotted line:y = 973  (ضخامت ۲px)
```

### باندهای نوشتاری (کف هر باند = خط چاپ‌شده)

| باند | x | y | w | h | عرض قابل استفاده | baseline (سایز ۱۸) |
|---|---|---|---|---|---|---|
| ۱ | 287 | 919 | 961 | 26 | **358** (چیپ عنوان کم شده) | **938** |
| ۲ | 287 | 948 | 961 | 24 | 961 | **965** |
| ۳ | 287 | 975 | 961 | 22 | 961 | **990** |

```
Safe margin:      left 4 · right 4 · top 2 · bottom 2  (+ obstacle clearance 6)
baseline_gap:     1px  (فاصلهٔ جوهر تا خط چاپ‌شده)
Max lines:        3
Font:             Vazirmatn Medium
Max font size:    18
Min font size:    13   (زیر این حد ناخواناست؛ به‌جای کوچک‌تر کردن، خطا می‌دهیم)
Reference ink:    top −15 · bottom +6 · height 21  (در سایز ۱۸)
```

### نگاشت RTL (تأیید عددی)

```
slot 1: x 1110..1251   ← نزدیک‌ترین ستون به کارت روز (کارت از x=1328)
slot 2: x  956..1097
slot 3: x  802..943
slot 4: x  646..788
slot 5: x  493..632
slot 6: x  345..480
slot 7: x  197..332
slot 8: x   49..184
```

---

## ۴. مدل جدید رندر

```
Plan → normalize → order → wrap → fit → baseline → TextLine → (Pillow | Chromium)
                                    ↑
                        تنها منبع مختصات: compose.py + JSON
```

* **`app/rendering/compose.py`** (جدید) تنها جایی است که مختصات تولید می‌شود.
  هر دو بک‌اند دقیقاً همان لیست `TextLine` را می‌کشند ⇒ *Preview = Final*،
  *PNG = PDF*، *Pillow = Chromium*.
* **baseline واقعی**: `baseline = band.bottom − ink.bottom − baseline_gap`.
  `ink` از یک رشتهٔ مرجع ثابت (بلندترین صعود/نزول‌های فارسی + لاتین + ارقام)
  گرفته می‌شود؛ پس ریتم خطوط ثابت است و جوهر هرگز از باند بیرون نمی‌زند.
* **مانع‌آگاهی**: `obstacles` در JSON. باندی که با هنر چاپ‌شده تلاقی دارد عرض
  کمتری می‌گیرد؛ اگر متن جا نشود به باند بعدی می‌رود — نه روی چیپ.
* **چیدمان ترجیحی «یک تکلیف در هر خط»**؛ اگر تعداد از باندها بیشتر شد، حالت
  جریانی با جداکنندهٔ «—» و انتخاب **کمترین تعداد خط**.
* **سه وضعیت** `FIT / TIGHT / OVERFLOW`. فقط `OVERFLOW` جلوی تولید را می‌گیرد.
* **بدون Magic Number**: هر افست نام و مبدأ دارد (`baseline_gap`,
  `safe_margin`, `obstacle_clearance`, `rule_stroke`, `pad_x/pad_y`).
* **بدون Hard-code**: تست `test_no_hardcoded_coordinates_in_the_renderers`
  همچنان پاس است؛ رندرر Pillow حالا هیچ عددی از هندسه ندارد.

### بک‌اند HTML
متن به‌صورت **SVG `<text>`** با `x` = لبهٔ راست و `y` = baseline کشیده می‌شود،
نه با line-box مرورگر. بنابراین متریک داخلی Chromium در جای‌گذاری دخالت ندارد.
اسکریپت داخل مرورگر دیگر چیزی را تغییر نمی‌دهد؛ فقط اگر عرض اندازه‌گیری‌شده از
کادر بیشتر شود **هشدار لاگ** می‌دهد (آشکارساز واگرایی دو بک‌اند).

### کش
`plan_hash` حالا از `layout.cache_key = "<template_version>+<fingerprint>"`
ساخته می‌شود؛ `fingerprint` هش کل JSON هندسه است، پس تغییر یک مختصات هم کش را
باطل می‌کند. `renderer_version` هر دو بک‌اند به `2.0.0` رفت ⇒ کل کش قبلی باطل.

---

## ۵. نتایج ممیزی پیکسلی (stray = جوهر خارج از هر کادر اعلام‌شده)

| سناریو | stray | issues |
|---|---|---|
| برنامهٔ واقعی §46 (۴ روز + ۴ تکلیف) | **0** | 0 |
| تکالیف خالی | **0** | 0 |
| تکلیف کوتاه | **0** | 0 |
| تکلیف متوسط | **0** | 0 |
| تکلیف بلند (۷۵ نویسه) | **0** | 0 |
| چند تکلیف (۳ و ۵ مورد) | **0** | 0 |
| فارسی + ارقام فارسی | **0** | 0 |
| فارسی + انگلیسی (Biology Chapter 3) | **0** | 0 |
| اموجی (📚 ✅ ⚠️) | **0** | 0 |
| نویسه‌های خاص `+ - / : % () [] {} ، ؛ ؟ !` | **0** | 0 |
| **استرس: ۵۶ سلول پر + متن ترکیبی + اموجی + تکلیف بلند** | **0** | 0 |
| سرریز عمدی (۲۰ تکلیف بلند) | **0** | 1 (تشخیص داده شد) |

تطابق دو بک‌اند (جعبهٔ جوهر، پیکسل):

```
assign line 1   Pillow (713,919,1241,942)   Chromium (713,919,1242,942)
assign line 2   Pillow (1087,953,1242,969)  Chromium (1087,953,1242,969)
assign line 3   Pillow (1074,978,1242,994)  Chromium (1074,978,1242,993)
cell sat[0]     Pillow (1197,234,1244,297)  Chromium (1197,234,1243,297)
date سبت        Pillow (1381,282,1457,294)  Chromium (1381,282,1457,294)
```
حداکثر اختلاف: **۱ پیکسل** (رسترایزر).

---

## ۶. رفتار خطا برای کاربر (§۴۳/۴۴)

وقتی متن جا نمی‌شود:

```
✅ تکالیف ذخیره شد.

⚠️ تکالیف بیش از ظرفیت قالب است.

متن فعلی از فضای قابل استفادهٔ بخش تکالیف بیشتر است و همهٔ آن روی برگه
چاپ نمی‌شود. لطفاً آن را کوتاه‌تر کنید.

[ ✏️ ویرایش تکالیف ]
[ ⬅️ بازگشت ]
```

و در مرحلهٔ «تأیید و تولید»، `report.ok == False` جلوی تولید فایل را می‌گیرد
(رفتار قبلی، حالا با پیام دقیق‌تر). دکمهٔ ویرایش به هندلر واقعی
(`AssignCB(action="open")`) وصل است — نه یک callback بی‌هندلر.

---

## ۷. عملکرد (بدون رگرسیون)

| عملیات | قبل | بعد |
|---|---|---|
| PNG برنامهٔ خالی | 1215.9 ms | 1208.4 ms |
| PNG با ۵۶ سلول پر | 1373.4 ms | 1387.1 ms |
| PNG با ۵۶ سلول + تکالیف بلند | 1405.1 ms | 1423.2 ms |
| `validate()` روی ۵۶ سلول | 46.4 ms | 47.6 ms |
| PDF از رستر ۲× (۳۰۰ DPI) | 280.0 ms | 282.8 ms |

اختلاف‌ها در حد نویز (<۱.۵٪). فونت‌ها همچنان با `lru_cache` کش می‌شوند و
متریک جوهر هم کش‌شده است (`ink_metrics`, maxsize=512).

---

## ۸. فایل‌های تغییر یافته / جدید

**جدید**
```
app/rendering/compose.py              لایهٔ چیدمان مشترک (تنها منبع مختصات)
tests/test_assignment_layout.py       ۳۳ تست ممیزی
tests/goldens/*.png + goldens.json    ۴ Golden Image نسخه‌دار
tools/ink_audit.py                    ممیزی پیکسلی جوهر
tools/goldens.py                      بازتولید Goldenها
```

**تغییر یافته**
```
config/template_weekly_v2.json        هندسهٔ اندازه‌گیری‌شدهٔ تکالیف + obstacles
app/rendering/fit.py                  متریک جوهر، place_in_bands، place_in_box، FIT/TIGHT/OVERFLOW
app/rendering/layout.py               outer/title/body/bands/obstacles/usable_widths/fingerprint
app/rendering/assignments.py          فقط نرمال‌سازی و شماره‌گذاری
app/rendering/pillow_renderer.py      بازنویسی روی Composition (بدون هندسه)
app/rendering/html_renderer.py        SVG با baseline دقیق، حذف refit مرورگر
app/rendering/templates/weekly_plan.html.j2
app/services/plan_service.py          کش بر پایهٔ cache_key
app/bot/{texts,keyboards}.py + handlers/advisor.py   پیام و دکمهٔ سرریز تکالیف
tools/calibrate.py                    زیردستور assignment (Debug Overlay)
REVIEW.md · VERSION · app/__init__.py
```

**دست‌نخورده**: `assets/templates/weekly_plan_v1.png`، `config/template_weekly_v1.json`
و کل مسیر v1 (بندها از روی `rules` مشتق می‌شوند؛ تست‌های v1 بدون تغییر پاس‌اند).

---

## ۹. خروجی‌های بصری

```
out/audit/real-example.png / .pdf     برنامهٔ دقیق §۴۶
out/audit/assignment-{empty,short,medium,long,multiline,persian,mixed,emoji,overflow}.png
out/audit/stress-56.png               ۵۶ سلول پر + متن ترکیبی + اموجی
out/audit/html-v2.png                 خروجی بک‌اند Chromium
out/calibration/rotbeland-weekly-v2-assignment.png   اورلی دیباگ
tests/goldens/*.png                   Golden Imageها
```

---

## ۱۰. ابزارها

```bash
python -m tools.calibrate assignment      # اورلی: outer/title/body/باند/baseline/جعبهٔ جوهر
python -m tools.calibrate probe           # پروب معنایی ۵۶ سلول
python -m tools.ink_audit                 # ممیزی پیکسلی جوهر
python -m tools.goldens                   # بازتولید Golden (فقط با تغییر عمدی قالب)
```

---

## ۱۱. آنچه عمداً انجام نشد

* **ZIP / Release جدید ساخته نشد** — طبق دستور صریح شما.
* **بازچینش خودکار متن بین چند خط برای زیبایی** (balanced wrap): الگوریتم
  کمترین تعداد خط را انتخاب می‌کند؛ پخش‌کردن یک متن کوتاه روی سه خط ارزش بصری
  ندارد و ریسک ناهماهنگی با انتظار مشاور دارد.
* **کوچک‌کردن فونت زیر ۱۳**: عمداً ممنوع؛ به‌جای آن خطای قابل‌فهم به کاربر.
* **بریدن (clip) متن**: در هیچ حالتی متن بی‌اطلاع کاربر بریده نمی‌شود.
