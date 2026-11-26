"""Astro."""

from __future__ import annotations

from .base import FrameworkFacts, _js

ASTRO = FrameworkFacts(
    name="Astro",
    anchor="framework:astro",
    npm_packages=("astro",),
    entry_globs=(
        "src/pages/*",
        *_js("src/middleware", "src/middleware/index", "src/content/config"),
        *_js("src/content.config", "src/actions/index"),
    ),
)
