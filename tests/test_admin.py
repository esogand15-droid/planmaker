"""Role integrity + admin control panel (final security hardening)."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.bot.main import build_dispatcher  # noqa: E402
from app.bot.texts import AdminCB, Nav, StudentCB  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db.models import Base, Role  # noqa: E402
from app.domain.models import Activity  # noqa: E402
from app.domain.persian import saturday_of, today_local  # noqa: E402
from app.rendering.factory import get_renderer  # noqa: E402
from app.repositories.repositories import UserRepository  # noqa: E402
from app.security import is_admin, is_admin_env  # noqa: E402
from app.services.admin_service import AdminService  # noqa: E402
from app.services.invites import InviteOutcome  # noqa: E402
from app.services.plan_manager import AccessDenied, PlanManager  # noqa: E402
from app.services.plan_service import WeeklyPlanService  # noqa: E402
from app.services.render_queue import RenderQueue  # noqa: E402
from tests.mocks import callback_update, make_bot, message_update  # noqa: E402

ADMIN_TG = 1001      # listed in ADMIN_IDS
ADVISOR_TG = 1002
ADVISOR2_TG = 1003
STUDENT_TG = 1004
OUTSIDER_TG = 1005


@pytest_asyncio.fixture()
async def sessionmaker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture()
async def world(sessionmaker):
    async with sessionmaker() as s:
        users = UserRepository(s)
        admin = await users.create("مدیر", Role.ADMIN, telegram_id=ADMIN_TG)
        advisor = await users.create("مشاور اول", Role.ADVISOR, telegram_id=ADVISOR_TG)
        advisor2 = await users.create("مشاور دوم", Role.ADVISOR, telegram_id=ADVISOR2_TG)
        manager = PlanManager(s)
        student = await manager.create_student(advisor, "علی رضایی", "دوازدهم تجربی")
        linked = await manager.create_student(advisor, "متصل")
        await users.claim_invite(linked, STUDENT_TG, None)
        await s.commit()
        return {
            "admin": admin.id, "advisor": advisor.id, "advisor2": advisor2.id,
            "student": student.id, "token": student.invite_token, "linked": linked.id,
        }


@pytest.fixture()
def queue(tmp_path):
    service = WeeklyPlanService(get_renderer("pillow"), storage_root=tmp_path / "generated")
    return RenderQueue(service, max_concurrent=2)


@pytest.fixture()
def bot_and_dp(queue, sessionmaker, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_ids", (ADMIN_TG,))
    bot, api = make_bot()
    return bot, api, build_dispatcher(queue, sessionmaker, admin_ids=(ADMIN_TG,))


def texts(api):
    return " ".join(api.texts())


def buttons(api):
    out = []
    for r in api.requests:
        markup = getattr(r, "reply_markup", None)
        if markup and getattr(markup, "inline_keyboard", None):
            out += [b.text for row in markup.inline_keyboard for b in row]
    return out


# ════════════════════ 1. invite can never change a role ════════════════════
async def test_advisor_opening_student_invite_keeps_everything_intact(
    bot_and_dp, sessionmaker, world
):
    """The exact production incident: an advisor taps their student's link."""
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, message_update(f"/start inv_{world['token']}", ADVISOR_TG, 1))

    assert "نقش و دسترسی حساب شما" in texts(api)
    async with sessionmaker() as s:
        users = UserRepository(s)
        advisor = await users.by_id(world["advisor"])
        student = await users.by_id(world["student"])

        assert advisor.role is Role.ADVISOR          # role unchanged
        assert advisor.telegram_id == ADVISOR_TG     # identity unchanged
        assert advisor.is_active is True             # not locked out
        assert student.telegram_id is None           # student not hijacked
        assert student.invite_token == world["token"]  # token NOT consumed
        assert await users.count_all_students() == 2   # no duplicate user


async def test_admin_opening_student_invite_stays_admin(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, message_update(f"/start inv_{world['token']}", ADMIN_TG, 1))

    assert "مدیر" in texts(api)
    async with sessionmaker() as s:
        admin = await UserRepository(s).by_id(world["admin"])
        student = await UserRepository(s).by_id(world["student"])
        assert admin.role is Role.ADMIN and admin.telegram_id == ADMIN_TG
        assert admin.is_active is True
        assert student.telegram_id is None


