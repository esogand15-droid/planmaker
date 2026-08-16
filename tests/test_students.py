"""Advisor-owned student onboarding, invites, timezone and retention (v1.1.0)."""
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
from app.bot.texts import ListCB, Nav, PlanCB, StudentCB  # noqa: E402
from app.db.models import Base, Role  # noqa: E402
from app.domain.models import Activity  # noqa: E402
from app.domain.persian import saturday_of, today_local  # noqa: E402
from app.rendering.factory import get_renderer  # noqa: E402
from app.repositories.repositories import UserRepository  # noqa: E402
from app.services.plan_manager import PlanManager, StudentError  # noqa: E402
from app.services.plan_service import WeeklyPlanService  # noqa: E402
from app.services.render_queue import RenderQueue  # noqa: E402
from tests.mocks import callback_update, make_bot, message_update  # noqa: E402

ADVISOR_TG = 501
OTHER_ADVISOR_TG = 502
NEWCOMER_TG = 503
ADMIN_TG = 504


@pytest_asyncio.fixture()
async def sessionmaker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture()
async def advisors(sessionmaker):
    async with sessionmaker() as s:
        users = UserRepository(s)
        a = await users.create("مشاور اول", Role.ADVISOR, telegram_id=ADVISOR_TG)
        b = await users.create("مشاور دوم", Role.ADVISOR, telegram_id=OTHER_ADVISOR_TG)
        await s.commit()
        return {"a": a.id, "b": b.id}


@pytest.fixture()
def queue(tmp_path):
    service = WeeklyPlanService(get_renderer("pillow"), storage_root=tmp_path / "generated")
    return RenderQueue(service, max_concurrent=2)


@pytest.fixture()
def bot_and_dp(queue, sessionmaker):
    from app.bot.handlers import advisor as advisor_mod
    from app.bot.handlers import common as common_mod
    from app.bot.handlers import fallback as fallback_mod
    from app.bot.handlers import student as student_mod

    for module in (common_mod, student_mod, advisor_mod, fallback_mod):
        module.router._parent_router = None
    bot, api = make_bot()
    return bot, api, build_dispatcher(queue, sessionmaker, admin_ids=(ADMIN_TG,))


def buttons(api):
    out = []
    for r in api.requests:
        markup = getattr(r, "reply_markup", None)
        if markup and getattr(markup, "inline_keyboard", None):
            out += [b.text for row in markup.inline_keyboard for b in row]
    return out


# ─────────────────────────── service layer ──────────────────────────────────
async def test_advisor_creates_own_student_without_admin(sessionmaker, advisors):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "علی رضایی", "دوازدهم تجربی")
        await s.commit()

        assert student.role == Role.STUDENT
        assert student.grade == "دوازدهم تجربی"
        assert student.created_by_id == advisor.id
        assert student.invite_token and len(student.invite_token) >= 16
        assert student.telegram_id is None and student.is_connected is False
        # linked immediately: it shows up in the advisor's roster
        roster = await manager.users.students_of(advisor.id)
        assert [u.full_name for u in roster] == ["علی رضایی"]


@pytest.mark.parametrize("bad", ["", " ", "ع", "  \n "])
async def test_student_name_validation(sessionmaker, advisors, bad):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        with pytest.raises(StudentError):
            await manager.create_student(advisor, bad)


async def test_duplicate_student_name_is_rejected(sessionmaker, advisors):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        await manager.create_student(advisor, "زهرا احمدی")
        with pytest.raises(StudentError):
            await manager.create_student(advisor, "زهرا  احمدی")  # whitespace-insensitive


async def test_two_advisors_may_have_same_named_students(sessionmaker, advisors):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        a = await manager.users.by_id(advisors["a"])
        b = await manager.users.by_id(advisors["b"])
        await manager.create_student(a, "علی رضایی")
        await manager.create_student(b, "علی رضایی")  # different roster: allowed
        await s.commit()
        assert len(await manager.users.students_of(a.id)) == 1
        assert len(await manager.users.students_of(b.id)) == 1


