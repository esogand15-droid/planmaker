"""Jalali calendar engine — the single source of truth for every date in the app.

No handler, service or keyboard may compute weekdays, ranges or formats on its
own. Two planning modes exist and are kept strictly apart:

* **calendar week** — Saturday → Friday, the classic Iranian study week;
* **custom range** — any start/end pair; the weekday of every day is derived
  from the real calendar, never from its position in a list.

`1405/05/26` is a Sunday, so in a custom range starting there, day #1 is
یکشنبه — not شنبه.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import jdatetime

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Asia/Tehran"))

#: python's weekday(): Mon=0 … Sat=5, Sun=6 → our Saturday-first week index
_PY_TO_INDEX = {5: 0, 6: 1, 0: 2, 1: 3, 2: 4, 3: 5, 4: 6}

WEEKDAY_KEYS = (
    "saturday", "sunday", "monday", "tuesday", "wednesday", "thursday", "friday",
)
WEEKDAY_FA = {
    "saturday": "شنبه",
    "sunday": "یکشنبه",
    "monday": "دوشنبه",
    "tuesday": "سه‌شنبه",
    "wednesday": "چهارشنبه",
    "thursday": "پنج‌شنبه",
    "friday": "جمعه",
}
JALALI_MONTHS = (
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
)

FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_TO_FA = str.maketrans("0123456789", FA_DIGITS)
_TO_EN = str.maketrans(FA_DIGITS + "٠١٢٣٤٥٦٧٨٩", "0123456789" * 2)

#: a plan may not span more than one template width (7 day rows)
MAX_PLAN_RANGE_DAYS = int(os.getenv("MAX_PLAN_RANGE_DAYS", "7"))


class DateRangeError(ValueError):
    """User-facing problem with a chosen range."""


@dataclass(frozen=True)
class PlanDate:
    """One concrete day of a plan: its date, real weekday and position."""

    index: int          # 1-based position inside the range
    date: date
    weekday_key: str    # saturday…friday, derived from the real calendar

    @property
    def weekday_fa(self) -> str:
        return WEEKDAY_FA[self.weekday_key]

    @property
    def label(self) -> str:
        """«یکشنبه ۲۶ مرداد ۱۴۰۵»"""
        return f"{self.weekday_fa} {JalaliDate.long(self.date)}"

    @property
    def short(self) -> str:
        """«۲۶ مرداد»"""
        return JalaliDate.day_month(self.date)


class JalaliDate:
    """Stateless helpers; everything here is Tehran-local by definition."""

    # ── now / today ──────────────────────────────────────────────────
    @staticmethod
    def now() -> datetime:
        return datetime.now(TIMEZONE)

    @staticmethod
    def today() -> date:
        return JalaliDate.now().date()

    # ── conversion ───────────────────────────────────────────────────
    @staticmethod
    def to_jalali(value: date) -> jdatetime.date:
        return jdatetime.date.fromgregorian(date=value)

    @staticmethod
    def from_jalali(year: int, month: int, day: int) -> date:
        return jdatetime.date(year, month, day).togregorian()

    # ── weekday ──────────────────────────────────────────────────────
    @staticmethod
    def weekday_index(value: date) -> int:
        """0 = Saturday … 6 = Friday, computed from the real calendar."""
        return _PY_TO_INDEX[value.weekday()]

    @staticmethod
    def weekday_key(value: date) -> str:
        return WEEKDAY_KEYS[JalaliDate.weekday_index(value)]

    @staticmethod
    def weekday_fa(value: date) -> str:
        return WEEKDAY_FA[JalaliDate.weekday_key(value)]

    # ── arithmetic ───────────────────────────────────────────────────
    @staticmethod
    def add_days(value: date, days: int) -> date:
        return value + timedelta(days=days)

    @staticmethod
    def saturday_of(value: date) -> date:
        """Start of the Iranian week containing `value`."""
        return value - timedelta(days=JalaliDate.weekday_index(value))

    @staticmethod
    def week_range(anchor: date | None = None, offset_weeks: int = 0) -> list[PlanDate]:
        """Calendar-week mode: Saturday → Friday."""
        start = JalaliDate.saturday_of(anchor or JalaliDate.today())
        start = start + timedelta(days=7 * offset_weeks)
        return JalaliDate.range(start, start + timedelta(days=6))

    # ── ranges ───────────────────────────────────────────────────────
    @staticmethod
    def validate_range(start: date, end: date, max_days: int | None = None) -> int:
        limit = max_days or MAX_PLAN_RANGE_DAYS
        if end < start:
            raise DateRangeError("تاریخ پایان نمی‌تواند قبل از تاریخ شروع باشد.")
        days = (end - start).days + 1
        if days > limit:
            raise DateRangeError(
                f"حداکثر طول بازه {JalaliDate.fa(limit)} روز است "
                f"(بازه انتخابی: {JalaliDate.fa(days)} روز)."
            )
        return days

    @staticmethod
    def range(start: date, end: date, max_days: int | None = None) -> list[PlanDate]:
        """Chronological list of real days — the backbone of custom ranges."""
        days = JalaliDate.validate_range(start, end, max_days)
        return [
            PlanDate(
                index=i + 1,
                date=start + timedelta(days=i),
                weekday_key=JalaliDate.weekday_key(start + timedelta(days=i)),
            )
            for i in range(days)
        ]

    @staticmethod
    def is_calendar_week(days: list[PlanDate]) -> bool:
        return (
            len(days) == 7
            and days[0].weekday_key == "saturday"
            and days[-1].weekday_key == "friday"
        )

    # ── formatting ───────────────────────────────────────────────────
    @staticmethod
    def fa(value) -> str:
        return str(value).translate(_TO_FA)

    @staticmethod
    def en_digits(text: str) -> str:
        return text.translate(_TO_EN)

    @staticmethod
    def short(value: date) -> str:
        """۱۴۰۵/۰۵/۲۶"""
        j = JalaliDate.to_jalali(value)
        return JalaliDate.fa(f"{j.year:04d}/{j.month:02d}/{j.day:02d}")

    @staticmethod
    def day_month(value: date) -> str:
        """۲۶ مرداد"""
        j = JalaliDate.to_jalali(value)
        return JalaliDate.fa(f"{j.day} {JALALI_MONTHS[j.month - 1]}")

    @staticmethod
    def long(value: date) -> str:
        """۲۶ مرداد ۱۴۰۵"""
        j = JalaliDate.to_jalali(value)
        return JalaliDate.fa(f"{j.day} {JALALI_MONTHS[j.month - 1]} {j.year}")

    @staticmethod
    def range_label(start: date, end: date) -> str:
        """«۲۶ تا ۲۹ مرداد ۱۴۰۵» / «۳۰ مرداد تا ۲ شهریور ۱۴۰۵»"""
        js, je = JalaliDate.to_jalali(start), JalaliDate.to_jalali(end)
        if start == end:
            return JalaliDate.long(start)
        if js.year == je.year and js.month == je.month:
            return JalaliDate.fa(
                f"{js.day} تا {je.day} {JALALI_MONTHS[je.month - 1]} {je.year}"
            )
        if js.year == je.year:
            return JalaliDate.fa(
                f"{js.day} {JALALI_MONTHS[js.month - 1]} تا "
                f"{je.day} {JALALI_MONTHS[je.month - 1]} {je.year}"
            )
        return f"{JalaliDate.long(start)} تا {JalaliDate.long(end)}"

    # ── parsing ──────────────────────────────────────────────────────
    @staticmethod
    def parse(text: str) -> date:
        """Accepts 1405/05/26, ۱۴۰۵-۰۵-۲۶, «1405 5 26»."""
        cleaned = (
            JalaliDate.en_digits(text.strip())
            .replace("-", "/").replace(".", "/").replace(" ", "/")
        )
        parts = [p for p in cleaned.split("/") if p]
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise DateRangeError("قالب تاریخ درست نیست. نمونه: ۱۴۰۵/۰۵/۲۶")
        year, month, day = (int(p) for p in parts)
        try:
            return JalaliDate.from_jalali(year, month, day)
        except Exception as exc:  # invalid day/month combination
            raise DateRangeError("چنین تاریخی در تقویم وجود ندارد.") from exc

    @staticmethod
    def parse_range(text: str) -> tuple[date, date]:
        """«1405/05/26 - 1405/05/29» or two dates on separate lines."""
        raw = text.replace("تا", "-").replace("،", "-").replace("\n", "-")
        chunks = [c.strip() for c in raw.split("-") if c.strip()]
        # a single date may itself contain dashes; re-join in pairs of three
        if len(chunks) == 2:
            return JalaliDate.parse(chunks[0]), JalaliDate.parse(chunks[1])
        if len(chunks) == 6:
            return (
                JalaliDate.parse("/".join(chunks[:3])),
                JalaliDate.parse("/".join(chunks[3:])),
            )
        raise DateRangeError(
            "دو تاریخ لازم است. نمونه:\n<code>۱۴۰۵/۰۵/۲۶ تا ۱۴۰۵/۰۵/۲۹</code>"
        )
