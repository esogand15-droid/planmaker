"""Tiny HTTP health endpoint.

A polling bot has no inbound port, but Railway (and any uptime monitor) is much
happier with a health check. Enabled only when PORT is set.
"""
from __future__ import annotations

import logging

from aiohttp import web

log = logging.getLogger(__name__)


class HealthServer:
    def __init__(self, port: int | None):
        self.port = port
        self._runner: web.AppRunner | None = None
        self._ready = False

    def mark_ready(self) -> None:
        self._ready = True

    def mark_unready(self) -> None:
        self._ready = False

    async def _health(self, _request: web.Request) -> web.Response:
        status = 200 if self._ready else 503
        return web.json_response(
            {"status": "ok" if self._ready else "starting", "service": "rotbeland-bot"},
            status=status,
        )

    async def start(self) -> None:
        if not self.port:
            return
        app = web.Application()
        app.router.add_get("/", self._health)
        app.router.add_get("/health", self._health)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()
        log.info("health endpoint listening on 0.0.0.0:%s/health", self.port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
