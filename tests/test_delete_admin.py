"""Delete integrity, admin control operations and Persian UI (final3)."""
from __future__ import annotations

import re
import sys
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.bot.main import build_dispatcher  # noqa: E402
from app.bot.texts import AdminCB, Nav, StudentCB  # noqa: E402
from app.db.models import AdvisorStudent, Base, PlanFile, Role, User, WeeklyPlanDB  # noqa: E402
from app.domain.models import Activity  # noqa: E402
from app.domain.persian import saturday_of, today_local  # noqa: E402
from app.rendering.factory import get_renderer  # noqa: E402
from app.repositories.repositories import UserRepository  # noqa: E402
from app.services.deletion import DeletionService  # noqa: E402
from app.services.plan_manager import AccessDenied, PlanManager, StudentError  # noqa: E402
from app.services.plan_service import WeeklyPlanService  # noqa: E402
from app.services.render_queue import RenderQueue  # noqa: E402
from tests.mocks import callback_update, make_bot, message_update  # noqa: E402

ADMIN_TG = 2001
ADV_TG = 2002
ADV2_TG = 2003
STU_TG = 2004


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
async def world(sessionmaker, queue):
    """Admin + two advisors + a student with a generated plan (files on disk)."""
    async with sessionmaker() as s:
        users = UserRepository(s)
        admin = await users.create("مدیر", Role.ADMIN, telegram_id=ADMIN_TG)
        advisor = await users.create("مشاور اول", Role.ADVISOR, telegram_id=ADV_TG)
        advisor2 = await users.create("مشاور دوم", Role.ADVISOR, telegram_id=ADV2_TG)

        manager = PlanManager(s, storage_root=queue.service.storage_root)
        student = await manager.create_student(advisor, "علی رضایی", "دوازدهم")
        await users.claim_invite(student, STU_TG, None)
        plan = await manager.create_plan(advisor, student.id, saturday_of(today_local()))
        await manager.set_slot(advisor, plan.id, "saturday", 0, Activity(0, subject="زیست"))
        await manager.plans.replace_assignments(plan.id, ["مرور فصل ۲"])
        await s.commit()

        result = await queue.generate(PlanManager.to_domain(plan), force=True)
        await manager.plans.mark_generated(
            plan, image_path=str(result.png_path), pdf_path=str(result.pdf_path),
            plan_hash=result.plan_hash, template_version="t", renderer_version="r",
            duration_ms=1,
        )
        await s.commit()
        return {
            "admin": admin.id, "advisor": advisor.id, "advisor2": advisor2.id,
            "student": student.id, "plan": plan.id,
            "png": Path(result.png_path), "pdf": Path(result.pdf_path),
        }


def texts(api):
    return " ".join(api.texts())


def buttons(api):
    out = []
    for r in api.requests:
        markup = getattr(r, "reply_markup", None)
        if markup and getattr(markup, "inline_keyboard", None):
            out += [b.text for row in markup.inline_keyboard for b in row]
    return out


async def counts(sessionmaker):
    async with sessionmaker() as s:
        async def n(model):
            return int((await s.execute(select(func.count()).select_from(model))).scalar_one())
        return {
            "students": int((await s.execute(
                select(func.count()).select_from(User).where(User.role == Role.STUDENT)
            )).scalar_one()),
            "links": await n(AdvisorStudent),
            "plans": await n(WeeklyPlanDB),
            "files": await n(PlanFile),
        }


# ═════════════════════════ 1. student deletion is real ═════════════════════
async def test_advisor_can_delete_own_student_for_real(sessionmaker, world, queue):
    before = await counts(sessionmaker)
    assert before["students"] == 1 and before["plans"] == 1

    async with sessionmaker() as s:
        service = DeletionService(s, queue.service.storage_root)
        advisor = await service.manager.users.by_id(world["advisor"])
        report = await service.delete_student(advisor, world["student"])
        await s.commit()

    after = await counts(sessionmaker)
    assert after == {"students": 0, "links": 0, "plans": 0, "files": 0}
    assert report.plans == 1 and report.links == 1 and report.files >= 2
    assert not world["png"].exists() and not world["pdf"].exists()


