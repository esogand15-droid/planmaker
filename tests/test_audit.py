"""Static audits: no orphan buttons, no dead handlers, no forgotten TODOs.

These tests are the guardrail for the whole UI surface — they walk the actual
keyboard builders and router registrations instead of trusting a checklist.
"""
from __future__ import annotations

import ast
import re
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.bot import keyboards as kb  # noqa: E402
from app.bot.handlers import admin as admin_mod  # noqa: E402
from app.bot.handlers import advisor as advisor_mod  # noqa: E402
from app.bot.handlers import common as common_mod  # noqa: E402
from app.bot.handlers import fallback as fallback_mod  # noqa: E402
from app.bot.handlers import student as student_mod  # noqa: E402
from app.bot.states import PlanFlow  # noqa: E402
from app.domain.models import Activity, WeeklyPlan  # noqa: E402
from app.domain.persian import saturday_of, today_local  # noqa: E402

APP_DIR = ROOT / "app"
HANDLER_MODULES = (common_mod, student_mod, advisor_mod, admin_mod)


def _sample_student(**kw):
    from app.db.models import Role, User

    return User(
        id=kw.get("id", 7), full_name=kw.get("name", "علی رضایی"),
        role=Role.STUDENT, grade=kw.get("grade", "دوازدهم تجربی"),
        telegram_id=kw.get("telegram_id"), invite_token=kw.get("invite_token", "t" * 32),
    )


def _sample_request():
    from app.db.models import AccessRequest, RequestStatus

    return AccessRequest(
        id=5, telegram_id=987654321, full_name="مهمان ناشناس",
        username="guest", status=RequestStatus.PENDING, visits=2,
    )


def _sample_advisor(active: bool = True):
    from app.db.models import Role, User

    return User(id=9, full_name="مشاور نمونه", role=Role.ADVISOR,
                telegram_id=555, is_active=active)


def _sample_plan(with_versions: bool = False, with_files: bool = False):
    from app.db.models import PlanFile, PlanStatusDB, WeeklyPlanDB

    plan = WeeklyPlanDB(
        id=3, student_id=7, advisor_id=1,
        week_start=saturday_of(today_local()),
        week_end=saturday_of(today_local()),
        status=PlanStatusDB.GENERATED, version=2,
    )
    plan.student = _sample_student()
    plan.advisor = _sample_advisor()
    if with_files:
        plan.image_path = "a.png"
        plan.pdf_path = "a.pdf"
    plan.files = (
        [
            PlanFile(id=i, plan_id=3, version=i, plan_hash="h", image_path="a.png",
                     pdf_path="a.pdf", template_version="t", renderer_version="r")
            for i in (1, 2)
        ]
        if with_versions
        else []
    )
    return plan


