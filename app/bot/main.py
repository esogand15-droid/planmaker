"""Bot bootstrap: wiring, startup checks, health endpoint, graceful shutdown."""
from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage

from ..config import settings
from ..db.session import dispose_engine, get_sessionmaker, init_engine, wait_for_database
from ..logging_config import setup_logging
from ..rendering.factory import get_renderer
from ..services.plan_service import WeeklyPlanService
from ..services.render_queue import RenderQueue
from .handlers import advisor, common, student
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
        observer.middleware(ThrottleMiddleware())
        observer.middleware(DatabaseMiddleware(sessionmaker))
        observer.middleware(UserMiddleware(tuple(admin_ids)))
        observer.middleware(ErrorMiddleware())

    dp.include_router(common.router)
    dp.include_router(student.router)
    dp.include_router(advisor.router)
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

    queue = RenderQueue(service, max_concurrent=settings.render_concurrency)
    storage = build_storage()
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(queue, get_sessionmaker(), settings.admin_ids, storage)

    health = HealthServer(settings.health_port)
    await health.start()

    me = await bot.get_me()
    log.info("authorized as @%s (id=%s)", me.username, me.id)
    # a single polling instance must own the update stream
    await bot.delete_webhook(drop_pending_updates=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    polling = asyncio.create_task(
        dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(), handle_signals=False)
    )
    health.mark_ready()

    await asyncio.wait({polling, asyncio.create_task(stop.wait())},
                       return_when=asyncio.FIRST_COMPLETED)

    log.info("shutdown requested — draining")
    health.mark_unready()
    await dp.stop_polling()
    with suppress(asyncio.CancelledError):
        await polling
    await queue.drain(timeout=30)
    await dp.storage.close()
    await bot.session.close()
    await dispose_engine()
    await health.stop()
    log.info("shutdown complete")


def main() -> None:  # pragma: no cover
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":  # pragma: no cover
    main()
