"""Generation queue: keeps CPU-heavy rendering off the event loop.

Renders run in a worker thread with bounded concurrency, so several advisors can
generate at the same time without blocking the bot or exhausting the machine.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from ..domain.models import WeeklyPlan
from ..services.plan_service import GeneratedPlan, WeeklyPlanService

log = logging.getLogger(__name__)

MAX_CONCURRENT = int(os.getenv("RENDER_CONCURRENCY", "2"))


class RenderQueue:
    def __init__(self, service: WeeklyPlanService, max_concurrent: int = MAX_CONCURRENT):
        self.service = service
        self._sem = asyncio.Semaphore(max_concurrent)
        self._inflight = 0

    @property
    def inflight(self) -> int:
        return self._inflight

    async def generate(self, plan: WeeklyPlan, *, force: bool = False) -> GeneratedPlan:
        async with self._sem:
            self._inflight += 1
            try:
                return await asyncio.to_thread(self.service.generate, plan, force=force)
            finally:
                self._inflight -= 1

    async def preview(self, plan: WeeklyPlan) -> bytes:
        async with self._sem:
            self._inflight += 1
            try:
                return await asyncio.to_thread(self.service.preview, plan)
            finally:
                self._inflight -= 1

    async def drain(self, timeout: float = 30.0) -> bool:
        """Wait for in-flight renders so a deploy never truncates a file."""
        async def _wait() -> None:
            while self._inflight:
                await asyncio.sleep(0.1)

        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(_wait(), timeout=timeout)
            return True
        log.warning("drain timed out with %s render(s) still running", self._inflight)
        return False

    async def validate(self, plan: WeeklyPlan):
        return await asyncio.to_thread(self.service.validate, plan)