def all_keyboards() -> dict:
    domain = WeeklyPlan(student_name="علی", student_id="7")
    domain.apply_week_start(saturday_of(today_local()))
    domain.day("saturday").set_slot(0, Activity(0, subject="زیست"))
    pending = _sample_student()
    connected = _sample_student(telegram_id=123, invite_token=None)
    plan = _sample_plan()

    return {
        "advisor_menu": kb.advisor_menu(),
        "student_menu": kb.student_menu(True),
        "students_list_pick": kb.students_list([pending], 0, 20, 8, mode="pick"),
        "students_list_card": kb.students_list([pending, connected], 0, 20, 8, mode="card"),
        "no_students": kb.no_students(),
        "student_card_pending": kb.student_card(pending),
        "student_card_connected": kb.student_card(connected),
        "connect_menu": kb.connect_menu(pending),
        "connect_menu_done": kb.connect_menu(connected),
        "invite_ready": kb.invite_ready(pending),
        "confirm_remove_student": kb.confirm_remove_student(7),
        "student_created": kb.student_created(pending),
        "week_choices": kb.week_choices(7, saturday_of(today_local())),
        "days_overview": kb.days_overview(3, domain),
        "day_editor": kb.day_editor(3, domain, "saturday"),
        "copy_targets": kb.copy_day_targets(3, "saturday"),
        "slot_editor": kb.slot_editor(3, "saturday", 0, True),
        "assignments": kb.assignments_editor(3, True),
        "preview": kb.preview_actions(3),
        "confirm": kb.confirm_actions(3),
        "generated": kb.generated_actions(3, True),
        "send_confirm": kb.send_confirm(3),
        "plan_card": kb.plan_card(plan, can_edit=True, can_send=True),
        "plan_card_versioned": kb.plan_card(
            _sample_plan(with_versions=True), can_edit=True, can_send=True
        ),
        "versions_list": kb.versions_list(_sample_plan(with_versions=True).files, 3),
        "plan_list": kb.plan_list([plan], 0, 20, 6, kind="history"),
        "plan_list_student": kb.plan_list([plan], 1, 20, 6, kind="student", ref=7),
        "confirm_delete": kb.confirm_delete(3),
        "back_only": kb.back_only(),
        # ── admin panel ──
        "advisor_menu_with_admin": kb.advisor_menu_with_admin(),
        "admin_menu": kb.admin_menu(),
        "admin_back": kb.admin_back(),
        "admin_advisors": kb.admin_advisors(
            [(_sample_advisor(), 3, 5)], 1, 40, 6
        ),
        "admin_advisor_card": kb.admin_advisor_card(_sample_advisor()),
        "admin_advisor_card_suspended": kb.admin_advisor_card(
            _sample_advisor(active=False)
        ),
        "admin_students": kb.admin_students([pending, connected], 1, 40, 6),
        "admin_students_of_advisor": kb.admin_students([pending], 0, 40, 6, ref=9),
        "admin_student_card": kb.admin_student_card(pending),
        "admin_confirm_suspend": kb.admin_confirm("do_suspend", 9),
        "admin_confirm_suspend_student": kb.admin_confirm("do_suspend_student", 7),
        "admin_confirm_cleanup": kb.admin_confirm("do_cleanup", 0),
        "admin_storage": kb.admin_storage(3),
        "admin_storage_clean": kb.admin_storage(0),
        "admin_system": kb.admin_system(),
        "admin_audit": kb.admin_audit(1, 40, 6),
        "admin_delete_advisor_plain": kb.admin_delete_advisor(9, has_students=False),
        "admin_delete_advisor_students": kb.admin_delete_advisor(9, has_students=True),
        "admin_pick_transfer": kb.admin_pick_advisor(
            [_sample_advisor()], 7, "del_advisor_to"
        ),
        "admin_pick_student_transfer": kb.admin_pick_advisor(
            [_sample_advisor()], 7, "transfer_to"
        ),
        "admin_connection_pending": kb.admin_connection(pending),
        "admin_connection_linked": kb.admin_connection(connected),
        "admin_plans": kb.admin_plans([plan], 1, 40, 6),
        "admin_plan_card": kb.admin_plan_card(_sample_plan(with_files=True)),
        "admin_confirm_del_student": kb.admin_confirm("del_student", 7),
        "admin_confirm_del_student_final": kb.admin_confirm("del_student_final", 7),
        "admin_confirm_del_plan": kb.admin_confirm("del_plan", 3),
        "admin_confirm_transfer": kb.admin_confirm("do_transfer", 7, "9"),
        "admin_confirm_unlink": kb.admin_confirm("do_unlink", 7),
        "confirm_remove_student_final": kb.confirm_remove_student(7, final=True),
        "range_summary": kb.range_summary(7),
        "confirm_new_invite": kb.confirm_new_invite(7),
        "student_card_with_link": kb.student_card(pending, invite_link="https://t.me/x"),
        "admin_requests": kb.admin_requests([_sample_request()], 1, 40, 6),
        "admin_request_card": kb.admin_request_card(5),
        "admin_pick_advisor_for_request": kb.admin_pick_advisor_for_request(
            [_sample_advisor()], 5
        ),
        "admin_no_advisors": kb.admin_no_advisors(),
    }


def emitted_payloads() -> dict[str, str]:
    out: dict[str, str] = {}
    for screen, markup in all_keyboards().items():
        for row in markup.inline_keyboard:
            for button in row:
                if button.callback_data:
                    out[f"{screen}:{button.text}"] = button.callback_data
    return out


# ───────────────────────────── callback plumbing ────────────────────────────
def test_no_callback_data_exceeds_the_telegram_limit():
    for origin, payload in emitted_payloads().items():
        assert len(payload.encode()) <= 64, f"{origin} → {payload} ({len(payload)}B)"


