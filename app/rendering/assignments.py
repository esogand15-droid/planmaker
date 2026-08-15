"""Assignment text composition shared by every renderer backend.

Both backends must produce the same wording and the same line split, otherwise
the PNG and the PDF (or preview and final output) could differ.
"""
from __future__ import annotations

from ..domain.models import WeeklyPlan
from ..domain.persian import apply_digit_style, normalize_fa
from .layout import TemplateLayout


def assignment_lines(plan: WeeklyPlan, layout: TemplateLayout) -> list[str]:
    """Numbered assignments distributed over the template's ruled lines."""
    items = sorted(plan.assignments, key=lambda a: a.order)
    if not items:
        return []
    cfg = layout.assignments_cfg
    rules = cfg.get("rules") or []
    buckets = max(1, len(rules))
    sep = cfg.get("separator", "   —   ")
    digits = layout.digits

    numbered = [
        apply_digit_style(f"{n}. {normalize_fa(a.text)}", digits)
        for n, a in enumerate(items, start=1)
    ]
    if buckets == 1:
        return [sep.join(numbered)]

    per = -(-len(numbered) // buckets)  # ceil
    chunks = [numbered[i:i + per] for i in range(0, len(numbered), per)]
    return [sep.join(c) for c in chunks[:buckets]]


def dropped_assignments(plan: WeeklyPlan, layout: TemplateLayout) -> int:
    """How many assignments would not fit on the ruled lines at all."""
    items = sorted(plan.assignments, key=lambda a: a.order)
    rules = layout.assignments_cfg.get("rules") or []
    buckets = max(1, len(rules))
    per = -(-len(items) // buckets) if items else 0
    shown = min(len(items), per * buckets)
    return len(items) - shown
