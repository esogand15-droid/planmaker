"""Security audit tests: authorization, IDOR, injection, path & secret safety."""
from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.bot.main import build_dispatcher  # noqa: E402
from app.bot.texts import AssignCB, DayCB, Nav, PlanCB, SlotCB, StudentCB  # noqa: E402
from app.config import Settings, mask_token, normalize_database_url  # noqa: E402
from app.db.models import Base, Role  # noqa: E402
from app.domain.models import Activity  # noqa: E402
from app.domain.persian import saturday_of  # noqa: E402
from app.logging_config import RedactingFilter, redact  # noqa: E402
from app.rendering.factory import get_renderer  # noqa: E402
from app.repositories.repositories import UserRepository  # noqa: E402
from app.services.plan_manager import AccessDenied, PlanManager  # noqa: E402
from app.services.plan_service import WeeklyPlanService  # noqa: E402
from app.services.render_queue import RenderQueue  # noqa: E402
from tests.mocks import callback_update, make_bot  # noqa: E402

ADVISOR_A_TG = 901
ADVISOR_B_TG = 902
STUDENT_A_TG = 903
STUDENT_B_TG = 904


@pytest_asyncio.fixture()
async def sessionmaker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture()
async def world(sessionmaker):
    """Two advisors, each with their own student and their own plan."""
    async with sessionmaker() as s:
        users = UserRepository(s)
        a = await users.create("مشاور A", Role.ADVISOR, telegram_id=ADVISOR_A_TG)
        b = await users.create("مشاور B", Role.ADVISOR, telegram_id=ADVISOR_B_TG)
        sa = await users.create("دانش‌آموز A", Role.STUDENT, telegram_id=STUDENT_A_TG)
        sb = await users.create("دانش‌آموز B", Role.STUDENT, telegram_id=STUDENT_B_TG)
        await users.link_student(a.id, sa.id)
        await users.link_student(b.id, sb.id)

        manager = PlanManager(s)
        plan_a = await manager.create_plan(a, sa.id, saturday_of(date.today()))
        plan_b = await manager.create_plan(b, sb.id, saturday_of(date.today()))
        await manager.set_slot(a, plan_a.id, "saturday", 0, Activity(0, subject="زیست"))
        await manager.set_slot(b, plan_b.id, "saturday", 0, Activity(0, subject="شیمی"))
        await s.commit()
        return {
            "advisor_a": a.id, "advisor_b": b.id,
            "student_a": sa.id, "student_b": sb.id,
            "plan_a": plan_a.id, "plan_b": plan_b.id,
        }


@pytest.fixture()
def queue(tmp_path):
    service = WeeklyPlanService(get_renderer("pillow"), storage_root=tmp_path / "generated")
    return RenderQueue(service, max_concurrent=2)


@pytest.fixture()
def bot_and_dp(queue, sessionmaker):
    bot, api = make_bot()
    return bot, api, build_dispatcher(queue, sessionmaker)


def _leaked(api, secret: str) -> bool:
    return any(secret in t for t in api.texts())


# ------------------------------------------------------------------ IDOR ----
@pytest.mark.parametrize(
    "payload_factory",
    [
        lambda pid: PlanCB(action="days", plan_id=pid).pack(),
        lambda pid: PlanCB(action="open", plan_id=pid).pack(),
        lambda pid: PlanCB(action="preview", plan_id=pid).pack(),
        lambda pid: PlanCB(action="confirm", plan_id=pid).pack(),
        lambda pid: PlanCB(action="generate", plan_id=pid).pack(),
        lambda pid: PlanCB(action="regenerate", plan_id=pid).pack(),
        lambda pid: PlanCB(action="png", plan_id=pid).pack(),
        lambda pid: PlanCB(action="pdf", plan_id=pid).pack(),
        lambda pid: PlanCB(action="ask_send", plan_id=pid).pack(),
        lambda pid: PlanCB(action="send", plan_id=pid).pack(),
        lambda pid: PlanCB(action="delete", plan_id=pid).pack(),
        lambda pid: PlanCB(action="copyweek", plan_id=pid).pack(),
        lambda pid: DayCB(action="open", plan_id=pid, day="saturday").pack(),
        lambda pid: DayCB(action="clear", plan_id=pid, day="saturday").pack(),
        lambda pid: DayCB(action="copyto", plan_id=pid, day="saturday", arg="monday").pack(),
        lambda pid: SlotCB(action="edit", plan_id=pid, day="saturday", slot=0).pack(),
        lambda pid: SlotCB(action="clear", plan_id=pid, day="saturday", slot=0).pack(),
        lambda pid: AssignCB(action="open", plan_id=pid).pack(),
        lambda pid: AssignCB(action="clear", plan_id=pid).pack(),
    ],
)
async def test_advisor_a_cannot_touch_plan_of_advisor_b(
    bot_and_dp, sessionmaker, world, payload_factory
):
    """Callback tampering: forge advisor B's plan_id while logged in as A."""
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(payload_factory(world["plan_b"]), ADVISOR_A_TG, 1))

    # no data of B may leak, and nothing may be mutated
    assert not _leaked(api, "شیمی")
    assert not _leaked(api, "دانش‌آموز B")
    assert api.calls("SendPhoto") == []
    assert api.calls("SendDocument") == []

    async with sessionmaker() as s:
        manager = PlanManager(s)
        plan_b = await manager.plans.get(world["plan_b"])
        assert plan_b is not None, "plan of advisor B must not be deleted"
        domain = PlanManager.to_domain(plan_b)
        assert domain.day("saturday").slot(0).subject == "شیمی"
        assert domain.day("monday").is_empty
        assert plan_b.status.value == "draft"  # never generated on B's behalf


