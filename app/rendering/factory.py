"""Renderer selection: HTML/Chromium primary, Pillow fallback."""
from __future__ import annotations

import logging
import os

from .base import BaseRenderer
from .html_renderer import HtmlRenderer
from .pillow_renderer import PillowRenderer
from .registry import DEFAULT_TEMPLATE, load_layout

log = logging.getLogger(__name__)

BACKENDS = {"pillow": PillowRenderer, "html": HtmlRenderer}


def get_renderer(
    backend: str | None = None, template: str | None = None
) -> BaseRenderer:
    """`backend` in {'html','pillow','auto'}; `template` is a version or config name."""
    backend = (backend or os.getenv("RENDER_BACKEND", "auto")).lower()
    layout = load_layout(template or DEFAULT_TEMPLATE)

    if backend == "auto":
        backend = "html" if HtmlRenderer.available() else "pillow"
    if backend == "html" and not HtmlRenderer.available():
        log.warning("Playwright unavailable — falling back to the Pillow renderer")
        backend = "pillow"
    if backend not in BACKENDS:
        raise ValueError(f"unknown render backend: {backend}")
    return BACKENDS[backend](layout)
