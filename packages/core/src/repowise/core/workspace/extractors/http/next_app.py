"""Next.js App Router HTTP provider dialect — ``app/**/route.ts``.

Unique among the dialects here in reading no route call at all: the App Router
takes the path from the file's own location and the verb from the name of each
exported handler, so both halves come from outside the source text. Both are
derived by ``ingestion.framework_routes``, shared with the graph-edge builder
that uses the same convention test to find files loaded without an import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import (
    HTTP_METHODS,
    next_route_path,
    next_route_verbs,
)

from ..base import line_at
from ..langs import JS_TS
from .dialect import build_provider_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext


class NextAppDialect:
    name = "next-app"
    extensions = JS_TS

    def extract(self, ctx: ScanContext) -> list[Contract]:
        path = next_route_path(ctx.rel_path)
        if path is None:
            return []
        out: list[Contract] = []
        for verb, offset in next_route_verbs(ctx.content):
            if verb not in HTTP_METHODS:
                continue
            # Bound by line, not by `handler=verb`: every route handler in the
            # repo exports the same few names, so a repo-wide lookup by name
            # would bind to whichever one it reached first.
            c = build_provider_contract(
                ctx,
                method=verb,
                path_raw=path,
                framework="next-app",
                line=line_at(ctx.content, offset),
            )
            if c is not None:
                out.append(c)
        return out
