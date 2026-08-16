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
from app.services.invites import InviteOutcome  # noqa: E402
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

        result = await manager.claim_invite(token, NEWCOMER_TG, "sara")
        await s.commit()
        assert result.outcome is InviteOutcome.LINKED
        claimed = result.student
        assert claimed.id == student.id
        assert claimed.telegram_id == NEWCOMER_TG
        assert claimed.invite_token is None      # one-time use
        assert claimed.is_connected

        # replaying the same token must fail
        replay = await manager.claim_invite(token, 99999, None)
        assert replay.outcome is InviteOutcome.INVALID


async def test_claiming_folds_away_a_stray_duplicate_row(sessionmaker, advisors):
    """If the student had somehow been auto-created before, no duplicates remain."""
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        stray = await manager.users.create("ناشناس", Role.STUDENT, telegram_id=NEWCOMER_TG)
        student = await manager.create_student(advisor, "سارا محمدی")
        await s.commit()

        result = await manager.claim_invite(student.invite_token, NEWCOMER_TG, None)
        await s.commit()

        assert result.outcome is InviteOutcome.LINKED
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


async def test_detaching_student_keeps_their_plans(sessionmaker, advisors):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "رضا کریمی")
        plan = await manager.create_plan(advisor, student.id, saturday_of(today_local()))
        await s.commit()

        await manager.detach_student(advisor, student.id)
        await s.commit()

        assert await manager.users.students_of(advisor.id) == []
        assert await manager.plans.get(plan.id) is not None  # data preserved
        assert await manager.users.by_id(student.id) is not None


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
            manager.detach_student(b, student.id),
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
    assert "لینک دعوت صادر شده" in text          # real connection state
    assert "?start=inv_" in text                  # the usable link is right there
    labels = buttons(api)
    # the card offers the full management set required by the spec
    for expected in ("برنامه این هفته", "برنامه جدید", "برنامه‌های قبلی",
                     "ویرایش اطلاعات", "اتصال به تلگرام", "حذف دانش‌آموز"):
        assert any(expected in b for b in labels), f"missing '{expected}': {labels}"


async def test_delete_student_from_bot(bot_and_dp, sessionmaker, advisors):
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
    await dp.feed_update(
        bot, callback_update(StudentCB(action="del_confirm", student_id=sid).pack(),
                             ADVISOR_TG, 2)
    )
    api.clear()
    await dp.feed_update(
        bot, callback_update(StudentCB(action="del", student_id=sid).pack(), ADVISOR_TG, 3)
    )
    async with sessionmaker() as s:
        assert await UserRepository(s).students_of(advisors["a"]) == []
        assert await UserRepository(s).by_id(sid) is None  # really gone


async def test_unknown_user_gets_no_account_but_a_queued_request(
    bot_and_dp, sessionmaker
):
    """No silent registration — the visit becomes a request an admin can grant."""
    from app.repositories.repositories import AccessRequestRepository

    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, message_update("/start", 999888, 1))
    assert any("درخواست دسترسی شما" in t for t in api.texts())
    async with sessionmaker() as s:
        assert await UserRepository(s).by_telegram_id(999888) is None   # no account
        request = await AccessRequestRepository(s).by_telegram_id(999888)
        assert request is not None and request.status.value == "pending"


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
    import app.domain.calendar as persian

    tehran_after_midnight = datetime(2026, 8, 16, 1, 0, tzinfo=ZoneInfo("Asia/Tehran"))
    assert tehran_after_midnight.astimezone(ZoneInfo("UTC")).date() == date(2026, 8, 15)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return tehran_after_midnight.astimezone(tz) if tz else tehran_after_midnight

    monkeypatch.setattr(persian, "datetime", _FrozenDateTime)
    assert persian.JalaliDate.today() == date(2026, 8, 16)  # Tehran's date, not UTC's


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


# ════════════════════ invite security (expiry / one-time / revoke) ═════════
async def test_invite_token_is_strong_and_unique(sessionmaker, advisors):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        tokens = set()
        for i in range(15):
            student = await manager.create_student(advisor, f"دانش‌آموز {i}")
            tokens.add(student.invite_token)
        await s.commit()
    assert len(tokens) == 15                      # no collisions
    assert all(len(t) >= 24 for t in tokens)      # ≥140 bits of entropy
    assert all(t.isascii() and " " not in t for t in tokens)