async def test_invite_claim_binds_telegram_account(sessionmaker, advisors):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "سارا محمدی")
        token = student.invite_token
        await s.commit()

        claimed = await manager.claim_invite(token, NEWCOMER_TG, "sara")
        await s.commit()
        assert claimed.id == student.id
        assert claimed.telegram_id == NEWCOMER_TG
        assert claimed.invite_token is None      # one-time use
        assert claimed.is_connected

        # replaying the same token must fail
        assert await manager.claim_invite(token, 99999, None) is None


async def test_claiming_folds_away_a_stray_duplicate_row(sessionmaker, advisors):
    """If the student had somehow been auto-created before, no duplicates remain."""
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        stray = await manager.users.create("ناشناس", Role.STUDENT, telegram_id=NEWCOMER_TG)
        student = await manager.create_student(advisor, "سارا محمدی")
        await s.commit()

        await manager.claim_invite(student.invite_token, NEWCOMER_TG, None)
        await s.commit()

        assert (await manager.users.by_telegram_id(NEWCOMER_TG)).id == student.id
        refreshed = await manager.users.by_id(stray.id)
        assert refreshed.telegram_id is None and refreshed.is_active is False


async def test_invite_refused_for_connected_student(sessionmaker, advisors):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "متصل")
        await manager.users.claim_invite(student, 777, None)
        with pytest.raises(StudentError):
            await manager.new_invite(advisor, student.id)


async def test_removing_student_keeps_their_plans(sessionmaker, advisors):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "رضا کریمی")
        plan = await manager.create_plan(advisor, student.id, saturday_of(today_local()))
        await s.commit()

        await manager.remove_student(advisor, student.id)
        await s.commit()

        assert await manager.users.students_of(advisor.id) == []
        assert await manager.plans.get(plan.id) is not None  # data preserved


async def test_advisor_cannot_manage_other_advisors_student(sessionmaker, advisors):
    from app.services.plan_manager import AccessDenied

    async with sessionmaker() as s:
        manager = PlanManager(s)
        a = await manager.users.by_id(advisors["a"])
        b = await manager.users.by_id(advisors["b"])
        student = await manager.create_student(a, "دانش‌آموز A")
        await s.commit()
        for call in (
            manager.get_student(b, student.id),
            manager.new_invite(b, student.id),
            manager.remove_student(b, student.id),
        ):
            with pytest.raises(AccessDenied):
                await call


# ─────────────────────────────── bot flow ───────────────────────────────────
async def test_full_onboarding_through_the_bot(bot_and_dp, sessionmaker, advisors):
    """Menu → دانش‌آموزان → ➕ افزودن → invite link → student claims it."""
    bot, api, dp = bot_and_dp

    api.clear()
    await dp.feed_update(bot, callback_update(Nav(to="students").pack(), ADVISOR_TG, 1))
    assert any("افزودن دانش‌آموز" in b for b in buttons(api))

    api.clear()
    await dp.feed_update(
        bot, callback_update(StudentCB(action="add", mode="card").pack(), ADVISOR_TG, 2)
    )
    assert any("نام و نام خانوادگی" in t for t in api.texts())

    api.clear()
    await dp.feed_update(bot, message_update("علی رضایی | دوازدهم تجربی", ADVISOR_TG, 3))
    text = " ".join(api.texts())
    assert "علی رضایی" in text
    assert "?start=inv_" in text, "the advisor must receive a usable invite link"

    async with sessionmaker() as s:
        student = (await UserRepository(s).students_of(advisors["a"]))[0]
        token = student.invite_token
        student_id = student.id
        assert student.grade == "دوازدهم تجربی"

    # the student opens the deep link
    api.clear()
    await dp.feed_update(bot, message_update(f"/start inv_{token}", NEWCOMER_TG, 4))
    assert any("خوش آمدی" in t for t in api.texts())

    async with sessionmaker() as s:
        student = await UserRepository(s).by_id(student_id)
        assert student.telegram_id == NEWCOMER_TG
        assert student.invite_token is None
        # and no duplicate row was created
        assert await UserRepository(s).count_all_students() == 1


