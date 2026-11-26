"""Next.js: the Pages Router."""

from __future__ import annotations

from .base import _JS_EXTS, FrameworkFacts, _js

NEXT_PAGES = FrameworkFacts(
    name="Next.js",
    anchor="framework:nextjs",
    npm_packages=("next",),
    # The Pages Router: every file under ``pages/`` is a route or API handler.
    # App Router files are named by basename in the dead-code never-flag globs.
    entry_globs=_js("pages/*", "src/pages/*", exts=(*_JS_EXTS, "mdx")),
)