async def test_invite_expires(sessionmaker, advisors):
    from datetime import timezone as tz

    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "منقضی")
        token, _ = await manager.new_invite(advisor, student.id)
        # rewind the expiry into the past
        student.invite_expires_at = datetime.now(tz.utc) - timedelta(minutes=1)
        await s.commit()

        assert (await manager.claim_invite(token, 4242, None)).outcome is InviteOutcome.EXPIRED
        refreshed = await manager.users.by_id(student.id)
        assert refreshed.telegram_id is None      # nothing was linked
        actions = [a.action for a in await manager.audit.recent()]
        assert "invite.expired" in actions


async def test_invite_default_ttl_is_two_weeks(sessionmaker, advisors):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "مهلت‌دار")
        _token, expires = await manager.new_invite(advisor, student.id)
        delta = expires.date() - today_local()
        assert 13 <= delta.days <= 14


async def test_invite_can_be_revoked(sessionmaker, advisors):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "ابطالی")
        token, _ = await manager.new_invite(advisor, student.id)
        await manager.revoke_invite(advisor, student.id)
        await s.commit()

        assert (await manager.claim_invite(token, 5252, None)).outcome is InviteOutcome.INVALID
        assert (await manager.users.by_id(student.id)).invite_token is None
        assert "student.invite_revoked" in [a.action for a in await manager.audit.recent()]


async def test_reissuing_an_invite_invalidates_the_previous_one(sessionmaker, advisors):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "چندبار")
        first, _ = await manager.new_invite(advisor, student.id)
        second, _ = await manager.new_invite(advisor, student.id)
        await s.commit()

        assert first != second
        stale = await manager.claim_invite(first, 6161, None)
        assert stale.outcome is InviteOutcome.INVALID                   # old link dead
        fresh = await manager.claim_invite(second, 6161, None)
        assert fresh.outcome is InviteOutcome.LINKED and fresh.student.id == student.id


@pytest.mark.parametrize("forged", ["", "x", "short", "a" * 15, "../../etc/passwd",
                                    "' OR 1=1 --", "inv_", "0" * 32])
async def test_forged_tokens_never_link_anyone(sessionmaker, advisors, forged):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "امن")
        await s.commit()
        rejected = await manager.claim_invite(forged, 7171, None)
        assert rejected.outcome is InviteOutcome.INVALID
        assert (await manager.users.by_id(student.id)).telegram_id is None


async def test_invite_cannot_rebind_a_connected_student(sessionmaker, advisors):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "قفل‌شده")
        token, _ = await manager.new_invite(advisor, student.id)
        await manager.users.attach_telegram_id(student, 8181)
        student.invite_token = token           # simulate a leaked, stale link
        await s.commit()

        blocked = await manager.claim_invite(token, 9191, None)        # different person
        assert blocked.outcome is InviteOutcome.ALREADY_LINKED
        assert (await manager.users.by_id(student.id)).telegram_id == 8181


async def test_manual_telegram_id_linking(sessionmaker, advisors):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "دستی")
        linked = await manager.link_telegram_id(advisor, student.id, 3131)
        await s.commit()
        assert linked.telegram_id == 3131 and linked.invite_token is None

        other = await manager.create_student(advisor, "دیگری")
        with pytest.raises(StudentError):
            await manager.link_telegram_id(advisor, other.id, 3131)  # already taken


async def test_creating_with_taken_telegram_id_is_rejected(sessionmaker, advisors):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        await manager.create_student(advisor, "اولی", telegram_id=2121)
        with pytest.raises(StudentError):
            await manager.create_student(advisor, "دومی", telegram_id=2121)


# ════════════════════════════ profile editing ══════════════════════════════
async def test_advisor_edits_own_student(sessionmaker, advisors):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "قدیم", "دهم")
        updated = await manager.edit_student(advisor, student.id, "جدید", "یازدهم تجربی")
        await s.commit()
        assert updated.full_name == "جدید" and updated.grade == "یازدهم تجربی"
        assert "student.edited" in [a.action for a in await manager.audit.recent()]


