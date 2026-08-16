"""Data access layer. All SQL lives here — services and handlers stay clean."""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload, selectinload

from ..db.models import (
    ActivityDB,
    AdvisorStudent,
    AssignmentDB,
    AuditLog,
    PlanDayDB,
    PlanFile,
    PlanStatusDB,
    Role,
    User,
    WeeklyPlanDB,
)


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def by_telegram_id(self, telegram_id: int) -> User | None:
        res = await self.s.execute(select(User).where(User.telegram_id == telegram_id))
        return res.scalar_one_or_none()

    async def by_id(self, user_id: int) -> User | None:
        return await self.s.get(User, user_id)

    async def create(
        self,
        full_name: str,
        role: Role = Role.STUDENT,
        telegram_id: int | None = None,
        **kw,
    ) -> User:
        user = User(full_name=full_name, role=role, telegram_id=telegram_id, **kw)
        self.s.add(user)
        await self.s.flush()
        return user

    async def touch_profile(self, user: User, username: str | None, full_name: str) -> User:
        if username and user.username != username:
            user.username = username
        if full_name and not user.full_name:
            user.full_name = full_name
        await self.s.flush()
        return user

    INVITE_TTL_DAYS = 14

    def _new_token(self) -> str:
        """32 chars of URL-safe entropy (~190 bits): not brute-forceable."""
        return secrets.token_urlsafe(24)[:32]

    async def create_student_for_advisor(
        self,
        advisor_id: int,
        full_name: str,
        *,
        grade: str | None = None,
        telegram_id: int | None = None,
    ) -> User:
        """Advisor-driven onboarding: create the student and link them at once.

        No admin, no shell access. If the Telegram id is unknown (the normal
        case) an invite token is generated instead; the student claims the
        account by opening the deep link.
        """
        from ..domain.persian import now_local

        student = User(
            full_name=full_name.strip(),
            role=Role.STUDENT,
            grade=(grade or None),
            telegram_id=telegram_id,
            created_by_id=advisor_id,
        )
        self.s.add(student)
        await self.s.flush()
        if telegram_id is None:
            await self.rotate_invite_token(student)
        await self.link_student(advisor_id, student.id)
        return student

    async def rotate_invite_token(self, student: User, ttl_days: int | None = None) -> str:
        """Issue a fresh single-use token; any previous one stops working."""
        from ..domain.persian import now_local

        ttl = ttl_days or self.INVITE_TTL_DAYS
        student.invite_token = self._new_token()
        student.invite_issued_at = now_local()
        student.invite_expires_at = now_local() + timedelta(days=ttl)
        await self.s.flush()
        return student.invite_token

    async def revoke_invite(self, student: User) -> None:
        student.invite_token = None
        student.invite_expires_at = None
        await self.s.flush()

    async def by_invite_token(self, token: str) -> User | None:
        if not token or len(token) < 16:  # ignore obviously forged tokens
            return None
        res = await self.s.execute(select(User).where(User.invite_token == token))
        return res.scalar_one_or_none()

    async def attach_telegram_id(self, student: User, telegram_id: int) -> User:
        """Manual linking by the advisor (they already know the numeric id)."""
        duplicate = await self.by_telegram_id(telegram_id)
        if duplicate is not None and duplicate.id != student.id:
            raise ValueError("telegram id already belongs to another account")
        student.telegram_id = telegram_id
        student.invite_token = None
        student.invite_expires_at = None
        await self.s.flush()
        return student

    async def update_student(
        self, student: User, *, full_name: str | None = None, grade: str | None = None
    ) -> User:
        if full_name:
            student.full_name = full_name
        student.grade = grade or None
        await self.s.flush()
        return student

    async def claim_invite(self, student: User, telegram_id: int, username: str | None) -> User:
        """Bind a Telegram account to an advisor-created student row."""
        from ..domain.persian import now_local  # noqa: F401  (kept for symmetry)

        duplicate = await self.by_telegram_id(telegram_id)
        if duplicate is not None and duplicate.id != student.id:
            # Only a plain, unused *student* row may be folded away. Advisors and
            # admins are never touched — the caller must reject those upstream.
            if duplicate.role is not Role.STUDENT:
                raise PermissionError(
                    f"refusing to fold {duplicate.role.value} account #{duplicate.id}"
                )
            duplicate.telegram_id = None
            duplicate.is_active = False
            await self.s.flush()
        student.telegram_id = telegram_id
        student.username = username
        student.invite_token = None
        student.invite_expires_at = None
        await self.s.flush()
        return student

    async def unlink_student(self, advisor_id: int, student_id: int) -> None:
        await self.s.execute(
            delete(AdvisorStudent).where(
                AdvisorStudent.advisor_id == advisor_id,
                AdvisorStudent.student_id == student_id,
            )
        )
        await self.s.flush()

    async def advisors_of(self, student_id: int) -> list[User]:
        stmt = (
            select(User)
            .join(AdvisorStudent, AdvisorStudent.advisor_id == User.id)
            .where(AdvisorStudent.student_id == student_id)
        )
        return list((await self.s.execute(stmt)).scalars())

    async def link_student(self, advisor_id: int, student_id: int) -> AdvisorStudent:
        existing = await self.s.execute(
            select(AdvisorStudent).where(
                AdvisorStudent.advisor_id == advisor_id,
                AdvisorStudent.student_id == student_id,
            )
        )
        link = existing.scalar_one_or_none()
        if link:
            return link
        link = AdvisorStudent(advisor_id=advisor_id, student_id=student_id)
        self.s.add(link)
        await self.s.flush()
        return link

    async def students_of(
        self, advisor_id: int, *, query: str | None = None, limit: int = 8, offset: int = 0
    ) -> list[User]:
        stmt = (
            select(User)
            .join(AdvisorStudent, AdvisorStudent.student_id == User.id)
            .where(AdvisorStudent.advisor_id == advisor_id, User.is_active.is_(True))
        )
        if query:
            pattern = f"%{query.strip()}%"
            stmt = stmt.where(or_(User.full_name.ilike(pattern), User.username.ilike(pattern)))
        stmt = stmt.order_by(User.full_name).limit(limit).offset(offset)
        return list((await self.s.execute(stmt)).scalars())

    async def count_students_of(self, advisor_id: int, query: str | None = None) -> int:
        stmt = (
            select(func.count())
            .select_from(User)
            .join(AdvisorStudent, AdvisorStudent.student_id == User.id)
            .where(AdvisorStudent.advisor_id == advisor_id, User.is_active.is_(True))
        )
        if query:
            pattern = f"%{query.strip()}%"
            stmt = stmt.where(or_(User.full_name.ilike(pattern), User.username.ilike(pattern)))
        return int((await self.s.execute(stmt)).scalar_one())

    async def all_students(
        self, *, query: str | None = None, limit: int = 8, offset: int = 0
    ) -> list[User]:
        """Admin view: every student in the system."""
        stmt = select(User).where(User.role == Role.STUDENT, User.is_active.is_(True))
        if query:
            pattern = f"%{query.strip()}%"
            stmt = stmt.where(or_(User.full_name.ilike(pattern), User.username.ilike(pattern)))
        return list(
            (await self.s.execute(stmt.order_by(User.full_name).limit(limit).offset(offset)))
            .scalars()
        )

    async def count_all_students(self, query: str | None = None) -> int:
        stmt = select(func.count()).select_from(User).where(
            User.role == Role.STUDENT, User.is_active.is_(True)
        )
        if query:
            pattern = f"%{query.strip()}%"
            stmt = stmt.where(or_(User.full_name.ilike(pattern), User.username.ilike(pattern)))
        return int((await self.s.execute(stmt)).scalar_one())

    async def is_assigned(self, advisor_id: int, student_id: int) -> bool:
        res = await self.s.execute(
            select(AdvisorStudent.id).where(
                AdvisorStudent.advisor_id == advisor_id,
                AdvisorStudent.student_id == student_id,
            )
        )
        return res.scalar_one_or_none() is not None


