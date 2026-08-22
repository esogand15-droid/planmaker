"""Application service: validate → render → store → hand files to the bot layer.

This is the only entry point the Telegram layer needs; it knows nothing about
Pillow, Chromium or file paths.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..domain.models import PlanStatus, WeeklyPlan
from ..domain.persian import today_local, week_label
from ..rendering.base import BaseRenderer, OverflowIssue
from ..rendering.base import BaseRenderer
from ..rendering.factory import get_renderer
from ..rendering.pdf import png_to_pdf

log = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORAGE = PACKAGE_ROOT / "generated"


class PlanGenerationError(Exception):
    """Raised for user-facing failures; never leaks stack traces to Telegram."""


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    issues: list[OverflowIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.issues

    def human(self) -> list[str]:
        return self.errors + [i.human() for i in self.issues]


@dataclass
class GeneratedPlan:
    plan_id: str
    png_path: Path
    pdf_path: Path
    caption: str
    file_stem: str
    cached: bool
    duration_ms: int
    renderer: str
    template_version: str
    plan_hash: str
    width: int
    height: int


class WeeklyPlanService:
    def __init__(
        self,
        renderer: BaseRenderer | None = None,
        storage_root: Path | str = DEFAULT_STORAGE,
        *,
        print_scale: float = 2.0,
        pdf_dpi: int = 300,
        fallback_renderer: BaseRenderer | None = None,
    ):
        self.renderer = renderer or get_renderer()
        self.storage_root = Path(storage_root)
        self.print_scale = print_scale
        self.pdf_dpi = pdf_dpi
        # If the browser backend dies at runtime (missing Chromium, OOM, crash)
        # we still ship a correct plan through the pure-Python renderer.
        if fallback_renderer is None and self.renderer.name != "pillow":
            from ..rendering.pillow_renderer import PillowRenderer

            fallback_renderer = PillowRenderer(self.renderer.layout)
        self.fallback_renderer = fallback_renderer
        self._by_template: dict[str, BaseRenderer] = {
            self.renderer.layout.version: self.renderer
        }

    def renderer_for(self, template_version: str | None) -> BaseRenderer:
        """Old plans keep their original sheet; new ones use the default."""
        from ..rendering.registry import resolve

        info = resolve(template_version)
        if info.version == self.renderer.layout.version:
            return self.renderer
        if info.version not in self._by_template:
            log.info("loading legacy template %s for an older plan", info.version)
            self._by_template[info.version] = get_renderer(
                getattr(self.renderer, "backend_key", "pillow"), info.version
            )
        return self._by_template[info.version]

    def _render_png(self, plan: WeeklyPlan, scale: float):
        renderer = self.renderer_for(getattr(plan, "template_version", None))
        try:
            return renderer.render_png(plan, scale=scale)
        except Exception:
            if self.fallback_renderer is None:
                raise
            log.exception(
                "renderer %s failed — falling back to %s",
                renderer.signature, self.fallback_renderer.signature,
            )
            return self.fallback_renderer.render_png(plan, scale=scale)

    # ------------------------------------------------------------------
    def layout_for(self, plan: WeeklyPlan):
        return self.renderer_for(getattr(plan, "template_version", None)).layout

    def validate(self, plan: WeeklyPlan) -> ValidationReport:
        errors: list[str] = []
        if not plan.student_name and not plan.student_id:
            errors.append("دانش‌آموز انتخاب نشده است.")
        if not plan.week_start or not plan.week_end:
            errors.append("بازه هفته مشخص نشده است.")
        elif plan.week_end <= plan.week_start:
            errors.append("تاریخ پایان هفته باید بعد از تاریخ شروع باشد.")
        if plan.activity_count == 0:
            errors.append("حداقل یک فعالیت باید در برنامه وارد شود.")
        renderer = self.renderer_for(getattr(plan, "template_version", None))
        if not renderer.layout.template_path.exists():
            errors.append("فایل قالب در دسترس نیست.")
        for weight in ("regular", "medium", "bold"):
            if not renderer.layout.font_path(weight).exists():
                errors.append("فونت برنامه در دسترس نیست.")
                break
        issues = renderer.validate(plan) if not errors else []
        return ValidationReport(errors=errors, issues=issues)

    # ------------------------------------------------------------------
    def file_stem(self, plan: WeeklyPlan) -> str:
        """ASCII-safe physical filename; the Persian text lives in the caption."""
        sid = plan.student_id or plan.id
        day = (plan.week_start or today_local()).isoformat()
        return f"rotbeland_weekly_plan_{sid}_{day}_v{plan.version}"

    def caption(self, plan: WeeklyPlan) -> str:
        label = (
            week_label(plan.week_start, plan.week_end)
            if plan.week_start and plan.week_end
            else ""
        )
        return f"برنامه هفتگی - {plan.student_name or 'دانش‌آموز'} - هفته {label}"

    def _dir_for(self, plan: WeeklyPlan) -> Path:
        d = plan.week_start or today_local()
        sid = plan.student_id or plan.id
        path = self.storage_root / f"{d.year:04d}" / f"{d.month:02d}" / str(sid)
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ------------------------------------------------------------------
    def generate(self, plan: WeeklyPlan, *, force: bool = False) -> GeneratedPlan:
        report = self.validate(plan)
        if report.errors:
            raise PlanGenerationError("؛ ".join(report.errors))

        started = time.perf_counter()
        renderer = self.renderer_for(getattr(plan, "template_version", None))
        layout = renderer.layout
        plan_hash = plan.content_hash(layout.version, renderer.signature)
        out_dir = self._dir_for(plan)
        stem = f"{self.file_stem(plan)}_{plan_hash}"
        png_path = out_dir / f"{stem}.png"
        pdf_path = out_dir / f"{stem}.pdf"

        cached = png_path.exists() and pdf_path.exists() and not force
        width = layout.width
        height = layout.height
        if not cached:
            # screen-quality PNG (native template resolution, sharp text)
            screen = self._render_png(plan, scale=1.0)
            if screen.issues:
                log.warning(
                    "plan %s rendered with %d overflow issue(s)", plan.id, len(screen.issues)
                )
            tmp_png = png_path.with_suffix(".png.tmp")
            tmp_png.write_bytes(screen.png)
            tmp_png.replace(png_path)  # atomic: never ship a half-written file
            width, height = screen.width, screen.height

            # print-quality raster → A4 PDF (identical layout, 300 DPI class)
            pdf_bytes = self._build_pdf(plan)
            tmp_pdf = pdf_path.with_suffix(".pdf.tmp")
            tmp_pdf.write_bytes(pdf_bytes)
            tmp_pdf.replace(pdf_path)

        plan.status = PlanStatus.GENERATED
        duration = int((time.perf_counter() - started) * 1000)
        log.info(
            "generate plan_id=%s student=%s advisor=%s renderer=%s template=%s "
            "hash=%s cached=%s duration_ms=%s",
            plan.id, plan.student_id, plan.advisor_id, renderer.signature,
            layout.version, plan_hash, cached, duration,
        )
        return GeneratedPlan(
            plan_id=plan.id,
            png_path=png_path,
            pdf_path=pdf_path,
            caption=self.caption(plan),
            file_stem=stem,
            cached=cached,
            duration_ms=duration,
            renderer=self.renderer.signature,
            template_version=layout.version,
            plan_hash=plan_hash,
            width=width,
            height=height,
        )

    def _build_pdf(self, plan: WeeklyPlan) -> bytes:
        vector = getattr(self.renderer, "render_pdf_vector", None)
        if vector is not None:
            try:
                return vector(plan)  # crisp, selectable text
            except Exception:  # pragma: no cover
                log.exception("vector PDF failed, falling back to raster PDF")
        hi = self._render_png(plan, scale=self.print_scale)
        return png_to_pdf(hi.png, dpi=self.pdf_dpi, orientation="landscape")

    # ------------------------------------------------------------------
    def preview(self, plan: WeeklyPlan) -> bytes:
        """Preview == final renderer, so previews can never lie."""
        return self._render_png(plan, scale=1.0).png
