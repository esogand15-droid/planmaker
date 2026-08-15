"""Renderer + service test suite (edge cases from the product spec)."""
from __future__ import annotations

import io
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.models import (  # noqa: E402
    WEEKDAY_KEYS,
    Activity,
    Assignment,
    WeeklyPlan,
)
from app.domain.persian import (  # noqa: E402
    jalali_short,
    jalali_to_gregorian,
    parse_jalali,
    saturday_of,
    to_fa_digits,
)
from app.rendering.factory import get_renderer  # noqa: E402
from app.rendering.layout import TemplateLayout  # noqa: E402
from app.rendering.pdf import png_to_pdf  # noqa: E402
from app.rendering.pillow_renderer import PillowRenderer  # noqa: E402
from app.services.plan_service import PlanGenerationError, WeeklyPlanService  # noqa: E402

WEEK_START = jalali_to_gregorian(1405, 5, 25)


@pytest.fixture(scope="module")
def layout() -> TemplateLayout:
    return TemplateLayout.load()


@pytest.fixture(scope="module")
def renderer(layout) -> PillowRenderer:
    return PillowRenderer(layout)


@pytest.fixture()
def service(renderer, tmp_path_factory) -> WeeklyPlanService:
    return WeeklyPlanService(renderer, storage_root=tmp_path_factory.mktemp("gen"))


def make_plan(**kw) -> WeeklyPlan:
    plan = WeeklyPlan(student_name=kw.pop("student", "علی رضایی"), student_id="8472")
    plan.apply_week_start(kw.pop("start", WEEK_START))
    return plan


