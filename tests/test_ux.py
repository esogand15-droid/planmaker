"""UX audit: every screen must be navigable, compact and consistent."""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.bot import keyboards as kb  # noqa: E402
from app.bot.main import build_dispatcher  # noqa: E402
from app.bot.texts import AssignCB, DayCB, Nav, PlanCB, SlotCB, StudentCB, WeekCB  # noqa: E402
from app.db.models import Base, Role  # noqa: E402
from app.domain.models import SLOTS_PER_DAY, WEEKDAY_KEYS, Activity  # noqa: E402
from app.domain.persian import saturday_of  # noqa: E402
from app.rendering.factory import get_renderer  # noqa: E402
from app.repositories.repositories import UserRepository  # noqa: E402
from app.services.plan_manager import PlanManager  # noqa: E402
from app.services.plan_service import WeeklyPlanService  # noqa: E402
from app.services.render_queue import RenderQueue  # noqa: E402
from tests.mocks import callback_update, make_bot, message_update  # noqa: E402

ADVISOR_TG = 701
STUDENT_TG = 702

BACK_HINTS = ("بازگشت", "منو", "انصراف", "تکمیل", "بله", "خیر", "تأیید", "ویرایش")
EMOJI_RE = re.compile(
    "[\U0001f300-\U0001faff\u2190-\u21ff\u2300-\u27bf\u2b00-\u2bff\ufe0f]"
)


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
        advisor = await users.create("مشاور", Role.ADVISOR, telegram_id=ADVISOR_TG)
        student = await users.create("علی رضایی", Role.STUDENT, telegram_id=STUDENT_TG)
        await users.link_student(advisor.id, student.id)
        manager = PlanManager(s)
        plan = await manager.create_plan(advisor, student.id, saturday_of(date.today()))
        await manager.set_slot(advisor, plan.id, "saturday", 0,
                               Activity(0, subject="زیست", topic="گوارش"))
        await s.commit()
        return {"advisor": advisor.id, "student": student.id, "plan": plan.id}


@pytest.fixture()
def bot_and_dp(tmp_path, sessionmaker):
    service = WeeklyPlanService(get_renderer("pillow"), storage_root=tmp_path)
    bot, api = make_bot()
    return bot, api, build_dispatcher(RenderQueue(service), sessionmaker)


def markups(api):
    out = []
    for r in api.requests:
        markup = getattr(r, "reply_markup", None)
        if markup and getattr(markup, "inline_keyboard", None):
            out.append(markup)
    return out


# ------------------------------------------------------- static keyboards ---
def _all_static_keyboards(plan_stub=None):
    from app.domain.models import WeeklyPlan

    domain = WeeklyPlan(student_name="علی", student_id="1")
    domain.apply_week_start(saturday_of(date.today()))
    return {
        "advisor_menu": kb.advisor_menu(),
        "student_menu": kb.student_menu(True),
        "week_choices": kb.week_choices(1, saturday_of(date.today())),
        "days_overview": kb.days_overview(1, domain),
        "day_editor": kb.day_editor(1, domain, "saturday"),
        "copy_targets": kb.copy_day_targets(1, "saturday"),
        "slot_editor": kb.slot_editor(1, "saturday", 0, True),
        "assignments": kb.assignments_editor(1, True),
        "preview": kb.preview_actions(1),
        "confirm": kb.confirm_actions(1),
        "generated": kb.generated_actions(1, True),
        "send_confirm": kb.send_confirm(1),
        "confirm_delete": kb.confirm_delete(1),
        "back_only": kb.back_only(),
    }


ROOT_SCREENS = {"advisor_menu", "student_menu"}  # nothing to go back to


@pytest.mark.parametrize(
    "name", [k for k in _all_static_keyboards() if k not in ROOT_SCREENS]
)
def test_every_screen_offers_a_way_out(name):
    markup = _all_static_keyboards()[name]
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert any(any(h in label for h in BACK_HINTS) for label in labels), (
        f"screen '{name}' has no back/cancel path: {labels}"
    )


