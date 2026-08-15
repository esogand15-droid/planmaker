"""Renderer contract shared by the Pillow and HTML/Playwright backends."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..domain.models import WeeklyPlan
from .layout import TemplateLayout


@dataclass
class OverflowIssue:
    scope: str            # "cell" | "assignments" | "date"
    weekday: str | None
    slot_index: int | None
    text: str
    message: str

    def human(self) -> str:
        from ..domain.models import WEEKDAY_FA

        if self.scope == "cell" and self.weekday is not None:
            return (
                f"«{WEEKDAY_FA[self.weekday]}» فعالیت شماره "
                f"{(self.slot_index or 0) + 1}: {self.message}"
            )
        if self.scope == "assignments":
            return f"بخش تکالیف: {self.message}"
        return self.message


@dataclass
class RenderResult:
    png: bytes
    issues: list[OverflowIssue] = field(default_factory=list)
    width: int = 0
    height: int = 0
    renderer: str = ""
    scale: float = 1.0

    @property
    def ok(self) -> bool:
        return not self.issues


class BaseRenderer(ABC):
    """A renderer draws a WeeklyPlan onto the official Rotbe Land template.

    Contract:
      * the template pixels are never altered (no blur/recompress/recolor);
      * only dates, activity texts and assignments are dynamic;
      * `validate()` must predict overflow *before* generation.
    """

    name: str = "base"
    renderer_version: str = "1.0.0"

    def __init__(self, layout: TemplateLayout):
        self.layout = layout

    @abstractmethod
    def render_png(self, plan: WeeklyPlan, scale: float = 1.0) -> RenderResult: ...

    @abstractmethod
    def validate(self, plan: WeeklyPlan) -> list[OverflowIssue]: ...

    @property
    def signature(self) -> str:
        return f"{self.name}-{self.renderer_version}"
