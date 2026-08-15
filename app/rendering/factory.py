"""Renderer selection: HTML/Chromium primary, Pillow fallback."""
from __future__ import annotations

import logging
import os

from .base import BaseRenderer
from .html_renderer import HtmlRenderer
from .layout import TemplateLayout
from .pillow_renderer import PillowRenderer

log = logging.getLogger(__name__)

BACKENDS = {"pillow": PillowRenderer, "html": HtmlRenderer}


def get_renderer(
    backend: str | None = None, template: str = "template_weekly_v1"
) -> BaseRenderer:
    """`backend` in {'html','pillow','auto'}; env RENDER_BACKEND overrides default."""
    backend = (backend or os.getenv("RENDER_BACKEND", "auto")).lower()
    layout = TemplateLayout.load(template)

    if backend == "auto":
        backend = "html" if HtmlRenderer.available() else "pillow"
    if backend == "html" and not HtmlRenderer.available():
        log.warning("Playwright unavailable — falling back to the Pillow renderer")
        backend = "pillow"
    if backend not in BACKENDS:
        raise ValueError(f"unknown render backend: {backend}")
    return BACKENDS[backend](layout)