@pytest.mark.parametrize("name", list(_all_static_keyboards().keys()))
def test_keyboards_are_compact_and_readable(name):
    markup = _all_static_keyboards()[name]
    rows = markup.inline_keyboard
    assert len(rows) <= 11, f"'{name}' has too many rows ({len(rows)})"
    for row in rows:
        # up to 3 per row only when the labels are short (e.g. weekday names)
        limit = 3 if all(len(b.text) <= 14 for b in row) else 2
        assert len(row) <= limit, f"'{name}' has a crowded row: {[b.text for b in row]}"
        for button in row:
            assert len(button.text) <= 34, f"label too long: {button.text!r}"
            assert len(EMOJI_RE.findall(button.text)) <= 2, (
                f"too many emoji in {button.text!r}"
            )
            assert button.text.strip() == button.text


@pytest.mark.parametrize("name", list(_all_static_keyboards().keys()))
def test_callback_data_is_short_and_structured(name):
    markup = _all_static_keyboards()[name]
    for row in markup.inline_keyboard:
        for button in row:
            if not button.callback_data:
                continue
            payload = button.callback_data
            assert len(payload.encode()) <= 64, f"callback too long: {payload}"
            assert payload.split(":")[0] in {"n", "st", "wk", "p", "d", "s", "a"}, payload


def test_day_editor_shows_all_eight_slots_with_empty_placeholder():
    from app.domain.models import WeeklyPlan

    domain = WeeklyPlan(student_name="علی", student_id="1")
    domain.apply_week_start(saturday_of(date.today()))
    domain.day("saturday").set_slot(0, Activity(0, subject="زیست", topic="گوارش"))
    labels = [b.text for row in kb.day_editor(1, domain, "saturday").inline_keyboard for b in row]
    slot_labels = [x for x in labels if x[0] in "۱۲۳۴۵۶۷۸"]
    assert len(slot_labels) == SLOTS_PER_DAY
    assert sum("خالی" in x for x in slot_labels) == SLOTS_PER_DAY - 1
    assert all(len(x) <= 34 for x in slot_labels)  # long activities are truncated


def test_days_overview_lists_all_seven_days():
    from app.domain.models import WeeklyPlan

    domain = WeeklyPlan(student_name="علی", student_id="1")
    domain.apply_week_start(saturday_of(date.today()))
    labels = [b.text for row in kb.days_overview(1, domain).inline_keyboard for b in row]
    for key in WEEKDAY_KEYS:
        assert any(kb.T.day_fa(key) in label for label in labels)


# ------------------------------------------------------------ live flow -----
async def test_every_step_of_the_real_flow_has_navigation(bot_and_dp, world):
    """Walk the documented flow and assert each screen is escapable."""
    bot, api, dp = bot_and_dp
    plan_id = world["plan"]
    steps = [
        ("start", message_update("/start", ADVISOR_TG, 1)),
        ("new", callback_update(Nav(to="new").pack(), ADVISOR_TG, 2)),
        ("student", callback_update(
            StudentCB(action="pick", student_id=world["student"]).pack(), ADVISOR_TG, 3)),
        ("week", callback_update(
            WeekCB(action="pick", student_id=world["student"], offset=0).pack(), ADVISOR_TG, 4)),
        ("days", callback_update(PlanCB(action="days", plan_id=plan_id).pack(), ADVISOR_TG, 5)),
        ("day", callback_update(
            DayCB(action="open", plan_id=plan_id, day="saturday").pack(), ADVISOR_TG, 6)),
        ("slot", callback_update(
            SlotCB(action="edit", plan_id=plan_id, day="saturday", slot=0).pack(), ADVISOR_TG, 7)),
        ("copy-day", callback_update(
            DayCB(action="copy", plan_id=plan_id, day="saturday").pack(), ADVISOR_TG, 8)),
        ("assignments", callback_update(
            AssignCB(action="open", plan_id=plan_id).pack(), ADVISOR_TG, 9)),
        ("confirm", callback_update(
            PlanCB(action="confirm", plan_id=plan_id).pack(), ADVISOR_TG, 10)),
        ("history", callback_update(Nav(to="history").pack(), ADVISOR_TG, 11)),
        ("drafts", callback_update(Nav(to="drafts").pack(), ADVISOR_TG, 12)),
        ("plan-card", callback_update(
            PlanCB(action="open", plan_id=plan_id).pack(), ADVISOR_TG, 13)),
        ("ask-delete", callback_update(
            PlanCB(action="ask_delete", plan_id=plan_id).pack(), ADVISOR_TG, 14)),
    ]
    for name, update in steps:
        api.clear()
        await dp.feed_update(bot, update)
        found = markups(api)
        assert found, f"step '{name}' produced no keyboard"
        labels = [b.text for row in found[-1].inline_keyboard for b in row]
        if name == "start":
            assert "➕ برنامه جدید" in labels  # root screen
            continue
        assert any(any(h in label for h in BACK_HINTS) for label in labels), (
            f"step '{name}' has no way back: {labels}"
        )


