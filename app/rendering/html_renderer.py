"""HTML/CSS + headless Chromium renderer.

Chromium gives browser-grade Persian shaping, bidi and line breaking, which is
the safest option for mixed fa/en text. Layout coordinates come from the very
same calibrated JSON the Pillow backend uses, so both backends agree.

Overflow is measured *in the browser* (scrollHeight/scrollWidth) and the font
size is shrunk until the text fits or the minimum size is reached.
"""
from __future__ import annotations

import base64
import logging
import os
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..domain.models import WEEKDAY_KEYS, WeeklyPlan
from ..domain.persian import apply_digit_style, jalali_short, normalize_fa
from .assignments import assignment_lines
from .base import BaseRenderer, OverflowIssue, RenderResult
from .layout import TemplateLayout

log = logging.getLogger(__name__)


def _browser_roots() -> list[Path]:
    env = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if env and env != "0":
        return [Path(env)]
    return [Path.home() / ".cache" / "ms-playwright", Path("/ms-playwright")]


def _chromium_installed() -> bool:
    for root in _browser_roots():
        try:
            if root.is_dir() and any(
                child.name.startswith("chromium") for child in root.iterdir()
            ):
                return True
        except OSError:  # pragma: no cover - unreadable directory
            continue
    return False


TEMPLATES_DIR = Path(__file__).parent / "templates"

FIT_SCRIPT = """
() => {
  const overflow = [];
  document.querySelectorAll('[data-fit]').forEach(el => {
    const min = parseFloat(el.dataset.min);
    let size = parseFloat(el.dataset.max);
    const fits = () => el.scrollHeight <= el.clientHeight + 1 &&
                       el.scrollWidth  <= el.clientWidth + 1;
    while (!fits() && size > min) {
      size -= 0.5;
      el.style.fontSize = size + 'px';
      if (el.classList.contains('assign')) el.style.lineHeight = el.clientHeight + 'px';
    }
    if (!fits()) overflow.push(el.id);
  });
  return overflow;
}
"""


@lru_cache(maxsize=8)
def _b64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