async def test_advisor_cannot_edit_another_advisors_student(sessionmaker, advisors):
    from app.services.plan_manager import AccessDenied

    async with sessionmaker() as s:
        manager = PlanManager(s)
        a = await manager.users.by_id(advisors["a"])
        b = await manager.users.by_id(advisors["b"])
        student = await manager.create_student(a, "مال A")
        await s.commit()
        with pytest.raises(AccessDenied):
            await manager.edit_student(b, student.id, "هک‌شده", None)
        with pytest.raises(AccessDenied):
            await manager.link_telegram_id(b, student.id, 1010)
        assert (await manager.users.by_id(student.id)).full_name == "مال A"


async def test_edit_rejects_duplicate_name(sessionmaker, advisors):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        await manager.create_student(advisor, "علی")
        second = await manager.create_student(advisor, "رضا")
        with pytest.raises(StudentError):
            await manager.edit_student(advisor, second.id, "علی", None)


# ══════════════════════ full connect flow through the bot ══════════════════
async def test_connect_screen_and_manual_id_through_bot(bot_and_dp, sessionmaker, advisors):
    bot, api, dp = bot_and_dp
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "اتصالی")
        await s.commit()
        sid = student.id

    api.clear()
    await dp.feed_update(
        bot, callback_update(StudentCB(action="connect", student_id=sid).pack(), ADVISOR_TG, 1)
    )
    labels = buttons(api)
    assert any("لینک" in b for b in labels) and any("آیدی عددی" in b for b in labels)

    api.clear()
    await dp.feed_update(
        bot, callback_update(StudentCB(action="invite", student_id=sid).pack(), ADVISOR_TG, 2)
    )
    text = " ".join(api.texts())
    assert "?start=inv_" in text and "اعتبار تا" in text

    api.clear()
    await dp.feed_update(
        bot, callback_update(StudentCB(action="revoke", student_id=sid).pack(), ADVISOR_TG, 3)
    )
    async with sessionmaker() as s:
        assert (await UserRepository(s).by_id(sid)).invite_token is None

    # manual numeric id path
    await dp.feed_update(
        bot, callback_update(StudentCB(action="setid", student_id=sid).pack(), ADVISOR_TG, 4)
    )
    api.clear()
    await dp.feed_update(bot, message_update("۵۵۵۴۴۴", ADVISOR_TG, 5))  # persian digits
    async with sessionmaker() as s:
        assert (await UserRepository(s).by_id(sid)).telegram_id == 555444


async def test_edit_student_through_bot(bot_and_dp, sessionmaker, advisors):
    bot, api, dp = bot_and_dp
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "قبلی", "دهم")
        await s.commit()
        sid = student.id

    await dp.feed_update(
        bot, callback_update(StudentCB(action="edit", student_id=sid).pack(), ADVISOR_TG, 1)
    )
    api.clear()
    await dp.feed_update(bot, message_update("علی جدید | دوازدهم ریاضی", ADVISOR_TG, 2))
    assert any("به‌روزرسانی" in t for t in api.texts())
    async with sessionmaker() as s:
        u = await UserRepository(s).by_id(sid)
        assert u.full_name == "علی جدید" and u.grade == "دوازدهم ریاضی"


async def test_add_student_with_optional_telegram_id_through_bot(
    bot_and_dp, sessionmaker, advisors
):
    bot, api, dp = bot_and_dp
    await dp.feed_update(
        bot, callback_update(StudentCB(action="add", mode="card").pack(), ADVISOR_TG, 1)
    )
    api.clear()
    await dp.feed_update(bot, message_update("سارا نوری | یازدهم | 777888", ADVISOR_TG, 2))
    assert any("وصل شد" in t for t in api.texts())
    async with sessionmaker() as s:
        student = (await UserRepository(s).students_of(advisors["a"]))[0]
        assert student.telegram_id == 777888 and student.invite_token is None


async def test_this_week_button_opens_or_starts_the_plan(bot_and_dp, sessionmaker, advisors):
    bot, api, dp = bot_and_dp
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(advisor, "این‌هفته‌ای")
        await s.commit()
        sid = student.id

    api.clear()  # no plan yet → creates one and shows the 7-day overview
    await dp.feed_update(
        bot, callback_update(StudentCB(action="thisweek", student_id=sid).pack(), ADVISOR_TG, 1)
    )
    assert any("شنبه" in b for b in buttons(api))
    async with sessionmaker() as s:
        plan = await PlanManager(s).plans.find_by_week(sid, saturday_of(today_local()))
        assert plan is not None

    api.clear()  # second press → opens the same plan, no duplicate
    await dp.feed_update(
        bot, callback_update(StudentCB(action="thisweek", student_id=sid).pack(), ADVISOR_TG, 2)
    )
    async with sessionmaker() as s:
        assert await PlanManager(s).plans.count_history(student_id=sid) == 1


