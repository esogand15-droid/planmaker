"""End-to-end bot flow tests against an in-memory database and a mocked API."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.bot.main import build_dispatcher  # noqa: E402
from app.bot.texts import AssignCB, DayCB, Nav, PlanCB, SlotCB, StudentCB, WeekCB  # noqa: E402
from app.db.models import Base, PlanStatusDB, Role  # noqa: E402
from app.domain.persian import saturday_of  # noqa: E402
from app.rendering.factory import get_renderer  # noqa: E402
from app.repositories.repositories import PlanRepository, UserRepository  # noqa: E402
from app.services.plan_manager import AccessDenied, PlanManager  # noqa: E402
from app.services.plan_service import WeeklyPlanService  # noqa: E402
from app.services.render_queue import RenderQueue  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from tests.mocks import callback_update, make_bot, message_update  # noqa: E402

ADVISOR_TG = 111
STUDENT_TG = 222
OUTSIDER_TG = 333


@pytest_asyncio.fixture()
async def sessionmaker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture()
async def seed(sessionmaker):
    """One advisor with one assigned student, plus an unrelated advisor."""
    async with sessionmaker() as s:
        users = UserRepository(s)
        advisor = await users.create("مشاور تست", Role.ADVISOR, telegram_id=ADVISOR_TG)
        student = await users.create("علی رضایی", Role.STUDENT, telegram_id=STUDENT_TG)
        other = await users.create("مشاور دیگر", Role.ADVISOR, telegram_id=OUTSIDER_TG)
        orphan = await users.create("دانش‌آموز بی‌ربط", Role.STUDENT)
        await users.link_student(advisor.id, student.id)
        await s.commit()
        return {
            "advisor_id": advisor.id,
            "student_id": student.id,
            "other_id": other.id,
            "orphan_id": orphan.id,
        }


@pytest.fixture()
def queue(tmp_path) -> RenderQueue:
    service = WeeklyPlanService(get_renderer("pillow"), storage_root=tmp_path / "generated")
    return RenderQueue(service, max_concurrent=2)


@pytest.fixture()
def bot_and_dp(queue, sessionmaker):
    # aiogram routers are module-level singletons; detach them so each test can
    # build a fresh dispatcher (production creates exactly one).
    from app.bot.handlers import advisor as advisor_mod
    from app.bot.handlers import common as common_mod
    from app.bot.handlers import student as student_mod

    for module in (common_mod, student_mod, advisor_mod):
        module.router._parent_router = None

    bot, session = make_bot()
    dp = build_dispatcher(queue, sessionmaker)
    return bot, session, dp


# ------------------------------------------------------------- repository ---
async def test_students_search_and_pagination(sessionmaker, seed):
    async with sessionmaker() as s:
        users = UserRepository(s)
        for i in range(12):
            extra = await users.create(f"دانش‌آموز {i}", Role.STUDENT)
            await users.link_student(seed["advisor_id"], extra.id)
        await s.commit()

        page1 = await users.students_of(seed["advisor_id"], limit=8, offset=0)
        page2 = await users.students_of(seed["advisor_id"], limit=8, offset=8)
        assert len(page1) == 8 and len(page2) == 5
        assert await users.count_students_of(seed["advisor_id"]) == 13
        found = await users.students_of(seed["advisor_id"], query="علی")
        assert [u.full_name for u in found] == ["علی رضایی"]


async def test_authorization_matrix(sessionmaker, seed):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(seed["advisor_id"])
        other = await manager.users.by_id(seed["other_id"])
        student = await manager.users.by_id(seed["student_id"])

        plan = await manager.create_plan(advisor, seed["student_id"], saturday_of(date.today()))
        await s.commit()

        # advisor owns it, student may view it, outsider may not
        await manager.ensure_can_edit_plan(advisor, plan)
        await manager.ensure_can_view_plan(student, plan)
        with pytest.raises(AccessDenied):
            await manager.ensure_can_edit_plan(other, plan)
        with pytest.raises(AccessDenied):
            await manager.ensure_can_view_plan(other, plan)
        with pytest.raises(AccessDenied):
            await manager.ensure_owns_student(other, seed["student_id"])
        with pytest.raises(AccessDenied):
            await manager.ensure_owns_student(advisor, seed["orphan_id"])


async def test_create_plan_is_idempotent_per_week(sessionmaker, seed):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(seed["advisor_id"])
        start = saturday_of(date.today())
        first = await manager.create_plan(advisor, seed["student_id"], start)
        second = await manager.create_plan(advisor, seed["student_id"], start)
        assert first.id == second.id
        assert len(first.days) == 7
        assert first.week_end == start + timedelta(days=6)


async def test_editing_generated_plan_bumps_version(sessionmaker, seed):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(seed["advisor_id"])
        plan = await manager.create_plan(advisor, seed["student_id"], saturday_of(date.today()))
        await manager.plans.mark_generated(
            plan, image_path="a.png", pdf_path="a.pdf", plan_hash="h",
            template_version="t", renderer_version="r", duration_ms=1,
        )
        assert plan.status == PlanStatusDB.GENERATED and plan.version == 1

        from app.domain.models import Activity

        await manager.set_slot(advisor, plan.id, "saturday", 0, Activity(0, subject="زیست"))
        assert plan.version == 2 and plan.status == PlanStatusDB.DRAFT
        assert len(plan.files) == 1  # the old artefact stays recoverable


async def test_copy_previous_week(sessionmaker, seed):
    from app.domain.models import Activity

    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(seed["advisor_id"])
        last_week = saturday_of(date.today()) - timedelta(days=7)
        old = await manager.create_plan(advisor, seed["student_id"], last_week)
        await manager.set_slot(advisor, old.id, "saturday", 0,
                               Activity(0, subject="زیست", topic="گوارش"))
        await manager.set_assignments(advisor, old.id, ["مرور فصل ۲"])

        new = await manager.create_plan(advisor, seed["student_id"], saturday_of(date.today()))
        copied = await manager.copy_previous_week(advisor, new.id)
        await s.commit()

        assert copied == 1
        domain = PlanManager.to_domain(new)
        assert domain.day("saturday").slot(0).subject == "زیست"
        assert [a.text for a in domain.assignments] == ["مرور فصل ۲"]


async def test_copy_day_and_clear_day(sessionmaker, seed):
    from app.domain.models import Activity

    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(seed["advisor_id"])
        plan = await manager.create_plan(advisor, seed["student_id"], saturday_of(date.today()))
        await manager.set_slot(advisor, plan.id, "saturday", 0, Activity(0, subject="زیست"))
        await manager.copy_day(advisor, plan.id, "saturday", "monday")
        assert PlanManager.to_domain(plan).day("monday").slot(0).subject == "زیست"

        await manager.clear_day(advisor, plan.id, "monday")
        assert PlanManager.to_domain(plan).day("monday").is_empty


# ------------------------------------------------------------- bot flow -----
async def test_full_advisor_flow(bot_and_dp, sessionmaker, seed):
    bot, api, dp = bot_and_dp

    # /start → advisor menu
    await dp.feed_update(bot, message_update("/start", ADVISOR_TG, 1))
    assert "برنامه هفتگی" in api.texts()[-1]
    assert "➕ برنامه جدید" in api.last_markup_buttons()

    # new plan → student list
    api.clear()
    await dp.feed_update(bot, callback_update(Nav(to="new").pack(), ADVISOR_TG, 2))
    assert "انتخاب دانش‌آموز" in api.texts()[-1]

    # pick student → week choices
    api.clear()
    await dp.feed_update(
        bot,
        callback_update(
            StudentCB(action="pick", student_id=seed["student_id"]).pack(), ADVISOR_TG, 3
        ),
    )
    assert "انتخاب هفته" in api.texts()[-1]

    # pick this week → plan created, days overview
    api.clear()
    await dp.feed_update(
        bot,
        callback_update(
            WeekCB(action="pick", student_id=seed["student_id"], offset=0).pack(),
            ADVISOR_TG, 4,
        ),
    )
    assert "شنبه" in " ".join(api.last_markup_buttons())

    async with sessionmaker() as s:
        plan = (await PlanRepository(s).drafts_of(seed["advisor_id"]))[0]
        plan_id = plan.id

    # open saturday, edit slot 1 through free text
    api.clear()
    await dp.feed_update(
        bot, callback_update(DayCB(action="open", plan_id=plan_id, day="saturday").pack(),
                             ADVISOR_TG, 5)
    )
    await dp.feed_update(
        bot, callback_update(
            SlotCB(action="edit", plan_id=plan_id, day="saturday", slot=0).pack(),
            ADVISOR_TG, 6)
    )
    api.clear()
    await dp.feed_update(
        bot, message_update("زیست | گوارش | ۴۰ تست | ۹۰ دقیقه", ADVISOR_TG, 7)
    )
    assert "ذخیره شد" in api.texts()[0]

    # the wizard advances to the next slot automatically
    await dp.feed_update(bot, message_update("ریاضی | تابع | ۳۰ تست", ADVISOR_TG, 8))
    async with sessionmaker() as s:
        domain = PlanManager.to_domain(await PlanRepository(s).get(plan_id))
        assert domain.day("saturday").filled_count == 2
        assert domain.day("saturday").slot(1).subject == "ریاضی"

    # assignments
    api.clear()
    await dp.feed_update(
        bot, callback_update(AssignCB(action="open", plan_id=plan_id).pack(), ADVISOR_TG, 9)
    )
    await dp.feed_update(
        bot, message_update("مرور فصل ۲ زیست\nحل ۵۰ تست ریاضی", ADVISOR_TG, 10)
    )
    async with sessionmaker() as s:
        domain = PlanManager.to_domain(await PlanRepository(s).get(plan_id))
        assert [a.text for a in domain.assignments] == ["مرور فصل ۲ زیست", "حل ۵۰ تست ریاضی"]

    # confirm summary
    api.clear()
    await dp.feed_update(
        bot, callback_update(PlanCB(action="confirm", plan_id=plan_id).pack(), ADVISOR_TG, 11)
    )
    assert "خلاصه برنامه" in api.texts()[-1]

    # generate → photo + pdf + action card
    api.clear()
    await dp.feed_update(
        bot, callback_update(PlanCB(action="generate", plan_id=plan_id).pack(), ADVISOR_TG, 12)
    )
    assert len(api.calls("SendPhoto")) == 1
    assert len(api.calls("SendDocument")) == 1
    assert "آماده شد" in api.texts()[-1]
    assert "📤 ارسال برای دانش‌آموز" in api.last_markup_buttons()

    async with sessionmaker() as s:
        plan = await PlanRepository(s).get(plan_id)
        assert plan.status == PlanStatusDB.GENERATED
        assert Path(plan.image_path).exists() and Path(plan.pdf_path).exists()
        assert len(plan.files) == 1

    # send to the student
    api.clear()
    await dp.feed_update(
        bot, callback_update(PlanCB(action="send", plan_id=plan_id).pack(), ADVISOR_TG, 13)
    )
    photo_targets = [m.chat_id for m in api.calls("SendPhoto")]
    assert STUDENT_TG in photo_targets
    assert any("ارسال شد" in t for t in api.texts())

    async with sessionmaker() as s:
        plan = await PlanRepository(s).get(plan_id)
        assert plan.status == PlanStatusDB.SENT and plan.sent_at is not None

    # student side: /start shows the plan menu, and can fetch the last plan
    api.clear()
    await dp.feed_update(bot, message_update("/start", STUDENT_TG, 14))
    assert "برنامه این هفته" in " ".join(api.last_markup_buttons())
    api.clear()
    await dp.feed_update(bot, callback_update(Nav(to="my_last").pack(), STUDENT_TG, 15))
    assert len(api.calls("SendPhoto")) == 1


async def test_draft_survives_and_resumes(bot_and_dp, sessionmaker, seed):
    bot, api, dp = bot_and_dp
    await dp.feed_update(bot, message_update("/start", ADVISOR_TG, 1))
    await dp.feed_update(bot, callback_update(Nav(to="new").pack(), ADVISOR_TG, 2))
    await dp.feed_update(
        bot,
        callback_update(
            StudentCB(action="pick", student_id=seed["student_id"]).pack(), ADVISOR_TG, 3
        ),
    )
    await dp.feed_update(
        bot,
        callback_update(
            WeekCB(action="pick", student_id=seed["student_id"], offset=0).pack(),
            ADVISOR_TG, 4,
        ),
    )
    async with sessionmaker() as s:
        plan_id = (await PlanRepository(s).drafts_of(seed["advisor_id"]))[0].id

    await dp.feed_update(
        bot, callback_update(
            SlotCB(action="edit", plan_id=plan_id, day="sunday", slot=2).pack(), ADVISOR_TG, 5)
    )
    await dp.feed_update(bot, message_update("فیزیک | نوسان | ۹۰ دقیقه", ADVISOR_TG, 6))

    # simulate a crash: state is dropped, database keeps the draft
    await dp.storage.close()
    api.clear()
    await dp.feed_update(bot, message_update("/start", ADVISOR_TG, 7))
    await dp.feed_update(bot, callback_update(Nav(to="drafts").pack(), ADVISOR_TG, 8))
    assert "پیش‌نویس" in api.texts()[-1]

    async with sessionmaker() as s:
        domain = PlanManager.to_domain(await PlanRepository(s).get(plan_id))
        assert domain.day("sunday").slot(2).subject == "فیزیک"


async def test_cannot_touch_other_advisors_plan(bot_and_dp, sessionmaker, seed):
    bot, api, dp = bot_and_dp
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(seed["advisor_id"])
        plan = await manager.create_plan(advisor, seed["student_id"], saturday_of(date.today()))
        await s.commit()
        plan_id = plan.id

    api.clear()
    await dp.feed_update(
        bot, callback_update(PlanCB(action="days", plan_id=plan_id).pack(), OUTSIDER_TG, 1)
    )
    assert any("دسترسی" in t or "در دسترس شما نیست" in t for t in api.texts())


async def test_generation_blocked_when_plan_is_empty(bot_and_dp, sessionmaker, seed):
    bot, api, dp = bot_and_dp
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(seed["advisor_id"])
        plan = await manager.create_plan(advisor, seed["student_id"], saturday_of(date.today()))
        await s.commit()
        plan_id = plan.id

    api.clear()
    await dp.feed_update(
        bot, callback_update(PlanCB(action="confirm", plan_id=plan_id).pack(), ADVISOR_TG, 1)
    )
    assert any("قابل تولید نیست" in t for t in api.texts())


async def test_overflow_warning_on_long_slot_text(bot_and_dp, sessionmaker, seed):
    bot, api, dp = bot_and_dp
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(seed["advisor_id"])
        plan = await manager.create_plan(advisor, seed["student_id"], saturday_of(date.today()))
        await s.commit()
        plan_id = plan.id

    await dp.feed_update(
        bot, callback_update(
            SlotCB(action="edit", plan_id=plan_id, day="saturday", slot=0).pack(),
            ADVISOR_TG, 1)
    )
    api.clear()
    long_text = (
        "زیست‌شناسی سلولی و مولکولی پیشرفته | فصل سوم گوارش و جذب مواد غذایی در بدن "
        "| مطالعه کامل درسنامه به همراه حل ۱۲۰ تست زمان‌دار و تحلیل کامل | ۲۴۰ دقیقه بدون وقفه"
    )
    await dp.feed_update(bot, message_update(long_text, ADVISOR_TG, 2))
    assert any("⚠️" in t for t in api.texts())


async def test_concurrent_generation_two_advisors(queue, sessionmaker, seed):
    """Two advisors generating at the same time get isolated, valid files."""
    import asyncio

    from app.domain.models import Activity

    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(seed["advisor_id"])
        other = await manager.users.by_id(seed["other_id"])
        second_student = await manager.users.create("سارا محمدی", Role.STUDENT)
        await manager.users.link_student(other.id, second_student.id)

        plan_a = await manager.create_plan(advisor, seed["student_id"], saturday_of(date.today()))
        plan_b = await manager.create_plan(other, second_student.id, saturday_of(date.today()))
        await manager.set_slot(advisor, plan_a.id, "saturday", 0, Activity(0, subject="زیست"))
        await manager.set_slot(other, plan_b.id, "saturday", 0, Activity(0, subject="شیمی"))
        await s.commit()

        domain_a = PlanManager.to_domain(plan_a)
        domain_b = PlanManager.to_domain(plan_b)

    results = await asyncio.gather(queue.generate(domain_a), queue.generate(domain_b))
    assert results[0].png_path != results[1].png_path
    assert all(r.png_path.exists() and r.pdf_path.exists() for r in results)
    assert all(r.png_path.stat().st_size > 10_000 for r in results)


async def test_audit_trail_records_actions(sessionmaker, seed):
    from app.domain.models import Activity

    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(seed["advisor_id"])
        plan = await manager.create_plan(advisor, seed["student_id"], saturday_of(date.today()))
        await manager.set_slot(advisor, plan.id, "saturday", 0, Activity(0, subject="زیست"))
        await manager.mark_sent(advisor, plan)
        await manager.delete_plan(advisor, plan.id)
        await s.commit()

        actions = [a.action for a in await manager.audit.recent()]
        assert {"plan.created", "plan.edited", "plan.sent", "plan.deleted"} <= set(actions)