async def test_deleted_student_disappears_from_the_bot_list(bot_and_dp, sessionmaker, world):
    """The reported bug: the message said 'deleted' but the list was unchanged."""
    bot, api, dp = bot_and_dp
    sid = world["student"]

    await dp.feed_update(bot, callback_update(
        StudentCB(action="ask_del", student_id=sid).pack(), ADV_TG, 1))
    await dp.feed_update(bot, callback_update(
        StudentCB(action="del_confirm", student_id=sid).pack(), ADV_TG, 2))
    api.clear()
    await dp.feed_update(bot, callback_update(
        StudentCB(action="del", student_id=sid).pack(), ADV_TG, 3))

    assert not any("علی رضایی" in b for b in buttons(api)), "student is still listed"
    assert any("ثبت نکرده‌اید" in t or "دانش‌آموزان" in t for t in api.texts())
    async with sessionmaker() as s:
        assert await UserRepository(s).by_id(sid) is None


async def test_admin_delete_also_clears_the_admin_list(bot_and_dp, sessionmaker, world):
    """Admins list every student, so the row must really be gone."""
    bot, api, dp = bot_and_dp
    sid = world["student"]
    for action in ("ask_del_student", "del_student", "del_student_final"):
        api.clear()
        await dp.feed_update(bot, callback_update(
            AdminCB(action=action, ref=sid).pack(), ADMIN_TG, 1))
    assert not any("علی رضایی" in b for b in buttons(api))
    async with sessionmaker() as s:
        assert await UserRepository(s).by_id(sid) is None


async def test_delete_requires_two_confirmations(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(
        StudentCB(action="ask_del", student_id=world["student"]).pack(), ADV_TG, 1))
    assert "حذف دانش‌آموز" in texts(api) and any("ادامه" in b for b in buttons(api))
    async with sessionmaker() as s:  # nothing deleted yet
        assert await UserRepository(s).by_id(world["student"]) is not None

    api.clear()
    await dp.feed_update(bot, callback_update(
        StudentCB(action="del_confirm", student_id=world["student"]).pack(), ADV_TG, 2))
    assert "تأیید نهایی" in texts(api) and any("حذف قطعی" in b for b in buttons(api))
    async with sessionmaker() as s:
        assert await UserRepository(s).by_id(world["student"]) is not None


async def test_confirmation_lists_the_real_impact(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(
        StudentCB(action="ask_del", student_id=world["student"]).pack(), ADV_TG, 1))
    body = texts(api)
    assert "۱ برنامه هفتگی" in body
    assert "فایل" in body


async def test_advisor_cannot_delete_another_advisors_student(sessionmaker, world, queue):
    async with sessionmaker() as s:
        service = DeletionService(s, queue.service.storage_root)
        intruder = await service.manager.users.by_id(world["advisor2"])
        with pytest.raises(AccessDenied):
            await service.delete_student(intruder, world["student"])
        await s.rollback()
    assert (await counts(sessionmaker))["students"] == 1


async def test_delete_via_forged_callback_is_rejected(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    for action in ("ask_del", "del_confirm", "del"):
        api.clear()
        await dp.feed_update(bot, callback_update(
            StudentCB(action=action, student_id=world["student"]).pack(), ADV2_TG, 1))
    async with sessionmaker() as s:
        assert await UserRepository(s).by_id(world["student"]) is not None


async def test_delete_tolerates_missing_files(sessionmaker, world, queue):
    world["png"].unlink()
    world["pdf"].unlink()
    async with sessionmaker() as s:
        service = DeletionService(s, queue.service.storage_root)
        advisor = await service.manager.users.by_id(world["advisor"])
        report = await service.delete_student(advisor, world["student"])
        await s.commit()
    assert report.files == 0                     # nothing to remove, still a success
    assert (await counts(sessionmaker))["students"] == 0


async def test_delete_rolls_back_and_reports_failure(bot_and_dp, sessionmaker, world,
                                                     queue, monkeypatch):
    """A mid-transaction failure must leave the database untouched."""
    from app.services import deletion as deletion_mod

    original = deletion_mod.DeletionService._remove_files

    async def boom(self, *args, **kwargs):
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(
        deletion_mod.DeletionService, "_remove_files",
        lambda self, paths: (_ for _ in ()).throw(RuntimeError("disk exploded")),
    )
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(
        StudentCB(action="del", student_id=world["student"]).pack(), ADV_TG, 1))

    assert any("انجام نشد" in t or "انجام نشد" in (getattr(r, "text", "") or "")
               for t in api.texts() + [getattr(r, "text", "") or ""
                                       for r in api.calls("AnswerCallbackQuery")])
    monkeypatch.setattr(deletion_mod.DeletionService, "_remove_files", original)
    async with sessionmaker() as s:
        assert await UserRepository(s).by_id(world["student"]) is not None
        assert await PlanManager(s).plans.get(world["plan"]) is not None
    after = await counts(sessionmaker)
    assert after["students"] == 1 and after["links"] == 1 and after["plans"] == 1


