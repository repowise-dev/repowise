"""aiohttp server HTTP provider dialect: ``web.get``, ``@routes.get`` and ``router.add_get``.

Three spellings register a route with a literal path:

* ``web.get("/users", handler)`` inside ``app.add_routes([...])``, and
  ``web.route("GET", "/users", handler)``;
* ``@routes.get("/users")`` on a ``web.RouteTableDef()`` binding;
* ``app.router.add_get("/users", handler)`` and ``add_route("GET", ...)``.

``web.view`` and ``add_view`` name no method and are recorded as ``*``.

Ceilings: ``add_subapp("/api", sub)`` mounts are not followed, so a sub-application's
routes are published without their prefix; ``add_resource("/x").add_route(...)``
and a path held in a variable are not read.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, NamedTuple

from ..base import line_at
from ..calls import outside_comments
from ..langs import PYTHON
from .dialect import METHODS, build_provider_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_VERBS = rf"{METHODS}|head|options"
_PATH = r"""\s*(?:r|u)?(?P<q>['"])(?P<path>[^'"]*)(?P=q)"""

# ``web.get("/p", h)`` and ``web.view("/p", h)``.
_WEB_VERB_RE = re.compile(rf"(?<![\w.])web\s*\.\s*(?P<verb>{_VERBS}|view)\s*\({_PATH}", re.IGNORECASE)
# ``web.route("GET", "/p", h)`` and ``router.add_route("GET", "/p", h)``.
_ROUTE_RE = re.compile(
    r"""(?:(?<![\w.])web\s*\.\s*route|\.\s*add_route)\s*\(\s*(?P<vq>['"])(?P<verb>\w+|\*)(?P=vq)\s*,"""
    + _PATH
)
# ``app.router.add_get("/p", h)`` and ``router.add_view("/p", h)``.
_ADD_RE = re.compile(rf"\.\s*add_(?P<verb>{_VERBS}|view)\s*\({_PATH}", re.IGNORECASE)
# ``routes = web.RouteTableDef()``, then ``@routes.get("/p")``.
_TABLE_RE = re.compile(r"^[ \t]*(?P<var>[A-Za-z_]\w*)\s*=\s*web\s*\.\s*RouteTableDef\s*\(", re.MULTILINE)


class _Route(NamedTuple):
    method: str
    path: str
    offset: int


def _method(verb: str) -> str:
    return "*" if verb.lower() in ("view", "*") else verb.upper()


def _routes(content: str) -> list[_Route]:
    found = [
        _Route(_method(m.group("verb")), m.group("path"), m.start())
        for regex in (_WEB_VERB_RE, _ROUTE_RE, _ADD_RE)
        for m in regex.finditer(content)
    ]
    for table in _TABLE_RE.finditer(content):
        decorator = re.compile(
            rf"@\s*{re.escape(table.group('var'))}\s*\.\s*(?P<verb>{_VERBS}|view)\s*\({_PATH}",
            re.IGNORECASE,
        )
        found += [
            _Route(_method(m.group("verb")), m.group("path"), m.start())
            for m in decorator.finditer(content)
        ]
    return found


class AiohttpWebDialect:
    name = "aiohttp"
    extensions = PYTHON
    requires = ("aiohttp",)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        for route in outside_comments(ctx, _routes(ctx.content)):
            c = build_provider_contract(
                ctx,
                method=route.method,
                path_raw=route.path,
                framework="aiohttp",
                line=line_at(ctx.content, route.offset),
            )
            if c is not None:
                out.append(c)
        return out
