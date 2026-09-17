"""Bot bootstrap: wiring, startup checks, health endpoint, graceful shutdown."""
from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage

from ..config import settings
from ..db.session import dispose_engine, get_sessionmaker, init_engine, wait_for_database
from ..logging_config import setup_logging
from ..rendering.factory import get_renderer
from ..services.backup import BackupScheduler
from ..services.plan_service import WeeklyPlanService
from ..services.render_queue import RenderQueue
from .handlers import admin, advisor, common, fallback, student
from .health import HealthServer
from .middlewares import (
    DatabaseMiddleware,
    ErrorMiddleware,
    ThrottleMiddleware,
    UserMiddleware,
)

log = logging.getLogger(__name__)


def build_storage() -> BaseStorage:
    """Redis is optional: all plan data lives in PostgreSQL, only the wizard
    position is kept in FSM storage. Redis just makes it survive a restart."""
    if not settings.redis_url:
        return MemoryStorage()
    try:
        from aiogram.fsm.storage.redis import RedisStorage

        storage = RedisStorage.from_url(settings.redis_url)
        log.info("FSM storage: redis")
        return storage
    except Exception as exc:  # pragma: no cover - needs a broken Redis URL
        log.warning("Redis unavailable (%s) — falling back to in-memory FSM storage", exc)
        return MemoryStorage()


def build_dispatcher(queue: RenderQueue, sessionmaker, admin_ids=(), storage=None) -> Dispatcher:
    dp = Dispatcher(storage=storage or MemoryStorage())
    dp["queue"] = queue

    for observer in (dp.message, dp.callback_query):
        # outermost first: the error shield must also cover session/user setup
        observer.middleware(ErrorMiddleware())
        observer.middleware(ThrottleMiddleware())
        observer.middleware(DatabaseMiddleware(sessionmaker))
        observer.middleware(UserMiddleware(tuple(admin_ids)))

    dp.include_router(common.router)
    dp.include_router(admin.router)
    dp.include_router(student.router)
    dp.include_router(advisor.router)
    dp.include_router(fallback.router)  # must stay last: catches orphan callbacks
    return dp


def preflight() -> WeeklyPlanService:
    """Fail fast and loudly on misconfiguration, before touching Telegram."""
    problems = settings.validate_for_runtime()
    if problems:
        for p in problems:
            log.error("config error: %s", p)
        raise SystemExit(1)

    service = WeeklyPlanService(
        get_renderer(settings.render_backend, settings.template),
        storage_root=settings.storage_root,
        print_scale=settings.print_scale,
        pdf_dpi=settings.pdf_dpi,
    )
    layout = service.renderer.layout
    if not layout.template_path.exists():
        raise SystemExit(f"template asset missing: {layout.template_path}")
    for weight in ("regular", "medium", "bold"):
        if not layout.font_path(weight).exists():
            raise SystemExit(f"font asset missing: {layout.font_path(weight)}")

    settings.storage_root.mkdir(parents=True, exist_ok=True)
    from PIL import features

    log.info(
        "renderer=%s template=%s raqm=%s",
        service.renderer.signature, layout.version, features.check("raqm"),
    )
    return service


async def run() -> None:  # pragma: no cover - runtime entry point
    setup_logging()
    log.info("starting Rotbe Land weekly planner · %s", settings.safe_summary())

    service = preflight()
    init_engine()
    await wait_for_database()
    # Never accept an update before the schema exists: this is what turned a
    # missing `alembic upgrade head` into «relation "users" does not exist» for
    # every single user. It migrates, verifies and self-heals, then reports.
    await bootstrap_schema()

    queue = RenderQueue(service, max_concurrent=settings.render_concurrency)
    storage = build_storage()
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(queue, get_sessionmaker(), settings.admin_ids, storage)

    health = HealthServer(settings.health_port)
    await health.start()

    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError as exc:
        await bot.session.close()
        log.critical(
            "BOT_TOKEN was rejected by Telegram (%s) — check the variable; the bot "
            "cannot receive a single update in this state", exc.message,
        )
        raise SystemExit(3) from exc
    except TelegramAPIError as exc:
        await bot.session.close()
        log.critical("cannot reach the Telegram API (%s) — check egress/DNS", exc)
        raise SystemExit(4) from exc
    log.info("authorized as @%s (id=%s)", me.username, me.id)
    # a single polling instance must own the update stream
    await bot.delete_webhook(drop_pending_updates=True)

    # automatic backups: the schedule itself lives in the database and is
    # edited from the admin panel (🛠 پنل مدیریت → 🧰 بکاپ‌گیری)
    backups = BackupScheduler(bot, get_sessionmaker())
    await backups.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    polling = asyncio.create_task(
        dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(), handle_signals=False)
    )
    health.mark_ready()

    # keep a reference: an unreferenced task can be garbage-collected mid-flight
    waiter = asyncio.create_task(stop.wait(), name="shutdown-waiter")
    try:
        await asyncio.wait({polling, waiter}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        waiter.cancel()
        with suppress(asyncio.CancelledError):
            await waiter

    log.info("shutdown requested — draining")
    health.mark_unready()
    await backups.stop()
    await dp.stop_polling()
    with suppress(asyncio.CancelledError):
        await polling
    await queue.drain(timeout=30)
    await dp.storage.close()
    await bot.session.close()
    await dispose_engine()
    await health.stop()
    log.info("shutdown complete")


async def bootstrap_schema() -> None:
    """Migrate + verify the schema; refuse to serve traffic if it stays broken."""
    from ..db.bootstrap import SchemaError, ensure_schema, migrations_available

    if not migrations_available():
        log.error(
            "no alembic revisions found under migrations/versions — the image was "
            "built without them; falling back to ORM table creation"
        )
    try:
        report = await ensure_schema()
    except SchemaError as exc:
        log.critical(
            "database schema is unusable: %s — fix DATABASE_URL or run "
            "`alembic upgrade head` manually; the bot will not accept updates "
            "in this state", exc,
        )
        raise SystemExit(2) from exc
    log.info(
        "schema ok · revision=%s head=%s migrated=%s created_from_orm=%s",
        report.get("revision"), report.get("head"),
        report.get("migrated"), report.get("created_from_orm") or "—",
    )


def main() -> None:  # pragma: no cover
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except SystemExit as exc:
        # 2 = schema unusable, 3 = BOT_TOKEN rejected, 4 = Telegram unreachable.
        # A container must die with that code so the platform restarts/alerts
        # instead of reporting a clean exit.
        raise SystemExit(exc.code if exc.code is not None else 0)


if __name__ == "__main__":  # pragma: no cover
    main()