async def test_student_deletion_is_audited(sessionmaker, world, queue):
    async with sessionmaker() as s:
        service = DeletionService(s, queue.service.storage_root)
        advisor = await service.manager.users.by_id(world["advisor"])
        await service.delete_student(advisor, world["student"])
        await s.commit()
        entries = await service.manager.audit.recent()
    deleted = [e for e in entries if e.action == "student.deleted"]
    assert deleted and "plans=1" in deleted[0].detail
    assert "source=advisor" in deleted[0].detail


# ═══════════════════════ 2. advisor deletion & transfer ════════════════════
async def test_admin_deletes_advisor_without_students(sessionmaker, world, queue):
    async with sessionmaker() as s:
        service = DeletionService(s, queue.service.storage_root)
        admin = await service.manager.users.by_id(world["admin"])
        report = await service.delete_advisor(admin, world["advisor2"])
        await s.commit()
        assert await service.manager.users.by_id(world["advisor2"]) is None
    assert report.students == 0


async def test_deleting_advisor_can_transfer_their_students(sessionmaker, world, queue):
    async with sessionmaker() as s:
        service = DeletionService(s, queue.service.storage_root)
        admin = await service.manager.users.by_id(world["admin"])
        report = await service.delete_advisor(
            admin, world["advisor"], strategy="transfer",
            target_advisor_id=world["advisor2"],
        )
        await s.commit()

        assert report.transferred == 1
        assert await service.manager.users.by_id(world["advisor"]) is None
        # the student and their plan survive under the new advisor
        student = await service.manager.users.by_id(world["student"])
        assert student is not None
        roster = await service.manager.users.students_of(world["advisor2"])
        assert [u.id for u in roster] == [world["student"]]
        plan = await service.manager.plans.get(world["plan"])
        assert plan is not None and plan.advisor_id == world["advisor2"]
    assert world["png"].exists()                  # files kept on transfer


async def test_deleting_advisor_detaches_students_without_wiping_them(
    sessionmaker, world, queue
):
    async with sessionmaker() as s:
        service = DeletionService(s, queue.service.storage_root)
        admin = await service.manager.users.by_id(world["admin"])
        report = await service.delete_advisor(admin, world["advisor"], strategy="detach")
        await s.commit()

        assert report.detached == 1
        assert await service.manager.users.by_id(world["student"]) is not None
        assert await service.manager.users.students_of(world["advisor2"]) == []
    after = await counts(sessionmaker)
    assert after["students"] == 1 and after["links"] == 0 and after["plans"] == 0


async def test_admin_cannot_delete_or_suspend_themselves(sessionmaker, world, queue):
    async with sessionmaker() as s:
        service = DeletionService(s, queue.service.storage_root)
        admin = await service.manager.users.by_id(world["admin"])
        with pytest.raises(StudentError):
            await service.delete_advisor(admin, world["admin"])


async def test_advisor_cannot_delete_an_advisor(sessionmaker, world, queue):
    async with sessionmaker() as s:
        service = DeletionService(s, queue.service.storage_root)
        advisor = await service.manager.users.by_id(world["advisor"])
        with pytest.raises(AccessDenied):
            await service.delete_advisor(advisor, world["advisor2"])


async def test_admin_transfers_a_student(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(
        AdminCB(action="transfer", ref=world["student"]).pack(), ADMIN_TG, 1))
    assert "تغییر مشاور" in texts(api) and any("مشاور دوم" in b for b in buttons(api))

    await dp.feed_update(bot, callback_update(
        AdminCB(action="transfer_to", ref=world["student"],
                arg=str(world["advisor2"])).pack(), ADMIN_TG, 2))
    api.clear()
    await dp.feed_update(bot, callback_update(
        AdminCB(action="do_transfer", ref=world["student"],
                arg=str(world["advisor2"])).pack(), ADMIN_TG, 3))

    async with sessionmaker() as s:
        users = UserRepository(s)
        assert [u.id for u in await users.students_of(world["advisor2"])] == [world["student"]]
        assert await users.students_of(world["advisor"]) == []


