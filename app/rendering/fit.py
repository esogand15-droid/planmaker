"""Smart text fitting: wrapping + auto font-size + overflow detection.

Shared by every renderer backend so that the overflow warnings the consultant
sees in the bot match exactly what would be drawn.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from .layout import Box


@lru_cache(maxsize=256)
def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    """Raqm layout engine = real HarfBuzz shaping for Persian (joined letters)."""
    try:
        return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)
    except Exception:  # pragma: no cover - Pillow without libraqm
        return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)


def raqm_available() -> bool:
    from PIL import features

    return bool(features.check("raqm"))


def text_width(text: str, font: ImageFont.FreeTypeFont) -> float:
    if not text:
        return 0.0
    return font.getlength(text, direction="rtl", language="fa")


@dataclass
class FitResult:
    lines: list[str]
    font_size: int
    overflow: bool
    reason: str = ""
    used_height: int = 0

    @property
    def ok(self) -> bool:
        return not self.overflow


def wrap_line(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    """Word wrap with hard character break for unbreakable tokens."""
    text = text.strip()
    if not text:
        return []
    if text_width(text, font) <= max_width:
        return [text]

    out: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}".strip()
        if text_width(candidate, font) <= max_width:
            current = candidate
            continue
        if current:
            out.append(current)
            current = ""
        if text_width(word, font) <= max_width:
            current = word
            continue
        # hard break a single oversized token
        chunk = ""
        for ch in word:
            if text_width(chunk + ch, font) <= max_width:
                chunk += ch
            else:
                if chunk:
                    out.append(chunk)
                chunk = ch
        current = chunk
    if current:
        out.append(current)
    return out


def fit_text(
    source_lines: list[str],
    box: Box,
    font_path: str | Path,
    *,
    max_size: int,
    min_size: int,
    line_gap: float = 1.3,
    pad_x: int = 6,
    pad_y: int = 4,
    max_lines: int | None = None,
) -> FitResult:
    """Find the largest font size at which all text fits inside `box`."""
    font_path = str(font_path)
    lines_in = [ln.strip() for ln in source_lines if ln and ln.strip()]
    if not lines_in:
        return FitResult(lines=[], font_size=max_size, overflow=False)

    avail_w = box.w - 2 * pad_x
    avail_h = box.h - 2 * pad_y
    if avail_w <= 4 or avail_h <= 4:
        return FitResult(lines_in, min_size, True, "cell too small")

    last: FitResult | None = None
    for size in range(max_size, min_size - 1, -1):
        font = load_font(font_path, size)
        wrapped: list[str] = []
        for ln in lines_in:
            wrapped.extend(wrap_line(ln, font, avail_w))
        step = size * line_gap
        used_h = int(round(step * len(wrapped)))
        too_many = max_lines is not None and len(wrapped) > max_lines
        last = FitResult(wrapped, size, False, "", used_h)
        if used_h <= avail_h and not too_many:
            return last

    # did not fit even at min_size
    result = last or FitResult(lines_in, min_size, True, "overflow")
    result.overflow = True
    result.reason = "متن برای این بخش طولانی است"
    return result