class PlanRepository:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def get(self, plan_id: int) -> WeeklyPlanDB | None:
        """Full load. `populate_existing` matters: the same object may already
        be in the identity map from a *light* list query with its heavy
        relationships deliberately unloaded."""
        stmt = (
            select(WeeklyPlanDB)
            .where(WeeklyPlanDB.id == plan_id)
            .options(
                selectinload(WeeklyPlanDB.student),
                selectinload(WeeklyPlanDB.advisor),
                selectinload(WeeklyPlanDB.days).selectinload(PlanDayDB.activities),
                selectinload(WeeklyPlanDB.assignments),
                selectinload(WeeklyPlanDB.files),
            )
            .execution_options(populate_existing=True)
        )
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def create(
        self, *, student_id: int, advisor_id: int, week_start: date, week_end: date
    ) -> WeeklyPlanDB:
        """Create a plan whose days come from the real calendar range."""
        from ..domain.calendar import JalaliDate

        plan = WeeklyPlanDB(
            student_id=student_id,
            advisor_id=advisor_id,
            week_start=week_start,
            week_end=week_end,
            status=PlanStatusDB.DRAFT,
        )
        plan.days = [
            PlanDayDB(weekday=d.weekday_key, day_index=d.index - 1, date=d.date)
            for d in JalaliDate.range(week_start, week_end)
        ]
        self.s.add(plan)
        await self.s.flush()
        # eager-load relationships now: lazy IO later would break the async context
        await self.s.refresh(plan, ["student", "advisor", "days", "assignments", "files"])
        return plan

    async def drafts_of(
        self, advisor_id: int, limit: int = 10, offset: int = 0
    ) -> list[WeeklyPlanDB]:
        stmt = (
            select(WeeklyPlanDB)
            .options(*self.LIST_ONLY)
            .where(
                WeeklyPlanDB.advisor_id == advisor_id,
                WeeklyPlanDB.status == PlanStatusDB.DRAFT,
            )
            .order_by(WeeklyPlanDB.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self.s.execute(stmt)).scalars())

    async def count_drafts_of(self, advisor_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(WeeklyPlanDB)
            .where(
                WeeklyPlanDB.advisor_id == advisor_id,
                WeeklyPlanDB.status == PlanStatusDB.DRAFT,
            )
        )
        return int((await self.s.execute(stmt)).scalar_one())

    async def files_of(self, plan_id: int) -> list[PlanFile]:
        stmt = (
            select(PlanFile)
            .where(PlanFile.plan_id == plan_id)
            .order_by(PlanFile.version.desc(), PlanFile.id.desc())
        )
        return list((await self.s.execute(stmt)).scalars())

    async def file_by_id(self, file_id: int) -> PlanFile | None:
        return await self.s.get(PlanFile, file_id)

    async def older_than(self, cutoff: date) -> list[WeeklyPlanDB]:
        stmt = select(WeeklyPlanDB).where(WeeklyPlanDB.week_end < cutoff)
        return list((await self.s.execute(stmt)).scalars())

    #: a list screen only needs the label fields + the student's name
    LIST_ONLY = (
        lazyload(WeeklyPlanDB.days),
        lazyload(WeeklyPlanDB.assignments),
        lazyload(WeeklyPlanDB.files),
        lazyload(WeeklyPlanDB.advisor),
        selectinload(WeeklyPlanDB.student),
    )

    async def history(
        self,
        *,
        advisor_id: int | None = None,
        student_id: int | None = None,
        limit: int = 6,
        offset: int = 0,
        only_generated: bool = False,
        light: bool = True,
    ) -> list[WeeklyPlanDB]:
        """`light` (default) skips the heavy relationships a list never shows."""
        stmt = select(WeeklyPlanDB)
        if light:
            stmt = stmt.options(*self.LIST_ONLY)
        if advisor_id is not None:
            stmt = stmt.where(WeeklyPlanDB.advisor_id == advisor_id)
        if student_id is not None:
            stmt = stmt.where(WeeklyPlanDB.student_id == student_id)
        if only_generated:
            stmt = stmt.where(
                WeeklyPlanDB.status.in_([PlanStatusDB.GENERATED, PlanStatusDB.SENT])
            )
        stmt = stmt.order_by(WeeklyPlanDB.week_start.desc()).limit(limit).offset(offset)
        return list((await self.s.execute(stmt)).scalars())

    async def count_history(
        self, *, advisor_id: int | None = None, student_id: int | None = None,
        only_generated: bool = False,
    ) -> int:
        stmt = select(func.count()).select_from(WeeklyPlanDB)
        if advisor_id is not None:
            stmt = stmt.where(WeeklyPlanDB.advisor_id == advisor_id)
        if student_id is not None:
            stmt = stmt.where(WeeklyPlanDB.student_id == student_id)
        if only_generated:
            stmt = stmt.where(
                WeeklyPlanDB.status.in_([PlanStatusDB.GENERATED, PlanStatusDB.SENT])
            )
        return int((await self.s.execute(stmt)).scalar_one())

    async def latest_for_student(self, student_id: int) -> WeeklyPlanDB | None:
        stmt = (
            select(WeeklyPlanDB)
            .where(
                WeeklyPlanDB.student_id == student_id,
                WeeklyPlanDB.status.in_([PlanStatusDB.GENERATED, PlanStatusDB.SENT]),
            )
            .order_by(WeeklyPlanDB.week_start.desc())
            .limit(1)
        )
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def previous_plan(
        self, student_id: int, before: date
    ) -> WeeklyPlanDB | None:
        stmt = (
            select(WeeklyPlanDB)
            .where(WeeklyPlanDB.student_id == student_id, WeeklyPlanDB.week_start < before)
            .order_by(WeeklyPlanDB.week_start.desc())
            .limit(1)
        )
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def find_by_week(self, student_id: int, week_start: date) -> WeeklyPlanDB | None:
        stmt = select(WeeklyPlanDB).where(
            WeeklyPlanDB.student_id == student_id, WeeklyPlanDB.week_start == week_start
        )
        return (await self.s.execute(stmt)).scalars().first()

    async def clear_day(self, plan_id: int, weekday: str) -> None:
        day = await self.day(plan_id, weekday)
        if day is None:
            return
        await self.s.execute(delete(ActivityDB).where(ActivityDB.plan_day_id == day.id))
        await self.s.flush()

    async def day(self, plan_id: int, weekday: str) -> PlanDayDB | None:
        stmt = select(PlanDayDB).where(
            PlanDayDB.plan_id == plan_id, PlanDayDB.weekday == weekday
        )
        return (await self.s.execute(stmt)).scalar_one_or_none()

    async def set_slot(
        self, plan_id: int, weekday: str, slot_index: int, values: dict[str, str] | None
    ) -> None:
        day = await self.day(plan_id, weekday)
        if day is None:
            raise ValueError("unknown weekday")
        await self.s.execute(
            delete(ActivityDB).where(
                ActivityDB.plan_day_id == day.id, ActivityDB.slot_index == slot_index
            )
        )
        if values and any(v.strip() for v in values.values()):
            self.s.add(ActivityDB(plan_day_id=day.id, slot_index=slot_index, **values))
        await self.s.flush()

    async def replace_assignments(self, plan_id: int, texts: list[str]) -> None:
        await self.s.execute(delete(AssignmentDB).where(AssignmentDB.plan_id == plan_id))
        for i, text in enumerate(t.strip() for t in texts if t.strip()):
            self.s.add(AssignmentDB(plan_id=plan_id, text=text, order=i))
        await self.s.flush()

    async def mark_generated(
        self,
        plan: WeeklyPlanDB,
        *,
        image_path: str,
        pdf_path: str,
        plan_hash: str,
        template_version: str,
        renderer_version: str,
        duration_ms: int,
    ) -> PlanFile:
        plan.status = PlanStatusDB.GENERATED
        plan.image_path = image_path
        plan.pdf_path = pdf_path
        plan.plan_hash = plan_hash
        plan.template_version = template_version
        plan.renderer_version = renderer_version
        plan.generated_at = datetime.now(timezone.utc)
        record = PlanFile(
            plan_id=plan.id,
            version=plan.version,
            plan_hash=plan_hash,
            image_path=image_path,
            pdf_path=pdf_path,
            template_version=template_version,
            renderer_version=renderer_version,
            duration_ms=duration_ms,
        )
        self.s.add(record)
        await self.s.flush()
        return record

    async def bump_version(self, plan: WeeklyPlanDB) -> None:
        """Editing a generated plan starts a new version; old files stay on disk."""
        if plan.status in (PlanStatusDB.GENERATED, PlanStatusDB.SENT):
            plan.version += 1
            plan.status = PlanStatusDB.DRAFT
            plan.image_file_id = None
            plan.pdf_file_id = None
            await self.s.flush()

    async def delete(self, plan: WeeklyPlanDB) -> None:
        await self.s.delete(plan)
        await self.s.flush()


class AuditRepository:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def log(
        self,
        action: str,
        *,
        actor_id: int | None = None,
        plan_id: int | None = None,
        student_id: int | None = None,
        detail: str | None = None,
    ) -> None:
        self.s.add(
            AuditLog(
                action=action,
                actor_id=actor_id,
                plan_id=plan_id,
                student_id=student_id,
                detail=detail,
            )
        )
        await self.s.flush()

    async def recent(self, limit: int = 20) -> list[AuditLog]:
        stmt = select(AuditLog).order_by(AuditLog.at.desc()).limit(limit)
        return list((await self.s.execute(stmt)).scalars())
