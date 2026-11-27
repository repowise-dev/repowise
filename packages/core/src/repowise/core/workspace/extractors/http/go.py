"""Go HTTP provider dialect — gin/echo ``r.GET("/path", ...)``, chi/fiber
``r.Get("/path", ...)``, gorilla/mux route chains, and stdlib
``.HandleFunc("/path", ...)`` (which carries no method, recorded as ``*``,
unless a Go 1.22 ``"GET /path"`` pattern or a gorilla ``.Methods(...)`` names one).

The route calls are recognised by ``ingestion.framework_routes`` and its
``framework_routes_chi`` / ``framework_routes_gorilla`` siblings, shared with
the graph-edge builder that reads the same calls for their handler argument.

Route groups (``api := r.Group("/api"); v1 := api.Group("/v1")``, gorilla
``PathPrefix(...).Subrouter()``) are resolved transitively, and chi/fiber
``Route("/api", func(r chi.Router) {...})`` closures by position, so a handler
records its full ``/api/v1/...`` path. A path built as ``base + "/x"`` is read
through the file's constants; a base the file does not settle (an environment
value) is the service's deployment prefix and is dropped, as a consumer's
leading base is.

A group handed to a register function (``articles.Register(v1.Group("/articles"))``)
serves that function's routes under the call site's prefix: see :mod:`.go_register`.
"""

from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import (
    GO_VERBLESS,
    HTTP_METHODS,
    go_groups,
    go_routes,
)
from repowise.core.ingestion.framework_routes_chi import route_scopes, title_case_routes
from repowise.core.ingestion.framework_routes_gorilla import gorilla_groups, gorilla_routes

from ..base import line_at
from ..calls import FileStrings, outside_comments
from ..langs import GO
from ..strings import GO_SYNTAX, call_arguments
from .dialect import build_provider_contract
from .go_register import RegisterPaths, route_prefix, router_args, unreadable_groups
from .mounts import compose_prefix, group_prefixes
from .paths import strip_leading_base_expr

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from repowise.core.ingestion.framework_routes import RouteMatch
    from repowise.core.ingestion.framework_routes_chi import RouteScope
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext


def _handler(route: RouteMatch) -> str | None:
    """A wrapped handler names the middleware, not the route's own function."""
    return None if route.handler_call else route.handler


def _method(verb: str) -> str | None:
    """The recorded method for *verb*: ``*`` when verbless; gin's ``Any``, OPTIONS and HEAD are not recorded."""
    if verb in GO_VERBLESS:
        return "*"
    return verb if verb in HTTP_METHODS else None


def _expression_path(route: RouteMatch, strings: FileStrings) -> str | None:
    """The path a ``base + "/x"`` argument denotes, or ``None`` when it is not settled."""
    args = call_arguments(strings.content, route.paren_offset)
    text = strings.text(args[0]) if args else None
    if text is None:
        return None
    path, _base = strip_leading_base_expr(text)
    return None if "${" in path else path


class GoDialect:
    name = "go"
    extensions = GO
    # The verb opens its call directly, as gofmt writes it; a chain may break
    # the line after the receiver's `.`. chi, fiber and gorilla calls are read
    # only in a file importing the router.
    requires = (
        "GET(", "POST(", "PUT(", "DELETE(", "PATCH(", "OPTIONS(", "HEAD(", "Any(", "Handle(",
        "HandleFunc(", "github.com/go-chi/chi", "github.com/gofiber/fiber",
        "github.com/gorilla/mux",
    )  # fmt: skip
    # A file handing a router to a register function may hold no route of its own.
    mount_requires = (
        "Group(", "github.com/gin-gonic/gin", "github.com/labstack/echo",
        "github.com/go-chi/chi", "github.com/gofiber/fiber", "github.com/gorilla/mux",
    )  # fmt: skip

    def collect_mounts(self, ctx: ScanContext) -> dict[str, str]:
        return router_args(ctx)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        groups = [*go_groups(content), *gorilla_groups(content)]
        refused = unreadable_groups(groups)
        prefixes = group_prefixes(g for g in groups if g.var not in refused)
        scopes = route_scopes(content)
        strings = FileStrings(content, GO_SYNTAX)
        routes = chain(go_routes(content), title_case_routes(content), gorilla_routes(content))
        register = RegisterPaths.for_file(ctx)
        out: list[Contract] = []
        for route in outside_comments(ctx, routes):
            if ctx.in_string(route.offset):
                continue  # another program's source, e.g. a test's raw-string fixture
            method = _method(route.verb)
            if method is None or route.receiver in refused:
                continue  # an unrecorded verb, or a prefix this cannot read
            path = route.path if route.path is not None else _expression_path(route, strings)
            if path is None:
                continue
            # An empty path under a group is still that group's route, so the
            # emptiness rule is `build_provider_contract`'s, after stitching —
            # the same order ASP.NET's MapGroup uses.
            for path_raw in _paths(route, path, scopes, prefixes, register):
                c = build_provider_contract(
                    ctx,
                    method=method,
                    path_raw=path_raw,
                    framework="go",
                    line=line_at(content, route.offset),
                    handler=_handler(route),
                )
                if c is not None:
                    out.append(c)
        return out


def _paths(
    route: RouteMatch,
    path: str,
    scopes: Sequence[RouteScope],
    prefixes: Mapping[str, str],
    register: RegisterPaths | None,
) -> list[str]:
    """The paths *route* serves: under the groups its function is handed, else its own."""
    served = register.paths(route, path, scopes) if register is not None else None
    if served is not None:
        return served
    return [
        compose_prefix(route_prefix(route.receiver or "", route.offset, scopes, prefixes), path)
    ]
