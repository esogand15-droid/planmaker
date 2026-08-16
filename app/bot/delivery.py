"""File delivery helpers.

Railway's filesystem is ephemeral unless a volume is attached, so we cache the
Telegram `file_id` of every artefact after the first upload. Re-sends then cost
no bandwidth and keep working even if the local PNG/PDF disappeared. If both the
file_id and the local file are gone, the plan is re-rendered from the database —
the source of truth is always the plan data, never the file.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from aiogram.types import FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models import WeeklyPlanDB
from ..services.plan_manager import PlanManager
from ..services.render_queue import RenderQueue

log = logging.getLogger(__name__)

Kind = Literal["png", "pdf"]


def cached_file_id(plan: WeeklyPlanDB, kind: Kind) -> str | None:
    return plan.image_file_id if kind == "png" else plan.pdf_file_id


def local_path(
    plan: WeeklyPlanDB, kind: Kind, storage_root: Path | str | None = None
) -> Path | None:
    """Resolve a stored artefact path, refusing anything outside the storage root."""
    raw = plan.image_path if kind == "png" else plan.pdf_path
    if not raw:
        return None
    try:
        path = Path(raw).resolve()
        root = Path(storage_root or settings.storage_root).resolve()
    except OSError:  # pragma: no cover - unreadable path
        return None
    if not path.is_relative_to(root):
        log.error("refusing to serve %s: outside STORAGE_ROOT", path)
        return None
    return path if path.exists() else None


def remember_file_id(plan: WeeklyPlanDB, kind: Kind, message: Message) -> None:
    """Store the Telegram file_id returned by the API for later re-sends."""
    file_id: str | None = None
    if kind == "png":
        if message.photo:
            file_id = message.photo[-1].file_id
        elif message.document:
            file_id = message.document.file_id
        if file_id:
            plan.image_file_id = file_id
    else:
        if message.document:
            file_id = message.document.file_id
            plan.pdf_file_id = file_id


async def ensure_artifacts(
    session: AsyncSession, plan: WeeklyPlanDB, queue: RenderQueue
) -> bool:
    """Guarantee that both artefacts are sendable; re-render if they vanished."""
    root = queue.service.storage_root
    have_png = bool(cached_file_id(plan, "png")) or local_path(plan, "png", root) is not None
    have_pdf = bool(cached_file_id(plan, "pdf")) or local_path(plan, "pdf", root) is not None
    if have_png and have_pdf:
        return True

    log.warning("artefacts missing for plan %s — re-rendering from the database", plan.id)
    domain = PlanManager.to_domain(plan)
    result = await queue.generate(domain, force=True)
    await PlanRepositoryUpdate(session).apply(plan, result)
    return True


class PlanRepositoryUpdate:
    """Tiny adapter so delivery does not import the repository layer directly."""

    def __init__(self, session: AsyncSession):
        from ..repositories.repositories import PlanRepository

        self.repo = PlanRepository(session)

    async def apply(self, plan: WeeklyPlanDB, result) -> None:
        await self.repo.mark_generated(
            plan,
            image_path=str(result.png_path),
            pdf_path=str(result.pdf_path),
            plan_hash=result.plan_hash,
            template_version=result.template_version,
            renderer_version=result.renderer,
            duration_ms=result.duration_ms,
        )


def input_for(
    plan: WeeklyPlanDB, kind: Kind, storage_root: Path | str | None = None
) -> str | FSInputFile | None:
    """Prefer the cached file_id; fall back to the file on disk."""
    file_id = cached_file_id(plan, kind)
    if file_id:
        return file_id
    path = local_path(plan, kind, storage_root)
    return FSInputFile(path) if path else None


def is_inside_storage(path: Path | str, storage_root: Path | str) -> bool:
    """Guard for any file we are about to serve from disk."""
    try:
        return Path(path).resolve().is_relative_to(Path(storage_root).resolve())
    except OSError:  # pragma: no cover - unreadable path
        return False
