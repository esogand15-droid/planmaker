"""SQLAlchemy 2.0 async ORM models.

Mirrors the domain dataclasses one-to-one so that mapping stays trivial:
User (advisor/student/admin) → Student ↔ Advisor assignment → WeeklyPlan →
PlanDay → Activity, plus Assignment, PlanFile (versioned artefacts) and AuditLog.
"""
from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Role(str, enum.Enum):
    ADMIN = "admin"
    ADVISOR = "advisor"
    STUDENT = "student"


class PlanStatusDB(str, enum.Enum):
    DRAFT = "draft"
    READY = "ready"
    GENERATED = "generated"
    SENT = "sent"
    ARCHIVED = "archived"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    phone: Mapped[str | None] = mapped_column(String(24))
    role: Mapped[Role] = mapped_column(
        Enum(Role, native_enum=False), default=Role.STUDENT, index=True
    )
    grade: Mapped[str | None] = mapped_column(String(64))  # پایه/رشته دانش‌آموز
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: one-time token used by an advisor-created student to claim their account
    invite_token: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    invite_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invite_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    @property
    def is_connected(self) -> bool:
        """True once the person has opened the bot and claimed the account."""
        return self.telegram_id is not None

    advisor_links: Mapped[list["AdvisorStudent"]] = relationship(
        back_populates="student",
        foreign_keys="AdvisorStudent.student_id",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.id} {self.role.value} {self.full_name!r}>"