async def test_admin_by_env_only_is_still_protected(sessionmaker, world):
    """Even if the DB row says 'advisor', ADMIN_IDS wins and blocks the invite."""
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(world["advisor"])
        result = await manager.claim_invite(
            world["token"], ADVISOR_TG, None, actor=advisor, is_admin_env=True
        )
        assert result.outcome is InviteOutcome.ROLE_CONFLICT
        assert (await manager.users.by_id(world["student"])).telegram_id is None


@pytest.mark.parametrize("role", [Role.ADMIN, Role.ADVISOR])
async def test_protected_roles_are_blocked_at_service_level(sessionmaker, world, role):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        actor = await manager.users.create(f"actor-{role.value}", role, telegram_id=7777)
        result = await manager.claim_invite(world["token"], 7777, None, actor=actor)
        assert result.outcome is InviteOutcome.ROLE_CONFLICT
        assert actor.role is role                       # unchanged
        assert (await manager.users.by_id(world["student"])).invite_token is not None


async def test_role_conflict_is_audited(sessionmaker, world):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(world["advisor"])
        await manager.claim_invite(world["token"], ADVISOR_TG, None, actor=advisor)
        await s.commit()
        actions = [a.action for a in await manager.audit.recent()]
        assert "invite.role_conflict" in actions
        assert "invite.opened" in actions


async def test_student_cannot_hijack_another_students_invite(sessionmaker, world):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        other_student = await manager.users.by_id(world["linked"])
        result = await manager.claim_invite(
            world["token"], STUDENT_TG, None, actor=other_student
        )
        assert result.outcome is InviteOutcome.CROSS_STUDENT
        assert (await manager.users.by_id(world["student"])).telegram_id is None
        assert (await manager.users.by_id(world["linked"])).telegram_id == STUDENT_TG


async def test_unknown_user_links_correctly(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, message_update(f"/start inv_{world['token']}", OUTSIDER_TG, 1))
    assert "خوش آمدی" in texts(api)
    async with sessionmaker() as s:
        student = await UserRepository(s).by_id(world["student"])
        assert student.telegram_id == OUTSIDER_TG and student.role is Role.STUDENT
        assert student.invite_token is None


async def test_same_student_reopening_the_link(sessionmaker, world):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        student = await manager.users.by_id(world["student"])
        token = student.invite_token
        first = await manager.claim_invite(token, OUTSIDER_TG, None)
        assert first.outcome is InviteOutcome.LINKED
        student.invite_token = token  # simulate the same link still in their chat
        again = await manager.claim_invite(token, OUTSIDER_TG, None, actor=student)
        assert again.outcome is InviteOutcome.ALREADY_SELF
        assert (await manager.users.by_id(world["student"])).invite_token is None


async def test_no_flow_can_downgrade_a_role(sessionmaker, world):
    """Sweep: no invite outcome may alter any role in the system."""
    async with sessionmaker() as s:
        manager = PlanManager(s)
        before = {
            u.id: u.role
            for u in [await manager.users.by_id(world[k])
                      for k in ("admin", "advisor", "advisor2", "student", "linked")]
        }
        for actor_key in ("admin", "advisor", "student", "linked"):
            actor = await manager.users.by_id(world[actor_key])
            await manager.claim_invite(world["token"], actor.telegram_id or 1, None, actor=actor)
        await s.commit()
        after = {
            u.id: u.role
            for u in [await manager.users.by_id(world[k])
                      for k in ("admin", "advisor", "advisor2", "student", "linked")]
        }
        assert before == after


# ════════════════════════ 2. admin authority ═══════════════════════════════
def test_admin_ids_parsing_is_robust(monkeypatch):
    monkeypatch.setenv("ADMIN_IDS", " 111, 222 ,,abc, 333 ")
    settings = Settings()
    assert settings.admin_ids == (111, 222, 333)   # malformed entries ignored

    monkeypatch.setenv("ADMIN_IDS", "")
    assert Settings().admin_ids == ()


def test_admin_authority_comes_from_env_not_the_database(monkeypatch):
    from app.config import settings
    from app.db.models import User

    monkeypatch.setattr(settings, "admin_ids", (999,))
    misfiled = User(id=1, full_name="x", role=Role.ADVISOR, telegram_id=999)
    assert is_admin_env(999) and is_admin(misfiled, 999)
    plain = User(id=2, full_name="y", role=Role.ADVISOR, telegram_id=5)
    assert not is_admin(plain, 5)