def test_every_emitted_callback_has_a_consumer():
    """Simulate aiogram's filter resolution for every button in the product."""
    from aiogram.filters.callback_data import CallbackData

    factories = {
        cls.__prefix__: cls
        for cls in _iter_subclasses(CallbackData)
        if getattr(cls, "__prefix__", None)
    }
    orphans = []
    for origin, payload in emitted_payloads().items():
        if payload == "noop":       # inert label button, answered by common.noop
            continue
        prefix = payload.split(":")[0]
        factory = factories.get(prefix)
        assert factory is not None, f"{origin}: unknown prefix {prefix!r}"
        parsed = factory.unpack(payload)
        if not _has_consumer(parsed):
            orphans.append(f"{origin} → {payload}")
    assert orphans == [], "buttons with no handler: " + ", ".join(orphans)


def _iter_subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _iter_subclasses(sub)


def _has_consumer(parsed) -> bool:
    """True when at least one registered handler's filters accept this payload."""
    for module in HANDLER_MODULES:
        for handler in module.router.callback_query.handlers:
            for flt in handler.filters or []:
                callback = getattr(flt, "callback", None)
                factory = getattr(callback, "callback_data", None)
                if factory is None or not isinstance(parsed, factory):
                    continue
                rule = getattr(callback, "rule", None)
                if rule is None:
                    return True
                try:
                    if rule.resolve(parsed):
                        return True
                except Exception:  # pragma: no cover - defensive
                    continue
    return False


def test_no_handler_is_unreachable():
    """Every callback handler must be triggerable from some screen."""
    payloads = list(emitted_payloads().values())
    from aiogram.filters.callback_data import CallbackData

    factories = {
        cls.__prefix__: cls
        for cls in _iter_subclasses(CallbackData)
        if getattr(cls, "__prefix__", None)
    }
    parsed_all = [
        factories[p.split(":")[0]].unpack(p) for p in payloads
        if p != "noop" and p.split(":")[0] in factories
    ]

    unreachable = []
    for module in HANDLER_MODULES:
        for handler in module.router.callback_query.handlers:
            filters = handler.filters or []
            cb_filters = [
                f for f in filters
                if getattr(getattr(f, "callback", None), "callback_data", None)
            ]
            if not cb_filters:
                continue  # plain F.data filters (e.g. noop) are checked elsewhere
            if not any(_matches(f, parsed) for f in cb_filters for parsed in parsed_all):
                unreachable.append(f"{module.__name__}.{handler.callback.__name__}")
    # documented exceptions: reached from message flows, not from a button
    allowed = set()
    assert set(unreachable) <= allowed, f"handlers with no UI trigger: {unreachable}"


def _matches(flt, parsed) -> bool:
    callback = flt.callback
    factory = callback.callback_data
    if not isinstance(parsed, factory):
        return False
    rule = getattr(callback, "rule", None)
    if rule is None:
        return True
    try:
        return bool(rule.resolve(parsed))
    except Exception:  # pragma: no cover
        return False


def test_callback_prefixes_are_short_and_unique():
    from aiogram.filters.callback_data import CallbackData

    prefixes = [
        cls.__prefix__ for cls in _iter_subclasses(CallbackData)
        if getattr(cls, "__prefix__", None)
    ]
    assert len(prefixes) == len(set(prefixes)), f"duplicate prefixes: {prefixes}"
    assert all(len(p) <= 2 for p in prefixes), prefixes


# ────────────────────────────── state machine ───────────────────────────────
def test_every_state_is_entered_and_left():
    """No FSM state may be declared without a handler that sets it."""
    sources = "".join(
        p.read_text(encoding="utf-8") for p in (APP_DIR / "bot").rglob("*.py")
    )
    states = [name for name in vars(PlanFlow) if not name.startswith("_")
              and name not in {"__states__"}]
    declared = [s for s in states if isinstance(getattr(PlanFlow, s), object)]
    for state in declared:
        if state in {"preview"}:  # reserved: preview is rendered, not a text step
            continue
        assert f"PlanFlow.{state}" in sources, f"state '{state}' is never used"
        # a state that accepts input must be reachable via set_state
        assert f"set_state(PlanFlow.{state})" in sources, (
            f"state '{state}' is filtered on but never entered"
        )
    assert "state.clear()" in sources, "no state is ever cleared"


