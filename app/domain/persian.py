"""Persian text helpers.

Date logic lives in `app.domain.calendar.JalaliDate`; the thin wrappers below
keep the older call sites working while delegating to that single engine.
"""
from __future__ import annotations

from datetime import date, datetime

from .calendar import (  # noqa: F401  (re-exported for existing imports)
    FA_DIGITS,
    JALALI_MONTHS,
    TIMEZONE,
    WEEKDAY_FA,
    WEEKDAY_KEYS,
    JalaliDate,
)


__all__ = [
    "FA_DIGITS", "JALALI_MONTHS", "TIMEZONE", "WEEKDAY_FA", "WEEKDAY_KEYS",
    "JalaliDate", "now_local", "today_local", "to_fa_digits", "to_en_digits",
    "apply_digit_style", "to_jalali", "jalali_short", "jalali_day_month",
    "week_label", "jalali_to_gregorian", "parse_jalali", "saturday_of",
    "normalize_fa", "shape_rtl",
]


def now_local() -> datetime:
    return JalaliDate.now()


def today_local() -> date:
    return JalaliDate.today()


def to_fa_digits(text: str) -> str:
    return JalaliDate.fa(text)


def to_en_digits(text: str) -> str:
    return JalaliDate.en_digits(text)


def apply_digit_style(text: str, style: str = "fa") -> str:
    return to_fa_digits(text) if style == "fa" else to_en_digits(text)


def to_jalali(value: date):
    return JalaliDate.to_jalali(value)


def jalali_short(value: date, digits: str = "fa") -> str:
    out = JalaliDate.short(value)
    return out if digits == "fa" else to_en_digits(out)


def jalali_day_month(value: date, digits: str = "fa") -> str:
    out = JalaliDate.day_month(value)
    return out if digits == "fa" else to_en_digits(out)


def week_label(start: date, end: date, digits: str = "fa") -> str:
    out = JalaliDate.range_label(start, end)
    return out if digits == "fa" else to_en_digits(out)


def jalali_to_gregorian(year: int, month: int, day: int) -> date:
    return JalaliDate.from_jalali(year, month, day)


def parse_jalali(text: str) -> date:
    from .calendar import DateRangeError

    try:
        return JalaliDate.parse(text)
    except DateRangeError as exc:
        raise ValueError(str(exc)) from exc


def saturday_of(value: date) -> date:
    return JalaliDate.saturday_of(value)


def normalize_fa(text: str) -> str:
    """Arabic ي/ك → Persian ی/ک, collapse whitespace, strip zero-width junk."""
    if not text:
        return ""
    table = str.maketrans({"ي": "ی", "ك": "ک", "\u200f": "", "\u200e": "", "\ufeff": ""})
    return " ".join(text.translate(table).split())


def shape_rtl(text: str) -> str:
    """Fallback bidi shaping for engines without HarfBuzz/Raqm."""
    import arabic_reshaper
    from bidi.algorithm import get_display

    return get_display(arabic_reshaper.reshape(text))
