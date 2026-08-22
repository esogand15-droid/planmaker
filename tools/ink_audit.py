"""Ink audit: where does dynamic text actually land, pixel by pixel?

The renderer output is diffed against the untouched template; every changed
pixel is "ink". The ink is then attributed to the region it should belong to
(cell / date / assignment line) and anything outside a declared bounding box is
reported. Used by the tests and by hand during calibration.
"""
from __future__ import annotations

import io
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.models import WEEKDAY_KEYS, WeeklyPlan  # noqa: E402
from app.rendering.layout import Box, TemplateLayout  # noqa: E402

DIFF_THRESHOLD = 12


def ink_mask(rendered: Image.Image, template: Image.Image) -> np.ndarray:
    a = np.asarray(rendered.convert("RGB")).astype(int)
    b = np.asarray(template.convert("RGB")).astype(int)
    return np.abs(a - b).sum(2) > DIFF_THRESHOLD


def ink_bbox(mask: np.ndarray, box: Box) -> tuple[int, int, int, int] | None:
    """Bounding box of ink inside `box`, in absolute coordinates."""
    patch = mask[box.y:box.bottom, box.x:box.right]
    ys, xs = np.nonzero(patch)
    if not len(ys):
        return None
    return (box.x + int(xs.min()), box.y + int(ys.min()),
            box.x + int(xs.max()), box.y + int(ys.max()))


@dataclass
class AuditReport:
    stray_pixels: int = 0
    regions: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems and self.stray_pixels == 0


def audit(plan: WeeklyPlan, layout: TemplateLayout, png: bytes) -> AuditReport:
    rendered = Image.open(io.BytesIO(png)).convert("RGB")
    template = Image.open(layout.template_path).convert("RGB")
    mask = ink_mask(rendered, template)
    report = AuditReport()

    allowed = np.zeros_like(mask)
    for day in WEEKDAY_KEYS:
        for box in layout.cells(day):
            allowed[box.y:box.bottom, box.x:box.right] = True
        d = layout.date_box(day)
        pad = layout.date_mask.get("pad", 3) + 1
        allowed[d.y - pad:d.bottom + pad, d.x - pad:d.right + pad] = True
    for band in layout.assignment_line_boxes():
        allowed[band.y:band.bottom, band.x:band.right] = True

    stray = mask & ~allowed
    report.stray_pixels = int(stray.sum())
    if report.stray_pixels:
        ys, xs = np.nonzero(stray)
        report.problems.append(
            f"{report.stray_pixels} ink pixels outside every declared box "
            f"(x {xs.min()}..{xs.max()}, y {ys.min()}..{ys.max()})"
        )

    # per-region attribution
    for day in WEEKDAY_KEYS:
        for i, box in enumerate(layout.cells(day)):
            bb = ink_bbox(mask, box)
            if bb:
                report.regions.append(f"cell {day}[{i}] ink={bb} box={box.as_tuple()}")
    for i, band in enumerate(layout.assignment_line_boxes()):
        bb = ink_bbox(mask, band)
        if bb:
            report.regions.append(f"assign[{i}] ink={bb} band={band.as_tuple()}")
    return report


def main() -> int:
    from app.rendering.pillow_renderer import PillowRenderer
    from app.rendering.registry import load_layout
    from tools.demo_plan import build_demo_plan  # type: ignore

    layout = load_layout(None)
    plan = build_demo_plan()
    rep = audit(plan, layout, PillowRenderer(layout).render_png(plan).png)
    for line in rep.regions:
        print(line)
    print("stray:", rep.stray_pixels)
    for p in rep.problems:
        print("PROBLEM:", p)
    return 0 if rep.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
