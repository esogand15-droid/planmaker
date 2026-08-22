"""Template calibration tool (developer/admin utility).

Usage (add --template <version|config> to target a specific sheet):
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
from app.rendering.registry import DEFAULT_TEMPLATE, available, load_layout  # noqa: E402
from app.rendering.pillow_renderer import PillowRenderer  # noqa: E402

OUT = ROOT / "out" / "calibration"
OUT.mkdir(parents=True, exist_ok=True)


def cmd_grid(layout: TemplateLayout) -> None:
    """Overlay every configured region so the coordinates can be eyeballed."""
    img = Image.open(layout.template_path).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    font = load_font(str(layout.font_path("regular")), 11)
    for row, weekday in enumerate(WEEKDAY_KEYS):
        for slot, box in enumerate(layout.cells(weekday)):
            draw.rectangle(box.as_tuple(), outline=(220, 30, 60, 200), width=1)
            draw.text((box.x + 3, box.y + 2), f"r{row + 1}s{slot + 1}", font=font,
                      fill=(220, 30, 60))
            draw.text((box.x + 3, box.y + 15), f"{box.x},{box.y}", font=font,
                      fill=(120, 120, 160))
        d = layout.date_box(weekday)
        draw.rectangle(d.as_tuple(), outline=(255, 170, 0, 230), width=1)
        draw.text((d.x, d.y - 12), f"date {d.x},{d.y}", font=font, fill=(200, 120, 0))
        card = layout.day_card(weekday)
        if card:
            draw.rectangle(card.as_tuple(), outline=(0, 180, 90, 160), width=1)
        name = layout.day_name_box(weekday)
        if name:
            draw.rectangle(name.as_tuple(), outline=(140, 0, 200, 160), width=1)
    a = layout.assignments_box
    draw.rectangle(a.as_tuple(), outline=(0, 120, 255, 220), width=1)
    for y in layout.assignments_cfg.get("rules", []):
        draw.line((a.x, y, a.right, y), fill=(0, 200, 120, 220), width=1)
    draw.text((a.x, a.y - 14), f"assignments {a.x},{a.y} {a.w}x{a.h}", font=font,
              fill=(0, 90, 200))
    path = OUT / f"{layout.version}-grid.png"
    img.save(path)
    print(f"→ {path}")


def cmd_probe(layout: TemplateLayout) -> None:
    """Re-measure the sheet from its own pixels and compare with the config."""
    img = Image.open(layout.template_path).convert("RGB")
    a = np.asarray(img).astype(int)
    grid = layout.grid
    cols = grid.get("column_bounds")
    rows = grid.get("row_bounds")

    if cols and rows:                     # v2-style config: explicit bounds
        print(f"  canvas: {img.size[0]}×{img.size[1]} (config "
              f"{layout.width}×{layout.height})")
        _verify_boxes(a, layout)
        return

    # v1-style config: derive the lines from the drawn grid
    lum = a.sum(2) / 3
    dark = lum < 170
    y0, y1 = grid["y_lines"][0] - 30, grid["y_lines"][-1] + 30
    cols_profile = dark[y0:y1, :].sum(0)
    rows_profile = dark[:, grid["x_lines"][0] + 10:grid["x_lines"][-1] - 10].sum(1)
    x_lines = _peaks([int(c) for c in cols_profile], threshold=(y1 - y0) * 0.6)
    y_lines = _peaks(
        [int(r) for r in rows_profile],
        threshold=(grid["x_lines"][-1] - grid["x_lines"][0]) * 0.6,
    )
    print("  detected x lines:", x_lines)
    print("  config   x lines:", grid["x_lines"])
    print("  detected y lines:", y_lines)
    print("  config   y lines:", grid["y_lines"])


def _verify_boxes(a, layout: TemplateLayout) -> None:
    """Semantic check: every configured box must sit on empty template space.

    Coordinate equality is the wrong test (strokes are a few pixels wide and
    the corners are rounded). What actually matters is that no dynamic region
    overlaps printed artwork and that each cell interior is blank.
    """
    import numpy as np

    def ink(box) -> int:
        patch = a[box.y:box.bottom, box.x:box.right]
        if patch.size == 0:
            return -1
        # anything clearly darker than the cream fill counts as printed ink
        return int((patch.sum(2) / 3 < 215).sum())

    problems = 0
    empty_cells = 0
    for weekday in WEEKDAY_KEYS:
        for slot, box in enumerate(layout.cells(weekday)):
            if box.x < 0 or box.right > layout.width or box.bottom > layout.height:
                print(f"  ✖ {weekday} slot {slot + 1}: outside the canvas")
                problems += 1
                continue
            found = ink(box)
            if found > 0:
                print(f"  ✖ {weekday} slot {slot + 1}: {found}px of artwork inside "
                      f"the text area {box.as_tuple()}")
                problems += 1
            else:
                empty_cells += 1
    print(f"  cells: {empty_cells}/56 clear of artwork")

    for weekday in WEEKDAY_KEYS:
        box = layout.date_box(weekday)
        found = ink(box)
        # the date box *should* contain the __/__/__ placeholder we mask over
        print(f"  {weekday:10} date box {box.as_tuple()} placeholder ink: {found}px")
        if found == 0:
            print("     ⚠ no placeholder found — is the box in the right place?")
            problems += 1

    a_box = layout.assignments_box
    print(f"  assignments {a_box.as_tuple()} ink: {ink(a_box)}px "
          f"(dotted rules are expected)")
    print("  → calibration OK" if problems == 0 else f"  → {problems} problem(s)")


def _report(what: str, measured: list[int], config: list[int], tolerance: int = 2) -> None:
    """The drawn stroke is a couple of pixels wide, so compare with a tolerance."""
    if len(measured) != len(config):
        print(f"  → {what}: DIFFERENT COUNT — measured {len(measured)}, "
              f"config {len(config)}; re-derive the config")
        return
    deviations = [abs(m - c) for m, c in zip(measured, config)]
    worst = max(deviations) if deviations else 0
    if worst <= tolerance:
        print(f"  → {what}: match (max deviation {worst}px, tolerance {tolerance}px)")
    else:
        print(f"  → {what}: DIFFERENT (max deviation {worst}px) — update the config")


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
    path = OUT / f"{layout.version}-fill.png"
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


def cmd_assignment(layout: TemplateLayout) -> None:
    """Debug overlay of the assignment area: boxes, bands, baselines, ink."""
    from PIL import ImageDraw

    from app.domain.models import Assignment, WeeklyPlan
    from app.domain.persian import jalali_to_gregorian
    from app.rendering.compose import fit_assignments
    from app.rendering.fit import ink_metrics, load_font, text_width

    outer = layout.assignments_outer
    title = layout.assignments_title
    body = layout.assignments_body
    bands = layout.assignment_line_boxes()
    widths = layout.assignment_usable_widths()
    cfg = layout.typography["assignments"]

    print(f"outer      x={outer.x} y={outer.y} w={outer.w} h={outer.h}")
    if title:
        print(f"title      x={title.x} y={title.y} w={title.w} h={title.h}")
    print(f"body       x={body.x} y={body.y} w={body.w} h={body.h}")
    print(f"rules      {layout.assignments_cfg.get('rules')}")
    print(f"max_lines  {len(bands)}   font {cfg['min_size']}..{cfg['max_size']}")
    for i, (band, usable) in enumerate(zip(bands, widths), start=1):
        ink = ink_metrics(str(layout.font_path("medium")), cfg["max_size"])
        gap = int(layout.assignments_cfg.get("baseline_gap", 1))
        print(f"  band {i}   x={band.x} y={band.y} w={band.w} h={band.h} "
              f"usable_w={usable}  baseline={band.bottom - ink.bottom - gap}")

    plan = WeeklyPlan(student_name="نمونه", student_id="0")
    plan.apply_week_start(jalali_to_gregorian(1405, 6, 7))
    for i, text in enumerate(("مرور فصل گوارش", "حل ۴۰ تست ریاضی",
                              "تحلیل تست‌های غلط")):
        plan.assignments.append(Assignment(text=text, order=i))
    placed = fit_assignments(plan, layout)

    img = Image.open(layout.template_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    draw.rectangle(outer.as_tuple(), outline=(0, 160, 0), width=2)
    if title:
        draw.rectangle(title.as_tuple(), outline=(255, 140, 0), width=2)
    draw.rectangle(body.as_tuple(), outline=(0, 90, 255), width=1)
    for i, band in enumerate(bands):
        draw.rectangle(band.as_tuple(), outline=(255, 0, 255), width=1)
        if i < len(placed.baselines):
            y = placed.baselines[i]
            draw.line((band.x, y, band.right, y), fill=(255, 0, 0), width=1)
            font = load_font(str(layout.font_path("medium")), placed.font_size)
            w = text_width(placed.lines[i], font)
            ink = ink_metrics(str(layout.font_path("medium")), placed.font_size)
            draw.rectangle(
                (band.right - w - cfg["pad_x"], y + ink.top,
                 band.right - cfg["pad_x"], y + ink.bottom),
                outline=(0, 200, 200), width=1,
            )
    out = ROOT / "out" / "calibration" / f"{layout.version}-assignment.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.crop((outer.x - 40, (title.y if title else outer.y) - 20,
              outer.right + 40, outer.bottom + 20)).save(out)
    print(f"overlay → {out}")
    print(f"status={placed.status} size={placed.font_size} "
          f"lines={len(placed.lines)} baselines={[round(b) for b in placed.baselines]}")


def cmd_templates() -> None:
    for info in available():
        mark = "★ فعال" if info.active else "  قدیمی"
        print(f"  {mark}  {info.version:24} config={info.config}")


def main() -> None:
    args = sys.argv[1:] or ["grid"]
    template = DEFAULT_TEMPLATE
    if "--template" in args:
        i = args.index("--template")
        template = args[i + 1]
        del args[i:i + 2]
    if args and args[0] == "templates":
        cmd_templates()
        return
    layout = load_layout(template)
    print(f"template: {layout.version}")
    cmd = args[0]
    if cmd == "grid":
        cmd_grid(layout)
    elif cmd == "probe":
        cmd_probe(layout)
    elif cmd == "fill":
        cmd_fill(layout)
    elif cmd == "nudge":
        cmd_nudge(layout, args[1], int(args[2]), int(args[3]))
    elif cmd == "assignment":
        cmd_assignment(layout)
    elif cmd == "set" and args[1] == "assignments":
        cmd_set_assignments(layout, *(int(v) for v in args[2:6]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
