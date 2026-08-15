"""Deployment-readiness tests: fallbacks, health, shutdown, DB retry, assets."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.bot.health import HealthServer  # noqa: E402
from app.rendering.factory import get_renderer  # noqa: E402
from app.rendering.html_renderer import HtmlRenderer  # noqa: E402
from app.rendering.layout import TemplateLayout  # noqa: E402
from app.rendering.pillow_renderer import PillowRenderer  # noqa: E402
from app.services.plan_service import WeeklyPlanService  # noqa: E402
from app.services.render_queue import RenderQueue  # noqa: E402
from tools.demo_plan import sparse_plan  # noqa: E402


# ------------------------------------------------------------- renderer -----
def test_auto_backend_falls_back_when_chromium_is_missing(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/nonexistent-browsers")
    HtmlRenderer.available.cache_clear()
    try:
        assert HtmlRenderer.available() is False
        assert get_renderer("auto").name == "pillow"
        assert get_renderer("html").name == "pillow"  # explicit request degrades safely
    finally:
        HtmlRenderer.available.cache_clear()


def test_service_recovers_when_the_browser_backend_crashes(tmp_path, monkeypatch):
    """Even a mid-render Chromium failure must still deliver a correct plan."""
    layout = TemplateLayout.load()
    broken = HtmlRenderer(layout)

    def explode(*_args, **_kwargs):
        raise RuntimeError("chromium crashed")

    monkeypatch.setattr(broken, "render_png", explode)
    monkeypatch.setattr(broken, "render_pdf_vector", explode)

    service = WeeklyPlanService(broken, storage_root=tmp_path)
    result = service.generate(sparse_plan(), force=True)
    assert result.png_path.exists() and result.png_path.stat().st_size > 10_000
    assert result.pdf_path.read_bytes()[:5] == b"%PDF-"


def test_assets_are_vendored_in_the_repository():
    """Production must never download fonts or templates at runtime."""
    layout = TemplateLayout.load()
    assert layout.template_path.exists() and layout.template_path.stat().st_size > 100_000
    for weight in ("regular", "medium", "bold"):
        font = layout.font_path(weight)
        assert font.exists() and font.suffix == ".ttf"
    assert (ROOT / "assets" / "fonts" / "Vazirmatn-OFL.txt").exists()  # license shipped


def test_pillow_has_harfbuzz_shaping():
    from PIL import features

    assert features.check("raqm"), "libraqm missing — Persian shaping would break"


# ---------------------------------------------------------------- queue -----
async def test_queue_drains_before_shutdown(tmp_path):
    service = WeeklyPlanService(PillowRenderer(TemplateLayout.load()), storage_root=tmp_path)
    queue = RenderQueue(service, max_concurrent=2)
    task = asyncio.create_task(queue.generate(sparse_plan(), force=True))
    await asyncio.sleep(0.05)
    assert await queue.drain(timeout=60) is True
    assert queue.inflight == 0
    result = await task
    assert result.png_path.exists()


# --------------------------------------------------------------- health -----
async def test_health_endpoint_reports_readiness():
    import aiohttp

    server = HealthServer(8099)
    await server.start()
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get("http://127.0.0.1:8099/health") as r:
                assert r.status == 503  # not ready yet
            server.mark_ready()
            async with http.get("http://127.0.0.1:8099/health") as r:
                assert r.status == 200
                assert (await r.json())["status"] == "ok"
    finally:
        await server.stop()


async def test_health_server_is_disabled_without_port():
    server = HealthServer(None)
    await server.start()
    await server.stop()  # must be a no-op, never raise


# ------------------------------------------------------------- database -----
class _FakeConn:
    async def execute(self, *_a, **_kw):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _FakeEngine:
    """Engine stub whose connect() fails `fail_times` times, then succeeds."""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.attempts = 0

    def connect(self):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise OSError("connection refused")
        return _FakeConn()


async def test_wait_for_database_retries_then_succeeds(monkeypatch):
    from app.db import session as session_mod

    engine = _FakeEngine(fail_times=2)
    monkeypatch.setattr(session_mod, "get_engine", lambda: engine)
    await session_mod.wait_for_database(retries=5, base_delay=0.01, max_delay=0.02)
    assert engine.attempts == 3  # two failures, third succeeds


async def test_wait_for_database_gives_up_with_clear_error(monkeypatch):
    from app.db import session as session_mod

    engine = _FakeEngine(fail_times=99)
    monkeypatch.setattr(session_mod, "get_engine", lambda: engine)
    with pytest.raises(OSError):
        await session_mod.wait_for_database(retries=3, base_delay=0.01, max_delay=0.01)
    assert engine.attempts == 3  # bounded retries, then a clear failure


async def test_engine_pool_settings_for_postgres(monkeypatch):
    from app.db import session as session_mod

    engine = session_mod.init_engine("postgresql+asyncpg://u:p@localhost/nonexistent")
    try:
        assert engine.pool.size() == 5  # DB_POOL_SIZE default
        assert engine.dialect.name == "postgresql"
    finally:
        await session_mod.dispose_engine()


# --------------------------------------------------------------- config -----
def test_dispatcher_builds_with_memory_storage_when_redis_absent(monkeypatch):
    from aiogram.fsm.storage.memory import MemoryStorage

    from app.bot.main import build_storage
    from app.config import settings

    monkeypatch.setattr(settings, "redis_url", None)
    assert isinstance(build_storage(), MemoryStorage)


def test_deployment_files_exist():
    for name in ("Dockerfile", "railway.toml", "docker-compose.yml", "docker-entrypoint.sh",
                 ".env.example", ".gitignore", ".dockerignore", "alembic.ini",
                 "requirements.txt", "README.md", "DEPLOY.md", "Procfile",
                 ".github/workflows/ci.yml"):
        assert (ROOT / name).exists(), f"missing {name}"
    assert (ROOT / "migrations" / "versions").is_dir()
    assert list((ROOT / "migrations" / "versions").glob("*.py")), "no alembic revision"


def test_gitignore_covers_runtime_artifacts():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".env", "__pycache__/", ".pytest_cache/", "out/", "generated/", "*.log"):
        assert pattern in ignored, f"{pattern} not ignored"
