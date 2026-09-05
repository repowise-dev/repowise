"""Remix HTTP provider dialect: ``app/routes/`` loaders and actions.

Remix serves a route because of where its file sits, so, as in the Next.js App
Router, the source text holds no path literal: the path is the file's name read
as a grammar and the verb is the name of each exported handler. Both come from
``ingestion.framework_routes``, shared with the graph-edge builder that uses the
same convention test to find files loaded without an import.

``action`` handles every verb that is not a GET and the file never says which,
so its route is recorded as ``*``, the marker the Django and Go dialects use for
a registration that names no method.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import (
    remix_route_path,
    remix_route_verbs,
)

from ..base import line_at
from ..langs import JS_TS
from .dialect import build_provider_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# `routes/` is an ordinary directory name and `loader`/`action` are ordinary
# export names, so the framework import is what says these files are served
# rather than imported. React Router v7's framework mode is Remix v2 renamed and
# uses the same convention, so both packages count. `react-router` matches as a
# whole specifier only: `react-router-dom` is the client-side router, and a
# single-page app built on it serves nothing.
_REMIX_IMPORT_RE = re.compile(r"""@remix-run/|react-router(?=["'/])""")

# The export each verb was read from, for the symbol the contract binds to.
_HANDLER_FOR_VERB = {"GET": "loader", "*": "action"}


class RemixDialect:
    name = "remix"
    extensions = JS_TS

    def extract(self, ctx: ScanContext) -> list[Contract]:
        # The path gate is the convention test: `remix_route_path` returns None
        # for anything that is not a route file, so the content is read only for
        # a file whose name already names a URL.
        path = remix_route_path(ctx.rel_path)
        if path is None:
            return []
        if not _REMIX_IMPORT_RE.search(ctx.content):
            return []
        out: list[Contract] = []
        for verb, offset in remix_route_verbs(ctx.content):
            # Bound by line, not by handler name: every route file in the repo
            # exports the same two names, so a repo-wide lookup by name would
            # bind to whichever one it reached first.
            c = build_provider_contract(
                ctx,
                method=verb,
                path_raw=path,
                framework="remix",
                line=line_at(ctx.content, offset),
                handler=_HANDLER_FOR_VERB[verb],
            )
            if c is not None:
                out.append(c)
        return out
