"""Ktor server HTTP provider dialect — ``routing { route("/api") { get("{id}") { } } }``.

A Ktor route is a bare call with a trailing handler block: ``get("/x") { }``,
or ``get { }`` with no path, which serves the enclosing route. ``route("/p")``
blocks nest prefixes, and the prefix a verb serves under is every ``route``
block around it, by lambda scope. Only a verb inside ``routing { }`` or
``install(Routing) { }`` has a known root; a verb with no path straight under
it serves ``/``. In an extension function such as ``fun Route.users()``, even
inside a ``route`` block, the prefix is wherever that function is called,
which this file cannot see, so it is refused.

The receiver is what keeps the Ktor client out: a request is a dotted call on
a client value (``client.get("/x")``), a route is a bare call on the implicit
``Route`` receiver. Ktor's typed ``get<Resource> { }`` names no path and is
skipped by the same shape, as is a ``route(path, HttpMethod.X)`` block whose
verb sits in an inner ``handle { }``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import HTTP_METHODS

from ..base import line_at
from ..calls import FileStrings, code_matches
from ..langs import KOTLIN
from ..strings import KOTLIN_SYNTAX, call_arguments, match_paren
from .dialect import build_provider_contract
from .lambda_routes import (
    PrefixScope,
    block_end,
    enclosing_prefix,
    is_call_site,
    trailing_block,
)
from .mounts import compose_prefix

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# The routing DSL's package. A file that never names it declares no routes.
_KTOR_ROUTING = "io.ktor.server.routing"

# A bare call of the DSL: `routing {`, `route("/p") {`, `get("/x") {`, `get {`.
# The look-behind drops a dotted call, which is a client request or a map read.
_CALL_RE = re.compile(r"(?<![\w.])(routing|route|get|post|put|patch|delete|head|options)\s*([({])")
# The plugin form of `routing { }`.
_INSTALL_RE = re.compile(r"(?<![\w.])install\s*\(\s*Routing\s*\)\s*\{")


@dataclass(frozen=True)
class _Call:
    """One DSL call and the block it opens.

    *path* is ``""`` when the call names none, and ``None`` when it names one
    this file cannot read.
    """

    name: str
    offset: int
    path: str | None
    n_args: int
    scope: PrefixScope


def _read_call(content: str, strings: FileStrings, m: re.Match[str]) -> _Call | None:
    """The call *m* opens, or ``None`` when it is not one that opens a block."""
    if not is_call_site(content, m.start()):
        return None
    args: list[str] = []
    block = m.end() - 1
    if m.group(2) == "(":
        close = match_paren(content, block)
        if close < 0:
            return None
        args = call_arguments(content, block, close) or []
        block = trailing_block(content, close + 1)
    end = block_end(content, block) if block >= 0 else -1
    if end < 0:
        return None  # a route always opens its handler or nested block
    path = strings.text(args[0]) if args else ""
    if path is not None and "${" in path:
        path = None
    return _Call(m.group(1), m.start(), path, len(args), PrefixScope(block, end, path))


def _within(scopes: list[PrefixScope], offset: int) -> bool:
    return any(s.start < offset < s.end for s in scopes)


def _served_path(call: _Call, routes: list[PrefixScope], routing: list[PrefixScope]) -> str | None:
    """The path *call* serves, or ``None`` when it is not a route this file can place."""
    if call.path is None or call.n_args > 1 or call.name.upper() not in HTTP_METHODS:
        return None
    if not _within(routing, call.offset):
        return None  # not under routing { } or install(Routing) { }: the prefix is the call site's
    prefix = enclosing_prefix(routes, call.offset)
    if prefix is None:
        return None
    # `get { }` straight under `routing { }` serves the root.
    return compose_prefix(prefix, call.path) or "/"


class KtorServerDialect:
    name = "ktor-server"
    extensions = KOTLIN
    requires = (_KTOR_ROUTING,)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        strings = FileStrings(content, KOTLIN_SYNTAX)
        calls = [c for m in code_matches(ctx, _CALL_RE) if (c := _read_call(content, strings, m))]
        routes = [c.scope for c in calls if c.name == "route"]
        routing = [c.scope for c in calls if c.name == "routing"]
        for m in code_matches(ctx, _INSTALL_RE):
            routing.append(PrefixScope(m.end() - 1, block_end(content, m.end() - 1), ""))

        out: list[Contract] = []
        for call in calls:
            path = _served_path(call, routes, routing)
            if path is None:
                continue
            c = build_provider_contract(
                ctx,
                method=call.name.upper(),
                path_raw=path,
                framework="ktor",
                line=line_at(content, call.offset),
            )
            if c is not None:
                out.append(c)
        return out
