"""Jalali date engine, custom ranges and the invite-link UX in the profile."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.bot.main import build_dispatcher  # noqa: E402
from app.bot.texts import StudentCB, WeekCB  # noqa: E402
from app.db.models import Base, Role  # noqa: E402
from app.domain.calendar import (  # noqa: E402
    MAX_PLAN_RANGE_DAYS,
    DateRangeError,
    JalaliDate,
)
from app.domain.models import WeeklyPlan  # noqa: E402
from app.rendering.factory import get_renderer  # noqa: E402
from app.repositories.repositories import PlanRepository, UserRepository  # noqa: E402
from app.services.plan_manager import PlanManager  # noqa: E402
from app.services.plan_service import WeeklyPlanService  # noqa: E402
from app.services.render_queue import RenderQueue  # noqa: E402
from tests.mocks import callback_update, make_bot, message_update  # noqa: E402

ADVISOR_TG = 4001
STUDENT_TG = 4002


# ════════════════════════ 1. the calendar engine itself ════════════════════
def test_weekday_comes_from_the_real_calendar():
    """The exact scenario from the brief: 26 → 29 Mordad 1405."""
    days = JalaliDate.range(JalaliDate.parse("1405/05/26"), JalaliDate.parse("1405/05/29"))
    assert [(d.index, d.weekday_fa, d.short) for d in days] == [
        (1, "دوشنبه", "۲۶ مرداد"),
        (2, "سه‌شنبه", "۲۷ مرداد"),
        (3, "چهارشنبه", "۲۸ مرداد"),
        (4, "پنج‌شنبه", "۲۹ مرداد"),
    ]
    # …and the weekday really is Monday, verified against the gregorian date
    assert JalaliDate.parse("1405/05/26").weekday() == 0  # Monday


@pytest.mark.parametrize(
    "jalali,expected",
    [
        ("1405/05/24", "شنبه"),
        ("1405/05/25", "یکشنبه"),
        ("1405/05/26", "دوشنبه"),
        ("1405/05/27", "سه‌شنبه"),
        ("1405/05/28", "چهارشنبه"),
        ("1405/05/29", "پنج‌شنبه"),
        ("1405/05/30", "جمعه"),
        ("1405/05/31", "شنبه"),
    ],
)
def test_weekday_mapping_is_exact(jalali, expected):
    assert JalaliDate.weekday_fa(JalaliDate.parse(jalali)) == expected


def test_no_hardcoded_saturday_assumption():
    """A range starting mid-week must not pretend day 1 is Saturday."""
    days = JalaliDate.range(JalaliDate.parse("1405/05/27"), JalaliDate.parse("1405/05/29"))
    assert days[0].weekday_key != "saturday"
    assert [d.weekday_key for d in days] == ["tuesday", "wednesday", "thursday"]


def test_single_day_range():
    day = JalaliDate.parse("1405/05/26")
    days = JalaliDate.range(day, day)
    assert len(days) == 1 and days[0].index == 1
    assert JalaliDate.range_label(day, day) == "۲۶ مرداد ۱۴۰۵"


def test_reversed_range_is_rejected():
    start, end = JalaliDate.parse("1405/05/29"), JalaliDate.parse("1405/05/26")
    with pytest.raises(DateRangeError) as exc:
        JalaliDate.range(start, end)
    assert "پایان" in str(exc.value)


def test_too_long_range_is_rejected():
    start = JalaliDate.parse("1405/05/01")
    with pytest.raises(DateRangeError):
        JalaliDate.range(start, JalaliDate.add_days(start, MAX_PLAN_RANGE_DAYS))
    # exactly at the limit is fine
    assert len(JalaliDate.range(start, JalaliDate.add_days(start, MAX_PLAN_RANGE_DAYS - 1))) \
        == MAX_PLAN_RANGE_DAYS


def test_full_calendar_week():
    days = JalaliDate.week_range(JalaliDate.parse("1405/05/26"))
    assert JalaliDate.is_calendar_week(days)
    assert [d.weekday_key for d in days][0] == "saturday"
    assert [d.weekday_key for d in days][-1] == "friday"
    assert days[0].date == JalaliDate.parse("1405/05/24")


def test_range_crossing_a_week_boundary():
    days = JalaliDate.range(JalaliDate.parse("1405/07/03"), JalaliDate.parse("1405/07/05"))
    assert [d.weekday_fa for d in days] == ["جمعه", "شنبه", "یکشنبه"]
    assert not JalaliDate.is_calendar_week(days)


def test_range_crossing_a_month_boundary():
    days = JalaliDate.range(JalaliDate.parse("1405/05/30"), JalaliDate.parse("1405/06/02"))
    assert [d.short for d in days] == ["۳۰ مرداد", "۳۱ مرداد", "۱ شهریور", "۲ شهریور"]
    label = JalaliDate.range_label(days[0].date, days[-1].date)
    assert "مرداد" in label and "شهریور" in label


def test_parse_accepts_persian_digits_and_separators():
    expected = JalaliDate.parse("1405/05/26")
    for text in ("۱۴۰۵/۰۵/۲۶", "1405-05-26", "۱۴۰۵ ۰۵ ۲۶", " 1405.05.26 "):
        assert JalaliDate.parse(text) == expected
    for bad in ("فردا", "1405/13/01", "1405/05/32", "", "12"):
        with pytest.raises(DateRangeError):
            JalaliDate.parse(bad)


def test_parse_range_in_one_message():
    start, end = JalaliDate.parse_range("۱۴۰۵/۰۵/۲۶ تا ۱۴۰۵/۰۵/۲۹")
    assert JalaliDate.validate_range(start, end) == 4
    with pytest.raises(DateRangeError):
        JalaliDate.parse_range("۱۴۰۵/۰۵/۲۶")


def test_tehran_timezone_is_authoritative(monkeypatch):
    import app.domain.calendar as cal

    moment = datetime(2026, 8, 16, 1, 0, tzinfo=ZoneInfo("Asia/Tehran"))
    assert moment.astimezone(ZoneInfo("UTC")).date() == date(2026, 8, 15)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment

    monkeypatch.setattr(cal, "datetime", _Frozen)
    assert cal.JalaliDate.today() == date(2026, 8, 16)


# ════════════════════════ 2. the domain honours ranges ═════════════════════
def test_partial_range_leaves_other_template_rows_empty():
    plan = WeeklyPlan(student_name="علی", student_id="1")
    plan.apply_range(JalaliDate.parse("1405/05/26"), JalaliDate.parse("1405/05/29"))

    assert plan.day_count == 4 and not plan.is_calendar_week
    assert [d.weekday for d in plan.plan_days] == [
        "monday", "tuesday", "wednesday", "thursday"
    ]
    # the official 7-row sheet keeps its other rows blank
    assert {d.weekday for d in plan.days if d.date is None} == {
        "saturday", "sunday", "friday"
    }


def test_plan_days_are_always_chronological():
    plan = WeeklyPlan(student_name="علی", student_id="1")
    plan.apply_range(JalaliDate.parse("1405/07/03"), JalaliDate.parse("1405/07/05"))
    dates = [d.date for d in plan.plan_days]
    assert dates == sorted(dates)
    assert [d.fa_name for d in plan.plan_days] == ["جمعه", "شنبه", "یکشنبه"]


def test_calendar_week_mode_snaps_to_saturday():
    plan = WeeklyPlan(student_name="علی", student_id="1")
    plan.apply_week_start(JalaliDate.parse("1405/05/26"))   # a Monday
    assert plan.is_calendar_week and plan.day_count == 7
    assert plan.week_start == JalaliDate.parse("1405/05/24")
    assert plan.day("saturday").date == plan.week_start


def test_stats_only_count_days_inside_the_range():
    from app.domain.models import Activity

    plan = WeeklyPlan(student_name="علی", student_id="1")
    plan.apply_range(JalaliDate.parse("1405/05/26"), JalaliDate.parse("1405/05/28"))
    plan.day("monday").set_slot(0, Activity(0, subject="زیست"))
    plan.day("saturday").set_slot(0, Activity(0, subject="خارج از بازه"))
    assert plan.activity_count == 1 and plan.filled_days == 1


# ═══════════════════════ 3. renderer keeps the template ════════════════════
def test_partial_range_renders_on_the_official_template(tmp_path):
    from app.domain.models import Activity

    plan = WeeklyPlan(student_name="علی رضایی", student_id="1")
    plan.apply_range(JalaliDate.parse("1405/05/26"), JalaliDate.parse("1405/05/29"))
    plan.day("monday").set_slot(0, Activity(0, subject="زیست", topic="گوارش"))

    service = WeeklyPlanService(get_renderer("pillow"), storage_root=tmp_path)
    result = service.generate(plan, force=True)
    assert result.png_path.exists() and result.pdf_path.exists()

    # rows outside the range stay pixel-identical to the blank template
    from PIL import Image

    layout = service.renderer.layout
    rendered = Image.open(result.png_path).convert("RGB")
    original = Image.open(layout.template_path).convert("RGB")
    for weekday in ("saturday", "sunday", "friday"):
        box = layout.cell(weekday, 0).as_tuple()
        assert list(rendered.crop(box).getdata()) == list(original.crop(box).getdata())


# ══════════════════════════ 4. the flow in the bot ═════════════════════════
@pytest_asyncio.fixture()
async def sessionmaker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture()
def queue(tmp_path):
    service = WeeklyPlanService(get_renderer("pillow"), storage_root=tmp_path / "generated")
    return RenderQueue(service, max_concurrent=2)


@pytest.fixture()
def bot_and_dp(queue, sessionmaker):
    bot, api = make_bot()
    return bot, api, build_dispatcher(queue, sessionmaker)


@pytest_asyncio.fixture()
async def world(sessionmaker):
    async with sessionmaker() as s:
        users = UserRepository(s)
        advisor = await users.create("مشاور", Role.ADVISOR, telegram_id=ADVISOR_TG)
        manager = PlanManager(s)
        student = await manager.create_student(advisor, "علی رضایی", "دوازدهم")
        await s.commit()
        return {"advisor": advisor.id, "student": student.id,
                "token": student.invite_token}


def texts(api):
    return " ".join(api.texts())


def buttons(api):
    out = []
    for r in api.requests:
        markup = getattr(r, "reply_markup", None)
        if markup and getattr(markup, "inline_keyboard", None):
            out += [b.text for row in markup.inline_keyboard for b in row]
    return out


async def test_custom_range_end_to_end(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    sid = world["student"]

    await dp.feed_update(bot, callback_update(
        StudentCB(action="pick", student_id=sid, mode="card").pack(), ADVISOR_TG, 1))
    api.clear()
    await dp.feed_update(bot, callback_update(
        WeekCB(action="custom", student_id=sid).pack(), ADVISOR_TG, 2))
    assert "تاریخ شروع" in texts(api)

    api.clear()
    await dp.feed_update(bot, message_update("۱۴۰۵/۰۵/۲۶", ADVISOR_TG, 3))
    assert "تاریخ پایان" in texts(api) and "دوشنبه" in texts(api)

    api.clear()
    await dp.feed_update(bot, message_update("۱۴۰۵/۰۵/۲۹", ADVISOR_TG, 4))
    body = texts(api)
    assert "خلاصه بازه" in body and "تعداد روز: ۴" in body
    for expected in ("۱. دوشنبه", "۲. سه‌شنبه", "۳. چهارشنبه", "۴. پنج‌شنبه"):
        assert expected in body

    api.clear()
    await dp.feed_update(bot, callback_update(
        WeekCB(action="confirm", student_id=sid).pack(), ADVISOR_TG, 5))
    labels = buttons(api)
    assert any("دوشنبه" in b for b in labels) and any("۲۶ مرداد" in b for b in labels)
    assert not any("جمعه" in b for b in labels), "days outside the range must not appear"

    async with sessionmaker() as s:
        repo = PlanRepository(s)
        listed = (await repo.drafts_of(world["advisor"]))[0]   # light list query
        assert listed.week_start == JalaliDate.parse("1405/05/26")
        assert listed.week_end == JalaliDate.parse("1405/05/29")
        plan = await repo.get(listed.id)                        # full load
        assert [d.weekday for d in plan.days] == [
            "monday", "tuesday", "wednesday", "thursday"
        ]


async def test_both_dates_in_one_message(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    await dp.feed_update(bot, callback_update(
        WeekCB(action="custom", student_id=world["student"]).pack(), ADVISOR_TG, 1))
    api.clear()
    await dp.feed_update(bot, message_update("۱۴۰۵/۰۵/۲۶ تا ۱۴۰۵/۰۵/۲۸", ADVISOR_TG, 2))
    assert "خلاصه بازه" in texts(api) and "تعداد روز: ۳" in texts(api)


async def test_calendar_week_mode_is_unchanged(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(
        WeekCB(action="pick", student_id=world["student"], offset=0).pack(), ADVISOR_TG, 1))
    labels = " ".join(buttons(api))
    for day in ("شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنج‌شنبه", "جمعه"):
        assert day in labels
    async with sessionmaker() as s:
        plan = (await PlanRepository(s).drafts_of(world["advisor"]))[0]
        assert (plan.week_end - plan.week_start).days == 6
        assert plan.week_start.weekday() == 5   # a real Saturday


async def test_too_long_range_is_refused_in_the_bot(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    await dp.feed_update(bot, callback_update(
        WeekCB(action="custom", student_id=world["student"]).pack(), ADVISOR_TG, 1))
    api.clear()
    await dp.feed_update(bot, message_update("۱۴۰۵/۰۵/۰۱ تا ۱۴۰۵/۰۵/۲۰", ADVISOR_TG, 2))
    assert "حداکثر طول بازه" in texts(api)


# ═════════════════ 5. invite link lives in the student profile ═════════════
async def test_profile_shows_the_real_invite_link(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(
        StudentCB(action="card", student_id=world["student"], mode="card").pack(),
        ADVISOR_TG, 1))
    body = texts(api)
    assert "وضعیت اتصال" in body
    assert "لینک دعوت صادر شده" in body
    assert f"?start=inv_{world['token']}" in body, "the real, usable link must be shown"
    assert "صادر شده:" in body and "اعتبار تا:" in body
    labels = buttons(api)
    assert any("کپی لینک" in b for b in labels) and any("ارسال لینک" in b for b in labels)


async def test_copy_and_share_produce_a_usable_link(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    for action in ("copylink", "sharelink"):
        api.clear()
        await dp.feed_update(bot, callback_update(
            StudentCB(action=action, student_id=world["student"]).pack(), ADVISOR_TG, 1))
        assert f"?start=inv_{world['token']}" in texts(api)


async def test_new_link_warns_before_invalidating_the_old_one(
    bot_and_dp, sessionmaker, world
):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(
        StudentCB(action="ask_invite", student_id=world["student"]).pack(), ADVISOR_TG, 1))
    assert "لینک قبلی باطل می‌شود" in texts(api)
    async with sessionmaker() as s:  # nothing changed yet
        assert (await UserRepository(s).by_id(world["student"])).invite_token == world["token"]

    api.clear()
    await dp.feed_update(bot, callback_update(
        StudentCB(action="invite", student_id=world["student"]).pack(), ADVISOR_TG, 2))
    async with sessionmaker() as s:
        fresh = (await UserRepository(s).by_id(world["student"])).invite_token
    assert fresh and fresh != world["token"]
    assert f"?start=inv_{fresh}" in texts(api)


async def test_profile_states_reflect_reality(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    sid = world["student"]

    async def card() -> str:
        api.clear()
        await dp.feed_update(bot, callback_update(
            StudentCB(action="card", student_id=sid, mode="card").pack(), ADVISOR_TG, 1))
        return texts(api)

    # revoked → no active link is advertised
    await dp.feed_update(bot, callback_update(
        StudentCB(action="revoke", student_id=sid).pack(), ADVISOR_TG, 2))
    body = await card()
    assert "هنوز لینکی صادر نشده" in body and "?start=inv_" not in body

    # expired → clearly marked, link not offered
    async with sessionmaker() as s:
        student = await UserRepository(s).by_id(sid)
        await UserRepository(s).rotate_invite_token(student)
        student.invite_expires_at = datetime.now(ZoneInfo("Asia/Tehran")) - timedelta(days=1)
        await s.commit()
    body = await card()
    assert "منقضی" in body and "?start=inv_" not in body

    # connected → shows the telegram id, never a consumed token
    async with sessionmaker() as s:
        student = await UserRepository(s).by_id(sid)
        await UserRepository(s).claim_invite(student, STUDENT_TG, None)
        await s.commit()
    body = await card()
    assert "🟢 متصل" in body and str(STUDENT_TG) in body
    assert "?start=inv_" not in body


# ═══════════════ 6. performance guards (query counts, not wall clock) ══════
async def test_list_screens_do_not_scale_with_data(sessionmaker, queue):
    """Regression guard: no N+1 in the two heaviest list screens."""
    from sqlalchemy import event

    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.create("مشاور", Role.ADVISOR, telegram_id=6001)
        for k in range(12):
            await manager.users.create(f"مشاور {k}", Role.ADVISOR, telegram_id=6100 + k)
        base = JalaliDate.saturday_of(JalaliDate.today())
        for i in range(25):
            student = await manager.create_student(advisor, f"دانش‌آموز {i}")
            await manager.create_plan(advisor, student.id, base)
        await s.commit()

    counter = {"n": 0}
    engine = sessionmaker.kw["bind"]

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _count(conn, cursor, statement, params, context, executemany):
        counter["n"] += 1

    from app.services.admin_service import AdminService

    async with sessionmaker() as s:
        counter["n"] = 0
        await AdminService(s).advisors(6, 0)          # 13 advisors
        advisors_queries = counter["n"]

        counter["n"] = 0
        await PlanRepository(s).history(advisor_id=1, limit=6)
        history_queries = counter["n"]

    # grouped counts + light loading keep both constant
    assert advisors_queries <= 4, f"advisor list does {advisors_queries} queries (N+1?)"
    assert history_queries <= 3, f"plan list does {history_queries} queries (N+1?)"


async def test_full_plan_load_after_a_light_list_query(sessionmaker):
    """A light list must not leave a half-loaded object in the identity map."""
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.create("مشاور", Role.ADVISOR, telegram_id=6002)
        student = await manager.create_student(advisor, "علی رضایی")
        await manager.create_plan(advisor, student.id,
                                  JalaliDate.saturday_of(JalaliDate.today()))
        await s.commit()

    async with sessionmaker() as s:                    # one Telegram update
        repo = PlanRepository(s)
        listed = (await repo.drafts_of(1))[0]          # light query first
        full = await repo.get(listed.id)               # …then the full card
        assert len(full.days) == 7                     # no MissingGreenlet
        assert full.files == [] and full.assignments == []
        assert full.student.full_name == "علی رضایی"


async def test_range_plan_from_the_database_still_renders(sessionmaker, tmp_path):
    """Regression: a 4-day plan loaded from the DB must fill all seven rows."""
    from app.domain.models import Activity

    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.create("مشاور", Role.ADVISOR, telegram_id=6003)
        student = await manager.create_student(advisor, "علی رضایی")
        plan = await manager.create_plan(
            advisor, student.id,
            JalaliDate.parse("1405/05/26"), JalaliDate.parse("1405/05/29"),
        )
        await manager.set_slot(advisor, plan.id, "monday", 0,
                               Activity(0, subject="زیست", topic="گوارش"))
        await s.commit()
        domain = PlanManager.to_domain(plan)

    assert len(domain.days) == 7 and domain.day_count == 4
    service = WeeklyPlanService(get_renderer("pillow"), storage_root=tmp_path)
    assert service.validate(domain).ok           # used to raise KeyError('saturday')
    result = service.generate(domain, force=True)
    assert result.png_path.exists() and result.pdf_path.exists()
