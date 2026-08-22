"""Domain model for Rotbe Land weekly plans.

Pure data — no Telegram, no DB, no rendering concerns.
The same objects are used by the draft system, the preview renderer and the
final PNG/PDF renderer, so what the consultant sees is always what is shipped.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import date
from enum import Enum
from typing import Any

from .calendar import WEEKDAY_FA as _WEEKDAY_FA
from .calendar import WEEKDAY_KEYS as _WEEKDAY_KEYS

SLOTS_PER_DAY = 8
DAYS_PER_WEEK = 7

# weekday names live in exactly one place — the calendar engine
WEEKDAY_KEYS = list(_WEEKDAY_KEYS)
WEEKDAY_FA = dict(_WEEKDAY_FA)


class PlanStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    GENERATED = "generated"
    SENT = "sent"
    ARCHIVED = "archived"


@dataclass
class Activity:
    """One cell of the template grid."""

    slot_index: int  # 0..7, 0 == right-most cell (RTL)
    subject: str = ""
    topic: str = ""
    description: str = ""
    duration: str = ""
    notes: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def is_empty(self) -> bool:
        return not any(
            p.strip() for p in (self.subject, self.topic, self.description, self.duration)
        )

    def render_lines(self) -> list[str]:
        """Lines to draw inside the cell. Empty parts never produce placeholders."""
        lines: list[str] = []
        if self.subject.strip():
            lines.append(self.subject.strip())
        if self.topic.strip():
            lines.append(self.topic.strip())
        if self.description.strip():
            lines.append(self.description.strip())
        if self.duration.strip():
            lines.append(self.duration.strip())
        return lines

    def summary(self) -> str:
        return " | ".join(self.render_lines())

    @classmethod
    def from_quick_entry(cls, slot_index: int, raw: str) -> "Activity":
        """`زیست | گوارش | 40 تست | 90 دقیقه` (also accepts newlines or '-')."""
        text = raw.replace("\n", "|").replace("،", "|")
        parts = [p.strip() for p in text.split("|")]
        parts = [p for p in parts if p]
        parts += [""] * (4 - len(parts)) if len(parts) < 4 else []
        return cls(
            slot_index=slot_index,
            subject=parts[0] if len(parts) > 0 else "",
            topic=parts[1] if len(parts) > 1 else "",
            description=parts[2] if len(parts) > 2 else "",
            duration=" ".join(parts[3:]).strip() if len(parts) > 3 else "",
        )


@dataclass
class PlanDay:
    weekday: str  # one of WEEKDAY_KEYS
    date: date | None = None  # gregorian; converted to Jalali at render time
    activities: list[Activity] = field(default_factory=list)

    @property
    def fa_name(self) -> str:
        return WEEKDAY_FA[self.weekday]

    def slot(self, index: int) -> Activity | None:
        for a in self.activities:
            if a.slot_index == index:
                return a if not a.is_empty else None
        return None

    def set_slot(self, index: int, activity: Activity | None) -> None:
        if not 0 <= index < SLOTS_PER_DAY:
            raise ValueError(f"slot_index out of range: {index}")
        self.activities = [a for a in self.activities if a.slot_index != index]
        if activity is not None and not activity.is_empty:
            activity.slot_index = index
            self.activities.append(activity)
        self.activities.sort(key=lambda a: a.slot_index)

    def first_free_slot(self) -> int | None:
        used = {a.slot_index for a in self.activities if not a.is_empty}
        for i in range(SLOTS_PER_DAY):
            if i not in used:
                return i
        return None

    @property
    def filled_count(self) -> int:
        return len([a for a in self.activities if not a.is_empty])

    @property
    def is_empty(self) -> bool:
        return self.filled_count == 0


@dataclass
class Assignment:
    text: str
    order: int = 0
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


@dataclass
class WeeklyPlan:
    student_name: str = ""
    student_id: str | None = None
    advisor_id: str | None = None
    week_start: date | None = None
    week_end: date | None = None
    status: PlanStatus = PlanStatus.DRAFT
    version: int = 1
    #: which official sheet this plan was (or will be) printed on
    template_version: str | None = None
    days: list[PlanDay] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def __post_init__(self) -> None:
        # The official sheet always has all seven rows; a custom range simply
        # leaves the rows outside it without a date (and therefore empty).
        present = {d.weekday for d in self.days}
        self.days.extend(
            PlanDay(weekday=key) for key in WEEKDAY_KEYS if key not in present
        )
        self.days.sort(key=lambda d: WEEKDAY_KEYS.index(d.weekday))
        if self.week_start:
            self.apply_week_start(self.week_start)

    # -- week helpers -------------------------------------------------
    def apply_week_start(self, start: date) -> None:
        """Calendar-week mode: snaps to the real Saturday, then Saturday→Friday."""
        from datetime import timedelta

        from .calendar import JalaliDate

        saturday = JalaliDate.saturday_of(start)
        self.apply_range(saturday, saturday + timedelta(days=6))

    def apply_range(self, start: date, end: date) -> None:
        """Custom-range mode: only the real days in [start, end] get a date.

        The weekday of every day comes from the actual calendar, so a range
        that begins mid-week fills exactly the rows it belongs in and leaves
        the other rows of the official template empty.
        """
        from .calendar import JalaliDate

        self.week_start = start
        self.week_end = end
        in_range = {d.weekday_key: d.date for d in JalaliDate.range(start, end)}
        for day in self.days:
            day.date = in_range.get(day.weekday)

    @property
    def plan_days(self) -> list["PlanDay"]:
        """Days that actually belong to the range, in chronological order."""
        dated = [d for d in self.days if d.date is not None]
        return sorted(dated, key=lambda d: d.date)

    @property
    def is_calendar_week(self) -> bool:
        from datetime import timedelta

        return bool(
            self.week_start
            and self.week_end
            and self.week_end - self.week_start == timedelta(days=6)
            and len(self.plan_days) == 7
        )

    @property
    def day_count(self) -> int:
        return len(self.plan_days)

    def day(self, weekday: str) -> PlanDay:
        for d in self.days:
            if d.weekday == weekday:
                return d
        raise KeyError(weekday)

    # -- stats --------------------------------------------------------
    @property
    def activity_count(self) -> int:
        return sum(d.filled_count for d in self.plan_days)

    @property
    def filled_days(self) -> int:
        return len([d for d in self.plan_days if not d.is_empty])

    @property
    def is_empty(self) -> bool:
        return self.activity_count == 0 and not self.assignments

    # -- copy helpers (Copy day / Duplicate week) ----------------------
    def copy_day(self, src: str, dst: str) -> None:
        source = self.day(src)
        target = self.day(dst)
        target.activities = [
            Activity(
                slot_index=a.slot_index,
                subject=a.subject,
                topic=a.topic,
                description=a.description,
                duration=a.duration,
                notes=a.notes,
            )
            for a in source.activities
        ]

    def duplicate(self, new_week_start: date) -> "WeeklyPlan":
        """Copy the plan onto a new start date, preserving the range length.

        A calendar week stays a calendar week (snapped to Saturday); a custom
        range keeps its own length and starts exactly where asked.
        """
        clone = WeeklyPlan(
            student_name=self.student_name,
            student_id=self.student_id,
            advisor_id=self.advisor_id,
            status=PlanStatus.DRAFT,
            version=1,
            days=[
                PlanDay(
                    weekday=d.weekday,
                    activities=[
                        Activity(
                            slot_index=a.slot_index,
                            subject=a.subject,
                            topic=a.topic,
                            description=a.description,
                            duration=a.duration,
                            notes=a.notes,
                        )
                        for a in d.activities
                    ],
                )
                for d in self.days
            ],
            assignments=[Assignment(text=a.text, order=a.order) for a in self.assignments],
        )
        from datetime import timedelta

        if self.is_calendar_week or self.week_start is None or self.week_end is None:
            clone.apply_week_start(new_week_start)
        else:
            span = (self.week_end - self.week_start).days
            clone.apply_range(new_week_start, new_week_start + timedelta(days=span))
        return clone

    # -- serialization / caching --------------------------------------
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["week_start"] = self.week_start.isoformat() if self.week_start else None
        data["week_end"] = self.week_end.isoformat() if self.week_end else None
        for d in data["days"]:
            d["date"] = d["date"].isoformat() if d["date"] else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WeeklyPlan":
        days = [
            PlanDay(
                weekday=d["weekday"],
                date=date.fromisoformat(d["date"]) if d.get("date") else None,
                activities=[Activity(**a) for a in d.get("activities", [])],
            )
            for d in data.get("days", [])
        ]
        plan = cls(
            id=data.get("id", uuid.uuid4().hex[:12]),
            student_name=data.get("student_name", ""),
            student_id=data.get("student_id"),
            advisor_id=data.get("advisor_id"),
            week_start=date.fromisoformat(data["week_start"]) if data.get("week_start") else None,
            week_end=date.fromisoformat(data["week_end"]) if data.get("week_end") else None,
            status=PlanStatus(data.get("status", "draft")),
            version=data.get("version", 1),
            days=days,
            assignments=[Assignment(**a) for a in data.get("assignments", [])],
        )
        return plan

    def content_hash(self, template_version: str, renderer_version: str) -> str:
        """Stable hash of everything that affects the rendered output → file cache."""
        payload = {
            "student": self.student_name,
            "week": [
                self.week_start.isoformat() if self.week_start else None,
                self.week_end.isoformat() if self.week_end else None,
            ],
            "days": [
                {
                    "d": d.weekday,
                    "date": d.date.isoformat() if d.date else None,
                    "a": sorted(
                        [[a.slot_index, a.subject, a.topic, a.description, a.duration]
                         for a in d.activities if not a.is_empty]
                    ),
                }
                for d in self.days
            ],
            "assignments": [a.text for a in sorted(self.assignments, key=lambda x: x.order)],
            "template": template_version,
            "renderer": renderer_version,
        }
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:20]
