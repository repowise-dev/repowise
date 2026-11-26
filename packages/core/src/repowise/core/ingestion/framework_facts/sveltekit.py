"""SvelteKit."""

from __future__ import annotations

from .base import FrameworkFacts, _js

SVELTEKIT = FrameworkFacts(
    name="SvelteKit",
    anchor="framework:sveltekit",
    npm_packages=("@sveltejs/kit",),
    entry_globs=(
        "src/routes/+*",
        "src/routes/*/+*",
        "src/params/*",
        *_js("src/hooks", "src/hooks.server", "src/hooks.client", "src/service-worker"),
        "src/service-worker/*",
    ),
)
