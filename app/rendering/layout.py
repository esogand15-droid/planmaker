"""Template layout: loads the calibrated coordinate config for a template version."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[2]  # .../rotbeland


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def scaled(self, s: float) -> "Box":
        return Box(round(self.x * s), round(self.y * s), round(self.w * s), round(self.h * s))

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.right, self.bottom)


class TemplateLayout:
    """All template geometry in one place — never hard-code coordinates elsewhere."""

    def __init__(self, data: dict[str, Any], config_path: Path):
        self._d = data
        self.config_path = config_path
        self.version: str = data["template_version"]
        self.width: int = data["canvas"]["width"]
        self.height: int = data["canvas"]["height"]
        self.template_path: Path = PACKAGE_ROOT / data["template_file"]
        self.digits: str = data.get("digits", "fa")

    # ---- factory ----
    @classmethod
    def load(cls, name: str = "template_weekly_v1") -> "TemplateLayout":
        path = PACKAGE_ROOT / "config" / f"{name}.json"
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh), path)

    # ---- geometry ----
    def cell(self, weekday: str, slot_index: int) -> Box:
        return Box(**self._d["cells"][weekday][slot_index])

    def cells(self, weekday: str) -> list[Box]:
        return [Box(**c) for c in self._d["cells"][weekday]]

    def date_box(self, weekday: str) -> Box:
        return Box(**self._d["date_boxes"][weekday])

    @property
    def assignments_box(self) -> Box:
        a = self._d["assignments"]
        return Box(a["x"], a["y"], a["w"], a["h"])

    @property
    def assignments_cfg(self) -> dict[str, Any]:
        return self._d["assignments"]

    @property
    def grid(self) -> dict[str, Any]:
        return self._d["grid"]

    @property
    def days(self) -> list[dict[str, Any]]:
        return self._d["days"]

    # ---- style ----
    def color(self, key: str) -> tuple[int, int, int]:
        return tuple(self._d["colors"][key])  # type: ignore[return-value]

    @property
    def typography(self) -> dict[str, Any]:
        return self._d["typography"]

    def font_path(self, weight: str = "regular") -> Path:
        return PACKAGE_ROOT / self._d["typography"][f"font_{weight}"]

    @property
    def date_mask(self) -> dict[str, Any]:
        return self._d.get("date_mask", {"enabled": True, "pad": 3})

    def day_card(self, weekday: str) -> Box | None:
        cards = self._d.get("day_cards") or {}
        return Box(**cards[weekday]) if weekday in cards else None

    def day_name_box(self, weekday: str) -> Box | None:
        boxes = self._d.get("day_name_boxes") or {}
        return Box(**boxes[weekday]) if weekday in boxes else None

    @property
    def static_regions(self) -> dict[str, Any]:
        return self._d.get("static_regions", {})

    @property
    def dynamic_regions(self) -> list[str]:
        return self._d.get("dynamic_regions", ["cells", "date_boxes", "assignments"])

    def color_or(self, key: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
        raw = self._d.get("colors", {}).get(key)
        return tuple(raw) if raw else fallback  # type: ignore[return-value]

    # ---- persistence (calibration tool writes back) ----
    def save(self) -> None:
        with open(self.config_path, "w", encoding="utf-8") as fh:
            json.dump(self._d, fh, ensure_ascii=False, indent=2)

    def raw(self) -> dict[str, Any]:
        return self._d
