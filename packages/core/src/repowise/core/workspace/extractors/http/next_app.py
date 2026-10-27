"""Next.js HTTP provider dialect: App Router ``app/**/route.ts`` and Pages Router
``pages/api/**``.

Unique among the dialects here in reading no route call at all: Next.js takes
the path from the file's own location. The App Router takes the verb from the
name of each exported handler; a Pages Router handler serves the verbs it
compares ``req.method`` against, or every verb (``*``) when it compares none.
Both conventions are derived in ``ingestion``, shared with the graph-edge
builder that uses the same convention test to find files loaded without an
import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import (
    HTTP_METHODS,
    next_route_path,
    next_route_verbs,
)
from repowise.core.ingestion.framework_routes_next_pages import (
    next_pages_api_path,
    next_pages_api_verbs,
)

from ..base import line_at
from ..langs import JS_TS
from .dialect import build_provider_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_ANY_VERB = HTTP_METHODS | {"*"}


class NextAppDialect:
    name = "next-app"
    extensions = JS_TS
    # `module.exports =` serves a Pages Router handler from plain JS.
    requires = ("export", "module.exports")

    def extract(self, ctx: ScanContext) -> list[Contract]:
        path = next_route_path(ctx.rel_path)
        framework = "next-app"
        if path is not None:
            verbs = next_route_verbs(ctx.content)
        else:
            path = next_pages_api_path(ctx.rel_path)
            framework = "next-pages"
            verbs = (next_pages_api_verbs(ctx.content) or []) if path is not None else []
        out: list[Contract] = []
        for verb, offset in verbs:
            if verb not in _ANY_VERB:
                continue
            # Bound by line, not by `handler=verb`: every route handler in the
            # repo exports the same few names, so a repo-wide lookup by name
            # would bind to whichever one it reached first.
            c = build_provider_contract(
                ctx,
                method=verb,
                path_raw=path,
                framework=framework,
                line=line_at(ctx.content, offset),
            )
            if c is not None:
                out.append(c)
        return out