# ═════════════════════════ timezone boundary ═══════════════════════════════
def test_midnight_tehran_week_boundary(monkeypatch):
    """23:00 UTC Friday is already Saturday 02:30 in Tehran → next week starts."""
    import app.domain.calendar as persian

    tehran = ZoneInfo("Asia/Tehran")
    moment = datetime(2026, 8, 15, 2, 30, tzinfo=tehran)  # Saturday 02:30 Tehran
    assert moment.astimezone(ZoneInfo("UTC")).date() == date(2026, 8, 14)  # Friday UTC

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment

    monkeypatch.setattr(persian, "datetime", _Frozen)
    today = persian.JalaliDate.today()
    assert today == date(2026, 8, 15)
    assert saturday_of(today) == today, "the Tehran week must roll over at local midnight"


# ═════════════════════════ file / version authorization ════════════════════
async def test_version_files_are_authorization_checked(bot_and_dp, sessionmaker, advisors,
                                                       queue, tmp_path):
    from app.bot.texts import FileCB

    bot, api, dp = bot_and_dp
    async with sessionmaker() as s:
        manager = PlanManager(s, storage_root=queue.service.storage_root)
        a = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(a, "صاحب فایل")
        plan = await manager.create_plan(a, student.id, saturday_of(today_local()))
        await manager.set_slot(a, plan.id, "saturday", 0, Activity(0, subject="زیست"))
        await s.commit()
        result = await queue.generate(PlanManager.to_domain(plan), force=True)
        record = await manager.plans.mark_generated(
            plan, image_path=str(result.png_path), pdf_path=str(result.pdf_path),
            plan_hash=result.plan_hash, template_version=result.template_version,
            renderer_version=result.renderer, duration_ms=1,
        )
        await s.commit()
        file_id = record.id

    # advisor B forges the version file id
    api.clear()
    await dp.feed_update(
        bot,
        callback_update(FileCB(action="get", file_id=file_id, kind="pdf").pack(),
                        OTHER_ADVISOR_TG, 1),
    )
    assert api.calls("SendDocument") == [], "IDOR: another advisor downloaded the file"

    # the owner can
    api.clear()
    await dp.feed_update(
        bot,
        callback_update(FileCB(action="get", file_id=file_id, kind="pdf").pack(),
                        ADVISOR_TG, 2),
    )
    assert len(api.calls("SendDocument")) == 1


async def test_version_list_is_authorization_checked(bot_and_dp, sessionmaker, advisors):
    bot, api, dp = bot_and_dp
    async with sessionmaker() as s:
        manager = PlanManager(s)
        a = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(a, "نسخه‌دار")
        plan = await manager.create_plan(a, student.id, saturday_of(today_local()))
        await s.commit()
        pid = plan.id

    api.clear()
    await dp.feed_update(
        bot, callback_update(PlanCB(action="versions", plan_id=pid).pack(),
                             OTHER_ADVISOR_TG, 1)
    )
    assert not any("نسخه‌های تولیدشده" in t for t in api.texts())


async def test_student_list_idor_via_callback(bot_and_dp, sessionmaker, advisors):
    """Advisor B forges every student-scoped callback of advisor A's student."""
    bot, api, dp = bot_and_dp
    async with sessionmaker() as s:
        manager = PlanManager(s)
        a = await manager.users.by_id(advisors["a"])
        student = await manager.create_student(a, "هدف")
        await s.commit()
        sid = student.id

    for action in ("card", "edit", "connect", "invite", "revoke", "setid",
                   "thisweek", "ask_del", "del"):
        api.clear()
        await dp.feed_update(
            bot,
            callback_update(StudentCB(action=action, student_id=sid).pack(),
                            OTHER_ADVISOR_TG, 1),
        )
        assert not any("هدف" in t for t in api.texts()), f"leak via '{action}'"

    async with sessionmaker() as s:
        survivor = await UserRepository(s).by_id(sid)
        assert survivor.full_name == "هدف" and survivor.telegram_id is None
        assert len(await UserRepository(s).students_of(advisors["a"])) == 1
