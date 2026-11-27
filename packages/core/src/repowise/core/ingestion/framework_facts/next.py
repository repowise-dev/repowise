"""Next.js: the Pages Router and the App Router."""

from __future__ import annotations

from ..framework_routes import next_app_router_file
from .base import _JS_EXTS, FrameworkFacts, _js

NEXT_PAGES = FrameworkFacts(
    name="Next.js",
    anchor="framework:nextjs",
    npm_packages=("next",),
    # The Pages Router: every file under ``pages/`` is a route or API handler.
    entry_globs=_js("pages/*", "src/pages/*", exts=(*_JS_EXTS, "mdx")),
)

NEXT_APP = FrameworkFacts(
    name="Next.js",
    anchor="framework:nextjs",
    npm_packages=("next",),
    # The App Router loads its segment files (``page``, ``layout``, ``route``,
    # the metadata files) by name from ``app/``. The same predicate decides
    # routes and execution roles, so there is one answer to "is this an App
    # Router file".
    entry_match=next_app_router_file,
    # Loaded from the app root or ``src/``. Next.js 16 renamed ``middleware``
    # to ``proxy``.
    entry_globs=_js(
        *(
            f"{src}{stem}"
            for src in ("", "src/")
            for stem in ("middleware", "proxy", "instrumentation", "instrumentation-client")
        )
    ),
)
