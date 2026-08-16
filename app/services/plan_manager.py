"""Plan manager: ORM ↔ domain mapping, authorization and plan operations.

The Telegram layer only ever calls this class plus WeeklyPlanService (rendering).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AccessRequest, PlanStatusDB, Role, User, WeeklyPlanDB
from ..domain.models import Activity, Assignment, PlanDay, WeeklyPlan
from ..repositories.repositories import (
    AccessRequestRepository,
    AuditRepository,
    PlanRepository,
    UserRepository,
)
from .invites import InviteOutcome, InviteResult, blocks_invite

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
        self.requests = AccessRequestRepository(session)
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

    def ensure_active(self, actor: User) -> None:
        """A suspended account keeps its data but loses every write action."""
        from ..security import is_admin

        if not actor.is_active and not is_admin(actor, actor.telegram_id):
            raise AccessDenied(
                "🔒 حساب شما موقتاً غیرفعال شده است. با مدیر سیستم تماس بگیرید."
            )

    async def ensure_owns_student(self, advisor: User, student_id: int) -> User:
        student = await self.users.by_id(student_id)
        if student is None:
            raise AccessDenied("دانش‌آموز پیدا نشد.")
        if advisor.role == Role.ADMIN:
            return student
        if not await self.users.is_assigned(advisor.id, student_id):
            raise AccessDenied("این دانش‌آموز به شما تخصیص داده نشده است.")
        return student

    # ------------------------------------------------- access requests ----
    async def approve_request(
        self,
        admin: User,
        request_id: int,
        role: Role,
        *,
        advisor_id: int | None = None,
    ) -> User:
        """Grant a role to someone who opened the bot without an invite.

        Only an admin may do this — never an invite link, never `/start`.
        """
        from ..db.models import RequestStatus
        from ..security import is_admin

        if not is_admin(admin, admin.telegram_id):
            raise AccessDenied("این عملیات فقط توسط مدیر انجام می‌شود.")
        if role not in (Role.ADVISOR, Role.STUDENT):
            raise StudentError("نقش انتخابی معتبر نیست.")

        request = await self.requests.by_id(request_id)
        if request is None:
            raise StudentError("درخواست پیدا نشد.")
        if request.status is RequestStatus.APPROVED:
            raise StudentError("این درخواست قبلاً تأیید شده است.")

        existing = await self.users.by_telegram_id(request.telegram_id)
        if existing is not None:
            raise StudentError("این شخص از قبل حساب دارد.")

        user = await self.users.create(
            full_name=request.full_name,
            role=role,
            telegram_id=request.telegram_id,
            username=request.username,
        )
        if role is Role.STUDENT:
            target = advisor_id or admin.id
            await self.users.link_student(target, user.id)

        request.status = RequestStatus.APPROVED
        request.handled_by_id = admin.id
        request.granted_role = role
        await self.s.flush()

        await self.audit.log(
            "access.approved",
            actor_id=admin.id,
            student_id=user.id if role is Role.STUDENT else None,
            detail=f"{request.full_name} → {role.value} (tg={request.telegram_id})",
        )
        log.info(
            "access request %s approved as %s by admin %s",
            request_id, role.value, admin.id,
        )
        return user

    async def reject_request(self, admin: User, request_id: int) -> "AccessRequest":
        from ..db.models import RequestStatus
        from ..security import is_admin

        if not is_admin(admin, admin.telegram_id):
            raise AccessDenied("این عملیات فقط توسط مدیر انجام می‌شود.")
        request = await self.requests.by_id(request_id)
        if request is None:
            raise StudentError("درخواست پیدا نشد.")
        request.status = RequestStatus.REJECTED
        request.handled_by_id = admin.id
        await self.s.flush()
        await self.audit.log(
            "access.rejected", actor_id=admin.id,
            detail=f"{request.full_name} (tg={request.telegram_id})",
        )
        return request

    async def create_advisor_by_telegram_id(
        self, admin: User, full_name: str, telegram_id: int
    ) -> User:
        """Add an advisor straight from the panel (no shell needed)."""
        from ..security import is_admin

        if not is_admin(admin, admin.telegram_id):
            raise AccessDenied("این عملیات فقط توسط مدیر انجام می‌شود.")
        name = " ".join((full_name or "").split())
        if len(name) < 2:
            raise StudentError("نام مشاور خیلی کوتاه است.")
        if await self.users.by_telegram_id(telegram_id) is not None:
            raise StudentError("این شناسه تلگرام قبلاً ثبت شده است.")
        advisor = await self.users.create(
            full_name=name, role=Role.ADVISOR, telegram_id=telegram_id
        )
        # if the person had been waiting in the queue, close their request
        request = await self.requests.by_telegram_id(telegram_id)
        if request is not None:
            from ..db.models import RequestStatus

            request.status = RequestStatus.APPROVED
            request.handled_by_id = admin.id
            request.granted_role = Role.ADVISOR
        await self.audit.log(
            "advisor.created", actor_id=admin.id, detail=f"{name} (tg={telegram_id})"
        )
        return advisor

    # ------------------------------------------------- student management --
    async def create_student(
        self,
        advisor: User,
        full_name: str,
        grade: str | None = None,
        telegram_id: int | None = None,
    ) -> User:
        """An advisor registers their own student. Returns the new row."""
        if advisor.role not in (Role.ADVISOR, Role.ADMIN):
            raise AccessDenied("فقط مشاور می‌تواند دانش‌آموز اضافه کند.")
        self.ensure_active(advisor)
        name = " ".join((full_name or "").split())
        if len(name) < 2:
            raise StudentError("نام دانش‌آموز خیلی کوتاه است.")
        if len(name) > 80:
            raise StudentError("نام دانش‌آموز خیلی طولانی است (حداکثر ۸۰ نویسه).")

        existing = await self.users.students_of(advisor.id, query=name, limit=1)
        if any(s.full_name == name for s in existing):
            raise StudentError("دانش‌آموزی با همین نام در فهرست شما وجود دارد.")

        if telegram_id is not None:
            taken = await self.users.by_telegram_id(telegram_id)
            if taken is not None:
                raise StudentError("این آیدی تلگرام قبلاً در سیستم ثبت شده است.")

        student = await self.users.create_student_for_advisor(
            advisor.id, name, grade=(grade or None), telegram_id=telegram_id
        )
        await self.audit.log(
            "student.created", actor_id=advisor.id, student_id=student.id, detail=name
        )
        log.info("student created advisor=%s student=%s", advisor.id, student.id)
        return student

    async def get_student(self, advisor: User, student_id: int) -> User:
        return await self.ensure_owns_student(advisor, student_id)

    async def new_invite(self, advisor: User, student_id: int) -> tuple[str, datetime]:
        """Issue a fresh single-use, expiring invite token. Returns (token, expiry)."""
        student = await self.ensure_owns_student(advisor, student_id)
        if student.is_connected:
            raise StudentError("این دانش‌آموز از قبل به ربات متصل است.")
        token = await self.users.rotate_invite_token(student)
        await self.audit.log(
            "student.invite_issued", actor_id=advisor.id, student_id=student.id,
            detail=f"expires={student.invite_expires_at:%Y-%m-%d}",
        )
        log.info("invite issued advisor=%s student=%s", advisor.id, student.id)
        return token, student.invite_expires_at

    async def revoke_invite(self, advisor: User, student_id: int) -> None:
        student = await self.ensure_owns_student(advisor, student_id)
        await self.users.revoke_invite(student)
        await self.audit.log(
            "student.invite_revoked", actor_id=advisor.id, student_id=student.id
        )

    async def claim_invite(
        self,
        token: str,
        telegram_id: int,
        username: str | None,
        actor: User | None = None,
        *,
        is_admin_env: bool = False,
    ) -> InviteResult:
        """Redeem an invite link. Role integrity is checked BEFORE any write.

        `actor` is the account already bound to `telegram_id` (None for a
        newcomer); `is_admin_env` is True when the Telegram id is listed in
        ADMIN_IDS, which outranks whatever the database says.
        """
        from ..domain.persian import now_local

        await self.audit.log(
            "invite.opened", actor_id=actor.id if actor else None, detail=str(telegram_id)
        )

        # ── 1. role protection comes first: never touch an admin/advisor ──
        if blocks_invite(actor, is_admin_env):
            role = "admin" if is_admin_env or (actor and actor.role == Role.ADMIN) else "advisor"
            log.warning(
                "invite blocked: %s account tg=%s tried a student link", role, telegram_id
            )
            await self._log_invite(InviteOutcome.ROLE_CONFLICT, actor, None, telegram_id)
            return InviteResult(InviteOutcome.ROLE_CONFLICT)

        # ── 2. token validity ──
        student = await self.users.by_invite_token(token)
        if student is None:
            await self._log_invite(InviteOutcome.INVALID, actor, None, telegram_id)
            return InviteResult(InviteOutcome.INVALID)

        expiry = student.invite_expires_at
        if expiry is not None:
            if expiry.tzinfo is None:  # SQLite hands back naive datetimes
                expiry = expiry.replace(tzinfo=now_local().tzinfo)
            if expiry < now_local():
                await self._log_invite(InviteOutcome.EXPIRED, actor, student, telegram_id)
                return InviteResult(InviteOutcome.EXPIRED, student)

        # ── 3. ownership rules ──
        if student.telegram_id is not None:
            if student.telegram_id == telegram_id:
                await self.users.revoke_invite(student)  # consume the link
                await self._log_invite(InviteOutcome.ALREADY_SELF, actor, student, telegram_id)
                return InviteResult(InviteOutcome.ALREADY_SELF, student)
            await self._log_invite(InviteOutcome.ALREADY_LINKED, actor, student, telegram_id)
            return InviteResult(InviteOutcome.ALREADY_LINKED, student)

        if actor is not None and actor.id != student.id:
            # an existing (student) account may not absorb another student
            await self._log_invite(InviteOutcome.CROSS_STUDENT, actor, student, telegram_id)
            return InviteResult(InviteOutcome.CROSS_STUDENT, student)

        # ── 4. safe to link ──
        await self.users.claim_invite(student, telegram_id, username)
        await self._log_invite(InviteOutcome.LINKED, actor, student, telegram_id)
        log.info("invite accepted student=%s tg=%s", student.id, telegram_id)
        return InviteResult(InviteOutcome.LINKED, student)

    async def _log_invite(
        self,
        outcome: InviteOutcome,
        actor: User | None,
        student: User | None,
        telegram_id: int,
    ) -> None:
        await self.audit.log(
            outcome.audit_action,
            actor_id=actor.id if actor else None,
            student_id=student.id if student else None,
            detail=f"tg={telegram_id} outcome={outcome.value}",
        )

    async def link_telegram_id(self, advisor: User, student_id: int, telegram_id: int) -> User:
        """Advisor already knows the numeric id — link directly, no invite needed."""
        student = await self.ensure_owns_student(advisor, student_id)
        if student.telegram_id == telegram_id:
            return student
        try:
            await self.users.attach_telegram_id(student, telegram_id)
        except ValueError:
            raise StudentError(
                "این آیدی تلگرام قبلاً به حساب دیگری وصل شده است."
            ) from None
        await self.audit.log(
            "student.linked_manually", actor_id=advisor.id, student_id=student.id,
            detail=str(telegram_id),
        )
        return student

    async def edit_student(
        self, advisor: User, student_id: int, full_name: str, grade: str | None
    ) -> User:
        student = await self.ensure_owns_student(advisor, student_id)
        name = " ".join((full_name or "").split())
        if len(name) < 2:
            raise StudentError("نام دانش‌آموز خیلی کوتاه است.")
        if len(name) > 80:
            raise StudentError("نام دانش‌آموز خیلی طولانی است (حداکثر ۸۰ نویسه).")
        clash = [
            u for u in await self.users.students_of(advisor.id, query=name)
            if u.full_name == name and u.id != student.id
        ]
        if clash:
            raise StudentError("دانش‌آموز دیگری با همین نام در فهرست شما هست.")
        await self.users.update_student(student, full_name=name, grade=grade)
        await self.audit.log(
            "student.edited", actor_id=advisor.id, student_id=student.id, detail=name
        )
        return student

    async def detach_student(self, advisor: User, student_id: int) -> None:
        """Only drop the advisor↔student link (used when transferring students)."""
        student = await self.ensure_owns_student(advisor, student_id)
        await self.users.unlink_student(advisor.id, student.id)
        await self.audit.log(
            "student.detached", actor_id=advisor.id, student_id=student.id
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
        # days outside the stored range keep date=None and stay empty on the sheet
        return domain

    # --------------------------------------------------------- operations --
    async def create_plan(
        self, advisor: User, student_id: int, week_start: date, week_end: date | None = None
    ) -> WeeklyPlanDB:
        """`week_end` omitted → classic Saturday→Friday calendar week."""
        from datetime import timedelta

        from ..domain.calendar import JalaliDate

        self.ensure_active(advisor)
        student = await self.ensure_owns_student(advisor, student_id)
        end = week_end or week_start + timedelta(days=6)
        JalaliDate.validate_range(week_start, end)
        existing = await self.plans.find_by_week(student.id, week_start)
        if existing is not None:
            return existing
        plan = await self.plans.create(
            student_id=student.id,
            advisor_id=advisor.id,
            week_start=week_start,
            week_end=end,
        )
        await self.audit.log(
            "plan.created", actor_id=advisor.id, plan_id=plan.id, student_id=student.id
        )
        return plan

    async def get_editable(self, actor: User, plan_id: int) -> WeeklyPlanDB:
        self.ensure_active(actor)
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