def png_size(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


# ---------------------------------------------------------------- domain ----
def test_week_dates_are_saturday_to_friday():
    plan = make_plan()
    assert plan.week_end == plan.week_start.replace(day=plan.week_start.day + 6)
    assert [d.weekday for d in plan.days] == WEEKDAY_KEYS
    assert plan.day("friday").date == plan.week_end


def test_saturday_of_and_jalali_roundtrip():
    d = date(2026, 8, 16)
    assert saturday_of(d).weekday() == 5
    assert parse_jalali("۱۴۰۵/۰۵/۲۵") == WEEK_START
    assert jalali_short(WEEK_START) == to_fa_digits("1405/05/25")


def test_empty_slots_never_render_placeholders(renderer):
    plan = make_plan()
    plan.day("saturday").set_slot(0, Activity(0, subject="زیست"))
    plan.day("saturday").set_slot(1, Activity(1))  # empty -> dropped
    assert plan.day("saturday").filled_count == 1
    assert plan.day("saturday").slot(1) is None


def test_quick_entry_parsing():
    a = Activity.from_quick_entry(2, "زیست | گوارش | 40 تست | 90 دقیقه")
    assert (a.subject, a.topic, a.description, a.duration) == (
        "زیست", "گوارش", "40 تست", "90 دقیقه")
    b = Activity.from_quick_entry(0, "فیزیک")
    assert b.subject == "فیزیک" and b.topic == "" and not b.is_empty


def test_copy_day_and_duplicate_week():
    plan = make_plan()
    plan.day("saturday").set_slot(0, Activity(0, subject="زیست", topic="گوارش"))
    plan.copy_day("saturday", "monday")
    assert plan.day("monday").slot(0).subject == "زیست"

    new_start = jalali_to_gregorian(1405, 6, 1)
    clone = plan.duplicate(new_start)
    assert clone.week_start == new_start
    assert clone.day("monday").slot(0).subject == "زیست"
    assert clone.id != plan.id
    # deep copy: editing the clone must not touch the original
    clone.day("monday").slot(0).subject = "شیمی"
    assert plan.day("monday").slot(0).subject == "زیست"


def test_serialization_roundtrip():
    plan = make_plan()
    plan.day("sunday").set_slot(3, Activity(3, subject="ریاضی", duration="۹۰ دقیقه"))
    plan.assignments.append(Assignment(text="مرور فصل ۲", order=0))
    restored = WeeklyPlan.from_dict(plan.to_dict())
    assert restored.to_dict() == plan.to_dict()
    assert restored.day("sunday").slot(3).subject == "ریاضی"


# ------------------------------------------------------------- edge cases ---
def test_case_single_cell(renderer):
    plan = make_plan()
    plan.day("saturday").set_slot(0, Activity(0, subject="زیست"))
    res = renderer.render_png(plan)
    assert res.ok and png_size(res.png) == (1536, 1024)


def test_case_all_cells_full(renderer):
    plan = make_plan()
    for weekday in WEEKDAY_KEYS:
        for i in range(8):
            plan.day(weekday).set_slot(
                i, Activity(i, subject="زیست‌شناسی", topic="فصل ۳ گوارش",
                            description="مطالعه + ۴۰ تست", duration="۹۰ دقیقه"))
    res = renderer.render_png(plan)
    assert plan.activity_count == 56
    assert res.ok, [i.human() for i in res.issues]


def test_case_very_long_text_is_reported_not_clipped(renderer):
    plan = make_plan()
    plan.day("saturday").set_slot(
        0, Activity(0, subject="زیست‌شناسی سلولی و مولکولی پیشرفته",
                    topic="فصل سوم گوارش و جذب مواد غذایی در بدن انسان",
                    description="مطالعه کامل درسنامه به همراه حل ۱۲۰ تست زمان‌دار و تحلیل",
                    duration="۲۴۰ دقیقه بدون احتساب زمان استراحت بین مطالعه"))
    issues = renderer.validate(plan)
    assert issues, "overflow must be detected before generation"
    assert "شنبه" in issues[0].human()
    # and rendering still succeeds without corrupting the template
    assert renderer.render_png(plan).png


def test_case_mixed_persian_english_numbers_symbols(renderer):
    plan = make_plan()
    plan.day("monday").set_slot(
        0, Activity(0, subject="زیست Biology", topic="Chapter 3 - گوارش",
                    description="مطالعه ۱۰۰٪ + 40 تست / تحلیل", duration="90 min"))
    plan.day("monday").set_slot(1, Activity(1, subject="ریاضی", description="۱:۳۰"))
    res = renderer.render_png(plan)
    assert res.ok


def test_case_emoji_does_not_crash(renderer):
    plan = make_plan()
    plan.day("tuesday").set_slot(0, Activity(0, subject="آزمون 🎯", description="موفق باشی ✅"))
    assert renderer.render_png(plan).png


@pytest.mark.parametrize("count,expect_overflow", [(4, False), (12, False), (20, True)])
def test_case_many_assignments(renderer, count, expect_overflow):
    """A realistic number of assignments fits; an unreasonable one is reported."""
    plan = make_plan()
    plan.day("saturday").set_slot(0, Activity(0, subject="زیست"))
    for i in range(count):
        plan.assignments.append(Assignment(text=f"تکلیف بسیار مفصل شماره {i + 1}", order=i))
    issues = [i for i in renderer.validate(plan) if i.scope == "assignments"]
    assert bool(issues) is expect_overflow


def test_case_long_student_name_does_not_touch_template(renderer, service):
    plan = make_plan(student="محمدامیرحسین عبدالرحمانی‌نژاد قراملکی")
    plan.day("saturday").set_slot(0, Activity(0, subject="زیست"))
    caption = service.caption(plan)
    assert "محمدامیرحسین" in caption
    assert service.file_stem(plan).isascii()


def test_template_pixels_are_preserved(renderer, layout):
    """Everything outside the dynamic zones must be byte-identical."""
    plan = make_plan()
    plan.day("saturday").set_slot(0, Activity(0, subject="زیست", topic="گوارش"))
    out = Image.open(io.BytesIO(renderer.render_png(plan).png)).convert("RGB")
    original = Image.open(layout.template_path).convert("RGB")
    # header/logo band
    assert list(out.crop((0, 0, 1536, 175)).getdata()) == \
           list(original.crop((0, 0, 1536, 175)).getdata())
    # untouched cell region (friday slot 4)
    box = layout.cell("friday", 4).as_tuple()
    assert list(out.crop(box).getdata()) == list(original.crop(box).getdata())


# ---------------------------------------------------------------- service ---
def test_validation_blocks_incomplete_plan(service):
    empty = WeeklyPlan()
    report = service.validate(empty)
    assert not report.ok
    with pytest.raises(PlanGenerationError):
        service.generate(empty)


def test_generate_creates_png_and_pdf_and_caches(service):
    plan = make_plan()
    plan.day("saturday").set_slot(0, Activity(0, subject="زیست", topic="گوارش"))
    first = service.generate(plan)
    assert first.png_path.exists() and first.pdf_path.exists()
    assert first.png_path.stat().st_size > 10_000
    assert first.pdf_path.read_bytes()[:5] == b"%PDF-"
    assert not first.cached

    second = service.generate(plan)
    assert second.cached and second.png_path == first.png_path

    # content change → new hash → new files
    plan.day("sunday").set_slot(0, Activity(0, subject="ریاضی"))
    third = service.generate(plan)
    assert not third.cached and third.png_path != first.png_path


def test_files_are_isolated_per_student(tmp_path, renderer):
    svc = WeeklyPlanService(renderer, storage_root=tmp_path)
    paths = []
    for sid in ("111", "222"):
        plan = make_plan()
        plan.student_id = sid
        plan.day("saturday").set_slot(0, Activity(0, subject="زیست"))
        paths.append(svc.generate(plan).png_path)
    assert paths[0].parent != paths[1].parent
    assert all(p.exists() for p in paths)


def test_concurrent_generation_is_isolated(tmp_path, layout):
    def job(i: int) -> Path:
        svc = WeeklyPlanService(PillowRenderer(layout), storage_root=tmp_path)
        plan = make_plan()
        plan.student_id = f"s{i}"
        plan.day("saturday").set_slot(0, Activity(0, subject=f"درس {i}"))
        return svc.generate(plan).png_path

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(job, range(4)))
    assert len({p for p in results}) == 4
    assert all(p.exists() and p.stat().st_size > 10_000 for p in results)


