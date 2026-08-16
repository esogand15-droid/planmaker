"""Plan manager: ORM ↔ domain mapping, authorization and plan operations.

The Telegram layer only ever calls this class plus WeeklyPlanService (rendering).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import PlanStatusDB, Role, User, WeeklyPlanDB
from ..domain.models import Activity, Assignment, PlanDay, WeeklyPlan
from ..repositories.repositories import AuditRepository, PlanRepository, UserRepository

log = logging.getLogger(__name__)


class AccessDenied(Exception):
    """Raised when an advisor touches a student/plan that is not theirs."""


class StudentError(Exception):
    """User-facing problem while creating/managing a student."""


class PlanManager:
    def __init__(self, session: AsyncSession, storage_root: Path | str | None = None):
        self.s = session
        self.users = UserRepository(session)
        self.plans = PlanRepository(session)
        self.audit = AuditRepository(session)
        # artefact deletion is confined to this directory
        from ..config import settings

        self.storage_root = Path(storage_root or settings.storage_root)

    # ------------------------------------------------------------- access --
    async def ensure_can_edit_plan(self, actor: User, plan: WeeklyPlanDB) -> None:
        if actor.role == Role.ADMIN:
            return
        if actor.role == Role.ADVISOR and plan.advisor_id == actor.id:
            return
        raise AccessDenied("این برنامه در دسترس شما نیست.")

    async def ensure_can_view_plan(self, actor: User, plan: WeeklyPlanDB) -> None:
        if actor.role == Role.ADMIN:
            return
        if actor.role == Role.ADVISOR and plan.advisor_id == actor.id:
            return
        if actor.role == Role.STUDENT and plan.student_id == actor.id:
            return
        raise AccessDenied("این برنامه در دسترس شما نیست.")

    async def ensure_owns_student(self, advisor: User, student_id: int) -> User:
        student = await self.users.by_id(student_id)
        if student is None:
            raise AccessDenied("دانش‌آموز پیدا نشد.")
        if advisor.role == Role.ADMIN:
            return student
        if not await self.users.is_assigned(advisor.id, student_id):
            raise AccessDenied("این دانش‌آموز به شما تخصیص داده نشده است.")
        return student

    # ------------------------------------------------- student management --
    async def create_student(
        self, advisor: User, full_name: str, grade: str | None = None
    ) -> User:
        """An advisor registers their own student. Returns the new row."""
        if advisor.role not in (Role.ADVISOR, Role.ADMIN):
            raise AccessDenied("فقط مشاور می‌تواند دانش‌آموز اضافه کند.")
        name = " ".join((full_name or "").split())
        if len(name) < 2:
            raise StudentError("نام دانش‌آموز خیلی کوتاه است.")
        if len(name) > 80:
            raise StudentError("نام دانش‌آموز خیلی طولانی است (حداکثر ۸۰ نویسه).")

        existing = await self.users.students_of(advisor.id, query=name, limit=1)
        if any(s.full_name == name for s in existing):
            raise StudentError("دانش‌آموزی با همین نام در فهرست شما وجود دارد.")

        student = await self.users.create_student_for_advisor(
            advisor.id, name, grade=(grade or None)
        )
        await self.audit.log(
            "student.created", actor_id=advisor.id, student_id=student.id, detail=name
        )
        return student

    async def get_student(self, advisor: User, student_id: int) -> User:
        return await self.ensure_owns_student(advisor, student_id)

    async def new_invite(self, advisor: User, student_id: int) -> str:
        student = await self.ensure_owns_student(advisor, student_id)
        if student.is_connected:
            raise StudentError("این دانش‌آموز از قبل به ربات متصل است.")
        token = student.invite_token or await self.users.rotate_invite_token(student)
        await self.audit.log(
            "student.invited", actor_id=advisor.id, student_id=student.id
        )
        return token

    async def claim_invite(self, token: str, telegram_id: int, username: str | None):
        student = await self.users.by_invite_token(token)
        if student is None:
            return None
        await self.users.claim_invite(student, telegram_id, username)
        await self.audit.log(
            "student.connected", actor_id=student.id, student_id=student.id
        )
        return student

    async def remove_student(self, advisor: User, student_id: int) -> None:
        """Detach a student from this advisor (plans and data are preserved)."""
        student = await self.ensure_owns_student(advisor, student_id)
        await self.users.unlink_student(advisor.id, student.id)
        await self.audit.log(
            "student.removed", actor_id=advisor.id, student_id=student.id
        )

    # ------------------------------------------------------------ mapping --
    @staticmethod
    def to_domain(plan: WeeklyPlanDB) -> WeeklyPlan:
        domain = WeeklyPlan(
            id=str(plan.id),
            student_name=plan.student.full_name if plan.student else "",
            student_id=str(plan.student_id),
            advisor_id=str(plan.advisor_id),
            version=plan.version,
            days=[
                PlanDay(
                    weekday=d.weekday,
                    date=d.date,
                    activities=[
                        Activity(
                            id=str(a.id),
                            slot_index=a.slot_index,
                            subject=a.subject or "",
                            topic=a.topic or "",
                            description=a.description or "",
                            duration=a.duration or "",
                            notes=a.notes or "",
                        )
                        for a in d.activities
                    ],
                )
                for d in sorted(plan.days, key=lambda d: d.day_index)
            ],
            assignments=[
                Assignment(id=str(a.id), text=a.text, order=a.order) for a in plan.assignments
            ],
        )
        domain.week_start = plan.week_start
        domain.week_end = plan.week_end
        return domain

    # --------------------------------------------------------- operations --
    async def create_plan(
        self, advisor: User, student_id: int, week_start: date
    ) -> WeeklyPlanDB:
        from datetime import timedelta

        student = await self.ensure_owns_student(advisor, student_id)
        existing = await self.plans.find_by_week(student.id, week_start)
        if existing is not None:
            return existing
        plan = await self.plans.create(
            student_id=student.id,
            advisor_id=advisor.id,
            week_start=week_start,
            week_end=week_start + timedelta(days=6),
        )
        await self.audit.log(
            "plan.created", actor_id=advisor.id, plan_id=plan.id, student_id=student.id
        )
        return plan

    async def get_editable(self, actor: User, plan_id: int) -> WeeklyPlanDB:
        plan = await self.plans.get(plan_id)
        if plan is None:
            raise AccessDenied("برنامه پیدا نشد.")
        await self.ensure_can_edit_plan(actor, plan)
        return plan

    async def get_viewable(self, actor: User, plan_id: int) -> WeeklyPlanDB:
        plan = await self.plans.get(plan_id)
        if plan is None:
            raise AccessDenied("برنامه پیدا نشد.")
        await self.ensure_can_view_plan(actor, plan)
        return plan

    async def set_slot(
        self, actor: User, plan_id: int, weekday: str, slot: int, activity: Activity | None
    ) -> None:
        plan = await self.get_editable(actor, plan_id)
        await self.plans.bump_version(plan)
        values = (
            None
            if activity is None or activity.is_empty
            else {
                "subject": activity.subject,
                "topic": activity.topic,
                "description": activity.description,
                "duration": activity.duration,
                "notes": activity.notes,
            }
        )
        await self.plans.set_slot(plan.id, weekday, slot, values)
        await self.s.refresh(plan)
        await self.audit.log(
            "plan.edited", actor_id=actor.id, plan_id=plan.id,
            student_id=plan.student_id, detail=f"{weekday}#{slot + 1}",
        )

    async def clear_day(self, actor: User, plan_id: int, weekday: str) -> None:
        plan = await self.get_editable(actor, plan_id)
        await self.plans.bump_version(plan)
        await self.plans.clear_day(plan.id, weekday)
        await self.s.refresh(plan)
        await self.audit.log(
            "plan.edited", actor_id=actor.id, plan_id=plan.id,
            student_id=plan.student_id, detail=f"clear:{weekday}",
        )

    async def copy_day(self, actor: User, plan_id: int, src: str, dst: str) -> None:
        plan = await self.get_editable(actor, plan_id)
        await self.plans.bump_version(plan)
        domain = self.to_domain(plan)
        domain.copy_day(src, dst)
        await self.plans.clear_day(plan.id, dst)
        for activity in domain.day(dst).activities:
            await self.plans.set_slot(
                plan.id, dst, activity.slot_index,
                {
                    "subject": activity.subject,
                    "topic": activity.topic,
                    "description": activity.description,
                    "duration": activity.duration,
                    "notes": activity.notes,
                },
            )
        await self.s.refresh(plan)
        await self.audit.log(
            "plan.edited", actor_id=actor.id, plan_id=plan.id,
            student_id=plan.student_id, detail=f"copy:{src}->{dst}",
        )

    async def copy_previous_week(self, actor: User, plan_id: int) -> int:
        """Fill this plan from the student's previous week. Returns copied count."""
        plan = await self.get_editable(actor, plan_id)
        previous = await self.plans.previous_plan(plan.student_id, plan.week_start)
        if previous is None:
            return 0
        await self.plans.bump_version(plan)
        source = self.to_domain(previous)
        copied = 0
        for day in source.days:
            await self.plans.clear_day(plan.id, day.weekday)
            for activity in day.activities:
                if activity.is_empty:
                    continue
                await self.plans.set_slot(
                    plan.id, day.weekday, activity.slot_index,
                    {
                        "subject": activity.subject,
                        "topic": activity.topic,
                        "description": activity.description,
                        "duration": activity.duration,
                        "notes": activity.notes,
                    },
                )
                copied += 1
        await self.plans.replace_assignments(plan.id, [a.text for a in source.assignments])
        await self.s.refresh(plan)
        await self.audit.log(
            "plan.edited", actor_id=actor.id, plan_id=plan.id,
            student_id=plan.student_id, detail=f"copy_week:{previous.id}",
        )
        return copied

    async def set_assignments(self, actor: User, plan_id: int, texts: list[str]) -> None:
        plan = await self.get_editable(actor, plan_id)
        await self.plans.bump_version(plan)
        await self.plans.replace_assignments(plan.id, texts)
        await self.s.refresh(plan)
        await self.audit.log(
            "plan.edited", actor_id=actor.id, plan_id=plan.id,
            student_id=plan.student_id, detail="assignments",
        )

    async def mark_sent(self, actor: User, plan: WeeklyPlanDB) -> None:
        plan.status = PlanStatusDB.SENT
        plan.sent_at = datetime.now(timezone.utc)
        await self.s.flush()
        await self.audit.log(
            "plan.sent", actor_id=actor.id, plan_id=plan.id, student_id=plan.student_id
        )

    async def delete_plan(self, actor: User, plan_id: int) -> int:
        """Delete the plan *and* its generated files. Returns files removed."""
        plan = await self.get_editable(actor, plan_id)
        student_id = plan.student_id
        removed = remove_plan_files(plan, self.storage_root)
        await self.plans.delete(plan)
        await self.audit.log(
            "plan.deleted", actor_id=actor.id, plan_id=plan_id, student_id=student_id,
            detail=f"files_removed={removed}",
        )
        return removed

    async def purge_older_than(self, days: int) -> tuple[int, int]:
        """Retention: drop plans (and files) whose week ended `days` ago."""
        if days <= 0:
            return (0, 0)
        from ..domain.persian import today_local

        cutoff = today_local() - timedelta(days=days)
        stale = await self.plans.older_than(cutoff)
        files = 0
        for plan in stale:
            files += remove_plan_files(plan, self.storage_root)
            await self.plans.delete(plan)
        if stale:
            await self.audit.log(
                "plan.purged", detail=f"plans={len(stale)} files={files} cutoff={cutoff}"
            )
        return (len(stale), files)


def remove_plan_files(plan: WeeklyPlanDB, storage_root: Path | str | None = None) -> int:
    """Delete every artefact of a plan, refusing paths outside the storage root."""
    from ..config import settings

    try:
        root = Path(storage_root or settings.storage_root).resolve()
    except OSError:  # pragma: no cover - unreadable storage root
        return 0

    candidates: set[str] = {p for p in (plan.image_path, plan.pdf_path) if p}
    for record in plan.files:
        candidates.update({record.image_path, record.pdf_path})

    removed = 0
    for raw in candidates:
        try:
            path = Path(raw).resolve()
        except OSError:
            continue
        if not path.is_relative_to(root):
            log.error("refusing to delete %s: outside STORAGE_ROOT", path)
            continue
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as exc:  # pragma: no cover - permission problems
            log.warning("could not delete %s: %s", path, exc)
    return removed