async def test_admin_menu_is_injected_for_admins_only(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, message_update("/start", ADMIN_TG, 1))
    assert any("پنل مدیریت" in b for b in buttons(api))

    api.clear()
    await dp.feed_update(bot, message_update("/start", ADVISOR_TG, 2))
    assert not any("پنل مدیریت" in b for b in buttons(api))


@pytest.mark.parametrize(
    "action",
    ["home", "advisors", "students", "system", "bot", "db", "storage", "stats",
     "audit", "settings", "advisor", "student", "do_suspend", "do_cleanup",
     "ask_cleanup", "advisor_students", "advisor_plans", "health"],
)
async def test_advisor_cannot_reach_any_admin_screen(bot_and_dp, world, action):
    """Forged admin callbacks from a non-admin are rejected, always."""
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(
        bot, callback_update(AdminCB(action=action, ref=world["advisor"]).pack(),
                             ADVISOR_TG, 1)
    )
    assert "فقط برای مدیر سیستم" in texts(api), f"'{action}' leaked to an advisor"


async def test_student_cannot_reach_the_admin_panel(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(AdminCB(action="home").pack(), STUDENT_TG, 1))
    assert "فقط برای مدیر سیستم" in texts(api)


async def test_suspending_an_admin_is_refused(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(
        bot, callback_update(AdminCB(action="do_suspend", ref=world["admin"]).pack(),
                             ADMIN_TG, 1)
    )
    async with sessionmaker() as s:
        assert (await UserRepository(s).by_id(world["admin"])).is_active is True


# ═══════════════════════ 3. admin panel screens ════════════════════════════
async def test_admin_can_open_every_panel_screen(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    screens = {
        "home": "پنل مدیریت",
        "advisors": "مشاوران",
        "students": "دانش‌آموزان",
        "system": "وضعیت سیستم",
        "bot": "وضعیت ربات",
        "db": "دیتابیس",
        "storage": "Storage",
        "stats": "آمار",
        "audit": "Audit",
        "settings": "تنظیمات",
    }
    for action, expected in screens.items():
        api.clear()
        await dp.feed_update(bot, callback_update(AdminCB(action=action).pack(), ADMIN_TG, 1))
        assert expected in texts(api), f"screen '{action}' did not render"
        assert api.calls("AnswerCallbackQuery"), f"screen '{action}' left a spinner"


async def test_admin_advisor_list_and_card(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(AdminCB(action="advisors").pack(), ADMIN_TG, 1))
    assert "مشاور اول" in texts(api) or any("مشاور اول" in b for b in buttons(api))

    api.clear()
    await dp.feed_update(
        bot, callback_update(AdminCB(action="advisor", ref=world["advisor"]).pack(),
                             ADMIN_TG, 2)
    )
    body = texts(api)
    assert "مشاور اول" in body and "دانش‌آموزان" in body
    assert any("غیرفعال" in b for b in buttons(api))


async def test_admin_can_suspend_and_reactivate_an_advisor(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(
        bot, callback_update(AdminCB(action="ask_suspend", ref=world["advisor"]).pack(),
                             ADMIN_TG, 1)
    )
    assert "مطمئن هستید" in texts(api)          # dangerous action is confirmed

    await dp.feed_update(
        bot, callback_update(AdminCB(action="do_suspend", ref=world["advisor"]).pack(),
                             ADMIN_TG, 2)
    )
    async with sessionmaker() as s:
        assert (await UserRepository(s).by_id(world["advisor"])).is_active is False

    await dp.feed_update(
        bot, callback_update(AdminCB(action="do_suspend", ref=world["advisor"]).pack(),
                             ADMIN_TG, 3)
    )
    async with sessionmaker() as s:
        advisor = await UserRepository(s).by_id(world["advisor"])
        assert advisor.is_active is True and advisor.role is Role.ADVISOR


async def test_suspended_advisor_cannot_write_but_keeps_data(sessionmaker, world):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(world["advisor"])
        advisor.is_active = False
        await s.commit()

        with pytest.raises(AccessDenied):
            await manager.create_student(advisor, "دانش‌آموز جدید")
        with pytest.raises(AccessDenied):
            await manager.create_plan(advisor, world["student"], saturday_of(today_local()))

        # data is untouched
        assert len(await manager.users.students_of(advisor.id)) == 2


async def test_admin_student_list_and_card(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(AdminCB(action="students").pack(), ADMIN_TG, 1))
    assert any("علی رضایی" in b for b in buttons(api))

    api.clear()
    await dp.feed_update(
        bot, callback_update(AdminCB(action="student", ref=world["student"]).pack(),
                             ADMIN_TG, 2)
    )
    body = texts(api)
    assert "علی رضایی" in body and "مشاور اول" in body and "دوازدهم تجربی" in body


async def test_admin_can_suspend_a_student(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    await dp.feed_update(
        bot,
        callback_update(AdminCB(action="ask_suspend_student", ref=world["student"]).pack(),
                        ADMIN_TG, 1),
    )
    api.clear()
    await dp.feed_update(
        bot,
        callback_update(AdminCB(action="do_suspend_student", ref=world["student"]).pack(),
                        ADMIN_TG, 2),
    )
    async with sessionmaker() as s:
        assert (await UserRepository(s).by_id(world["student"])).is_active is False


# ══════════════════════ 4. real numbers, not fakes ═════════════════════════
async def test_database_screen_uses_real_counts(sessionmaker, world):
    async with sessionmaker() as s:
        stats = await AdminService(s).db_stats()
        assert stats["users"] == 5           # admin + 2 advisors + 2 students
        assert stats["advisors"] == 3        # admin counts as an advisor-capable role
        assert stats["students"] == 2
        assert stats["plans"] == 0
        assert stats["latency_ms"] >= 0


async def test_statistics_reflect_actual_plans(sessionmaker, world):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(world["advisor"])
        await manager.create_plan(advisor, world["student"], saturday_of(today_local()))
        await manager.create_plan(
            advisor, world["student"], saturday_of(today_local()) - timedelta(days=70)
        )
        await s.commit()
        stats = await AdminService(s).statistics()
        assert stats["plans"] == 2 and stats["drafts"] == 2
        assert stats["week"] >= 1 and stats["month"] >= 1
        assert stats["sent"] == 0 and stats["generated"] == 0


async def test_storage_report_counts_files_and_orphans(sessionmaker, world, queue, tmp_path):
    async with sessionmaker() as s:
        manager = PlanManager(s, storage_root=queue.service.storage_root)
        advisor = await manager.users.by_id(world["advisor"])
        plan = await manager.create_plan(advisor, world["student"], saturday_of(today_local()))
        await manager.set_slot(advisor, plan.id, "saturday", 0, Activity(0, subject="زیست"))
        await s.commit()
        result = await queue.generate(PlanManager.to_domain(plan), force=True)
        await manager.plans.mark_generated(
            plan, image_path=str(result.png_path), pdf_path=str(result.pdf_path),
            plan_hash=result.plan_hash, template_version="t", renderer_version="r",
            duration_ms=1,
        )
        await s.commit()

        orphan = Path(queue.service.storage_root) / "orphan.png"
        orphan.write_bytes(b"x" * 100)

        report = await AdminService(s).storage_report(queue.service.storage_root)
        assert report.png == 2 and report.pdf == 1
        assert [p.name for p in report.orphans] == ["orphan.png"]
        assert report.total_bytes > 0 and report.human_size.endswith(("B", "KB", "MB"))

        removed = await AdminService(s).delete_orphans(queue.service.storage_root)
        assert removed == 1 and not orphan.exists()
        assert Path(result.png_path).exists()    # referenced files are kept


async def test_audit_screen_lists_real_events(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(AdminCB(action="audit").pack(), ADMIN_TG, 1))
    assert "student.created" in texts(api)


async def test_health_probe_reports_live_services(sessionmaker, queue):
    async with sessionmaker() as s:
        health = await AdminService(s).health(queue)
        assert health["db"] is True and health["raqm"] is True
        assert health["db_latency_ms"] >= 0
        assert health["inflight"] == 0
        assert isinstance(health["chromium"], bool)


async def test_admin_pagination_walks_long_lists(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(world["advisor"])
        for i in range(9):
            await manager.create_student(advisor, f"دانش‌آموز شماره {i}")
        await s.commit()

    api.clear()
    await dp.feed_update(bot, callback_update(AdminCB(action="students").pack(), ADMIN_TG, 1))
    assert any("بعدی" in b for b in buttons(api))
    api.clear()
    await dp.feed_update(
        bot, callback_update(AdminCB(action="students", page=1).pack(), ADMIN_TG, 2)
    )
    assert any("قبلی" in b for b in buttons(api))
