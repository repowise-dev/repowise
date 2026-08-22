"""One recognition of a framework's route-registration syntax, two consumers.

``ingestion.framework_edges`` turns a route into a file-to-file graph edge, from
the handler expression; ``workspace.extractors.http`` turns the same route into
an HTTP contract, from the method and path. Each was matching the construct with
its own regex and carrying its own bug history, so a fix landed on one side only
(``MapGroup`` was missing from both, and ``[Route(...)]`` prefixes from one).

The shared thing is the *match*, not the result: a :class:`RouteMatch` carries
every part either consumer reads, and each builds its own output from the parts
it cares about. Argument semantics stay with the consumer — Express middleware
chains name several handlers, ASP.NET minimal APIs name at most one.

Placed under ``ingestion`` because the workspace layer imports from it and never
the other way round.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

#: Verbs the contract layer records as an HTTP method. A consumer that sees a
#: wider verb set (Express ``use``/``all``) filters against this.
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH"})


@dataclass(frozen=True)
class RouteMatch:
    """One route-registration call site."""

    verb: str  # upper-cased as written; not necessarily an HTTP method
    path: str | None  # raw route text, None when the call carries no literal
    receiver: str | None  # variable the call hangs off, for group-prefix lookup
    handler: str | None  # handler argument, when the shape names one identifier
    offset: int  # match start, for line and nearest-prefix lookups
    paren_offset: int  # the '(' opening the call, for an argument scan


@dataclass(frozen=True)
class GroupMatch:
    """One router-group binding — ``var v1 = api.MapGroup("/v1")``."""

    var: str
    parent: str | None
    prefix: str


def _opt(value: str | None) -> str | None:
    return value or None


# ---------------------------------------------------------------------------
# ASP.NET
# ---------------------------------------------------------------------------

def _cs_str(name: str) -> str:
    """A C# string-literal argument, capturing its body as *name*.

    The optional verbatim (``@``) / interpolated (``$``) prefix is taken from
    ``extractors.http.csharp_http`` — the only one of this tree's three C# route
    matchers that handled either. Single quotes stay accepted because both
    matchers this replaces took them, and narrowing would be a reduction.
    """
    return rf"\$?@?(?P<{name}_q>['\"])(?P<{name}>[^'\"]*)(?P={name}_q)"


# app.MapGet("/users", GetUsers). The receiver may be empty for a fluent chain
# (`.MapGroup("/api").MapGet(...)`). The handler must be followed by `,` or `)`:
# without that a lambda's own parameter is captured as the handler name —
# `MapPut("/z", async ctx => ...)` yields `async`, which the graph side merely
# failed to resolve but which the contract side would persist and bind by.
_ASPNET_MAP_RE = re.compile(
    r"(?P<receiver>\w*)\s*\.\s*Map(?P<verb>Get|Post|Put|Delete|Patch)\s*(?P<paren>\()"
    rf"\s*{_cs_str('path')}"
    r"(?:\s*,\s*(?P<handler>[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*)\s*(?=[,)]))?",
    re.IGNORECASE,
)

# var admin = app.MapGroup("/admin") — groups nest, so the parent is captured for
# transitive composition by `mounts.group_prefixes`.
_ASPNET_GROUP_RE = re.compile(
    rf"(?P<var>\w+)\s*=\s*(?P<parent>\w*)\s*\.\s*MapGroup\s*\(\s*{_cs_str('prefix')}",
    re.IGNORECASE,
)


def aspnet_routes(content: str) -> Iterator[RouteMatch]:
    """Minimal-API ``.MapGet("/path", Handler)`` calls in *content*."""
    for m in _ASPNET_MAP_RE.finditer(content):
        handler = m.group("handler")
        yield RouteMatch(
            verb=m.group("verb").upper(),
            path=m.group("path"),
            receiver=_opt(m.group("receiver")),
            handler=re.sub(r"\s+", "", handler) if handler else None,
            offset=m.start(),
            paren_offset=m.start("paren"),
        )


def aspnet_groups(content: str) -> Iterator[GroupMatch]:
    """``MapGroup`` prefix bindings in *content*."""
    for m in _ASPNET_GROUP_RE.finditer(content):
        yield GroupMatch(
            var=m.group("var"), parent=_opt(m.group("parent")), prefix=m.group("prefix")
        )


# ---------------------------------------------------------------------------
# Express / Node
# ---------------------------------------------------------------------------

# `use` and `all` are here because the graph consumer scans them for handler
# functions; the contract consumer drops them via HTTP_METHODS.
_EXPRESS_VERBS = "get|post|put|delete|patch|options|head|all|use"

# router.get('/path', handler). The path literal is optional — `app.use(router)`
# carries none. The lookbehind excludes a word character as well as `@`, which
# neither copy did: `\b` alone admits `@app.get` (a FastAPI/NestJS decorator, not
# an Express route), and `(?<!@)` alone lets the engine restart inside the name
# and match `pp.get` there instead.
_EXPRESS_ROUTE_RE = re.compile(
    rf"(?<![\w@])(?P<receiver>\w+)\s*\.\s*(?P<verb>{_EXPRESS_VERBS})\s*(?P<paren>\()"
    r"""\s*(?:(?P<q>['"])(?P<path>[^'"]*)(?P=q))?""",
    re.IGNORECASE,
)


def express_routes(content: str) -> Iterator[RouteMatch]:
    """Router/app route registrations in *content*.

    ``handler`` is always None: an Express call takes a middleware chain, so
    naming one identifier would be a guess. The graph consumer scans the
    argument list itself from ``paren_offset``.
    """
    for m in _EXPRESS_ROUTE_RE.finditer(content):
        yield RouteMatch(
            verb=m.group("verb").upper(),
            path=m.group("path"),
            receiver=m.group("receiver"),
            handler=None,
            offset=m.start(),
            paren_offset=m.start("paren"),
        )
