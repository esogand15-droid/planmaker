"""Template calibration tool (developer/admin utility).

Usage:
  python -m tools.calibrate grid            # overlay grid + coordinates
  python -m tools.calibrate probe           # re-detect lines from the template
  python -m tools.calibrate fill            # fill every cell with sample text
  python -m tools.calibrate nudge cells dx dy      # shift all cell boxes
  python -m tools.calibrate nudge dates dx dy      # shift all date boxes
  python -m tools.calibrate set assignments x y w h

Outputs land in out/ so coordinates can be verified visually before saving.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.models import WEEKDAY_KEYS, Activity, Assignment, WeeklyPlan  # noqa: E402
from app.domain.persian import jalali_to_gregorian  # noqa: E402
from app.rendering.fit import load_font  # noqa: E402
from app.rendering.layout import TemplateLayout  # noqa: E402
from app.rendering.pillow_renderer import PillowRenderer  # noqa: E402

OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)


def cmd_grid(layout: TemplateLayout) -> None:
    img = Image.open(layout.template_path).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    font = load_font(str(layout.font_path("regular")), 11)
    for weekday in WEEKDAY_KEYS:
        for slot, box in enumerate(layout.cells(weekday)):
            draw.rectangle(box.as_tuple(), outline=(220, 30, 60, 200), width=1)
            draw.text((box.x + 3, box.y + 2), f"{slot + 1}", font=font, fill=(220, 30, 60))
            draw.text((box.x + 3, box.y + 14), f"{box.x},{box.y}", font=font,
                      fill=(120, 120, 160))
        d = layout.date_box(weekday)
        draw.rectangle(d.as_tuple(), outline=(255, 200, 0, 220), width=1)
    a = layout.assignments_box
    draw.rectangle(a.as_tuple(), outline=(0, 120, 255, 220), width=1)
    for y in layout.assignments_cfg.get("rules", []):
        draw.line((a.x, y, a.right, y), fill=(0, 200, 120, 220), width=1)
    path = OUT / "calibration_grid.png"
    img.save(path)
    print(f"→ {path}")


def cmd_probe(layout: TemplateLayout) -> None:
    """Detect grid lines straight from the template pixels (source of truth)."""
    a = np.asarray(Image.open(layout.template_path).convert("RGB")).astype(int)
    lum = a.sum(2) / 3
    dark = lum < 170
    g = layout.grid
    y0, y1 = g["y_lines"][0] - 30, g["y_lines"][-1] + 30
    cols = dark[y0:y1, :].sum(0)
    rows = dark[:, g["x_lines"][0] + 10:g["x_lines"][-1] - 10].sum(1)
    x_lines = _peaks([int(c) for c in cols], threshold=(y1 - y0) * 0.6)
    y_lines = _peaks([int(r) for r in rows], threshold=(g["x_lines"][-1] - g["x_lines"][0]) * 0.6)
    print("detected x lines:", x_lines)
    print("config   x lines:", g["x_lines"])
    print("detected y lines:", y_lines)
    print("config   y lines:", g["y_lines"])


def _peaks(profile: list[int], threshold: float) -> list[int]:
    hits = [i for i, v in enumerate(profile) if v > threshold]
    out: list[int] = []
    for i in hits:
        if not out or i - out[-1] > 4:
            out.append(i)
    return out


def cmd_fill(layout: TemplateLayout) -> None:
    """Worst case: every cell full, to eyeball padding and overflow behaviour."""
    plan = WeeklyPlan(student_name="تست کالیبراسیون", student_id="0")
    plan.apply_week_start(jalali_to_gregorian(1405, 5, 25))
    for weekday in WEEKDAY_KEYS:
        day = plan.day(weekday)
        for i in range(8):
            day.set_slot(i, Activity(i, subject="زیست‌شناسی", topic="فصل ۳ گوارش",
                                     description="مطالعه + ۴۰ تست", duration="۹۰ دقیقه"))
    for i in range(6):
        plan.assignments.append(Assignment(text=f"تکلیف نمونه شماره {i + 1}", order=i))
    renderer = PillowRenderer(layout)
    res = renderer.render_png(plan)
    path = OUT / "calibration_fill.png"
    path.write_bytes(res.png)
    print(f"→ {path}  issues={len(res.issues)}")
    for issue in res.issues:
        print("   ⚠", issue.human())


def cmd_nudge(layout: TemplateLayout, target: str, dx: int, dy: int) -> None:
    data = layout.raw()
    if target == "cells":
        for weekday in data["cells"]:
            for c in data["cells"][weekday]:
                c["x"] += dx
                c["y"] += dy
    elif target == "dates":
        for weekday in data["date_boxes"]:
            data["date_boxes"][weekday]["x"] += dx
            data["date_boxes"][weekday]["y"] += dy
    else:
        raise SystemExit("target must be cells|dates")
    layout.save()
    print(f"moved {target} by ({dx},{dy}) → {layout.config_path}")


def cmd_set_assignments(layout: TemplateLayout, x: int, y: int, w: int, h: int) -> None:
    data = layout.raw()
    data["assignments"].update({"x": x, "y": y, "w": w, "h": h})
    layout.save()
    print("assignments box updated")


def main() -> None:
    args = sys.argv[1:] or ["grid"]
    layout = TemplateLayout.load()
    cmd = args[0]
    if cmd == "grid":
        cmd_grid(layout)
    elif cmd == "probe":
        cmd_probe(layout)
    elif cmd == "fill":
        cmd_fill(layout)
    elif cmd == "nudge":
        cmd_nudge(layout, args[1], int(args[2]), int(args[3]))
    elif cmd == "set" and args[1] == "assignments":
        cmd_set_assignments(layout, *(int(v) for v in args[2:6]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