class AdvisorStudent(Base, TimestampMixin):
    """Authorization edge: an advisor may only touch students assigned to them."""

    __tablename__ = "advisor_students"
    __table_args__ = (UniqueConstraint("advisor_id", "student_id", name="uq_advisor_student"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advisor_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    advisor: Mapped[User] = relationship(foreign_keys=[advisor_id])
    student: Mapped[User] = relationship(back_populates="advisor_links", foreign_keys=[student_id])


class WeeklyPlanDB(Base, TimestampMixin):
    __tablename__ = "weekly_plans"
    __table_args__ = (
        Index("ix_plan_student_week", "student_id", "week_start"),
        Index("ix_plan_advisor_status", "advisor_id", "status"),
        # drafts are listed newest-first per advisor
        Index("ix_plan_advisor_updated", "advisor_id", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    advisor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    week_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[PlanStatusDB] = mapped_column(
        Enum(PlanStatusDB, native_enum=False), default=PlanStatusDB.DRAFT, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    template_version: Mapped[str | None] = mapped_column(String(64))
    renderer_version: Mapped[str | None] = mapped_column(String(64))
    plan_hash: Mapped[str | None] = mapped_column(String(40), index=True)
    image_path: Mapped[str | None] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(Text)
    image_file_id: Mapped[str | None] = mapped_column(Text)  # telegram file_id reuse
    pdf_file_id: Mapped[str | None] = mapped_column(Text)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    student: Mapped[User] = relationship(foreign_keys=[student_id], lazy="selectin")
    advisor: Mapped[User] = relationship(foreign_keys=[advisor_id], lazy="selectin")
    days: Mapped[list["PlanDayDB"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", lazy="selectin",
        order_by="PlanDayDB.day_index",
    )
    assignments: Mapped[list["AssignmentDB"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", lazy="selectin",
        order_by="AssignmentDB.order",
    )
    files: Mapped[list["PlanFile"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", lazy="selectin",
        order_by="PlanFile.version",
    )


class PlanDayDB(Base):
    __tablename__ = "plan_days"
    __table_args__ = (UniqueConstraint("plan_id", "weekday", name="uq_plan_weekday"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("weekly_plans.id", ondelete="CASCADE"), index=True
    )
    weekday: Mapped[str] = mapped_column(String(12), nullable=False)
    day_index: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[date | None] = mapped_column(Date)

    plan: Mapped[WeeklyPlanDB] = relationship(back_populates="days")
    activities: Mapped[list["ActivityDB"]] = relationship(
        back_populates="day", cascade="all, delete-orphan", lazy="selectin",
        order_by="ActivityDB.slot_index",
    )


class ActivityDB(Base):
    __tablename__ = "activities"
    __table_args__ = (UniqueConstraint("plan_day_id", "slot_index", name="uq_day_slot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_day_id: Mapped[int] = mapped_column(
        ForeignKey("plan_days.id", ondelete="CASCADE"), index=True
    )
    slot_index: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(String(80), default="")
    topic: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(String(200), default="")
    duration: Mapped[str] = mapped_column(String(60), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    day: Mapped[PlanDayDB] = relationship(back_populates="activities")


class AssignmentDB(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("weekly_plans.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    plan: Mapped[WeeklyPlanDB] = relationship(back_populates="assignments")


class PlanFile(Base, TimestampMixin):
    """One row per generated version → previous versions stay recoverable."""

    __tablename__ = "plan_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("weekly_plans.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    image_path: Mapped[str] = mapped_column(Text, nullable=False)
    pdf_path: Mapped[str] = mapped_column(Text, nullable=False)
    template_version: Mapped[str] = mapped_column(String(64), nullable=False)
    renderer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    plan: Mapped[WeeklyPlanDB] = relationship(back_populates="files")


class RequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class AccessRequest(Base, TimestampMixin):
    """Someone opened the bot without an invite.

    They are NOT given an account: their visit is recorded as a request so the
    admin can grant a role deliberately from the panel. This keeps the "no
    silent registration" rule intact while making unknown visitors reachable.
    """

    __tablename__ = "access_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus, native_enum=False),
        default=RequestStatus.PENDING,
        nullable=False,
        index=True,
    )
    visits: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    handled_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    granted_role: Mapped[Role | None] = mapped_column(Enum(Role, native_enum=False))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    plan_id: Mapped[int | None] = mapped_column(Integer, index=True)
    student_id: Mapped[int | None] = mapped_column(Integer, index=True)
    detail: Mapped[str | None] = mapped_column(Text)


class BackupSchedule(str, enum.Enum):
    """How often the bot backs itself up and mails the archive to the admin."""

    HOURLY = "hourly"
    HOURS = "hours"        # every `backup_every_hours` hours
    DAILY = "daily"        # at `backup_hour` (Asia/Tehran)
    WEEKLY = "weekly"      # on `backup_weekday` (0 = شنبه) at `backup_hour`


class BotSettings(Base):
    """Single-row runtime configuration the admin panel may change.

    Everything here used to live in environment variables, which meant a
    redeploy for every tweak. The row is created on demand with the values of
    `settings` (the env defaults), so an existing deployment keeps behaving
    exactly as before until an admin changes something in the panel.
    """

    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    backup_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    backup_schedule: Mapped[BackupSchedule] = mapped_column(
        Enum(BackupSchedule, native_enum=False),
        default=BackupSchedule.DAILY,
        nullable=False,
    )
    #: hour of day, Asia/Tehran (0-23) — used by `daily` and `weekly`
    backup_hour: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    #: 0 = شنبه … 6 = جمعه — used by `weekly`
    backup_weekday: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: used by `hours`
    backup_every_hours: Mapped[int] = mapped_column(Integer, default=12, nullable=False)
    #: how many archives are kept (on disk and in this table)
    backup_keep: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    #: comma separated Telegram chat ids; empty → every ADMIN_IDS entry
    backup_recipients: Mapped[str | None] = mapped_column(Text)
    backup_last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: set when an automatic run failed, cleared by the next successful one
    backup_last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def recipient_list(self) -> list[int]:
        """Explicit recipients, falling back to ADMIN_IDS from the environment."""
        from ..config import settings

        ids: list[int] = []
        for chunk in (self.backup_recipients or "").replace(" ", "").split(","):
            if chunk.lstrip("-").isdigit():
                value = int(chunk)
                if value not in ids:
                    ids.append(value)
        if ids:
            return ids
        explicit = list(settings.backup_chat_ids)
        return explicit or list(settings.admin_ids)


class BackupStatus(str, enum.Enum):
    OK = "ok"
    FAILED = "failed"


class BackupLog(Base):
    """One row per backup attempt — the panel shows the real history."""

    __tablename__ = "backup_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )
    #: automatic (scheduler) or manual (panel button / CLI)
    trigger: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)
    status: Mapped[BackupStatus] = mapped_column(
        Enum(BackupStatus, native_enum=False),
        default=BackupStatus.OK,
        nullable=False,
        index=True,
    )
    filename: Mapped[str | None] = mapped_column(Text)
    path: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tables: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: "pg_dump" or "python"
    engine: Mapped[str | None] = mapped_column(String(16))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    recipients: Mapped[str | None] = mapped_column(Text)
    #: how many admins actually received the archive
    delivered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    @property
    def human_size(self) -> str:
        size = float(self.size_bytes or 0)
        for unit in ("B", "KB", "MB"):
            if size < 1024 or unit == "MB":
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size / 1024:.2f} GB"
