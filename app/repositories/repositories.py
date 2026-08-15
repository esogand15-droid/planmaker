"""Data access layer. All SQL lives here — services and handlers stay clean."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
        return await self.s.get(WeeklyPlanDB, plan_id)

    async def create(
        self, *, student_id: int, advisor_id: int, week_start: date, week_end: date
    ) -> WeeklyPlanDB:
        from ..domain.models import WEEKDAY_KEYS

        plan = WeeklyPlanDB(
            student_id=student_id,
            advisor_id=advisor_id,
            week_start=week_start,
            week_end=week_end,
            status=PlanStatusDB.DRAFT,
        )
        from datetime import timedelta

        plan.days = [
            PlanDayDB(weekday=key, day_index=i, date=week_start + timedelta(days=i))
            for i, key in enumerate(WEEKDAY_KEYS)
        ]
        self.s.add(plan)
        await self.s.flush()
        # eager-load relationships now: lazy IO later would break the async context
        await self.s.refresh(plan, ["student", "advisor", "days", "assignments", "files"])
        return plan

    async def drafts_of(self, advisor_id: int, limit: int = 10) -> list[WeeklyPlanDB]:
        stmt = (
            select(WeeklyPlanDB)
            .where(
                WeeklyPlanDB.advisor_id == advisor_id,
                WeeklyPlanDB.status == PlanStatusDB.DRAFT,
            )
            .order_by(WeeklyPlanDB.updated_at.desc())
            .limit(limit)
        )
        return list((await self.s.execute(stmt)).scalars())

    async def history(
        self,
        *,
        advisor_id: int | None = None,
        student_id: int | None = None,
        limit: int = 6,
        offset: int = 0,
        only_generated: bool = False,
    ) -> list[WeeklyPlanDB]:
        stmt = select(WeeklyPlanDB)
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
