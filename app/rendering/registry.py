"""Template registry — several official sheets can coexist.

A plan remembers the template it was generated with, so an older plan keeps
rendering exactly as it was printed while new plans use the current default.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .layout import PACKAGE_ROOT, TemplateLayout

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TemplateInfo:
    version: str        # the identifier stored on every plan
    config: str         # config/<config>.json
    label: str          # human, Persian
    active: bool        # False = legacy, still renderable


TEMPLATES: dict[str, TemplateInfo] = {
    "rotbeland-weekly-v1": TemplateInfo(
        version="rotbeland-weekly-v1",
        config="template_weekly_v1",
        label="قالب نسخه ۱ (قدیمی)",
        active=False,
    ),
    "rotbeland-weekly-v2": TemplateInfo(
        version="rotbeland-weekly-v2",
        config="template_weekly_v2",
        label="قالب رسمی رتبه لند — نسخه ۲",
        active=True,
    ),
}

#: what new plans use
DEFAULT_TEMPLATE = "rotbeland-weekly-v2"

#: legacy aliases: a config name may be passed where a version is expected
_ALIASES = {info.config: version for version, info in TEMPLATES.items()}


def resolve(name: str | None) -> TemplateInfo:
    """Accept a version id, a config name, or None (→ default)."""
    if not name:
        return TEMPLATES[DEFAULT_TEMPLATE]
    if name in TEMPLATES:
        return TEMPLATES[name]
    if name in _ALIASES:
        return TEMPLATES[_ALIASES[name]]
    log.warning("unknown template %r — falling back to %s", name, DEFAULT_TEMPLATE)
    return TEMPLATES[DEFAULT_TEMPLATE]


def load_layout(name: str | None = None) -> TemplateLayout:
    return TemplateLayout.load(resolve(name).config)


def available() -> list[TemplateInfo]:
    return list(TEMPLATES.values())


def verify_assets() -> list[str]:
    """Every registered template must have its image and config on disk."""
    problems: list[str] = []
    for info in TEMPLATES.values():
        config = PACKAGE_ROOT / "config" / f"{info.config}.json"
        if not config.exists():
            problems.append(f"missing config: {config}")
            continue
        layout = TemplateLayout.load(info.config)
        if not layout.template_path.exists():
            problems.append(f"missing image: {layout.template_path}")
    return problems
