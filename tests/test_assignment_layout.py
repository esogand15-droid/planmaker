"""Deep renderer audit — assignment layout, cell layout, geometry, backends.

Every assertion here is about *where the ink actually lands*: the renderer output
is diffed against the untouched template and the resulting ink is checked
against the boxes declared in the template config. Nothing is measured against a
number typed into a test.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.models import WEEKDAY_KEYS, Activity, Assignment, WeeklyPlan  # noqa: E402
from app.domain.persian import jalali_to_gregorian  # noqa: E402
from app.rendering.compose import compose, fit_assignments  # noqa: E402
from app.rendering.fit import (  # noqa: E402
    FIT,
    OVERFLOW,
    TIGHT,
    ink_metrics,
    load_font,
    text_width,
)
from app.rendering.html_renderer import HtmlRenderer  # noqa: E402
from app.rendering.pdf import png_to_pdf  # noqa: E402
from app.rendering.pillow_renderer import PillowRenderer  # noqa: E402
from app.rendering.registry import load_layout  # noqa: E402
from tools.ink_audit import audit, ink_bbox, ink_mask  # noqa: E402

WEEK = jalali_to_gregorian(1405, 6, 7)
GOLDEN_DIR = ROOT / "tests" / "goldens"


@pytest.fixture(scope="module")
def layout():
    return load_layout("rotbeland-weekly-v2")


@pytest.fixture(scope="module")
def renderer(layout):
    return PillowRenderer(layout)


@pytest.fixture(scope="module")
def template(layout):
    return Image.open(layout.template_path).convert("RGB")


def plan_with(assignments: list[str], *, cells: int = 1) -> WeeklyPlan:
    plan = WeeklyPlan(student_name="علی رضایی", student_id="8472")
    plan.apply_week_start(WEEK)
    for i in range(cells):
        plan.day(WEEKDAY_KEYS[i % 7]).set_slot(
            i % 8, Activity(i % 8, subject="زیست", topic="گوارش",
                            description="۴۰ تست", duration="۹۰ دقیقه"))
    for i, text in enumerate(assignments):
        plan.assignments.append(Assignment(text=text, order=i))
    return plan


def rendered(renderer, plan) -> Image.Image:
    return Image.open(io.BytesIO(renderer.render_png(plan).png)).convert("RGB")


# ═════════════════════════════ geometry from pixels ════════════════════════
def test_assignment_geometry_matches_the_printed_sheet(layout, template):
    """Every declared coordinate is verified against the artwork itself."""
    a = np.asarray(template).astype(int)
    bg = np.array(layout.color("page_bg"))
    mask = np.abs(a - bg).sum(2) > 30

    outer, body = layout.assignments_outer, layout.assignments_body
    rules = layout.assignments_cfg["rules"]

    # the two dotted rules really are printed where the config says they are
    for rule in rules:
        row = mask[rule - 1:rule + 1, body.x:body.right].sum()
        assert row > 300, f"no printed rule at y={rule}"
        blank = mask[rule + 6:rule + 12, body.x + 40:body.right - 40].sum()
        assert blank == 0, f"y={rule} is not an isolated rule"

    # the panel border frames the body on all four sides
    assert outer.y < body.y < rules[0] < rules[1] < body.bottom < outer.bottom
    assert outer.x < body.x and body.right < outer.right


def test_assignment_bands_tile_the_body_without_touching_the_rules(layout):
    bands = layout.assignment_line_boxes()
    body = layout.assignments_body
    rules = list(layout.assignments_cfg["rules"])
    assert len(bands) == layout.assignments_cfg["max_lines"] == 3

    for band in bands:
        assert body.y <= band.y and band.bottom <= body.bottom
        assert band.x == body.x and band.right == body.right
    for a, b in zip(bands, bands[1:]):
        assert a.bottom < b.y                      # a rule fits in between
    for band, rule in zip(bands, rules):
        assert band.bottom < rule                  # ink stops above the rule


def test_title_chip_is_declared_as_an_obstacle(layout):
    """Band 1 crosses the printed «تکالیف» chip, so its width must shrink."""
    obstacles = layout.assignment_obstacles()
    assert obstacles, "the printed title chip must be declared"
    widths = layout.assignment_usable_widths()
    bands = layout.assignment_line_boxes()
    assert widths[0] < bands[0].w                  # narrowed by the chip
    assert widths[1] == bands[1].w and widths[2] == bands[2].w


# ═════════════════════════════ baselines & ink ═════════════════════════════
def test_assignment_baseline_sits_on_its_rule(layout):
    placed = fit_assignments(plan_with(["مرور فصل گوارش", "حل ۴۰ تست ریاضی",
                                        "تحلیل تست‌های غلط"]), layout)
    bands = layout.assignment_line_boxes()
    ink = ink_metrics(str(layout.font_path("medium")), placed.font_size)
    gap = layout.assignments_cfg["baseline_gap"]
    assert len(placed.baselines) == 3
    for baseline, band in zip(placed.baselines, bands):
        assert baseline == band.bottom - ink.bottom - gap
        assert baseline + ink.top >= band.y        # ink never leaves the band


@pytest.mark.parametrize("texts", [
    ["مرور فصل ۲ زیست"],
    ["مرور فصل گوارش", "حل ۴۰ تست ریاضی"],
    ["مرور فصل گوارش", "حل ۴۰ تست ریاضی", "تحلیل تست‌های غلط"],
    ["مرور زیست", "حل ۴۰ تست ریاضی", "تحلیل آزمون", "مرور لغات زبان"],
    ["مرور Biology Chapter 3 + 40 تست", "حل ۵۰ تست ریاضی (۹۰ دقیقه)"],
    ["📚 مطالعه زیست", "✅ حل ۴۰ تست", "⚠️ مرور فصل ۲"],
    ["+ - / : % () [] {} ، ؛ ؟ ! مرور Chapter 2 - فصل ۲ ۱۴۰۵"],
    ["مرور کامل فصل گوارش و نکات مهم، سپس حل تست‌های علامت‌دار و تحلیل تمام "
     "تست‌های غلط و مرور خلاصه‌نویسی جلسه قبل"],
])
def test_assignment_ink_stays_inside_its_band(layout, renderer, template, texts):
    plan = plan_with(texts)
    mask = ink_mask(rendered(renderer, plan), template)
    bands = layout.assignment_line_boxes()
    panel = layout.assignments_outer

    # no ink anywhere in the panel outside the declared bands
    allowed = np.zeros_like(mask)
    for band in bands:
        allowed[band.y:band.bottom, band.x:band.right] = True
    panel_ink = np.zeros_like(mask)
    panel_ink[panel.y:panel.bottom, panel.x:panel.right] = mask[
        panel.y:panel.bottom, panel.x:panel.right]
    assert int((panel_ink & ~allowed).sum()) == 0

    # and every band that was written into keeps its ink strictly inside
    for band in bands:
        box = ink_bbox(mask, band)
        if box is None:
            continue
        x0, y0, x1, y1 = box
        assert band.y <= y0 and y1 <= band.bottom
        assert band.x <= x0 and x1 <= band.right


def test_assignment_never_writes_over_the_title_chip(layout, renderer, template):
    """Five items flow onto one line — it must move below the chip, not under it."""
    plan = plan_with(["مرور زیست", "حل ۴۰ تست ریاضی", "تحلیل آزمون",
                      "مرور لغات زبان", "خلاصه‌نویسی فیزیک"])
    mask = ink_mask(rendered(renderer, plan), template)
    for chip in layout.assignment_obstacles():
        assert int(mask[chip.y:chip.bottom, chip.x:chip.right].sum()) == 0


def test_no_ink_at_all_outside_the_declared_boxes(layout, renderer):
    plan = plan_with(["مرور فصل گوارش", "حل ۴۰ تست ریاضی", "تحلیل تست‌های غلط"],
                     cells=8)
    report = audit(plan, layout, renderer.render_png(plan).png)
    assert report.stray_pixels == 0, report.problems


# ═══════════════════════════ wrapping / fitting ════════════════════════════
def test_assignment_empty_draws_nothing(layout, renderer, template):
    plan = plan_with([])
    mask = ink_mask(rendered(renderer, plan), template)
    panel = layout.assignments_outer
    assert int(mask[panel.y:panel.bottom, panel.x:panel.right].sum()) == 0
    assert fit_assignments(plan, layout).lines == []


def test_assignment_single_item_uses_the_first_usable_line(layout):
    placed = fit_assignments(plan_with(["مرور فصل ۲ زیست"]), layout)
    assert len(placed.lines) == 1
    assert placed.baselines[0] == pytest.approx(
        layout.assignment_line_boxes()[0].bottom
        - ink_metrics(str(layout.font_path("medium")), placed.font_size).bottom
        - layout.assignments_cfg["baseline_gap"])


def test_assignment_list_layout_keeps_one_item_per_line(layout):
    items = ["مرور فصل گوارش", "حل ۴۰ تست ریاضی", "تحلیل تست‌های غلط"]
    placed = fit_assignments(plan_with(items), layout)
    assert len(placed.lines) == len(items)
    for line, item in zip(placed.lines, items):
        assert item in line


def test_assignment_wraps_instead_of_running_off_the_sheet(layout):
    long_text = "مرور " + "فصل گوارش و تحلیل تست‌های غلط " * 6
    placed = fit_assignments(plan_with([long_text]), layout)
    widths = layout.assignment_usable_widths()
    font = load_font(str(layout.font_path("medium")), placed.font_size)
    pad = layout.typography["assignments"]["pad_x"]
    for i, line in enumerate(placed.lines):
        band_index = len(widths) - len(placed.lines) + i
        assert text_width(line, font) <= widths[band_index] - 2 * pad + 1


def test_assignment_font_scaling_has_a_readable_floor(layout):
    cfg = layout.typography["assignments"]
    placed = fit_assignments(
        plan_with([f"تکلیف بسیار مفصل شماره {i}" for i in range(30)]), layout)
    assert placed.font_size >= cfg["min_size"] >= 12
    assert placed.status == OVERFLOW


def test_assignment_overflow_is_detected_before_rendering(layout, renderer):
    plan = plan_with([f"تکلیف بسیار مفصل و طولانی شماره {i + 1} با توضیح اضافه"
                      for i in range(20)])
    issues = [i for i in renderer.validate(plan) if i.scope == "assignments"]
    assert issues and "ظرفیت" in issues[0].message
    # validation predicts exactly what rendering reports
    assert [i.message for i in renderer.render_png(plan).issues] == \
           [i.message for i in issues]


def test_fit_states_are_distinct(layout):
    assert fit_assignments(plan_with(["مرور زیست"]), layout).status in (FIT, TIGHT)
    assert fit_assignments(
        plan_with(["الف" * 400]), layout).status == OVERFLOW


def test_assignment_mixed_rtl_ltr_keeps_every_token(layout):
    placed = fit_assignments(plan_with(["مرور Biology Chapter 3 - ۴۰ تست"]), layout)
    joined = " ".join(placed.lines)
    # Latin words survive untouched; digits follow the template's digit style
    for token in ("Biology", "Chapter", "مرور", "تست", "۳", "۴۰"):
        assert token in joined
    assert joined.index("Biology") < joined.index("Chapter")   # LTR run intact


# ═════════════════════════ cells, dates, 56-cell audit ═════════════════════
def test_all_56_cells_keep_their_ink_inside(layout, renderer, template):
    plan = WeeklyPlan(student_name="ع", student_id="1")
    plan.apply_week_start(WEEK)
    for weekday in WEEKDAY_KEYS:
        for slot in range(8):
            plan.day(weekday).set_slot(slot, Activity(
                slot, subject="زیست Biology", topic="Chapter 3 - فصل ۲",
                description="📚 مطالعه + ۴۰ تست ✅", duration="۹۰ دقیقه"))
    assert plan.activity_count == 56
    mask = ink_mask(rendered(renderer, plan), template)
    for weekday in WEEKDAY_KEYS:
        for slot, box in enumerate(layout.cells(weekday)):
            found = ink_bbox(mask, box)
            assert found, f"{weekday}[{slot}] was not written"
            x0, y0, x1, y1 = found
            assert box.x <= x0 and x1 <= box.right
            assert box.y <= y0 and y1 <= box.bottom
    report = audit(plan, layout, renderer.render_png(plan).png)
    assert report.stray_pixels == 0, report.problems


def test_rtl_slot_one_is_next_to_the_day_card(layout):
    cells = layout.cells("saturday")
    card = layout.day_card("saturday")
    assert cells[0].x > cells[-1].x                 # slot 1 on the right
    assert card and cells[0].right < card.x         # …right next to the card
    for a, b in zip(cells, cells[1:]):
        assert b.right < a.x                        # strictly right to left


def test_dates_stay_inside_their_chip(layout, renderer, template):
    plan = plan_with(["مرور فصل ۲"])
    mask = ink_mask(rendered(renderer, plan), template)
    for weekday in WEEKDAY_KEYS:
        box = layout.date_box(weekday)
        found = ink_bbox(mask, box)
        assert found, f"{weekday}: no date drawn"
        x0, y0, x1, y1 = found
        assert box.x <= x0 and x1 <= box.right
        assert box.y <= y0 and y1 <= box.bottom
        name = layout.day_name_box(weekday)
        assert int(mask[name.y:name.bottom, name.x:name.right].sum()) == 0


# ═══════════════════════ backend / output consistency ══════════════════════
def test_preview_and_final_share_one_composition(layout, renderer):
    plan = plan_with(["مرور فصل گوارش", "حل ۴۰ تست ریاضی"], cells=4)
    first = compose(plan, layout)
    second = compose(plan, layout)
    assert first.lines == second.lines
    assert [i.message for i in renderer.validate(plan)] == \
           [i.message for i in renderer.render_png(plan).issues]


def test_png_and_pdf_have_the_same_geometry(renderer, layout):
    plan = plan_with(["مرور فصل گوارش", "حل ۴۰ تست ریاضی"], cells=4)
    png = renderer.render_png(plan, scale=2.0).png
    pdf = png_to_pdf(png, dpi=300)
    assert pdf[:5] == b"%PDF-"
    small = Image.open(io.BytesIO(renderer.render_png(plan).png))
    big = Image.open(io.BytesIO(png))
    assert (big.width, big.height) == (small.width * 2, small.height * 2)
    # the 2x raster is the same layout, just denser
    band = layout.assignment_line_boxes()[0]
    a = np.asarray(small.convert("L"))[band.y:band.bottom, band.x:band.right] < 150
    b = np.asarray(big.convert("L"))[band.y * 2:band.bottom * 2,
                                     band.x * 2:band.right * 2] < 150
    assert a.any() and b.any()
    assert abs(a.sum() * 4 - b.sum()) / (a.sum() * 4) < 0.35


html_only = pytest.mark.skipif(
    not HtmlRenderer.available(), reason="Playwright/Chromium not installed")


@html_only
def test_html_and_pillow_agree_pixel_wise(layout, renderer):
    plan = plan_with(["مرور فصل گوارش", "حل ۴۰ تست ریاضی", "تحلیل تست‌های غلط"],
                     cells=4)
    pil = Image.open(io.BytesIO(renderer.render_png(plan).png)).convert("L")
    web = Image.open(io.BytesIO(HtmlRenderer(layout).render_png(plan).png)).convert("L")
    a, b = np.asarray(pil).astype(int), np.asarray(web).astype(int)

    boxes = list(layout.assignment_line_boxes())
    boxes += [layout.cell(WEEKDAY_KEYS[i], 0) for i in range(4)]
    boxes += [layout.date_box(d) for d in WEEKDAY_KEYS]
    for box in boxes:
        pa = a[box.y:box.bottom, box.x:box.right] < 150
        pb = b[box.y:box.bottom, box.x:box.right] < 150
        if not pa.any() and not pb.any():
            continue
        ya, xa = np.nonzero(pa)
        yb, xb = np.nonzero(pb)
        assert abs(int(xa.min()) - int(xb.min())) <= 2
        assert abs(int(xa.max()) - int(xb.max())) <= 2
        assert abs(int(ya.min()) - int(yb.min())) <= 2
        assert abs(int(ya.max()) - int(yb.max())) <= 2


# ══════════════════════════════ golden images ══════════════════════════════
GOLDEN_CASES = {
    "assignment_short": ["مرور فصل ۲ زیست"],
    "assignment_list": ["مرور فصل گوارش", "حل ۴۰ تست ریاضی", "تحلیل تست‌های غلط"],
    "assignment_flow": ["مرور زیست", "حل ۴۰ تست ریاضی", "تحلیل آزمون",
                        "مرور لغات زبان", "خلاصه‌نویسی فیزیک"],
    "assignment_mixed": ["مرور Biology Chapter 3 + 40 تست",
                         "حل ۵۰ تست ریاضی (۹۰ دقیقه)"],
}


@pytest.mark.parametrize("name", sorted(GOLDEN_CASES))
def test_golden_assignment_area(layout, renderer, name):
    """The assignment strip must not drift; regenerate with tools/goldens.py."""
    plan = plan_with(GOLDEN_CASES[name])
    panel = layout.assignments_outer
    crop = (panel.x - 10, panel.y - 40, panel.right + 10, panel.bottom + 10)
    got = Image.open(io.BytesIO(renderer.render_png(plan).png)).convert("RGB").crop(crop)

    golden_path = GOLDEN_DIR / f"{name}.png"
    assert golden_path.exists(), f"missing golden: {golden_path}"
    golden = Image.open(golden_path).convert("RGB")
    assert got.size == golden.size
    diff = np.abs(np.asarray(got).astype(int) - np.asarray(golden).astype(int)).sum(2)
    changed = int((diff > 24).sum())
    assert changed <= diff.size * 0.001, (
        f"{name}: {changed} pixels differ from the golden image")


def test_golden_metadata_is_versioned():
    meta = json.loads((GOLDEN_DIR / "goldens.json").read_text(encoding="utf-8"))
    assert meta["template_version"] == "rotbeland-weekly-v2"
    assert meta["renderer_version"] == PillowRenderer.renderer_version
    assert set(meta["cases"]) == set(GOLDEN_CASES)
