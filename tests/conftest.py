"""Shared test setup.

aiogram routers are module-level singletons and may only be attached to one
Dispatcher. Production builds exactly one; the suite builds a fresh dispatcher
per test, so the routers are detached automatically before each test.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _detach_routers():
    from app.bot.handlers import admin, advisor, common, fallback, student

    for module in (common, admin, student, advisor, fallback):
        module.router._parent_router = None
    yield
