"""Access requests: unknown visitors and admin-granted roles."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.bot.main import build_dispatcher  # noqa: E402
from app.bot.texts import AdminCB  # noqa: E402
from app.db.models import Base, RequestStatus, Role  # noqa: E402
from app.rendering.factory import get_renderer  # noqa: E402
from app.repositories.repositories import (  # noqa: E402
    AccessRequestRepository,
    UserRepository,
)
from app.services.plan_manager import AccessDenied, PlanManager, StudentError  # noqa: E402
from app.services.plan_service import WeeklyPlanService  # noqa: E402
from app.services.render_queue import RenderQueue  # noqa: E402
from tests.mocks import callback_update, make_bot, message_update  # noqa: E402

ADMIN_TG = 8001
ADVISOR_TG = 8002
VISITOR_TG = 8003
VISITOR2_TG = 8004


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
def bot_and_dp(queue, sessionmaker, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_ids", (ADMIN_TG,))
    bot, api = make_bot()
    return bot, api, build_dispatcher(queue, sessionmaker, admin_ids=(ADMIN_TG,))


@pytest_asyncio.fixture()
async def world(sessionmaker):
    async with sessionmaker() as s:
        users = UserRepository(s)
        admin = await users.create("مدیر", Role.ADMIN, telegram_id=ADMIN_TG)
        advisor = await users.create("مشاور", Role.ADVISOR, telegram_id=ADVISOR_TG)
        await s.commit()
        return {"admin": admin.id, "advisor": advisor.id}


def texts(api):
    return " ".join(api.texts())


def buttons(api):
    out = []
    for r in api.requests:
        markup = getattr(r, "reply_markup", None)
        if markup and getattr(markup, "inline_keyboard", None):
            out += [b.text for row in markup.inline_keyboard for b in row]
    return out


async def visit(dp, bot, api, tg=VISITOR_TG, update_id=1):
    api.clear()
    await dp.feed_update(bot, message_update("/start", tg, update_id))
    return api


# ═════════════════════════ 1. the visitor's side ═══════════════════════════
async def test_visitor_is_queued_not_registered(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    await visit(dp, bot, api)

    assert "درخواست دسترسی شما برای مدیر ارسال شد" in texts(api)
    async with sessionmaker() as s:
        assert await UserRepository(s).by_telegram_id(VISITOR_TG) is None
        request = await AccessRequestRepository(s).by_telegram_id(VISITOR_TG)
        assert request is not None
        assert request.status is RequestStatus.PENDING and request.visits == 1


async def test_repeat_visits_do_not_duplicate_the_request(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    await visit(dp, bot, api, update_id=1)
    await visit(dp, bot, api, update_id=2)
    await visit(dp, bot, api, update_id=3)

    assert "در انتظار بررسی مدیر" in texts(api)   # second-visit wording
    async with sessionmaker() as s:
        repo = AccessRequestRepository(s)
        assert await repo.count_pending() == 1
        assert (await repo.by_telegram_id(VISITOR_TG)).visits == 3


async def test_rejected_visitor_is_told_and_not_requeued(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    await visit(dp, bot, api)
    async with sessionmaker() as s:
        manager = PlanManager(s)
        admin = await manager.users.by_id(world["admin"])
        request = await manager.requests.by_telegram_id(VISITOR_TG)
        await manager.reject_request(admin, request.id)
        await s.commit()

    await visit(dp, bot, api, update_id=9)
    assert "تأیید نشد" in texts(api)
    async with sessionmaker() as s:
        assert await AccessRequestRepository(s).count_pending() == 0


# ═════════════════════════ 2. the admin grants a role ══════════════════════
async def test_admin_grants_advisor_role_from_the_panel(bot_and_dp, sessionmaker, world):
    """The exact request: someone entered without an invite → make them an advisor."""
    bot, api, dp = bot_and_dp
    await visit(dp, bot, api)

    api.clear()  # the pending count is visible on the panel home screen
    await dp.feed_update(bot, callback_update(AdminCB(action="home").pack(), ADMIN_TG, 1))
    assert "درخواست دسترسی در انتظار" in texts(api)
    assert any("درخواست‌های دسترسی" in b for b in buttons(api))

    api.clear()
    await dp.feed_update(bot, callback_update(AdminCB(action="requests").pack(), ADMIN_TG, 2))
    assert str(VISITOR_TG) in texts(api)

    async with sessionmaker() as s:
        request_id = (await AccessRequestRepository(s).by_telegram_id(VISITOR_TG)).id

    api.clear()
    await dp.feed_update(bot, callback_update(
        AdminCB(action="request", ref=request_id).pack(), ADMIN_TG, 3))
    labels = buttons(api)
    assert any("تأیید به‌عنوان مشاور" in b for b in labels)
    assert any("تأیید به‌عنوان دانش‌آموز" in b for b in labels)
    assert any("رد درخواست" in b for b in labels)

    api.clear()
    await dp.feed_update(bot, callback_update(
        AdminCB(action="grant", ref=request_id, arg="advisor").pack(), ADMIN_TG, 4))

    async with sessionmaker() as s:
        created = await UserRepository(s).by_telegram_id(VISITOR_TG)
        assert created is not None and created.role is Role.ADVISOR
        assert created.is_active
        request = await AccessRequestRepository(s).by_telegram_id(VISITOR_TG)
        assert request.status is RequestStatus.APPROVED
        assert request.granted_role is Role.ADVISOR
        assert request.handled_by_id == world["admin"]

    # the person is notified immediately
    notified = [m for m in api.calls("SendMessage") if m.chat_id == VISITOR_TG]
    assert notified and "دسترسی شما تأیید شد" in (notified[0].text or "")


async def test_new_advisor_can_use_the_bot_right_away(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    await visit(dp, bot, api)
    async with sessionmaker() as s:
        request_id = (await AccessRequestRepository(s).by_telegram_id(VISITOR_TG)).id
    await dp.feed_update(bot, callback_update(
        AdminCB(action="grant", ref=request_id, arg="advisor").pack(), ADMIN_TG, 1))

    api.clear()
    await dp.feed_update(bot, message_update("/start", VISITOR_TG, 2))
    labels = buttons(api)
    assert any("برنامه جدید" in b for b in labels)
    assert any("دانش‌آموزان من" in b for b in labels)
    assert not any("پنل مدیریت" in b for b in labels)   # advisor, not admin


async def test_admin_grants_student_role_with_an_advisor(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    await visit(dp, bot, api)
    async with sessionmaker() as s:
        request_id = (await AccessRequestRepository(s).by_telegram_id(VISITOR_TG)).id

    api.clear()
    await dp.feed_update(bot, callback_update(
        AdminCB(action="grant_student", ref=request_id).pack(), ADMIN_TG, 1))
    assert any("مشاور" in b for b in buttons(api))

    await dp.feed_update(bot, callback_update(
        AdminCB(action="grant", ref=request_id, arg="student",
                page=world["advisor"]).pack(), ADMIN_TG, 2))

    async with sessionmaker() as s:
        users = UserRepository(s)
        created = await users.by_telegram_id(VISITOR_TG)
        assert created.role is Role.STUDENT
        roster = await users.students_of(world["advisor"])
        assert [u.id for u in roster] == [created.id]


async def test_admin_rejects_a_request(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    await visit(dp, bot, api)
    async with sessionmaker() as s:
        request_id = (await AccessRequestRepository(s).by_telegram_id(VISITOR_TG)).id

    api.clear()
    await dp.feed_update(bot, callback_update(
        AdminCB(action="reject", ref=request_id).pack(), ADMIN_TG, 1))

    async with sessionmaker() as s:
        assert await UserRepository(s).by_telegram_id(VISITOR_TG) is None
        request = await AccessRequestRepository(s).by_telegram_id(VISITOR_TG)
        assert request.status is RequestStatus.REJECTED


async def test_double_approval_is_refused(sessionmaker, world):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        admin = await manager.users.by_id(world["admin"])
        request = await manager.requests.record(VISITOR_TG, "مهمان", None)
        await manager.approve_request(admin, request.id, Role.ADVISOR)
        with pytest.raises(StudentError):
            await manager.approve_request(admin, request.id, Role.ADVISOR)


async def test_approving_an_existing_account_is_refused(sessionmaker, world):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        admin = await manager.users.by_id(world["admin"])
        request = await manager.requests.record(ADVISOR_TG, "همان مشاور", None)
        with pytest.raises(StudentError):
            await manager.approve_request(admin, request.id, Role.ADVISOR)


# ═══════════════════════════ 3. authorization ══════════════════════════════
@pytest.mark.parametrize("action,arg", [("requests", ""), ("request", ""),
                                        ("grant", "advisor"), ("reject", ""),
                                        ("grant_student", ""), ("add_advisor", "")])
async def test_advisor_cannot_touch_the_request_queue(
    bot_and_dp, sessionmaker, world, action, arg
):
    bot, api, dp = bot_and_dp
    await visit(dp, bot, api)
    async with sessionmaker() as s:
        request_id = (await AccessRequestRepository(s).by_telegram_id(VISITOR_TG)).id

    api.clear()
    await dp.feed_update(bot, callback_update(
        AdminCB(action=action, ref=request_id, arg=arg).pack(), ADVISOR_TG, 1))
    assert "فقط برای مدیر سیستم" in texts(api)
    async with sessionmaker() as s:
        assert await UserRepository(s).by_telegram_id(VISITOR_TG) is None


async def test_service_level_authorization(sessionmaker, world):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(world["advisor"])
        request = await manager.requests.record(VISITOR_TG, "مهمان", None)
        for call in (
            manager.approve_request(advisor, request.id, Role.ADVISOR),
            manager.reject_request(advisor, request.id),
            manager.create_advisor_by_telegram_id(advisor, "نفوذی", 5555),
        ):
            with pytest.raises(AccessDenied):
                await call


async def test_invite_still_cannot_grant_a_role(bot_and_dp, sessionmaker, world):
    """The request queue must not weaken the invite security rule."""
    bot, api, dp = bot_and_dp
    async with sessionmaker() as s:
        manager = PlanManager(s)
        advisor = await manager.users.by_id(world["advisor"])
        student = await manager.create_student(advisor, "علی رضایی")
        token = student.invite_token
        await s.commit()

    api.clear()
    await dp.feed_update(bot, message_update(f"/start inv_{token}", ADVISOR_TG, 1))
    assert "تغییر نکرده است" in texts(api)
    async with sessionmaker() as s:
        assert (await UserRepository(s).by_telegram_id(ADVISOR_TG)).role is Role.ADVISOR


async def test_admin_cannot_grant_an_admin_role(sessionmaker, world):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        admin = await manager.users.by_id(world["admin"])
        request = await manager.requests.record(VISITOR_TG, "مهمان", None)
        with pytest.raises(StudentError):
            await manager.approve_request(admin, request.id, Role.ADMIN)


# ══════════════════════ 4. adding an advisor directly ══════════════════════
async def test_admin_adds_an_advisor_by_telegram_id(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(
        AdminCB(action="add_advisor").pack(), ADMIN_TG, 1))
    assert "افزودن مشاور" in texts(api)

    api.clear()
    await dp.feed_update(bot, message_update("سارا احمدی | ۸۰۰۹۰۰", ADMIN_TG, 2))
    assert "اضافه شد" in texts(api)
    async with sessionmaker() as s:
        created = await UserRepository(s).by_telegram_id(800900)
        assert created is not None and created.role is Role.ADVISOR


async def test_adding_an_advisor_closes_a_pending_request(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    await visit(dp, bot, api)
    await dp.feed_update(bot, callback_update(
        AdminCB(action="add_advisor").pack(), ADMIN_TG, 1))
    await dp.feed_update(bot, message_update(f"مهمان تأییدشده | {VISITOR_TG}", ADMIN_TG, 2))

    async with sessionmaker() as s:
        assert (await UserRepository(s).by_telegram_id(VISITOR_TG)).role is Role.ADVISOR
        request = await AccessRequestRepository(s).by_telegram_id(VISITOR_TG)
        assert request.status is RequestStatus.APPROVED
        assert await AccessRequestRepository(s).count_pending() == 0


async def test_duplicate_telegram_id_is_refused(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    await dp.feed_update(bot, callback_update(
        AdminCB(action="add_advisor").pack(), ADMIN_TG, 1))
    api.clear()
    await dp.feed_update(bot, message_update(f"تکراری | {ADVISOR_TG}", ADMIN_TG, 2))
    assert "قبلاً ثبت شده" in texts(api)


async def test_requests_are_audited(sessionmaker, world):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        admin = await manager.users.by_id(world["admin"])
        first = await manager.requests.record(VISITOR_TG, "الف", None)
        second = await manager.requests.record(VISITOR2_TG, "ب", None)
        await manager.approve_request(admin, first.id, Role.ADVISOR)
        await manager.reject_request(admin, second.id)
        await s.commit()
        actions = [a.action for a in await manager.audit.recent()]
    assert "access.approved" in actions and "access.rejected" in actions


def test_audit_labels_are_persian():
    import re

    from app.bot.texts import audit_fa

    for action in ("access.approved", "access.rejected"):
        assert not re.search(r"[A-Za-z]", audit_fa(action)), audit_fa(action)