async def test_created_student_is_immediately_usable_for_a_plan(
    bot_and_dp, sessionmaker, advisors
):
    bot, api, dp = bot_and_dp
    await dp.feed_update(
        bot, callback_update(StudentCB(action="add", mode="card").pack(), ADVISOR_TG, 1)
    )
    await dp.feed_update(bot, message_update("مهدی نوری", ADVISOR_TG, 2))
    async with sessionmaker() as s:
        student_id = (await UserRepository(s).students_of(advisors["a"]))[0].id

    api.clear()
    await dp.feed_update(
        bot,
        callback_update(
            StudentCB(action="pick", student_id=student_id, mode="card").pack(),
            ADVISOR_TG, 3,
        ),
    )
    assert any("انتخاب هفته" in t for t in api.texts())


async def test_student_card_shows_connection_state(bot_and_dp, sessionmaker, advisors):
    bot, api, dp = bot_and_dp
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "نیما صادقی", "یازدهم ریاضی")
        await s.commit()
        sid = student.id

    api.clear()
    await dp.feed_update(
        bot,
        callback_update(StudentCB(action="card", student_id=sid, mode="card").pack(),
                        ADVISOR_TG, 1),
    )
    text = " ".join(api.texts())
    assert "نیما صادقی" in text and "یازدهم ریاضی" in text
    assert "در انتظار اتصال" in text
    assert any("لینک دعوت" in b for b in buttons(api))


async def test_remove_student_from_bot(bot_and_dp, sessionmaker, advisors):
    bot, api, dp = bot_and_dp
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "حذف‌شدنی")
        await s.commit()
        sid = student.id

    await dp.feed_update(
        bot, callback_update(StudentCB(action="ask_del", student_id=sid).pack(),
                             ADVISOR_TG, 1)
    )
    api.clear()
    await dp.feed_update(
        bot, callback_update(StudentCB(action="del", student_id=sid).pack(), ADVISOR_TG, 2)
    )
    async with sessionmaker() as s:
        assert await UserRepository(s).students_of(advisors["a"]) == []


async def test_unknown_user_is_not_registered_silently(bot_and_dp, sessionmaker):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, message_update("/start", 999888, 1))
    assert any("لینک دعوت" in t for t in api.texts())
    async with sessionmaker() as s:
        assert await UserRepository(s).by_telegram_id(999888) is None  # no junk rows


async def test_admin_from_env_is_registered(bot_and_dp, sessionmaker):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, message_update("/start", ADMIN_TG, 1))
    async with sessionmaker() as s:
        admin = await UserRepository(s).by_telegram_id(ADMIN_TG)
        assert admin is not None and admin.role == Role.ADMIN


async def test_invalid_invite_token_is_explained(bot_and_dp):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, message_update("/start inv_deadbeef", 424243, 1))
    assert any("معتبر نیست" in t for t in api.texts())


async def test_id_command_helps_operator(bot_and_dp, advisors):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, message_update("/id", ADVISOR_TG, 1))
    assert any(str(ADVISOR_TG) in t for t in api.texts())


# ───────────────────────────── pagination fixes ─────────────────────────────
async def test_student_can_page_through_their_own_plans(bot_and_dp, sessionmaker, advisors):
    """Regression: paging used to filter by advisor_id and showed nothing."""
    bot, api, dp = bot_and_dp
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "پرکار")
        await manager.users.claim_invite(student, NEWCOMER_TG, None)
        start = saturday_of(today_local())
        for i in range(9):
            plan = await manager.create_plan(advisor, student.id, start - timedelta(days=7 * i))
            await manager.plans.mark_generated(
                plan, image_path="a.png", pdf_path="a.pdf", plan_hash=f"h{i}",
                template_version="t", renderer_version="r", duration_ms=1,
            )
        await s.commit()

    api.clear()
    await dp.feed_update(bot, callback_update(ListCB(kind="mine", page=1).pack(),
                                              NEWCOMER_TG, 1))
    labels = buttons(api)
    assert any("📅" in b for b in labels), f"second page is empty: {labels}"


async def test_advisor_can_page_through_drafts(bot_and_dp, sessionmaker, advisors):
    """Regression: drafts had no pagination and were capped at one page."""
    bot, api, dp = bot_and_dp
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "پیش‌نویسی")
        start = saturday_of(today_local())
        for i in range(9):
            await manager.create_plan(advisor, student.id, start - timedelta(days=7 * i))
        await s.commit()

    api.clear()
    await dp.feed_update(bot, callback_update(Nav(to="drafts").pack(), ADVISOR_TG, 1))
    assert any("بعدی" in b for b in buttons(api)), "no next-page button on drafts"

    api.clear()
    await dp.feed_update(bot, callback_update(ListCB(kind="drafts", page=1).pack(),
                                              ADVISOR_TG, 2))
    assert any("📝" in b for b in buttons(api))