class HtmlRenderer(BaseRenderer):
    name = "html-chromium"
    renderer_version = "1.0.0"

    def __init__(self, layout: TemplateLayout):
        super().__init__(layout)
        self._env = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR),
            autoescape=select_autoescape(["html", "j2"]),
        )

    # ------------------------------------------------------------------
    @staticmethod
    @lru_cache(maxsize=1)
    def available() -> bool:
        """True only when Playwright *and* an installed Chromium are present.

        Checking the import alone is not enough: on a fresh host the package can
        be installed while `playwright install chromium` was never run, and every
        render would then fail at launch time. The check is filesystem-only so it
        stays silent and instant (no driver process is started).
        """
        try:
            import playwright  # noqa: F401
        except Exception as exc:
            log.info("Playwright not importable (%s) — HTML backend disabled", exc)
            return False
        if not _chromium_installed():
            log.warning(
                "Chromium is not installed (run: python -m playwright install chromium) "
                "— HTML backend disabled, using the Pillow renderer"
            )
            return False
        return True

    # ------------------------------------------------------------------
    def build_html(self, plan: WeeklyPlan) -> str:
        lay = self.layout
        tcfg = lay.typography
        digits = lay.digits

        dates = []
        for weekday in WEEKDAY_KEYS:
            day = plan.day(weekday)
            if not day.date:
                continue
            box = lay.date_box(weekday)
            dates.append({
                "key": weekday,
                "box": box,
                "text": f"تاریخ : {jalali_short(day.date, digits)}",
                "size": tcfg["date"]["max_size"],
            })

        cells = []
        for weekday in WEEKDAY_KEYS:
            for activity in plan.day(weekday).activities:
                if activity.is_empty:
                    continue
                cells.append({
                    "weekday": weekday,
                    "slot": activity.slot_index,
                    "box": lay.cell(weekday, activity.slot_index),
                    "lines": [
                        apply_digit_style(normalize_fa(ln), digits)
                        for ln in activity.render_lines()
                    ],
                    "bold_first": bool(activity.subject.strip()),
                    "size": tcfg["cell"]["max_size"],
                    "min_size": tcfg["cell"]["min_size"],
                    "pad_x": tcfg["cell"]["pad_x"],
                    "pad_y": tcfg["cell"]["pad_y"],
                })

        assignments = []
        acfg = lay.assignments_cfg
        rules = acfg.get("rules") or []
        box = lay.assignments_box
        lines = assignment_lines(plan, self.layout)
        for i, text in enumerate(lines):
            rule_y = rules[i] if i < len(rules) else box.y + 28 * (i + 1)
            height = 24
            assignments.append({
                "text": text,
                "right": box.right,
                "width": box.w,
                "top": rule_y - acfg.get("rule_offset", 6) - height,
                "height": height,
                "size": tcfg["assignments"]["max_size"],
                "min_size": tcfg["assignments"]["min_size"],
            })

        return self._env.get_template("weekly_plan.html.j2").render(
            W=lay.width,
            H=lay.height,
            pad=lay.date_mask.get("pad", 3),
            template_b64=_b64(str(lay.template_path)),
            font_regular_b64=_b64(str(lay.font_path("regular"))),
            font_medium_b64=_b64(str(lay.font_path("medium"))),
            font_bold_b64=_b64(str(lay.font_path("bold"))),
            green=_rgb(lay.color("brand_green")),
            cell_color=_rgb(lay.color("cell_text")),
            date_color=_rgb(lay.color("date_text")),
            assign_color=_rgb(lay.color("assignment_text")),
            cell_line_gap=tcfg["cell"]["line_gap"],
            dates=dates,
            cells=cells,
            assignments=assignments,
        )

    # ------------------------------------------------------------------
    def render_png(self, plan: WeeklyPlan, scale: float = 1.0) -> RenderResult:
        from playwright.sync_api import sync_playwright

        html = self.build_html(plan)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--font-render-hinting=none"])
            page = browser.new_page(
                viewport={"width": self.layout.width, "height": self.layout.height},
                device_scale_factor=scale,
            )
            page.set_content(html, wait_until="load")
            page.wait_for_timeout(120)  # let webfonts settle
            overflowing = page.evaluate(FIT_SCRIPT)
            png = page.screenshot(type="png")
            browser.close()

        return RenderResult(
            png=png,
            issues=[_issue_from_id(i) for i in overflowing],
            width=round(self.layout.width * scale),
            height=round(self.layout.height * scale),
            renderer=self.signature,
            scale=scale,
        )

    def render_pdf_vector(self, plan: WeeklyPlan) -> bytes:
        """True vector text PDF (A4 landscape) straight from Chromium."""
        from playwright.sync_api import sync_playwright

        html = self.build_html(plan)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(
                viewport={"width": self.layout.width, "height": self.layout.height}
            )
            page.set_content(html, wait_until="load")
            page.wait_for_timeout(120)
            page.evaluate(FIT_SCRIPT)
            pdf = page.pdf(
                width=f"{self.layout.width}px",
                height=f"{self.layout.height}px",
                print_background=True,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
                scale=1,
            )
            browser.close()
        return pdf

    # ------------------------------------------------------------------
    def validate(self, plan: WeeklyPlan) -> list[OverflowIssue]:
        """Cheap prediction via the shared Pillow fit engine (no browser boot)."""
        from .pillow_renderer import PillowRenderer

        return PillowRenderer(self.layout).validate(plan)


def _rgb(c) -> str:
    return f"rgb({c[0]},{c[1]},{c[2]})"


def _issue_from_id(element_id: str) -> OverflowIssue:
    if element_id.startswith("cell-"):
        _, weekday, slot = element_id.split("-", 2)
        return OverflowIssue("cell", weekday, int(slot), "",
                             "متن بیش از ظرفیت سلول است")
    if element_id.startswith("assign-"):
        return OverflowIssue("assignments", None, None, "",
                             "تکالیف بیش از ظرفیت بخش است")
    return OverflowIssue("date", None, None, "", "متن بیش از ظرفیت است")
