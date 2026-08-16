"""Admin authority.

ADMIN_IDS (a Railway/env variable) is the single source of truth for admin
power. It outranks the database: if the row says "advisor" but the Telegram id
is listed, the person still gets the admin panel — and no user-facing flow may
ever downgrade them.
"""
from __future__ import annotations

from .config import settings
from .db.models import Role, User


def is_admin_env(telegram_id: int | None) -> bool:
    """True when the Telegram id is configured in ADMIN_IDS."""
    return telegram_id is not None and telegram_id in settings.admin_ids


def is_admin(user: User | None, telegram_id: int | None = None) -> bool:
    """Admin power = listed in ADMIN_IDS, or stored role is ADMIN."""
    if is_admin_env(telegram_id):
        return True
    if user is None:
        return False
    if is_admin_env(user.telegram_id):
        return True
    return user.role is Role.ADMIN


def is_advisor(user: User | None) -> bool:
    return user is not None and user.role in (Role.ADVISOR, Role.ADMIN)


def is_active(user: User | None) -> bool:
    """Suspended accounts keep their data but lose write access."""
    return user is not None and user.is_active
