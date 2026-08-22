"""Go HTTP provider dialect — gin/echo/chi ``r.GET("/path", ...)`` and
stdlib ``.HandleFunc("/path", ...)`` (which carries no method, recorded as
``*``).

The route call is recognised by ``ingestion.framework_routes``, shared with the
graph-edge builder that reads the same call for its handler argument.

Route groups (``api := r.Group("/api"); v1 := api.Group("/v1")``) are resolved
transitively so a handler on ``v1`` records its full ``/api/v1/...`` path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import HTTP_METHODS, go_groups, go_routes

from ..base import line_at
from ..langs import GO
from .dialect import build_provider_contract
from .mounts import compose_prefix, group_prefixes

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# Registration selectors that carry no verb of their own.
_VERBLESS = ("HANDLE", "HANDLEFUNC")


class GoDialect:
    name = "go"
    extensions = GO

    def extract(self, ctx: ScanContext) -> list[Contract]:
        prefixes = group_prefixes(go_groups(ctx.content))
        out: list[Contract] = []
        for route in go_routes(ctx.content):
            if route.verb in _VERBLESS:
                method = "*"
            elif route.verb in HTTP_METHODS:
                method = route.verb
            else:
                continue  # gin's `Any`, and OPTIONS/HEAD, are not recorded here
            c = build_provider_contract(
                ctx,
                method=method,
                # An empty path under a group is still that group's route, so
                # the emptiness rule is `build_provider_contract`'s, after
                # stitching — the same order ASP.NET's MapGroup uses.
                path_raw=compose_prefix(
                    prefixes.get(route.receiver or "", ""), route.path or ""
                ),
                framework="go",
                line=line_at(ctx.content, route.offset),
                # A wrapped handler names the middleware, not the route's own
                # function, so binding by it would be wrong.
                handler=None if route.handler_call else route.handler,
            )
            if c is not None:
                out.append(c)
        return out
