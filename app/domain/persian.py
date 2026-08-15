"""Persian text / date utilities: digits, Jalali conversion, RTL shaping fallback."""
from __future__ import annotations

from datetime import date

import jdatetime

FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
EN_DIGITS = "0123456789"
_TO_FA = str.maketrans(EN_DIGITS, FA_DIGITS)
_TO_EN = str.maketrans(FA_DIGITS + "٠١٢٣٤٥٦٧٨٩", EN_DIGITS + EN_DIGITS)

JALALI_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]


def to_fa_digits(text: str) -> str:
    return text.translate(_TO_FA)


def to_en_digits(text: str) -> str:
    return text.translate(_TO_EN)


def apply_digit_style(text: str, style: str) -> str:
    return to_fa_digits(text) if style == "fa" else to_en_digits(text)


def to_jalali(d: date) -> jdatetime.date:
    return jdatetime.date.fromgregorian(date=d)


def jalali_short(d: date, digits: str = "fa") -> str:
    """1405/05/25 → ۱۴۰۵/۰۵/۲۵"""
    j = to_jalali(d)
    return apply_digit_style(f"{j.year:04d}/{j.month:02d}/{j.day:02d}", digits)


def jalali_day_month(d: date, digits: str = "fa") -> str:
    """25 مرداد"""
    j = to_jalali(d)
    return apply_digit_style(f"{j.day} {JALALI_MONTHS[j.month - 1]}", digits)


def week_label(start: date, end: date, digits: str = "fa") -> str:
    js, je = to_jalali(start), to_jalali(end)
    if js.month == je.month:
        return apply_digit_style(
            f"{js.day} تا {je.day} {JALALI_MONTHS[je.month - 1]} {je.year}", digits
        )
    return apply_digit_style(
        f"{js.day} {JALALI_MONTHS[js.month - 1]} تا {je.day} {JALALI_MONTHS[je.month - 1]} {je.year}",
        digits,
    )


def jalali_to_gregorian(year: int, month: int, day: int) -> date:
    return jdatetime.date(year, month, day).togregorian()


def parse_jalali(text: str) -> date:
    """Accepts 1405/05/25, ۱۴۰۵-۰۵-۲۵, 1405 5 25."""
    cleaned = to_en_digits(text.strip()).replace("-", "/").replace(".", "/").replace(" ", "/")
    parts = [p for p in cleaned.split("/") if p]
    if len(parts) != 3:
        raise ValueError("invalid jalali date")
    y, m, d = (int(p) for p in parts)
    return jalali_to_gregorian(y, m, d)


def saturday_of(d: date) -> date:
    """Start of the Iranian week (Saturday) containing `d`."""
    # python weekday(): Mon=0 .. Sat=5, Sun=6
    offset = (d.weekday() - 5) % 7
    from datetime import timedelta

    return d - timedelta(days=offset)


def normalize_fa(text: str) -> str:
    """Arabic ي/ك → Persian ی/ک, collapse whitespace, strip zero-width junk."""
    if not text:
        return ""
    table = str.maketrans({"ي": "ی", "ك": "ک", "\u200f": "", "\u200e": "", "\ufeff": ""})
    out = text.translate(table)
    return " ".join(out.split())


def shape_rtl(text: str) -> str:
    """Fallback bidi shaping for engines without HarfBuzz/Raqm.

    Pillow built with libraqm shapes natively — in that case this must NOT be
    used (double shaping breaks the text). The renderer decides.
    """
    import arabic_reshaper
    from bidi.algorithm import get_display

    return get_display(arabic_reshaper.reshape(text))