async def test_cancel_command_works_from_any_input_state(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    plan_id = world["plan"]
    await dp.feed_update(
        bot, callback_update(
            SlotCB(action="edit", plan_id=plan_id, day="saturday", slot=3).pack(), ADVISOR_TG, 1)
    )
    api.clear()
    await dp.feed_update(bot, message_update("/cancel", ADVISOR_TG, 2))
    assert any("لغو" in t for t in api.texts())

    # after cancelling, a plain message is no longer swallowed by the wizard
    api.clear()
    await dp.feed_update(bot, message_update("سلام", ADVISOR_TG, 3))
    assert api.calls("SendMessage") == [] or not any("ذخیره شد" in t for t in api.texts())


async def test_start_escapes_a_stuck_wizard(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    await dp.feed_update(
        bot, callback_update(AssignCB(action="open", plan_id=world["plan"]).pack(), ADVISOR_TG, 1)
    )
    api.clear()
    await dp.feed_update(bot, message_update("/start", ADVISOR_TG, 2))
    labels = [b.text for row in markups(api)[-1].inline_keyboard for b in row]
    assert "➕ برنامه جدید" in labels


async def test_invalid_custom_week_date_is_reported_not_crashing(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    await dp.feed_update(
        bot, callback_update(
            WeekCB(action="custom", student_id=world["student"]).pack(), ADVISOR_TG, 1)
    )
    api.clear()
    await dp.feed_update(bot, message_update("فردا", ADVISOR_TG, 2))
    assert any("نامعتبر" in t for t in api.texts())
    # a valid date afterwards still works (state survived the mistake)
    api.clear()
    await dp.feed_update(bot, message_update("1405/07/03", ADVISOR_TG, 3))
    assert any("شنبه" in " ".join(b.text for row in m.inline_keyboard for b in row)
               for m in markups(api))


async def test_student_screen_is_minimal(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, message_update("/start", STUDENT_TG, 1))
    labels = [b.text for row in markups(api)[-1].inline_keyboard for b in row]
    assert len(labels) <= 3, f"student menu is too busy: {labels}"


# ────────────────────────── button coverage (regression) ───────────────────
# Bug found in production v1.0.1: the main-menu button «👨‍🎓 دانش‌آموزان» had no
# handler, so Telegram spun forever. These tests press EVERY button of EVERY
# screen and require a real answer.

def _collect_callback_data() -> dict[str, str]:
    """Every callback_data the UI can emit, keyed by screen:label."""
    from app.domain.models import WeeklyPlan

    domain = WeeklyPlan(student_name="علی", student_id="1")
    domain.apply_week_start(saturday_of(date.today()))
    domain.day("saturday").set_slot(0, Activity(0, subject="زیست"))

    screens = _all_static_keyboards()
    screens["students_list"] = kb.students_list([], 0, 0, 8)
    out: dict[str, str] = {}
    for screen, markup in screens.items():
        for row in markup.inline_keyboard:
            for button in row:
                if button.callback_data:
                    out[f"{screen}:{button.text}"] = button.callback_data
    return out


def test_every_button_has_a_registered_handler():
    """Static check: each callback_data matches at least one router filter."""
    from aiogram.types import CallbackQuery, Chat, Message, User as TgUser
    from aiogram.types import Update  # noqa: F401

    from app.bot.handlers import advisor as advisor_mod
    from app.bot.handlers import common as common_mod
    from app.bot.handlers import fallback as fallback_mod
    from app.bot.handlers import student as student_mod

    prefixes: set[str] = set()
    for module in (common_mod, student_mod, advisor_mod, fallback_mod):
        for handler in module.router.callback_query.handlers:
            for flt in handler.filters or []:
                callback = getattr(flt, "callback", None)
                # CallbackData factory filters expose the owning class
                factory = getattr(callback, "callback_data", None)
                if factory is not None:
                    prefixes.add(factory.__prefix__)
    assert {"n", "st", "wk", "p", "d", "s", "a"} <= prefixes


@pytest.mark.parametrize("origin,payload", sorted(_collect_callback_data().items()))
async def test_pressing_any_button_gets_an_answer(bot_and_dp, world, origin, payload):
    """Dynamic check: no button may leave the user with a spinning clock."""
    bot, api, dp = bot_and_dp
    payload = payload.replace(":1:", f":{world['plan']}:")  # point at a real plan
    if payload.endswith(":1"):
        payload = payload[:-2] + f":{world['plan']}"
    api.clear()
    await dp.feed_update(bot, callback_update(payload, ADVISOR_TG, 1))

    answered = api.calls("AnswerCallbackQuery")
    produced_output = api.calls("SendMessage") or api.calls("EditMessageText") \
        or api.calls("SendPhoto") or api.calls("SendDocument")
    assert answered or produced_output, (
        f"button '{origin}' ({payload}) produced no response — Telegram would hang"
    )
    # the fallback router must not be the one rescuing us
    alerts = [a.text for a in answered if getattr(a, "text", None)]
    assert not any("دیگر معتبر نیست" in (t or "") for t in alerts), (
        f"button '{origin}' ({payload}) has no dedicated handler"
    )


async def test_students_menu_button_opens_the_roster(bot_and_dp, world):
    """The exact bug reported in production."""
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(Nav(to="students").pack(), ADVISOR_TG, 1))
    assert api.calls("AnswerCallbackQuery"), "callback was never answered"
    text = " ".join(api.texts())
    assert "دانش‌آموز" in text
    buttons = [b.text for m in markups(api) for row in m.inline_keyboard for b in row]
    assert any("علی رضایی" in b for b in buttons), buttons  # roster is clickable


async def test_students_screen_is_empty_state_when_nobody_assigned(
    bot_and_dp, sessionmaker
):
    bot, api, dp = bot_and_dp
    async with sessionmaker() as s:
        await UserRepository(s).create("مشاور تنها", Role.ADVISOR, telegram_id=7777)
        await s.commit()
    api.clear()
    await dp.feed_update(bot, callback_update(Nav(to="students").pack(), 7777, 1))
    assert any("هنوز دانش‌آموزی ثبت نکرده‌اید" in t for t in api.texts()), api.texts()
    buttons = [b.text for m in markups(api) for row in m.inline_keyboard for b in row]
    assert any("افزودن دانش‌آموز" in b for b in buttons), buttons
    assert api.calls("AnswerCallbackQuery")


async def test_unknown_callback_is_answered_not_hanging(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update("n:totally_unknown", ADVISOR_TG, 1))
    answered = api.calls("AnswerCallbackQuery")
    assert answered, "orphan callback must still be answered"
    assert any("معتبر نیست" in (a.text or "") for a in answered)


async def test_preview_then_generate_is_not_throttled(bot_and_dp, world):
    """Regression: a shared render cooldown swallowed the generate press."""
    from app.bot.middlewares import ThrottleMiddleware

    bot, api, dp = bot_and_dp
    pid = world["plan"]
    await dp.feed_update(bot, callback_update(PlanCB(action="preview", plan_id=pid).pack(),
                                              ADVISOR_TG, 1))
    api.clear()
    await dp.feed_update(bot, callback_update(PlanCB(action="generate", plan_id=pid).pack(),
                                              ADVISOR_TG, 2))
    assert api.calls("SendPhoto"), "generate right after preview must still run"

    assert ThrottleMiddleware.HEAVY_ACTIONS  # documented list of protected actions


async def test_double_click_on_the_same_render_button_is_suppressed():
    """Deterministic unit check of the per-action cooldown (no rendering delay)."""
    from aiogram.types import User as TgUser

    from app.bot.middlewares import ThrottleMiddleware

    middleware = ThrottleMiddleware(heavy_cooldown=30.0)
    calls = []

    async def handler(event, data):
        calls.append(event.data)
        return "ran"

    class _Event:
        def __init__(self, data):
            self.data = data

        async def answer(self, *a, **kw):
            return None

    data = {"event_from_user": TgUser(id=42, is_bot=False, first_name="x")}
    assert await middleware(handler, _Event("p:generate:1:0"), data) == "ran"
    assert await middleware(handler, _Event("p:generate:1:0"), data) is None  # dupe
    # a different heavy action is NOT blocked by the previous one
    assert await middleware(handler, _Event("p:preview:1:0"), data) == "ran"
    # ordinary navigation is never blocked
    assert await middleware(handler, _Event("n:menu"), data) == "ran"
    assert calls == ["p:generate:1:0", "p:preview:1:0", "n:menu"]
