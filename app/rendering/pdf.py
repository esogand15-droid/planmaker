"""PDF export — print ready, visually identical to the PNG.

Strategy: the plan is rendered once at print scale (default 2x → ~300 DPI on A4
landscape) and that exact raster is placed on an A4 page with the aspect ratio
preserved (no stretching, no cropping). Because PNG and PDF come from the same
RenderResult, the two outputs can never drift apart.
"""
from __future__ import annotations

import io

from PIL import Image

A4_LANDSCAPE_MM = (297.0, 210.0)
A4_PORTRAIT_MM = (210.0, 297.0)
MM_PER_INCH = 25.4


def png_to_pdf(
    png_bytes: bytes,
    *,
    dpi: int = 300,
    orientation: str = "landscape",
    margin_mm: float = 6.0,
    background: tuple[int, int, int] = (255, 255, 255),
) -> bytes:
    """Place `png_bytes` centered on an A4 page at `dpi`, keeping aspect ratio."""
    page_mm = A4_LANDSCAPE_MM if orientation == "landscape" else A4_PORTRAIT_MM
    page_px = (
        round(page_mm[0] / MM_PER_INCH * dpi),
        round(page_mm[1] / MM_PER_INCH * dpi),
    )
    margin_px = round(margin_mm / MM_PER_INCH * dpi)

    src = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    avail = (page_px[0] - 2 * margin_px, page_px[1] - 2 * margin_px)
    ratio = min(avail[0] / src.width, avail[1] / src.height)
    target = (max(1, round(src.width * ratio)), max(1, round(src.height * ratio)))
    resample = Image.LANCZOS if ratio < 1 else Image.BICUBIC
    fitted = src.resize(target, resample) if target != src.size else src

    page = Image.new("RGB", page_px, background)
    page.paste(fitted, ((page_px[0] - target[0]) // 2, (page_px[1] - target[1]) // 2))

    buf = io.BytesIO()
    page.save(buf, format="PDF", resolution=float(dpi), quality=100)
    return buf.getvalue()