async def test_student_plans_list_from_the_card(bot_and_dp, sessionmaker, advisors):
    bot, api, dp = bot_and_dp
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "کارنامه‌دار")
        await manager.create_plan(advisor, student.id, saturday_of(today_local()))
        await s.commit()
        sid = student.id

    api.clear()
    await dp.feed_update(bot, callback_update(ListCB(kind="student", ref=sid).pack(),
                                              ADVISOR_TG, 1))
    assert any("کارنامه‌دار" in t for t in api.texts())


# ─────────────────────────── timezone & retention ───────────────────────────
def test_today_follows_tehran_not_the_server(monkeypatch):
    """At 01:00 Tehran the UTC server is still on the previous day."""
    import app.domain.persian as persian

    tehran_after_midnight = datetime(2026, 8, 16, 1, 0, tzinfo=ZoneInfo("Asia/Tehran"))
    assert tehran_after_midnight.astimezone(ZoneInfo("UTC")).date() == date(2026, 8, 15)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return tehran_after_midnight.astimezone(tz) if tz else tehran_after_midnight

    monkeypatch.setattr(persian, "datetime", _FrozenDateTime)
    assert persian.today_local() == date(2026, 8, 16)  # Tehran's date, not UTC's


def test_week_starts_on_saturday_in_local_time():
    start = saturday_of(today_local())
    assert start.weekday() == 5
    assert 0 <= (today_local() - start).days <= 6


async def test_deleting_a_plan_also_deletes_its_files(sessionmaker, advisors, queue, tmp_path):
    async with sessionmaker() as s:
        manager = PlanManager(s, storage_root=queue.service.storage_root)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "فایل‌دار")
        plan = await manager.create_plan(advisor, student.id, saturday_of(today_local()))
        await manager.set_slot(advisor, plan.id, "saturday", 0, Activity(0, subject="زیست"))
        await s.commit()

        result = await queue.generate(PlanManager.to_domain(plan), force=True)
        await manager.plans.mark_generated(
            plan, image_path=str(result.png_path), pdf_path=str(result.pdf_path),
            plan_hash=result.plan_hash, template_version=result.template_version,
            renderer_version=result.renderer, duration_ms=result.duration_ms,
        )
        await s.commit()
        assert result.png_path.exists() and result.pdf_path.exists()

        removed = await manager.delete_plan(advisor, plan.id)
        await s.commit()
        assert removed == 2
        assert not result.png_path.exists() and not result.pdf_path.exists()


async def test_retention_purges_old_plans(sessionmaker, advisors, tmp_path):
    async with sessionmaker() as s:
        manager = PlanManager(s, storage_root=tmp_path)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "قدیمی")
        old = await manager.create_plan(
            advisor, student.id, saturday_of(today_local()) - timedelta(days=400)
        )
        fresh = await manager.create_plan(advisor, student.id, saturday_of(today_local()))
        await s.commit()

        plans, _files = await manager.purge_older_than(180)
        await s.commit()
        assert plans == 1
        assert await manager.plans.get(old.id) is None
        assert await manager.plans.get(fresh.id) is not None
        assert await manager.purge_older_than(0) == (0, 0)  # disabled by default


async def test_file_deletion_refuses_paths_outside_storage(sessionmaker, advisors, tmp_path):
    (tmp_path / "generated").mkdir(parents=True, exist_ok=True)
    outsider = tmp_path / "important.png"
    outsider.write_bytes(b"do not delete me")

    async with sessionmaker() as s:
        manager = PlanManager(s, storage_root=tmp_path / "generated")
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "خطرناک")
        plan = await manager.create_plan(advisor, student.id, saturday_of(today_local()))
        plan.image_path = str(outsider)
        await s.commit()

        await manager.delete_plan(advisor, plan.id)
        await s.commit()
    assert outsider.exists(), "a path outside STORAGE_ROOT must never be deleted"
