"""Admin panel data — every number comes from a real query or a live probe.

Nothing here is hard-coded or faked: counts come from the database, storage
figures from the filesystem, and service health from actual checks.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models import (
    ActivityDB,
    AdvisorStudent,
    AuditLog,
    PlanFile,
    PlanStatusDB,
    Role,
    User,
    WeeklyPlanDB,
)
from ..domain.persian import today_local

log = logging.getLogger(__name__)

STARTED_AT = time.time()


def uptime() -> str:
    seconds = int(time.time() - STARTED_AT)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


@dataclass
class StorageReport:
    path: Path
    png: int = 0
    pdf: int = 0
    total_bytes: int = 0
    orphans: list[Path] = field(default_factory=list)
    mounted: bool = False

    @property
    def total(self) -> int:
        return self.png + self.pdf

    @property
    def human_size(self) -> str:
        size = float(self.total_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"


class AdminService:
    """Read-mostly queries for the admin panel."""

    def __init__(self, session: AsyncSession):
        self.s = session

    # ───────────────────────────── advisors ─────────────────────────────
    async def advisors(self, limit: int, offset: int) -> list[tuple[User, int, int]]:
        stmt = (
            select(User)
            .where(User.role.in_([Role.ADVISOR, Role.ADMIN]))
            .order_by(User.full_name)
            .limit(limit)
            .offset(offset)
        )
        rows = list((await self.s.execute(stmt)).scalars())
        if not rows:
            return []
        ids = [a.id for a in rows]
        # two grouped queries instead of two per advisor (no N+1)
        student_counts = dict(
            (
                await self.s.execute(
                    select(AdvisorStudent.advisor_id, func.count())
                    .where(AdvisorStudent.advisor_id.in_(ids))
                    .group_by(AdvisorStudent.advisor_id)
                )
            ).all()
        )
        plan_counts = dict(
            (
                await self.s.execute(
                    select(WeeklyPlanDB.advisor_id, func.count())
                    .where(WeeklyPlanDB.advisor_id.in_(ids))
                    .group_by(WeeklyPlanDB.advisor_id)
                )
            ).all()
        )
        return [
            (a, int(student_counts.get(a.id, 0)), int(plan_counts.get(a.id, 0)))
            for a in rows
        ]

    async def count_advisors(self) -> int:
        stmt = (
            select(func.count())
            .select_from(User)
            .where(User.role.in_([Role.ADVISOR, Role.ADMIN]))
        )
        return int((await self.s.execute(stmt)).scalar_one())

    async def advisor_detail(self, advisor_id: int) -> dict:
        advisor = await self.s.get(User, advisor_id)
        if advisor is None:
            return {}
        week_start = today_local() - timedelta(days=today_local().weekday())
        students = int(
            (
                await self.s.execute(
                    select(func.count())
                    .select_from(AdvisorStudent)
                    .where(AdvisorStudent.advisor_id == advisor_id)
                )
            ).scalar_one()
        )
        plans = int(
            (
                await self.s.execute(
                    select(func.count())
                    .select_from(WeeklyPlanDB)
                    .where(WeeklyPlanDB.advisor_id == advisor_id)
                )
            ).scalar_one()
        )
        this_week = int(
            (
                await self.s.execute(
                    select(func.count())
                    .select_from(WeeklyPlanDB)
                    .where(
                        WeeklyPlanDB.advisor_id == advisor_id,
                        WeeklyPlanDB.week_start >= week_start,
                    )
                )
            ).scalar_one()
        )
        last = (
            await self.s.execute(
                select(AuditLog.at)
                .where(AuditLog.actor_id == advisor_id)
                .order_by(AuditLog.at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        drafts = int(
            (
                await self.s.execute(
                    select(func.count())
                    .select_from(WeeklyPlanDB)
                    .where(
                        WeeklyPlanDB.advisor_id == advisor_id,
                        WeeklyPlanDB.status == PlanStatusDB.DRAFT,
                    )
                )
            ).scalar_one()
        )
        sent = int(
            (
                await self.s.execute(
                    select(func.count())
                    .select_from(WeeklyPlanDB)
                    .where(
                        WeeklyPlanDB.advisor_id == advisor_id,
                        WeeklyPlanDB.status == PlanStatusDB.SENT,
                    )
                )
            ).scalar_one()
        )
        return {
            "advisor": advisor,
            "students": students,
            "plans": plans,
            "drafts": drafts,
            "sent": sent,
            "this_week": this_week,
            "last_seen": last,
        }

    async def search_advisors(self, query: str, limit: int) -> list[tuple[User, int, int]]:
        """Search by name or Telegram id."""
        pattern = f"%{query.strip()}%"
        clauses = [User.full_name.ilike(pattern)]
        if query.strip().isdigit():
            clauses.append(User.telegram_id == int(query.strip()))
        stmt = (
            select(User)
            .where(User.role.in_([Role.ADVISOR, Role.ADMIN]), or_(*clauses))
            .order_by(User.full_name)
            .limit(limit)
        )
        found = list((await self.s.execute(stmt)).scalars())
        out = []
        for advisor in found:
            detail = await self.advisor_detail(advisor.id)
            out.append((advisor, detail["students"], detail["plans"]))
        return out

    async def search_students(self, query: str, limit: int) -> list[User]:
        pattern = f"%{query.strip()}%"
        clauses = [User.full_name.ilike(pattern), User.grade.ilike(pattern)]
        if query.strip().isdigit():
            clauses.append(User.telegram_id == int(query.strip()))
        stmt = (
            select(User)
            .where(User.role == Role.STUDENT, or_(*clauses))
            .order_by(User.full_name)
            .limit(limit)
        )
        return list((await self.s.execute(stmt)).scalars())

    async def advisor_candidates(self, exclude: int = 0) -> list[User]:
        """Advisors available as a transfer target."""
        stmt = (
            select(User)
            .where(
                User.role.in_([Role.ADVISOR, Role.ADMIN]),
                User.is_active.is_(True),
                User.id != exclude,
            )
            .order_by(User.full_name)
            .limit(10)
        )
        return list((await self.s.execute(stmt)).scalars())

    async def students_of_advisor(
        self, advisor_id: int, limit: int, offset: int
    ) -> tuple[list[User], int]:
        base = (
            select(User)
            .join(AdvisorStudent, AdvisorStudent.student_id == User.id)
            .where(AdvisorStudent.advisor_id == advisor_id)
        )
        rows = list(
            (await self.s.execute(base.order_by(User.full_name).limit(limit).offset(offset)))
            .scalars()
        )
        total = int(
            (
                await self.s.execute(
                    select(func.count())
                    .select_from(AdvisorStudent)
                    .where(AdvisorStudent.advisor_id == advisor_id)
                )
            ).scalar_one()
        )
        return rows, total

    # ───────────────────────────── students ─────────────────────────────
    async def students(self, limit: int, offset: int) -> list[User]:
        stmt = (
            select(User)
            .where(User.role == Role.STUDENT)
            .order_by(User.full_name)
            .limit(limit)
            .offset(offset)
        )
        return list((await self.s.execute(stmt)).scalars())

    async def count_students(self) -> int:
        stmt = select(func.count()).select_from(User).where(User.role == Role.STUDENT)
        return int((await self.s.execute(stmt)).scalar_one())

    async def student_detail(self, student_id: int) -> dict:
        student = await self.s.get(User, student_id)
        if student is None:
            return {}
        advisors = list(
            (
                await self.s.execute(
                    select(User)
                    .join(AdvisorStudent, AdvisorStudent.advisor_id == User.id)
                    .where(AdvisorStudent.student_id == student_id)
                )
            ).scalars()
        )
        plans = int(
            (
                await self.s.execute(
                    select(func.count())
                    .select_from(WeeklyPlanDB)
                    .where(WeeklyPlanDB.student_id == student_id)
                )
            ).scalar_one()
        )
        drafts = int(
            (
                await self.s.execute(
                    select(func.count())
                    .select_from(WeeklyPlanDB)
                    .where(
                        WeeklyPlanDB.student_id == student_id,
                        WeeklyPlanDB.status == PlanStatusDB.DRAFT,
                    )
                )
            ).scalar_one()
        )
        last = (
            await self.s.execute(
                select(AuditLog.at)
                .where(AuditLog.student_id == student_id)
                .order_by(AuditLog.at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return {
            "student": student,
            "advisors": advisors,
            "plans": plans,
            "drafts": drafts,
            "last_seen": last,
        }

    # ───────────────────────────── database ─────────────────────────────
    async def db_stats(self) -> dict:
        started = time.perf_counter()
        await self.s.execute(text("SELECT 1"))
        latency_ms = (time.perf_counter() - started) * 1000

        async def count(model, *where) -> int:
            stmt = select(func.count()).select_from(model)
            for clause in where:
                stmt = stmt.where(clause)
            return int((await self.s.execute(stmt)).scalar_one())

        return {
            "users": await count(User),
            "advisors": await count(User, User.role.in_([Role.ADVISOR, Role.ADMIN])),
            "students": await count(User, User.role == Role.STUDENT),
            "plans": await count(WeeklyPlanDB),
            "drafts": await count(WeeklyPlanDB, WeeklyPlanDB.status == PlanStatusDB.DRAFT),
            "files": await count(PlanFile),
            "activities": await count(ActivityDB),
            "latency_ms": latency_ms,
        }

    # ────────────────────────────── statistics ──────────────────────────
    async def statistics(self) -> dict:
        today = today_local()
        week_start = today - timedelta(days=(today.weekday() - 5) % 7)
        month_start = today.replace(day=1)

        async def plans_since(since: date | None) -> int:
            stmt = select(func.count()).select_from(WeeklyPlanDB)
            if since is not None:
                stmt = stmt.where(WeeklyPlanDB.week_start >= since)
            return int((await self.s.execute(stmt)).scalar_one())

        async def count_status(status: PlanStatusDB) -> int:
            stmt = (
                select(func.count())
                .select_from(WeeklyPlanDB)
                .where(WeeklyPlanDB.status == status)
            )
            return int((await self.s.execute(stmt)).scalar_one())

        async def audit_count(action: str) -> int:
            stmt = select(func.count()).select_from(AuditLog).where(AuditLog.action == action)
            return int((await self.s.execute(stmt)).scalar_one())

        generated = int(
            (
                await self.s.execute(
                    select(func.count())
                    .select_from(WeeklyPlanDB)
                    .where(WeeklyPlanDB.generated_at.is_not(None))
                )
            ).scalar_one()
        )
        return {
            "advisors": await self.count_advisors(),
            "students": await self.count_students(),
            "plans": await plans_since(None),
            "drafts": await count_status(PlanStatusDB.DRAFT),
            "sent": await count_status(PlanStatusDB.SENT),
            "generated": generated,
            "today": await plans_since(today),
            "week": await plans_since(week_start),
            "month": await plans_since(month_start),
            "invites_issued": await audit_count("student.invite_issued"),
            "invites_blocked": await audit_count("invite.role_conflict"),
        }

    # ─────────────────────────────── audit ──────────────────────────────
    async def audit_page(self, limit: int, offset: int) -> tuple[list[AuditLog], int]:
        rows = list(
            (
                await self.s.execute(
                    select(AuditLog).order_by(AuditLog.at.desc()).limit(limit).offset(offset)
                )
            ).scalars()
        )
        total = int((await self.s.execute(select(func.count()).select_from(AuditLog))).scalar_one())
        return rows, total

    # ────────────────────────────── storage ─────────────────────────────
    async def storage_report(self, root: Path | str | None = None) -> StorageReport:
        path = Path(root or settings.storage_root)
        report = StorageReport(path=path, mounted=path.exists())
        if not path.exists():
            return report

        known: set[str] = set()
        for column in (WeeklyPlanDB.image_path, WeeklyPlanDB.pdf_path):
            known |= {
                str(Path(p).resolve())
                for p in (await self.s.execute(select(column))).scalars()
                if p
            }
        for column in (PlanFile.image_path, PlanFile.pdf_path):
            known |= {
                str(Path(p).resolve())
                for p in (await self.s.execute(select(column))).scalars()
                if p
            }

        for file in path.rglob("*"):
            if not file.is_file():
                continue
            if file.suffix == ".png":
                report.png += 1
            elif file.suffix == ".pdf":
                report.pdf += 1
            else:
                continue
            report.total_bytes += file.stat().st_size
            if str(file.resolve()) not in known:
                report.orphans.append(file)
        return report

    async def delete_orphans(self, root: Path | str | None = None) -> int:
        """Remove generated files no database row points at (inside the root)."""
        report = await self.storage_report(root)
        removed = 0
        for file in report.orphans:
            try:
                file.unlink()
                removed += 1
            except OSError as exc:  # pragma: no cover - permission problems
                log.warning("could not delete orphan %s: %s", file, exc)
        if removed:
            log.info("storage cleanup removed %s orphan file(s)", removed)
        return removed

    # ─────────────────────────────── health ─────────────────────────────
    async def health(self, queue=None) -> dict:
        from PIL import features

        from ..rendering.html_renderer import HtmlRenderer

        db_ok, latency = True, 0.0
        started = time.perf_counter()
        try:
            await self.s.execute(text("SELECT 1"))
            latency = (time.perf_counter() - started) * 1000
        except Exception as exc:  # pragma: no cover - only on a broken database
            log.error("health: database unreachable: %s", exc)
            db_ok = False

        storage_ok = Path(settings.storage_root).exists()
        renderer = queue.service.renderer.signature if queue else "-"
        return {
            "bot": True,
            "db": db_ok,
            "db_latency_ms": latency,
            "redis": bool(settings.redis_url),
            "renderer": renderer,
            "chromium": HtmlRenderer.available(),
            "raqm": bool(features.check("raqm")),
            "storage": storage_ok,
            "inflight": queue.inflight if queue else 0,
            "uptime": uptime(),
        }
