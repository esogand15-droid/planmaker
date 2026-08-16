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
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False), default=Role.STUDENT)
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
