"""Visual smoke test: renders the sample plans with both backends into out/."""
from __future__ import annotations
import shutil, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.rendering.factory import get_renderer
from app.rendering.html_renderer import HtmlRenderer
from app.services.plan_service import WeeklyPlanService
from tools.demo_plan import full_plan, sparse_plan

OUT = ROOT / "out"; OUT.mkdir(exist_ok=True)

def main() -> None:
    backends = ["pillow"] + (["html"] if HtmlRenderer.available() else [])
    for backend in backends:
        svc = WeeklyPlanService(get_renderer(backend))
        for name, plan in (("full", full_plan()), ("sparse", sparse_plan())):
            report = svc.validate(plan)
            t0 = time.perf_counter()
            gen = svc.generate(plan, force=True)
            ms = int((time.perf_counter() - t0) * 1000)
            shutil.copy(gen.png_path, OUT / f"{backend}_{name}.png")
            shutil.copy(gen.pdf_path, OUT / f"{backend}_{name}.pdf")
            print(f"{backend:>7} {name:<7} {ms:>5} ms  valid={report.ok} "
                  f"png={gen.png_path.stat().st_size//1024}KB "
                  f"pdf={gen.pdf_path.stat().st_size//1024}KB")
            for msg in report.human():
                print("        ⚠", msg)

if __name__ == "__main__":
    main()