async def test_advisor_cannot_transfer_students(sessionmaker, world, queue):
    async with sessionmaker() as s:
        service = DeletionService(s, queue.service.storage_root)
        advisor = await service.manager.users.by_id(world["advisor"])
        with pytest.raises(AccessDenied):
            await service.transfer_student(advisor, world["student"], world["advisor2"])


# ═══════════════════════ 3. plans, connection, files ═══════════════════════
async def test_admin_deletes_a_plan_with_its_files(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    await dp.feed_update(bot, callback_update(
        AdminCB(action="ask_del_plan", ref=world["plan"]).pack(), ADMIN_TG, 1))
    api.clear()
    await dp.feed_update(bot, callback_update(
        AdminCB(action="del_plan", ref=world["plan"]).pack(), ADMIN_TG, 2))
    async with sessionmaker() as s:
        assert await PlanManager(s).plans.get(world["plan"]) is None
    assert not world["png"].exists() and not world["pdf"].exists()


async def test_admin_can_view_plan_history_of_a_student(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(
        AdminCB(action="student_plans", ref=world["student"]).pack(), ADMIN_TG, 1))
    assert "مدیریت برنامه‌ها" in texts(api)


async def test_admin_unlinks_telegram(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    await dp.feed_update(bot, callback_update(
        AdminCB(action="ask_unlink", ref=world["student"]).pack(), ADMIN_TG, 1))
    api.clear()
    await dp.feed_update(bot, callback_update(
        AdminCB(action="do_unlink", ref=world["student"]).pack(), ADMIN_TG, 2))
    async with sessionmaker() as s:
        student = await UserRepository(s).by_id(world["student"])
        assert student.telegram_id is None and student.role is Role.STUDENT


async def test_admin_reissues_an_invite(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    async with sessionmaker() as s:
        student = await UserRepository(s).by_id(world["student"])
        student.telegram_id = None
        await s.commit()
    api.clear()
    await dp.feed_update(bot, callback_update(
        AdminCB(action="reissue", ref=world["student"]).pack(), ADMIN_TG, 1))
    assert "?start=inv_" in texts(api)
    async with sessionmaker() as s:
        assert (await UserRepository(s).by_id(world["student"])).invite_token


async def test_admin_edits_advisor_and_student(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    await dp.feed_update(bot, callback_update(
        AdminCB(action="edit_advisor", ref=world["advisor"]).pack(), ADMIN_TG, 1))
    await dp.feed_update(bot, message_update("مشاور ویرایش‌شده", ADMIN_TG, 2))

    await dp.feed_update(bot, callback_update(
        AdminCB(action="edit_student", ref=world["student"]).pack(), ADMIN_TG, 3))
    await dp.feed_update(bot, message_update("علی جدید | یازدهم", ADMIN_TG, 4))

    async with sessionmaker() as s:
        users = UserRepository(s)
        advisor = await users.by_id(world["advisor"])
        student = await users.by_id(world["student"])
        assert advisor.full_name == "مشاور ویرایش‌شده" and advisor.role is Role.ADVISOR
        assert student.full_name == "علی جدید" and student.grade == "یازدهم"
        assert student.role is Role.STUDENT      # editing never changes a role


async def test_admin_search(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    await dp.feed_update(bot, callback_update(
        AdminCB(action="search_student").pack(), ADMIN_TG, 1))
    api.clear()
    await dp.feed_update(bot, message_update("علی", ADMIN_TG, 2))
    assert any("علی رضایی" in b for b in buttons(api))

    await dp.feed_update(bot, callback_update(
        AdminCB(action="search_advisor").pack(), ADMIN_TG, 3))
    api.clear()
    await dp.feed_update(bot, message_update(str(ADV_TG), ADMIN_TG, 4))
    assert any("مشاور اول" in b for b in buttons(api))


# ═══════════════════════ 4. profiles are separated ═════════════════════════
async def test_advisor_profile(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(Nav(to="profile").pack(), ADV_TG, 1))
    body = texts(api)
    assert "پروفایل من" in body and "مشاور اول" in body
    assert "دانش‌آموزان من" in " ".join(buttons(api))
    assert "پنل مدیریت" not in " ".join(buttons(api))     # advisor sees no admin entry


async def test_student_profile_has_no_advisor_tools(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(Nav(to="profile").pack(), STU_TG, 1))
    body = texts(api)
    assert "پروفایل من" in body and "علی رضایی" in body
    labels = " ".join(buttons(api))
    for forbidden in ("برنامه جدید", "دانش‌آموزان", "پیش‌نویس", "پنل مدیریت"):
        assert forbidden not in labels, f"menu leakage: {forbidden}"


async def test_admin_sees_admin_entry_in_profile(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(Nav(to="profile").pack(), ADMIN_TG, 1))
    assert "پنل مدیریت" in " ".join(buttons(api))


# ═══════════════════════ 5. the interface is Persian ═══════════════════════
ENGLISH_WORDS = [
    "Storage", "Audit", "Logs", "Connected", "Disconnected", "Online", "Offline",
    "Database", "System", "Settings", "Statistics", "History", "Draft",
    "Generate", "Generated", "Delete", "Edit", "Back", "Next", "Previous",
    "Cancel", "Confirm", "Status", "Active", "Inactive", "Pending", "Expired",
    "Invalid", "Error", "Success", "Long Polling", "Fallback", "Available",
]


def test_no_english_words_in_user_facing_strings():
    """UI copy must be Persian; internal identifiers may stay English."""
    import app.bot.texts as T

    offenders = []
    for name in dir(T):
        if not name.isupper():
            continue
        value = getattr(T, name)
        strings = (
            [value] if isinstance(value, str)
            else list(value.values()) if isinstance(value, dict) else []
        )
        for text in strings:
            # ignore code samples/links, html tags and placeholders
            cleaned = re.sub(r"<[^>]+>|\{[^}]*\}|https?://\S+|python -m [^\n]*", " ", text)
            for word in ENGLISH_WORDS:
                if re.search(rf"\b{word}\b", cleaned):
                    offenders.append(f"{name}: {word}")
    assert offenders == [], offenders


def test_no_english_words_in_button_labels():
    sys.path.insert(0, str(ROOT / "tests"))
    from test_audit import all_keyboards

    offenders = []
    for screen, markup in all_keyboards().items():
        for row in markup.inline_keyboard:
            for button in row:
                for word in ENGLISH_WORDS:
                    if re.search(rf"\b{word}\b", button.text):
                        offenders.append(f"{screen}: {button.text}")
    assert offenders == [], offenders


def test_audit_actions_are_translated():
    from app.bot.texts import AUDIT_ACTIONS_FA, audit_fa

    for action in ("student.deleted", "advisor.deleted", "plan.generated",
                   "invite.accepted", "invite.role_conflict", "telegram.unlinked",
                   "student.advisor_changed", "storage.cleanup"):
        assert action in AUDIT_ACTIONS_FA, f"{action} has no Persian label"
        assert not re.search(r"[A-Za-z]", audit_fa(action)), audit_fa(action)


def test_plan_statuses_are_translated():
    from app.bot.texts import PLAN_STATUS_FA
    from app.db.models import PlanStatusDB

    for status in PlanStatusDB:
        assert status.value in PLAN_STATUS_FA, status.value


async def test_admin_screens_contain_no_english(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    screens = ["home", "advisors", "students", "plans", "storage", "db", "stats",
               "audit", "system", "bot", "settings"]
    for action in screens:
        api.clear()
        await dp.feed_update(bot, callback_update(
            AdminCB(action=action).pack(), ADMIN_TG, 1))
        body = re.sub(r"<[^>]+>|<code>.*?</code>|https?://\S+", " ", texts(api))
        body = re.sub(r"/data/\S+|sqlite\S+|postgresql\S+", " ", body)
        for word in ENGLISH_WORDS:
            assert not re.search(rf"\b{word}\b", body), f"'{word}' on screen '{action}'"


async def test_no_dead_delete_button(bot_and_dp, sessionmaker, world):
    """Every delete button must lead to a real, verifiable deletion."""
    bot, api, dp = bot_and_dp
    before = await counts(sessionmaker)
    api.clear()
    await dp.feed_update(bot, callback_update(
        StudentCB(action="ask_del", student_id=world["student"]).pack(), ADV_TG, 1))
    await dp.feed_update(bot, callback_update(
        StudentCB(action="del_confirm", student_id=world["student"]).pack(), ADV_TG, 2))
    await dp.feed_update(bot, callback_update(
        StudentCB(action="del", student_id=world["student"]).pack(), ADV_TG, 3))
    after = await counts(sessionmaker)
    assert after["students"] == before["students"] - 1
    assert after["links"] == 0 and after["plans"] == 0 and after["files"] == 0
