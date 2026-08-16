"""Pillow + libraqm renderer (HarfBuzz shaping, real RTL bidi).

Pixel-accurate: the official template PNG is used untouched as the base layer;
only dynamic text is composited on top.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from ..domain.models import WEEKDAY_KEYS, WeeklyPlan
from ..domain.persian import apply_digit_style, jalali_short, normalize_fa, shape_rtl
from .assignments import assignment_lines
from .base import BaseRenderer, OverflowIssue, RenderResult
from .fit import fit_text, load_font, raqm_available, text_width
from .layout import TemplateLayout


class PillowRenderer(BaseRenderer):
    name = "pillow"
    renderer_version = "1.0.0"

    def __init__(self, layout: TemplateLayout):
        super().__init__(layout)
        self._template_cache: dict[float, Image.Image] = {}
        self._raqm = raqm_available()

    # ------------------------------------------------------------------
    # template handling
    # ------------------------------------------------------------------
    def _template(self, scale: float) -> Image.Image:
        """Loaded once per scale and kept in memory (no disk churn per render)."""
        if scale not in self._template_cache:
            img = Image.open(self.layout.template_path).convert("RGB")
            if img.size != (self.layout.width, self.layout.height):
                raise ValueError(
                    f"Template size mismatch: {img.size} != "
                    f"({self.layout.width}, {self.layout.height})"
                )
            if scale != 1.0:
                img = img.resize(
                    (round(img.width * scale), round(img.height * scale)),
                    Image.LANCZOS,
                )
            self._template_cache[scale] = img
        return self._template_cache[scale].copy()

    def _shaped(self, text: str) -> str:
        """With libraqm Pillow shapes natively; otherwise pre-shape ourselves."""
        return text if self._raqm else shape_rtl(text)

    def _draw_line(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        x_right: float,
        y: float,
        font,
        fill,
    ) -> None:
        """RTL line drawn from its right edge."""
        if self._raqm:
            draw.text((x_right, y), text, font=font, fill=fill, anchor="ra",
                      direction="rtl", language="fa")
        else:
            w = font.getlength(shape_rtl(text))
            draw.text((x_right - w, y), shape_rtl(text), font=font, fill=fill)

    # ------------------------------------------------------------------
    # fitting helpers (shared with validate())
    # ------------------------------------------------------------------
    def _cell_fit(self, weekday: str, slot_index: int, lines: list[str]):
        cfg = self.layout.typography["cell"]
        return fit_text(
            lines,
            self.layout.cell(weekday, slot_index),
            self.layout.font_path("regular"),
            max_size=cfg["max_size"],
            min_size=cfg["min_size"],
            line_gap=cfg["line_gap"],
            pad_x=cfg["pad_x"],
            pad_y=cfg["pad_y"],
        )

    def _assignments_fit(self, plan: WeeklyPlan):
        """Assignments sit on the template's ruled (dotted) lines."""
        cfg = self.layout.typography["assignments"]
        acfg = self.layout.assignments_cfg
        rules = acfg.get("rules") or []
        box = self.layout.assignments_box
        # the fit box is the ruled band; max_lines == number of dotted rules
        return fit_text(
            self._assignment_lines(plan),
            box,
            self.layout.font_path("medium"),
            max_size=cfg["max_size"],
            min_size=cfg["min_size"],
            line_gap=cfg["line_gap"],
            pad_x=cfg["pad_x"],
            pad_y=cfg["pad_y"],
            max_lines=len(rules) or None,
        )

    def _assignment_lines(self, plan: WeeklyPlan) -> list[str]:
        return assignment_lines(plan, self.layout)

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------
    def validate(self, plan: WeeklyPlan) -> list[OverflowIssue]:
        issues: list[OverflowIssue] = []
        for weekday in WEEKDAY_KEYS:
            day = plan.day(weekday)
            for activity in day.activities:
                if activity.is_empty:
                    continue
                lines = [
                    apply_digit_style(normalize_fa(ln), self.layout.digits)
                    for ln in activity.render_lines()
                ]
                res = self._cell_fit(weekday, activity.slot_index, lines)
                if res.overflow:
                    issues.append(
                        OverflowIssue(
                            scope="cell",
                            weekday=weekday,
                            slot_index=activity.slot_index,
                            text=activity.summary(),
                            message=res.reason or "متن بیش از ظرفیت سلول است",
                        )
                    )
        if plan.assignments:
            res = self._assignments_fit(plan)
            if res.overflow:
                issues.append(
                    OverflowIssue(
                        scope="assignments",
                        weekday=None,
                        slot_index=None,
                        text=" / ".join(a.text for a in plan.assignments),
                        message=res.reason or "تعداد یا طول تکالیف بیش از ظرفیت است",
                    )
                )
        return issues

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def render_png(self, plan: WeeklyPlan, scale: float = 1.0) -> RenderResult:
        img = self._template(scale)
        draw = ImageDraw.Draw(img)
        issues: list[OverflowIssue] = []

        self._draw_dates(draw, img, plan, scale, issues)
        self._draw_cells(draw, plan, scale, issues)
        self._draw_assignments(draw, plan, scale, issues)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return RenderResult(
            png=buf.getvalue(),
            issues=issues,
            width=img.width,
            height=img.height,
            renderer=self.signature,
            scale=scale,
        )

    # -- dates ---------------------------------------------------------
    def _draw_dates(self, draw, img, plan: WeeklyPlan, scale: float, issues: list) -> None:
        cfg = self.layout.typography["date"]
        mask_cfg = self.layout.date_mask
        green = self.layout.color("brand_green")
        color = self.layout.color("date_text")
        for weekday in WEEKDAY_KEYS:
            day = plan.day(weekday)
            if not day.date:
                continue
            box = self.layout.date_box(weekday).scaled(scale)
            if mask_cfg.get("enabled", True):
                pad = round(mask_cfg.get("pad", 3) * scale)
                draw.rectangle(
                    (box.x - pad, box.y - pad, box.right + pad, box.bottom + pad),
                    fill=green,
                )
            text = f"تاریخ : {jalali_short(day.date, self.layout.digits)}"
            size = max(8, round(cfg["max_size"] * scale))
            font = load_font(str(self.layout.font_path("medium")), size)
            while text_width(text, font) > box.w and size > round(cfg["min_size"] * scale):
                size -= 1
                font = load_font(str(self.layout.font_path("medium")), size)
            y = box.y + (box.h - size * 1.15) / 2
            self._draw_line(draw, text, box.right, y, font, color)

    # -- activity cells -------------------------------------------------
    def _draw_cells(self, draw, plan: WeeklyPlan, scale: float, issues: list) -> None:
        cfg = self.layout.typography["cell"]
        color = self.layout.color("cell_text")
        font_bold = str(self.layout.font_path("bold"))
        font_reg = str(self.layout.font_path("regular"))
        for weekday in WEEKDAY_KEYS:
            day = plan.day(weekday)
            for activity in day.activities:
                if activity.is_empty:
                    continue  # empty cells stay truly empty
                lines = [
                    apply_digit_style(normalize_fa(ln), self.layout.digits)
                    for ln in activity.render_lines()
                ]
                res = self._cell_fit(weekday, activity.slot_index, lines)
                if res.overflow:
                    issues.append(
                        OverflowIssue("cell", weekday, activity.slot_index,
                                      activity.summary(),
                                      res.reason or "متن بیش از ظرفیت سلول است")
                    )
                box = self.layout.cell(weekday, activity.slot_index).scaled(scale)
                size = max(7, round(res.font_size * scale))
                pad_x = round(cfg["pad_x"] * scale)
                pad_y = round(cfg["pad_y"] * scale)
                step = size * cfg["line_gap"]
                total = step * len(res.lines)
                y = box.y + max(pad_y, (box.h - total) / 2)
                first_line_is_subject = bool(activity.subject.strip())
                for i, line in enumerate(res.lines):
                    bold = first_line_is_subject and i == 0
                    font = load_font(font_bold if bold else font_reg, size)
                    self._draw_line(draw, line, box.right - pad_x, y, font, color)
                    y += step

    # -- assignments -----------------------------------------------------
    def _draw_assignments(self, draw, plan: WeeklyPlan, scale: float, issues: list) -> None:
        if not plan.assignments:
            return
        cfg = self.layout.typography["assignments"]
        res = self._assignments_fit(plan)
        if res.overflow:
            issues.append(
                OverflowIssue("assignments", None, None,
                              " / ".join(a.text for a in plan.assignments),
                              res.reason or "تکالیف بیش از ظرفیت است")
            )
        box = self.layout.assignments_box.scaled(scale)
        acfg = self.layout.assignments_cfg
        size = max(7, round(res.font_size * scale))
        font = load_font(str(self.layout.font_path("medium")), size)
        color = self.layout.color("assignment_text")
        pad_x = round(cfg["pad_x"] * scale)
        rules = acfg.get("rules") or []
        offset = acfg.get("rule_offset", 6)
        if rules:
            # baseline-ish placement just above each dotted rule of the template
            for line, rule_y in zip(res.lines, rules):
                y = round((rule_y - offset) * scale) - size
                self._draw_line(draw, line, box.right - pad_x, y, font, color)
        else:
            step = size * cfg["line_gap"]
            total = step * len(res.lines)
            y = box.y + max(round(cfg["pad_y"] * scale), (box.h - total) / 2)
            for line in res.lines:
                self._draw_line(draw, line, box.right - pad_x, y, font, color)
                y += step
