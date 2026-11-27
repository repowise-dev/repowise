"""Next.js: the Pages Router and the App Router."""

from __future__ import annotations

from ..framework_routes import NEXT_APP_ROUTER_EXTS, next_app_router_file
from .base import _JS_EXTS, FrameworkFacts, _js

NEXT_PAGES = FrameworkFacts(
    name="Next.js",
    anchor="framework:nextjs",
    npm_packages=("next",),
    # The Pages Router: every file under ``pages/`` is a route or API handler.
    entry_globs=_js("pages/*", "src/pages/*", exts=(*_JS_EXTS, "mdx")),
)


#: Metadata files: each serves its own URL beside its segment. Their names are
#: too common (``icon.tsx``, ``manifest.ts``) for the path-only App Router
#: predicate, so only an app that depends on ``next`` reads them.
_METADATA_STEMS = frozenset(
    {"sitemap", "robots", "manifest", "icon", "apple-icon", "opengraph-image", "twitter-image"}
)


def _app_router_file(rel: str) -> bool:
    """An App Router file of the app whose root *rel* is relative to.

    The ``app/`` directory sits at the app root or under ``src/``; one deeper
    belongs to another package, which answers for itself.
    """
    if not rel.startswith(("app/", "src/app/")):
        return False
    if next_app_router_file(rel):
        return True
    name = rel.rsplit("/", 1)[-1]
    return any(name.endswith(ext) and name[: -len(ext)] in _METADATA_STEMS for ext in NEXT_APP_ROUTER_EXTS)


NEXT_APP = FrameworkFacts(
    name="Next.js",
    anchor="framework:nextjs",
    npm_packages=("next",),
    # The App Router loads its segment files (``page``, ``layout``, ``route``)
    # by name from ``app/``. The same predicate decides routes and execution
    # roles, so there is one answer to "is this an App Router file".
    entry_match=_app_router_file,
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
