"""Real, transactional deletion of students, advisors and plans.

Rules enforced here (never in the UI layer):

* authorization is re-checked for every target;
* everything happens in one transaction — a failure rolls the whole thing back
  and the caller must not report success;
* generated files are removed only inside STORAGE_ROOT, and a missing file is
  not an error;
* deleting an advisor never silently destroys their students: the caller must
  choose to transfer or detach them;
* the configured admin can never delete or demote themselves.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db.models import AdvisorStudent, PlanFile, Role, User, WeeklyPlanDB
from ..security import is_admin
from .plan_manager import AccessDenied, PlanManager, StudentError

log = logging.getLogger(__name__)


@dataclass
class DeletionReport:
    """What a delete actually did — used for the confirmation text and audit."""

    name: str = ""
    plans: int = 0
    drafts: int = 0
    versions: int = 0
    files: int = 0
    links: int = 0
    students: int = 0
    transferred: int = 0
    detached: int = 0

    def summary(self) -> str:
        return (
            f"plans={self.plans} drafts={self.drafts} versions={self.versions} "
            f"files={self.files} links={self.links} students={self.students} "
            f"transferred={self.transferred} detached={self.detached}"
        )


class DeletionService:
    """One place for every destructive operation."""

    def __init__(self, session: AsyncSession, storage_root: Path | str | None = None):
        self.s = session
        self.manager = PlanManager(session, storage_root)
        self.storage_root = Path(storage_root or settings.storage_root)

    # ─────────────────────────────── previews ───────────────────────────────
    async def preview_student(self, actor: User, student_id: int) -> DeletionReport:
        """Exact impact of deleting a student — shown before asking to confirm."""
        student = await self._student_in_scope(actor, student_id)
        plans = list(
            (
                await self.s.execute(
                    select(WeeklyPlanDB).where(WeeklyPlanDB.student_id == student.id)
                )
            ).scalars()
        )
        versions = int(
            (
                await self.s.execute(
                    select(func.count())
                    .select_from(PlanFile)
                    .where(PlanFile.plan_id.in_([p.id for p in plans] or [0]))
                )
            ).scalar_one()
        )
        links = int(
            (
                await self.s.execute(
                    select(func.count())
                    .select_from(AdvisorStudent)
                    .where(AdvisorStudent.student_id == student.id)
                )
            ).scalar_one()
        )
        return DeletionReport(
            name=student.full_name,
            plans=len(plans),
            drafts=len([p for p in plans if p.status.value == "draft"]),
            versions=versions,
            files=len(self._files_of_plans(plans)),
            links=links,
        )

    async def preview_advisor(self, actor: User, advisor_id: int) -> DeletionReport:
        advisor = await self._advisor_target(actor, advisor_id)
        students = int(
            (
                await self.s.execute(
                    select(func.count())
                    .select_from(AdvisorStudent)
                    .where(AdvisorStudent.advisor_id == advisor.id)
                )
            ).scalar_one()
        )
        plans = list(
            (
                await self.s.execute(
                    select(WeeklyPlanDB).where(WeeklyPlanDB.advisor_id == advisor.id)
                )
            ).scalars()
        )
        return DeletionReport(
            name=advisor.full_name,
            students=students,
            plans=len(plans),
            drafts=len([p for p in plans if p.status.value == "draft"]),
            files=len(self._files_of_plans(plans)),
        )

    # ─────────────────────────────── students ───────────────────────────────
    async def delete_student(self, actor: User, student_id: int) -> DeletionReport:
        """Remove a student and everything that belongs to them, atomically."""
        student = await self._student_in_scope(actor, student_id)
        if student.id == actor.id:
            raise StudentError("نمی‌توانید حساب خودتان را حذف کنید.")

        report = DeletionReport(name=student.full_name)
        try:
            plans = list(
                (
                    await self.s.execute(
                        select(WeeklyPlanDB).where(WeeklyPlanDB.student_id == student.id)
                    )
                ).scalars()
            )
            report.plans = len(plans)
            report.drafts = len([p for p in plans if p.status.value == "draft"])
            files = self._files_of_plans(plans)
            report.versions = sum(len(p.files) for p in plans)

            for plan in plans:
                await self.s.delete(plan)          # cascades days/activities/files

            result = await self.s.execute(
                sql_delete(AdvisorStudent).where(AdvisorStudent.student_id == student.id)
            )
            report.links = result.rowcount or 0

            student.invite_token = None            # kill any pending invite
            student.invite_expires_at = None
            await self.s.delete(student)
            await self.s.flush()                   # fails here → nothing committed

            report.files = self._remove_files(files)
        except Exception:
            await self.s.rollback()
            log.exception("student deletion failed, rolled back (id=%s)", student_id)
            raise

        await self.manager.audit.log(
            "student.deleted",
            actor_id=actor.id,
            student_id=student_id,
            detail=f"{report.name} · {report.summary()} · "
                   f"source={'admin' if is_admin(actor, actor.telegram_id) else 'advisor'}",
        )
        log.info("student %s deleted by %s (%s)", student_id, actor.id, report.summary())
        return report

    # ─────────────────────────────── advisors ───────────────────────────────
    async def delete_advisor(
        self,
        actor: User,
        advisor_id: int,
        *,
        strategy: str = "detach",
        target_advisor_id: int | None = None,
    ) -> DeletionReport:
        """Delete an advisor. Their students are transferred or detached, never wiped."""
        advisor = await self._advisor_target(actor, advisor_id)
        report = DeletionReport(name=advisor.full_name)

        target: User | None = None
        if strategy == "transfer":
            if not target_advisor_id:
                raise StudentError("مشاور مقصد انتخاب نشده است.")
            target = await self.manager.users.by_id(target_advisor_id)
            if target is None or target.role not in (Role.ADVISOR, Role.ADMIN):
                raise StudentError("مشاور مقصد معتبر نیست.")
            if target.id == advisor.id:
                raise StudentError("مشاور مقصد نمی‌تواند خود همین مشاور باشد.")

        try:
            links = list(
                (
                    await self.s.execute(
                        select(AdvisorStudent).where(
                            AdvisorStudent.advisor_id == advisor.id
                        )
                    )
                ).scalars()
            )
            report.students = len(links)

            plans = list(
                (
                    await self.s.execute(
                        select(WeeklyPlanDB).where(WeeklyPlanDB.advisor_id == advisor.id)
                    )
                ).scalars()
            )
            report.plans = len(plans)
            report.drafts = len([p for p in plans if p.status.value == "draft"])

            if strategy == "transfer" and target is not None:
                for link in links:
                    existing = await self.s.execute(
                        select(AdvisorStudent).where(
                            AdvisorStudent.advisor_id == target.id,
                            AdvisorStudent.student_id == link.student_id,
                        )
                    )
                    if existing.scalar_one_or_none() is None:
                        self.s.add(
                            AdvisorStudent(
                                advisor_id=target.id, student_id=link.student_id
                            )
                        )
                    await self.s.delete(link)
                    report.transferred += 1
                for plan in plans:                 # plans follow their students
                    plan.advisor_id = target.id
                report.plans = 0                   # nothing was destroyed
                report.drafts = 0
            else:
                files = self._files_of_plans(plans)
                for plan in plans:
                    await self.s.delete(plan)
                for link in links:
                    await self.s.delete(link)
                    report.detached += 1
                report.files = len(files)

            await self.s.delete(advisor)
            await self.s.flush()

            if strategy != "transfer":
                report.files = self._remove_files(self._pending_files)
        except Exception:
            await self.s.rollback()
            log.exception("advisor deletion failed, rolled back (id=%s)", advisor_id)
            raise

        await self.manager.audit.log(
            "advisor.deleted",
            actor_id=actor.id,
            detail=f"{report.name} · strategy={strategy} · {report.summary()}",
        )
        log.info("advisor %s deleted by %s (%s)", advisor_id, actor.id, report.summary())
        return report

    # ──────────────────────────────── plans ─────────────────────────────────
    async def delete_plan(self, actor: User, plan_id: int) -> DeletionReport:
        plan = await self.manager.plans.get(plan_id)
        if plan is None:
            raise StudentError("برنامه پیدا نشد.")
        if not is_admin(actor, actor.telegram_id):
            await self.manager.ensure_can_edit_plan(actor, plan)
        report = DeletionReport(
            name=f"{plan.student.full_name} — {plan.week_start}",
            plans=1,
            versions=len(plan.files),
        )
        report.files = await self.manager.delete_plan(actor, plan_id)
        return report

    # ─────────────────────────────── transfer ───────────────────────────────
    async def transfer_student(
        self, actor: User, student_id: int, new_advisor_id: int
    ) -> tuple[User, User]:
        """Move a student to another advisor (admin only)."""
        if not is_admin(actor, actor.telegram_id):
            raise AccessDenied("تغییر مشاور فقط توسط مدیر انجام می‌شود.")
        student = await self.manager.users.by_id(student_id)
        target = await self.manager.users.by_id(new_advisor_id)
        if student is None or student.role is not Role.STUDENT:
            raise StudentError("دانش‌آموز پیدا نشد.")
        if target is None or target.role not in (Role.ADVISOR, Role.ADMIN):
            raise StudentError("مشاور مقصد معتبر نیست.")

        await self.s.execute(
            sql_delete(AdvisorStudent).where(AdvisorStudent.student_id == student.id)
        )
        self.s.add(AdvisorStudent(advisor_id=target.id, student_id=student.id))
        # future plans belong to the new advisor; history keeps its author
        await self.s.flush()
        await self.manager.audit.log(
            "student.advisor_changed",
            actor_id=actor.id,
            student_id=student.id,
            detail=f"→ {target.full_name}",
        )
        return student, target

    async def unlink_telegram(self, actor: User, student_id: int) -> User:
        """Detach a Telegram account from a student (admin only)."""
        if not is_admin(actor, actor.telegram_id):
            raise AccessDenied("این عملیات فقط توسط مدیر انجام می‌شود.")
        student = await self.manager.users.by_id(student_id)
        if student is None or student.role is not Role.STUDENT:
            raise StudentError("دانش‌آموز پیدا نشد.")
        student.telegram_id = None
        student.username = None
        await self.s.flush()
        await self.manager.audit.log(
            "telegram.unlinked", actor_id=actor.id, student_id=student.id
        )
        return student

    # ─────────────────────────────── internals ──────────────────────────────
    _pending_files: list[Path] = []

    async def _student_in_scope(self, actor: User, student_id: int) -> User:
        """Admins reach every student; advisors only their own."""
        if is_admin(actor, actor.telegram_id):
            student = await self.manager.users.by_id(student_id)
            if student is None or student.role is not Role.STUDENT:
                raise StudentError("دانش‌آموز پیدا نشد.")
            return student
        self.manager.ensure_active(actor)
        return await self.manager.ensure_owns_student(actor, student_id)

    async def _advisor_target(self, actor: User, advisor_id: int) -> User:
        if not is_admin(actor, actor.telegram_id):
            raise AccessDenied("حذف مشاور فقط توسط مدیر انجام می‌شود.")
        advisor = await self.manager.users.by_id(advisor_id)
        if advisor is None or advisor.role not in (Role.ADVISOR, Role.ADMIN):
            raise StudentError("مشاور پیدا نشد.")
        if advisor.id == actor.id:
            raise StudentError("⚠️ این عملیات روی مدیر اصلی مجاز نیست.")
        if is_admin(advisor, advisor.telegram_id):
            raise StudentError("⚠️ این عملیات روی مدیر اصلی مجاز نیست.")
        return advisor

    def _files_of_plans(self, plans: list[WeeklyPlanDB]) -> list[Path]:
        paths: set[Path] = set()
        for plan in plans:
            for raw in (plan.image_path, plan.pdf_path):
                if raw:
                    paths.add(Path(raw))
            for record in plan.files:
                paths.update({Path(record.image_path), Path(record.pdf_path)})
        self._pending_files = list(paths)
        return self._pending_files

    def _remove_files(self, paths: list[Path]) -> int:
        """Delete artefacts inside STORAGE_ROOT; a missing file is not an error."""
        try:
            root = self.storage_root.resolve()
        except OSError:  # pragma: no cover
            return 0
        removed = 0
        for path in paths:
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if not resolved.is_relative_to(root):
                log.error("refusing to delete %s: outside storage root", resolved)
                continue
            try:
                resolved.unlink()
                removed += 1
            except FileNotFoundError:
                continue
            except OSError as exc:  # pragma: no cover
                log.warning("could not delete %s: %s", resolved, exc)
        return removed
