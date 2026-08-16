"""Middlewares: DB session per update, user resolution/registration, error shield."""
from __future__ import annotations

import logging
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User as TgUser
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..db.models import Role
from ..repositories.repositories import UserRepository
from . import texts as T

log = logging.getLogger(__name__)


class DatabaseMiddleware(BaseMiddleware):
    def __init__(self, sessionmaker: async_sessionmaker):
        self.sessionmaker = sessionmaker

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        async with self.sessionmaker() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise


class UserMiddleware(BaseMiddleware):
    """Resolves the Telegram user behind every update.

    Registration is *invitation based*: random people who find the bot are not
    written to the database. A row is created only for configured admins; every
    other account must be created by an advisor and claimed through an invite
    deep link (handled in handlers/common.py, which receives user=None).
    """

    INVITE_PREFIX = "/start inv_"

    def __init__(self, admin_ids: tuple[int, ...] = ()):
        self.admin_ids = set(admin_ids)

    @staticmethod
    def _is_invite_start(event: TelegramObject) -> bool:
        text = getattr(event, "text", None) or ""
        return text.startswith("/start") and "inv_" in text

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        session = data.get("session")
        if tg_user is None or session is None:
            return await handler(event, data)

        repo = UserRepository(session)
        user = await repo.by_telegram_id(tg_user.id)
        full_name = tg_user.full_name or str(tg_user.id)

        is_admin_id = tg_user.id in self.admin_ids
        if user is None and is_admin_id:
            user = await repo.create(
                full_name=full_name,
                role=Role.ADMIN,
                telegram_id=tg_user.id,
                username=tg_user.username,
            )
            log.info("registered admin from ADMIN_IDS tg=%s", tg_user.id)
        elif user is not None:
            await repo.touch_profile(user, tg_user.username, full_name)
            # ADMIN_IDS may only ever *promote*; nothing here can demote a role
            if is_admin_id and user.role != Role.ADMIN:
                log.info("promoting tg=%s to admin (ADMIN_IDS)", tg_user.id)
                user.role = Role.ADMIN
            if is_admin_id and not user.is_active:
                user.is_active = True  # an admin can never be locked out

        if user is None:
            # unknown account: only an invite deep link may proceed
            if self._is_invite_start(event):
                data["user"] = None
                data["is_admin"] = is_admin_id
                return await handler(event, data)

            # No account is created here — the visit is queued as a request so
            # an admin can grant a role deliberately from the panel.
            from ..repositories.repositories import AccessRequestRepository

            request = await AccessRequestRepository(session).record(
                tg_user.id, full_name, tg_user.username
            )
            log.info(
                "access request from tg=%s (%s) · visits=%s · status=%s",
                tg_user.id, full_name, request.visits, request.status.value,
            )
            await _reply(event, _visitor_message(request))
            return None

        data["user"] = user
        data["is_admin"] = is_admin_id or user.role is Role.ADMIN
        return await handler(event, data)


class ThrottleMiddleware(BaseMiddleware):
    """Cheap per-user rate limit so one advisor cannot flood the render queue."""

    # Tuned for a fast advisor tapping through the wizard: ~4 actions/second
    # sustained with a 20-action burst. The cooldown is **per heavy action**, so
    # pressing «پیش‌نمایش» and then «تولید» right away works; only hammering the
    # *same* render button twice is suppressed.
    def __init__(self, rate: float = 0.25, burst: int = 20, heavy_cooldown: float = 2.0):
        self.rate = rate
        self.burst = burst
        self.heavy_cooldown = heavy_cooldown
        self._tokens: dict[int, tuple[float, float]] = {}
        self._heavy: dict[tuple[int, str], float] = {}

    HEAVY_ACTIONS = ("p:generate", "p:regenerate", "p:preview")

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        import time

        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)
        now = time.monotonic()
        uid = tg_user.id

        payload = getattr(event, "data", None) or ""
        heavy = next((a for a in self.HEAVY_ACTIONS if payload.startswith(a)), None)
        if heavy is not None:
            key = (uid, heavy)
            last = self._heavy.get(key, 0.0)
            if now - last < self.heavy_cooldown:
                log.info("suppressed duplicate %s from tg=%s", heavy, uid)
                if isinstance(event, CallbackQuery):
                    await event.answer("در حال انجام است، چند لحظه صبر کنید…")
                return None
            self._heavy[key] = now

        tokens, last_seen = self._tokens.get(uid, (float(self.burst), now))
        tokens = min(self.burst, tokens + (now - last_seen) / self.rate)
        if tokens < 1:
            self._tokens[uid] = (tokens, now)
            log.warning("throttled user tg=%s", uid)
            if isinstance(event, CallbackQuery):
                await event.answer("کمی آرام‌تر 🙂", show_alert=False)
            return None
        self._tokens[uid] = (tokens - 1, now)
        return await handler(event, data)


class ErrorMiddleware(BaseMiddleware):
    """Backend errors are logged with full context, users see a calm message."""

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        from ..services.plan_manager import AccessDenied
        from ..services.plan_service import PlanGenerationError

        try:
            return await handler(event, data)
        except AccessDenied as exc:
            await _reply(event, str(exc) or T.ACCESS_DENIED)
        except PermissionError as exc:
            log.warning("permission denied: %s", exc)
            await _reply(event, str(exc) or T.ACCESS_DENIED)
        except PlanGenerationError as exc:
            await _reply(event, f"⚠️ {exc}")
        except Exception:
            log.exception("unhandled error in handler; update=%r", event)
            await _reply(event, T.GENERIC_ERROR)
        return None


def _visitor_message(request) -> str:
    """What an unknown visitor sees — honest about where their request stands."""
    from ..db.models import RequestStatus

    if request.status is RequestStatus.REJECTED:
        return T.ACCESS_REJECTED
    if request.visits > 1:
        return T.ACCESS_PENDING_AGAIN
    return T.ACCESS_PENDING


async def _reply(event: TelegramObject, text: str) -> None:
    try:
        if isinstance(event, CallbackQuery):
            await event.answer()
            if event.message:
                await event.message.answer(text)
        elif isinstance(event, Message):
            await event.answer(text)
    except Exception:  # pragma: no cover - never fail inside the error path
        log.exception("failed to deliver error message")