async def test_student_cannot_edit_or_read_other_students_plan(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    api.clear()
    # student A tries to open student B's plan
    await dp.feed_update(
        bot, callback_update(PlanCB(action="open", plan_id=world["plan_b"]).pack(), STUDENT_A_TG, 1)
    )
    assert not _leaked(api, "دانش‌آموز B")
    # …and tries an advisor-only mutation on their own plan
    api.clear()
    await dp.feed_update(
        bot,
        callback_update(
            SlotCB(action="clear", plan_id=world["plan_a"], day="saturday", slot=0).pack(),
            STUDENT_A_TG, 2,
        ),
    )
    async with sessionmaker() as s:
        domain = PlanManager.to_domain(await PlanManager(s).plans.get(world["plan_a"]))
        assert domain.day("saturday").slot(0).subject == "زیست"


async def test_student_picker_rejects_unassigned_student(bot_and_dp, sessionmaker, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(
        bot,
        callback_update(
            StudentCB(action="pick", student_id=world["student_b"]).pack(), ADVISOR_A_TG, 1
        ),
    )
    assert any("تخصیص" in t or "دسترسی" in t for t in api.texts())
    async with sessionmaker() as s:
        plans = await PlanManager(s).plans.history(advisor_id=world["advisor_a"], limit=50)
        assert all(p.student_id != world["student_b"] for p in plans)


async def test_nonexistent_plan_id_is_handled_gracefully(bot_and_dp, world):
    bot, api, dp = bot_and_dp
    api.clear()
    await dp.feed_update(bot, callback_update(PlanCB(action="days", plan_id=999999).pack(),
                                              ADVISOR_A_TG, 1))
    assert any("پیدا نشد" in t or "دسترسی" in t for t in api.texts())
    assert not any("Traceback" in t or "Error" in t for t in api.texts())


async def test_service_layer_authorization_matrix(sessionmaker, world):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        a = await manager.users.by_id(world["advisor_a"])
        b = await manager.users.by_id(world["advisor_b"])
        sa = await manager.users.by_id(world["student_a"])
        plan_b = await manager.plans.get(world["plan_b"])

        for actor in (a, sa):
            with pytest.raises(AccessDenied):
                await manager.ensure_can_edit_plan(actor, plan_b)
            with pytest.raises(AccessDenied):
                await manager.ensure_can_view_plan(actor, plan_b)
        await manager.ensure_can_edit_plan(b, plan_b)

        # admin may do everything
        admin = await manager.users.create("ادمین", Role.ADMIN, telegram_id=999)
        await manager.ensure_can_edit_plan(admin, plan_b)
        await manager.ensure_owns_student(admin, world["student_b"])


# ------------------------------------------------------------- injection ----
@pytest.mark.parametrize(
    "malicious",
    ["'; DROP TABLE users; --", "%", "_", "' OR 1=1 --", "\\", "علی%'"],
)
async def test_student_search_is_injection_safe(sessionmaker, world, malicious):
    async with sessionmaker() as s:
        users = UserRepository(s)
        results = await users.students_of(world["advisor_a"], query=malicious)
        # query is parameterised: at worst it matches nothing, never leaks B
        assert all(u.id == world["student_a"] for u in results)
        assert await users.count_students_of(world["advisor_a"], malicious) <= 1
        assert await users.by_id(world["student_b"]) is not None  # table intact


async def test_malicious_text_is_stored_and_rendered_verbatim(sessionmaker, world, queue):
    """Injection-ish payloads must never break rendering or escape the cell."""
    async with sessionmaker() as s:
        manager = PlanManager(s)
        a = await manager.users.by_id(world["advisor_a"])
        payload = "<script>alert(1)</script> & 'x' | ../../etc/passwd"
        await manager.set_slot(a, world["plan_a"], "sunday", 0,
                               Activity.from_quick_entry(0, payload))
        await s.commit()
        domain = PlanManager.to_domain(await manager.plans.get(world["plan_a"]))
    result = await queue.generate(domain, force=True)
    assert result.png_path.exists() and result.pdf_path.exists()


# ------------------------------------------------------- files & secrets ----
async def test_generated_filenames_are_ascii_and_inside_storage(sessionmaker, world, queue, tmp_path):
    async with sessionmaker() as s:
        manager = PlanManager(s)
        a = await manager.users.by_id(world["advisor_a"])
        student = await manager.users.by_id(world["student_a"])
        student.full_name = "علی ../../etc/passwd \x00 <script>"
        await s.commit()
        domain = PlanManager.to_domain(await manager.plans.get(world["plan_a"]))

    result = await queue.generate(domain, force=True)
    assert result.png_path.name.isascii() and " " not in result.png_path.name
    assert ".." not in str(result.png_path)
    storage_root = Path(queue.service.storage_root).resolve()
    assert result.png_path.resolve().is_relative_to(storage_root)
    assert result.pdf_path.resolve().is_relative_to(storage_root)


async def test_path_traversal_in_stored_path_is_refused(sessionmaker, world, tmp_path, monkeypatch):
    from app.bot import delivery

    monkeypatch.setattr(delivery.settings, "storage_root", tmp_path / "generated")
    (tmp_path / "generated").mkdir(parents=True, exist_ok=True)
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"top secret")

    async with sessionmaker() as s:
        plan = await PlanManager(s).plans.get(world["plan_a"])
        plan.image_path = str(secret)  # simulate a poisoned row
        assert delivery.local_path(plan, "png") is None
        assert delivery.input_for(plan, "png") is None


def test_token_and_dsn_are_redacted_in_logs(caplog):
    token = "1234567890:" + "AAHfake" + "_x" * 20  # synthetic, never a real token
    dsn = "postgresql+asyncpg://user:supersecret@db.internal/rotbeland"
    assert token not in redact(f"connecting with {token}")
    assert "supersecret" not in redact(dsn)
    assert "<TOKEN>" in redact(f"https://api.telegram.org/bot{token}/getMe")

    logger = logging.getLogger("secret-test")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO):
        logger.info("token=%s dsn=%s", token, dsn)
    assert token not in caplog.text and "supersecret" not in caplog.text