# ─────────────────────────── dead code / hygiene ────────────────────────────
def test_no_todo_or_fixme_in_production_code():
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\b(TODO|FIXME|XXX|HACK)\b", line):
                offenders.append(f"{path.relative_to(ROOT)}:{i}")
    assert offenders == [], offenders


def test_no_bare_placeholders_or_not_implemented():
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and isinstance(node.exc, (ast.Name, ast.Call)):
                name = getattr(node.exc, "id", None) or getattr(
                    getattr(node.exc, "func", None), "id", None
                )
                if name == "NotImplementedError":
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = [n for n in node.body if not isinstance(n, ast.Expr)]
                if len(body) == 1 and isinstance(body[0], ast.Pass):
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{node.lineno} ({node.name} is empty)"
                    )
    assert offenders == [], offenders


def test_no_unused_private_helpers():
    """A module-level _helper that nobody calls is dead weight."""
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:  # module level only
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                    node.name.startswith("_") and not node.name.startswith("__"):
                if len(re.findall(rf"\b{re.escape(node.name)}\b", source)) <= 1:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.name}")
    assert offenders == [], offenders


def test_no_unused_imports_in_app():
    """A name imported but never mentioned again is dead weight."""
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [(a.asname or a.name).split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.module == "__future__":
                    continue
                names = [a.asname or a.name for a in node.names if a.name != "*"]
            for name in names:
                # one occurrence == the import statement itself
                if len(re.findall(rf"\b{re.escape(name)}\b", source)) <= 1:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {name}")
    assert offenders == [], offenders


def test_no_unused_ui_strings():
    texts_src = (APP_DIR / "bot" / "texts.py").read_text(encoding="utf-8")
    names = re.findall(r"^([A-Z][A-Z_0-9]{2,})\s*=", texts_src, re.M)
    others = "".join(
        p.read_text(encoding="utf-8")
        for p in (APP_DIR / "bot").rglob("*.py")
        if p.name != "texts.py"
    )
    unused = [
        n for n in names
        if f"T.{n}" not in others
        and f"texts.{n}" not in others
        # a constant consumed by a helper inside texts.py itself is still used
        and len(re.findall(rf"\b{n}\b", texts_src)) <= 1
    ]
    assert unused == [], f"unused UI strings: {unused}"


# ───────────────────────────── database audit ───────────────────────────────
def test_database_fields_are_either_used_or_documented():
    """Every column must be written somewhere, or listed as a documented reserve."""
    from app.db import models

    app_src = "".join(
        p.read_text(encoding="utf-8") for p in APP_DIR.rglob("*.py")
        if p.name != "models.py"
    )
    documented_reserves = {
        ("users", "phone"),            # planned: SMS delivery of plans
        ("activities", "notes"),       # planned: private advisor notes per activity
    }
    unused = []
    for table in models.Base.metadata.sorted_tables:
        for column in table.columns:
            if column.primary_key or column.foreign_keys:
                continue
            if column.name in {"created_at", "updated_at", "id"}:
                continue
            if (table.name, column.name) in documented_reserves:
                continue
            if not re.search(rf"\b{re.escape(column.name)}\b", app_src):
                unused.append(f"{table.name}.{column.name}")
    assert unused == [], f"columns never touched by the app: {unused}"


def test_plan_status_values_are_all_reachable():
    from app.db.models import PlanStatusDB

    app_src = "".join(p.read_text(encoding="utf-8") for p in APP_DIR.rglob("*.py"))
    reserved = {"READY", "ARCHIVED"}  # documented: kept for future workflow states
    for status in PlanStatusDB:
        if status.name in reserved:
            continue
        assert f"PlanStatusDB.{status.name}" in app_src or f'"{status.value}"' in app_src, (
            f"status {status.name} is never set"
        )


def test_reserved_schema_is_documented_in_review():
    review = (ROOT / "REVIEW.md").read_text(encoding="utf-8")
    for token in ("phone", "notes", "READY", "ARCHIVED"):
        assert token in review, f"reserved schema '{token}' is not documented"


# ─────────────────────────────── versioning ─────────────────────────────────
def test_version_marker_exists():
    from app import __version__

    assert re.fullmatch(r"\d+\.\d+\.\d+(-\w+)?", __version__), __version__
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == __version__
