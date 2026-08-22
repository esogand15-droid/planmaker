"""Regenerate the golden images used by tests/test_assignment_layout.py.

    python -m tools.goldens

Run this only when a template or a layout rule changed *on purpose*, and say so
in the commit message: the goldens are the visual contract of the sheet.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.rendering.pillow_renderer import PillowRenderer  # noqa: E402
from app.rendering.registry import load_layout  # noqa: E402
from tests.test_assignment_layout import GOLDEN_CASES, plan_with  # noqa: E402

GOLDEN_DIR = ROOT / "tests" / "goldens"


def main() -> None:
    layout = load_layout("rotbeland-weekly-v2")
    renderer = PillowRenderer(layout)
    panel = layout.assignments_outer
    crop = (panel.x - 10, panel.y - 40, panel.right + 10, panel.bottom + 10)
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    for name, texts in sorted(GOLDEN_CASES.items()):
        plan = plan_with(texts)
        img = Image.open(io.BytesIO(renderer.render_png(plan).png)).convert("RGB")
        img.crop(crop).save(GOLDEN_DIR / f"{name}.png")
        print(f"wrote {name}.png")

    (GOLDEN_DIR / "goldens.json").write_text(json.dumps({
        "template_version": layout.version,
        "renderer_version": PillowRenderer.renderer_version,
        "crop": list(crop),
        "cases": sorted(GOLDEN_CASES),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote goldens.json")


if __name__ == "__main__":
    main()