def test_settings_never_expose_secrets_and_validate_config(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1234567890:" + "AAHfake" + "_x" * 20)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/db")
    monkeypatch.setenv("ENVIRONMENT", "production")
    s = Settings()
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert s.validate_for_runtime() == []
    summary = str(s.safe_summary())
    assert "AAHfake" not in summary and ":p@" not in summary  # masked
    assert mask_token(s.bot_token).endswith(":***")

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./x.db")
    assert any("SQLite" in p for p in Settings().validate_for_runtime())
    monkeypatch.delenv("BOT_TOKEN")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert any("BOT_TOKEN" in p for p in Settings().validate_for_runtime())


def test_database_url_normalisation():
    assert normalize_database_url("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert normalize_database_url("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert "sslmode" not in normalize_database_url("postgres://u:p@h/db?sslmode=require")
    assert normalize_database_url("postgresql+asyncpg://u:p@h/db").count("asyncpg") == 1


def test_no_secrets_committed_in_repository():
    """Static sweep of the tracked source tree for real-looking credentials."""
    import re

    token_re = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{35,}\b")
    skip_dirs = {".git", "generated", "out", "__pycache__", ".pytest_cache", "node_modules"}
    offenders: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".md", ".yml", ".yaml",
                                                     ".toml", ".ini", ".json", ".example",
                                                     ".sh", ".j2"}:
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if token_re.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"possible secrets in: {offenders}"
    assert not (ROOT / ".env").exists(), ".env must never be committed"