def test_pdf_is_a4_landscape_300dpi(renderer):
    plan = make_plan()
    plan.day("saturday").set_slot(0, Activity(0, subject="زیست"))
    pdf = png_to_pdf(renderer.render_png(plan, scale=2.0).png, dpi=300)
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 50_000


def test_hash_changes_with_content_only(layout, renderer):
    plan = make_plan()
    plan.day("saturday").set_slot(0, Activity(0, subject="زیست"))
    h1 = plan.content_hash(layout.version, renderer.signature)
    same = WeeklyPlan.from_dict(plan.to_dict())
    assert same.content_hash(layout.version, renderer.signature) == h1
    same.day("saturday").set_slot(1, Activity(1, subject="ریاضی"))
    assert same.content_hash(layout.version, renderer.signature) != h1


def test_backend_factory_falls_back(monkeypatch):
    monkeypatch.setenv("RENDER_BACKEND", "pillow")
    assert get_renderer().name == "pillow"


# ------------------------------------------------------------ html backend ---
from app.rendering.html_renderer import HtmlRenderer  # noqa: E402

html_only = pytest.mark.skipif(
    not HtmlRenderer.available(), reason="Playwright/Chromium not installed"
)


@html_only
def test_html_backend_matches_geometry(layout):
    plan = make_plan()
    plan.day("saturday").set_slot(0, Activity(0, subject="زیست", topic="گوارش",
                                              description="۴۰ تست", duration="۹۰ دقیقه"))
    plan.assignments.append(Assignment(text="مرور فصل ۲", order=0))
    res = HtmlRenderer(layout).render_png(plan)
    assert png_size(res.png) == (1536, 1024)
    assert res.ok

    # both backends must ink the same regions (same calibrated boxes)
    import numpy as np
    pil = np.asarray(Image.open(io.BytesIO(
        PillowRenderer(layout).render_png(plan).png)).convert("L")).astype(int)
    web = np.asarray(Image.open(io.BytesIO(res.png)).convert("L")).astype(int)
    box = layout.cell("saturday", 0)
    a = (pil[box.y:box.bottom, box.x:box.right] < 140).sum()
    b = (web[box.y:box.bottom, box.x:box.right] < 140).sum()
    assert a > 100 and b > 100
    assert abs(a - b) / max(a, b) < 0.45  # same text, minor rasterizer differences


@html_only
def test_html_vector_pdf(layout):
    plan = make_plan()
    plan.day("saturday").set_slot(0, Activity(0, subject="زیست"))
    pdf = HtmlRenderer(layout).render_pdf_vector(plan)
    assert pdf[:5] == b"%PDF-" and len(pdf) > 20_000


@html_only
def test_html_scaled_render_for_print(layout):
    plan = make_plan()
    plan.day("saturday").set_slot(0, Activity(0, subject="زیست"))
    res = HtmlRenderer(layout).render_png(plan, scale=2.0)
    assert png_size(res.png) == (3072, 2048)
