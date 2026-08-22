"""Template v2 migration: coordinates, pixel preservation and backward compatibility."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.calendar import JalaliDate  # noqa: E402
from app.domain.models import Activity, Assignment, WeeklyPlan  # noqa: E402
from app.rendering.factory import get_renderer  # noqa: E402
from app.rendering.pillow_renderer import PillowRenderer  # noqa: E402
from app.rendering.registry import (  # noqa: E402
    DEFAULT_TEMPLATE,
    TEMPLATES,
    available,
    load_layout,
    resolve,
    verify_assets,
)
from app.services.plan_service import WeeklyPlanService  # noqa: E402

V1 = "rotbeland-weekly-v1"
V2 = "rotbeland-weekly-v2"


@pytest.fixture(scope="module")
def layout():
    return load_layout(V2)


@pytest.fixture(scope="module")
def template_pixels(layout):
    return Image.open(layout.template_path).convert("RGB")


def sample_plan(template_version: str | None = V2, full: bool = False) -> WeeklyPlan:
    plan = WeeklyPlan(student_name="علی رضایی", student_id="1",
                      template_version=template_version)
    plan.apply_week_start(JalaliDate.parse("1405/05/24"))
    if full:
        for day in plan.days:
            for i in range(8):
                day.set_slot(i, Activity(i, subject="زیست‌شناسی", topic="فصل ۳ گوارش",
                                         description="مطالعه + ۴۰ تست", duration="۹۰ دقیقه"))
    else:
        plan.day("saturday").set_slot(
            0, Activity(0, subject="زیست", topic="گوارش",
                        description="۴۰ تست", duration="۹۰ دقیقه"))
    plan.assignments.append(Assignment(text="مرور فصل ۲", order=0))
    return plan


# ════════════════════════════ registry & versions ══════════════════════════
def test_v2_is_the_default_and_v1_is_preserved():
    assert DEFAULT_TEMPLATE == V2
    assert set(TEMPLATES) == {V1, V2}
    assert TEMPLATES[V2].active and not TEMPLATES[V1].active
    assert resolve(None).version == V2
    assert resolve(V1).version == V1
    assert resolve("template_weekly_v1").version == V1   # config-name alias
    assert resolve("nonsense").version == V2             # safe fallback
    assert verify_assets() == []                         # both sheets on disk


def test_both_template_assets_exist_untouched():
    for version in (V1, V2):
        lay = load_layout(version)
        assert lay.template_path.exists(), version
        with Image.open(lay.template_path) as img:
            assert img.size == (lay.width, lay.height)
    assert load_layout(V1).template_path != load_layout(V2).template_path


def test_canvas_is_exactly_1536x1024(layout, template_pixels):
    assert (layout.width, layout.height) == (1536, 1024)
    assert template_pixels.size == (1536, 1024)


# ═══════════════════════════════ geometry ══════════════════════════════════
def test_grid_is_7_rows_by_8_columns(layout):
    assert layout.grid["rows"] == 7 and layout.grid["columns"] == 8
    total = sum(len(layout.cells(d["key"])) for d in layout.days)
    assert total == 56


def test_slot_one_is_nearest_the_day_column(layout):
    """RTL: slot 1 must be the right-most cell, next to the day cards."""
    for day in (d["key"] for d in layout.days):
        boxes = layout.cells(day)
        xs = [b.x for b in boxes]
        assert xs == sorted(xs, reverse=True), f"{day}: slots are mirrored"
        assert boxes[0].right < layout.day_card(day).x   # never under the card


def test_cells_are_inside_the_canvas_and_do_not_overlap(layout):
    seen = []
    for day in (d["key"] for d in layout.days):
        for box in layout.cells(day):
            assert 0 <= box.x and box.right <= layout.width
            assert 0 <= box.y and box.bottom <= layout.height
            assert box.w > 60 and box.h > 40      # usable text space
            seen.append(box)
    for i, a in enumerate(seen):
        for b in seen[i + 1:]:
            overlap = not (a.right <= b.x or b.right <= a.x
                           or a.bottom <= b.y or b.bottom <= a.y)
            assert not overlap, f"{a.as_tuple()} overlaps {b.as_tuple()}"


def test_every_cell_sits_on_blank_artwork(layout, template_pixels):
    """No text region may land on a printed border, icon or decoration."""
    import numpy as np

    a = np.asarray(template_pixels).astype(int)
    for day in (d["key"] for d in layout.days):
        for slot, box in enumerate(layout.cells(day)):
            patch = a[box.y:box.bottom, box.x:box.right]
            ink = int((patch.sum(2) / 3 < 215).sum())
            assert ink == 0, f"{day} slot {slot + 1}: {ink}px of artwork inside"


def test_date_boxes_cover_the_printed_placeholder(layout, template_pixels):
    import numpy as np

    a = np.asarray(template_pixels).astype(int)
    for day in (d["key"] for d in layout.days):
        box = layout.date_box(day)
        patch = a[box.y:box.bottom, box.x:box.right]
        assert int((patch.sum(2) / 3 < 200).sum()) > 100, f"{day}: placeholder missing"
        assert box.right < layout.width


def test_day_cards_and_name_boxes_are_defined(layout):
    for day in (d["key"] for d in layout.days):
        card, name, date = (layout.day_card(day), layout.day_name_box(day),
                            layout.date_box(day))
        assert card and name and date
        assert card.x <= name.x and name.bottom <= date.y   # name above the date
        assert date.bottom <= card.bottom + 8


def test_assignment_region_sits_between_the_dotted_rules(layout, template_pixels):
    box = layout.assignments_box
    rules = layout.assignments_cfg["rules"]
    assert len(rules) == 2
    assert box.y < rules[0] < rules[1] < box.bottom
    assert box.x > 200 and box.right < 1460           # inside the printed panel


def test_no_hardcoded_coordinates_in_the_renderers():
    """No renderer may contain a coordinate that belongs in the layout config."""
    import re

    cfg = json.loads((ROOT / "config" / "template_weekly_v2.json").read_text("utf-8"))
    coordinates: set[int] = set()
    for row in cfg["cells"].values():
        for box in row:
            coordinates.update({box["x"], box["y"], box["w"], box["h"]})
    for section in ("day_cards", "day_name_boxes", "date_boxes"):
        for box in cfg[section].values():
            coordinates.update(box.values())
    coordinates.update(cfg["assignments"]["rules"])
    coordinates.update({cfg["assignments"]["x"], cfg["assignments"]["y"]})
    coordinates = {c for c in coordinates if c > 40}     # ignore paddings/sizes

    for name in ("pillow_renderer.py", "html_renderer.py", "pdf.py", "fit.py"):
        source = (ROOT / "app" / "rendering" / name).read_text(encoding="utf-8")
        numbers = {int(n) for n in re.findall(r"\b\d{2,4}\b", source)}
        leaked = numbers & coordinates
        assert not leaked, f"{name} hard-codes template coordinates: {sorted(leaked)}"


def test_config_files_hold_every_coordinate():
    cfg = json.loads((ROOT / "config" / "template_weekly_v2.json").read_text("utf-8"))
    for key in ("canvas", "grid", "cells", "day_cards", "day_name_boxes",
                "date_boxes", "assignments", "typography", "colors",
                "static_regions", "dynamic_regions"):
        assert key in cfg, f"missing section: {key}"
    assert cfg["template_version"] == V2
    assert len(cfg["cells"]) == 7 and all(len(v) == 8 for v in cfg["cells"].values())


# ═══════════════════════════ rendering & pixels ════════════════════════════
def test_static_pixels_are_preserved(layout, template_pixels, tmp_path):
    """Only dates, activity cells and assignments may differ from the sheet."""
    import numpy as np

    renderer = PillowRenderer(layout)
    rendered = Image.open(io.BytesIO(renderer.render_png(sample_plan(full=True)).png))
    diff = (np.abs(np.asarray(rendered).astype(int)
                   - np.asarray(template_pixels).astype(int)).sum(2) > 12)

    allowed = np.zeros_like(diff)
    for day in (d["key"] for d in layout.days):
        for box in layout.cells(day):
            allowed[box.y:box.bottom, box.x:box.right] = True
        d = layout.date_box(day)
        pad = layout.date_mask.get("pad", 3) + 1
        allowed[d.y - pad:d.bottom + pad, d.x - pad:d.right + pad] = True
    a = layout.assignments_box
    allowed[a.y:a.bottom, a.x:a.right] = True

    stray = diff & ~allowed
    assert stray.sum() == 0, f"{stray.sum()} pixels changed outside the dynamic areas"

    # the logo, header bars, day names and decorations are byte-identical
    for name, (x0, y0, x1, y1) in {
        "header/logo": (0, 0, 1536, 185),
        "column headers": (40, 185, 1260, 218),
        "assignment title": (600, 892, 1000, 930),
        "bottom decoration": (0, 1000, 1536, 1024),
    }.items():
        assert diff[y0:y1, x0:x1].sum() == 0, f"{name} was modified"
    for day in (d["key"] for d in layout.days):
        n = layout.day_name_box(day)
        assert diff[n.y:n.bottom, n.x:n.right].sum() == 0, f"{day} name was modified"


def test_dates_are_actually_written(layout):
    import numpy as np

    renderer = PillowRenderer(layout)
    plan = sample_plan()
    rendered = np.asarray(
        Image.open(io.BytesIO(renderer.render_png(plan).png)).convert("RGB")
    ).astype(int)
    original = np.asarray(Image.open(layout.template_path).convert("RGB")).astype(int)
    for day in (d["key"] for d in layout.days):
        box = layout.date_box(day)
        changed = (np.abs(rendered - original).sum(2) > 12)[
            box.y:box.bottom, box.x:box.right
        ].sum()
        assert changed > 50, f"{day}: the real date was not drawn"


def test_all_56_cells_render_without_overflow(layout):
    renderer = PillowRenderer(layout)
    plan = sample_plan(full=True)
    assert plan.activity_count == 56
    result = renderer.render_png(plan)
    assert result.ok, [i.human() for i in result.issues]
    assert Image.open(io.BytesIO(result.png)).size == (1536, 1024)


@pytest.mark.parametrize(
    "text",
    ["زیست", "ریاضی", "فیزیک", "شیمی", "زبان انگلیسی", "Biology",
     "40 تست", "۹۰ دقیقه", "زیست Biology - Chapter 3", "مطالعه ۱۰۰٪ + ۴۰ تست",
     "آزمون 🎯", "۱:۳۰ / ۲:۰۰"],
)
def test_rtl_and_mixed_text(layout, text):
    renderer = PillowRenderer(layout)
    plan = sample_plan()
    plan.day("sunday").set_slot(0, Activity(0, subject=text, topic="Chapter 3",
                                            description="40 تست", duration="۹۰ دقیقه"))
    result = renderer.render_png(plan)
    assert result.png and Image.open(io.BytesIO(result.png)).size == (1536, 1024)


def test_long_text_is_reported_before_generation(layout):
    renderer = PillowRenderer(layout)
    plan = sample_plan()
    plan.day("monday").set_slot(0, Activity(
        0, subject="زیست‌شناسی سلولی و مولکولی پیشرفته کنکور",
        topic="فصل سوم گوارش و جذب مواد غذایی در بدن انسان",
        description="مطالعه کامل درسنامه به همراه حل ۱۲۰ تست زمان‌دار و تحلیل",
        duration="۲۴۰ دقیقه بدون احتساب استراحت"))
    issues = renderer.validate(plan)
    assert issues and "دوشنبه" in issues[0].human()


def test_pdf_uses_the_same_template(tmp_path):
    service = WeeklyPlanService(get_renderer("pillow"), storage_root=tmp_path)
    result = service.generate(sample_plan(full=True), force=True)
    assert result.template_version == V2
    assert result.pdf_path.read_bytes()[:5] == b"%PDF-"
    assert result.pdf_path.stat().st_size > 50_000
    assert Image.open(result.png_path).size == (1536, 1024)


# ═══════════════════════ backward compatibility & cache ════════════════════
def test_old_plans_still_render_on_v1(tmp_path):
    service = WeeklyPlanService(get_renderer("pillow"), storage_root=tmp_path)
    old = service.generate(sample_plan(template_version=V1), force=True)
    new = service.generate(sample_plan(template_version=V2), force=True)

    assert old.template_version == V1 and new.template_version == V2
    assert old.png_path != new.png_path
    # each output really carries its own sheet
    for result, version in ((old, V1), (new, V2)):
        expected = Image.open(load_layout(version).template_path).convert("RGB")
        got = Image.open(result.png_path).convert("RGB")
        assert got.size == expected.size
        # the untouched header band identifies the sheet
        assert list(got.crop((0, 0, 1536, 150)).getdata()) == \
            list(expected.crop((0, 0, 1536, 150)).getdata())


def test_cache_key_is_template_aware():
    plan_v1 = sample_plan(template_version=V1)
    plan_v2 = sample_plan(template_version=V2)
    h1 = plan_v1.content_hash(V1, "pillow-1.0.0")
    h2 = plan_v2.content_hash(V2, "pillow-1.0.0")
    assert h1 != h2, "a template change must invalidate the cache"
    assert plan_v2.content_hash(V2, "pillow-1.0.0") == h2   # stable


def test_no_v1_output_is_reused_for_v2(tmp_path):
    service = WeeklyPlanService(get_renderer("pillow"), storage_root=tmp_path)
    first = service.generate(sample_plan(template_version=V1), force=True)
    second = service.generate(sample_plan(template_version=V2))
    assert not second.cached
    assert second.png_path != first.png_path
    third = service.generate(sample_plan(template_version=V2))
    assert third.cached and third.png_path == second.png_path


def test_default_service_uses_v2(tmp_path):
    service = WeeklyPlanService(get_renderer("pillow"), storage_root=tmp_path)
    assert service.renderer.layout.version == V2
    result = service.generate(sample_plan(template_version=None), force=True)
    assert result.template_version == V2


# ═══════════════════════ custom range on the new sheet ═════════════════════
def test_custom_range_weekday_mapping(tmp_path):
    """A four-day range must land on its real weekday rows, nothing else."""
    plan = WeeklyPlan(student_name="علی", student_id="1", template_version=V2)
    plan.apply_range(JalaliDate.parse("1405/05/26"), JalaliDate.parse("1405/05/29"))

    assert [(d.index, d.weekday_fa) for d in
            JalaliDate.range(JalaliDate.parse("1405/05/26"),
                             JalaliDate.parse("1405/05/29"))] == [
        (1, "دوشنبه"), (2, "سه‌شنبه"), (3, "چهارشنبه"), (4, "پنج‌شنبه")
    ]
    assert [d.weekday for d in plan.plan_days] == [
        "monday", "tuesday", "wednesday", "thursday"
    ]
    plan.day("monday").set_slot(0, Activity(0, subject="زیست"))

    service = WeeklyPlanService(get_renderer("pillow"), storage_root=tmp_path)
    result = service.generate(plan, force=True)
    assert result.png_path.exists()

    # rows outside the range stay byte-identical to the blank sheet
    lay = load_layout(V2)
    rendered = Image.open(result.png_path).convert("RGB")
    original = Image.open(lay.template_path).convert("RGB")
    for day in ("saturday", "sunday", "friday"):
        box = lay.cells(day)[0].as_tuple()
        assert list(rendered.crop(box).getdata()) == list(original.crop(box).getdata())
        date = lay.date_box(day).as_tuple()
        assert list(rendered.crop(date).getdata()) == list(original.crop(date).getdata())


def test_calibration_tool_supports_both_templates():
    import subprocess

    for version in (V1, V2):
        out = subprocess.run(
            [sys.executable, "-m", "tools.calibrate", "grid", "--template", version],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        assert out.returncode == 0, out.stderr
        assert (ROOT / "out" / "calibration" / f"{version}-grid.png").exists()


def test_registry_lists_both_templates():
    versions = {info.version for info in available()}
    assert versions == {V1, V2}
