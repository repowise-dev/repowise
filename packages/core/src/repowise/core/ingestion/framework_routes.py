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
    #: The handler expression is itself invoked (``api.RequireSession(h)``), so
    #: it names a wrapper. The graph consumer still wants the edge to it; the
    #: contract consumer must not bind a route to its middleware.
    handler_call: bool = False


@dataclass(frozen=True)
class GroupMatch:
    """One router-group binding — ``var v1 = api.MapGroup("/v1")``."""

    var: str
    parent: str | None
    prefix: str


def _opt(value: str | None) -> str | None:
    return value or None


def _groups(pattern: re.Pattern[str], content: str) -> Iterator[GroupMatch]:
    """Every ``var``/``parent``/``prefix`` match of *pattern* in *content*."""
    for m in pattern.finditer(content):
        yield GroupMatch(
            var=m.group("var"), parent=_opt(m.group("parent")), prefix=m.group("prefix")
        )


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
    return _groups(_ASPNET_GROUP_RE, content)


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


# ---------------------------------------------------------------------------
# Shared scanning helper
# ---------------------------------------------------------------------------


def match_paren(text: str, open_idx: int, quotes: str = "\"'`") -> int:
    """Index of the ``)`` closing the ``(`` at *open_idx*, or -1.

    *quotes* is what opens a string literal, so a paren inside one is ignored.
    Rust must pass ``'"'``: its ``'`` is a lifetime, not a quote.
    """
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        c = text[i]
        if c in quotes:
            i += 1
            while i < n and text[i] != c:
                i += 2 if text[i] == "\\" else 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


# ---------------------------------------------------------------------------
# Go — gin / echo / chi routers and stdlib net/http
# ---------------------------------------------------------------------------

# `HandleFunc` precedes `Handle` so the longer name wins the alternation.
_GO_VERBS = "GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD|Any|HandleFunc|Handle"

# r.GET("/users", GetUsers). The receiver may be empty on an inline chain. A
# wrapped handler (`api.RequireSession(h)`) is admitted and flagged, but `func`
# never is: it is the only way Go spells an inline closure, and without the
# exclusion `r.GET("/x", func(c *gin.Context) {...})` yields the handler `func`.
_GO_ROUTE_RE = re.compile(
    rf"(?P<receiver>\w*)\s*\.\s*(?P<verb>{_GO_VERBS})\s*(?P<paren>\()"
    r"""\s*(?P<q>["'])(?P<path>[^"']*)(?P=q)"""
    r"(?:\s*,\s*(?P<handler>(?!func\b)[A-Za-z_][\w.]*)\s*(?P<after>[,)(]))?"
)

# api := r.Group("/v1") — groups nest, so the parent is captured for transitive
# composition by `mounts.group_prefixes`.
_GO_GROUP_RE = re.compile(
    r"""(?P<var>\w+)\s*:?=\s*(?P<parent>\w+)\s*\.\s*Group\s*\(\s*["'](?P<prefix>[^"']+)["']"""
)


def go_routes(content: str) -> Iterator[RouteMatch]:
    """Router and ``net/http`` handler registrations in *content*."""
    for m in _GO_ROUTE_RE.finditer(content):
        yield RouteMatch(
            verb=m.group("verb").upper(),
            path=m.group("path"),
            receiver=_opt(m.group("receiver")),
            handler=m.group("handler"),
            offset=m.start(),
            paren_offset=m.start("paren"),
            handler_call=m.group("after") == "(",
        )


def go_groups(content: str) -> Iterator[GroupMatch]:
    """``Group`` prefix bindings in *content*."""
    return _groups(_GO_GROUP_RE, content)


# ---------------------------------------------------------------------------
# Laravel
# ---------------------------------------------------------------------------

# `resource`/`apiResource`/`any`/`match` name no single verb; the contract
# consumer drops them, the graph consumer wants their controller.
_LARAVEL_VERBS = "get|post|put|patch|delete|any|match|resource|apiResource"

_LARAVEL_CALL_RE = re.compile(
    rf"Route::(?P<verb>{_LARAVEL_VERBS})\s*(?P<paren>\()", re.IGNORECASE
)

# The path literal, when the first argument is one. It often is not:
# `Route::post('/hook/'.config('x'), ...)` truncates to the literal head, which
# is what the matcher this replaces recorded too.
_LARAVEL_PATH_RE = re.compile(r"""\s*(?P<q>["'])(?P<path>[^"']*)(?P=q)""")

# Three handler spellings co-exist: the array form, the legacy
# 'Controller@method' string, and the bare `Controller::class` of a resource
# route. The array branch is first so `[C::class, 'm']` is not read as bare.
_LARAVEL_HANDLER_RE = re.compile(
    r"\[\s*(?P<array>[\w\\]+)\s*::\s*class"
    r"""|["'](?P<legacy>[\w\\]+)@\w+["']"""
    r"|(?P<cls>[\w\\]+)\s*::\s*class"
)


def laravel_routes(content: str) -> Iterator[RouteMatch]:
    """``Route::verb(...)`` registrations in *content*.

    The arguments are delimited by the call's own parens: a route's path is
    routinely a concatenation containing a call of its own, which a scan to the
    next comma reads wrongly and a scan to the next paren stops short of.

    ``handler`` is the controller class only; the member name the array form
    also carries is dropped, since the graph consumer links to the class's file.
    """
    for m in _LARAVEL_CALL_RE.finditer(content):
        close = match_paren(content, m.start("paren"))
        if close == -1:
            continue
        args = content[m.end() : close]
        path = _LARAVEL_PATH_RE.match(args)
        handler = _LARAVEL_HANDLER_RE.search(args, path.end() if path else 0)
        yield RouteMatch(
            verb=m.group("verb").upper(),
            path=path.group("path") or None if path else None,
            receiver=None,
            handler=(
                handler.group("array") or handler.group("legacy") or handler.group("cls")
                if handler
                else None
            ),
            offset=m.start(),
            paren_offset=m.start("paren"),
        )


# ---------------------------------------------------------------------------
# Rust — axum
# ---------------------------------------------------------------------------

# The head of `.route("/path", <method router>)`.
_AXUM_ROUTE_HEAD_RE = re.compile(
    r"""\.\s*route\s*(?P<paren>\()\s*(?P<q>["'])(?P<path>[^"']+)(?P=q)\s*,"""
)

# A verb inside the method-router argument. `on` names no literal verb
# (`on(MethodFilter::GET, h)`); the contract consumer drops it. Requiring the
# closing paren is what makes a `get(|| async {...})` closure yield no handler.
_AXUM_METHOD_RE = re.compile(
    r"\b(?P<verb>get|post|put|delete|patch|head|options|trace|on)\s*\("
    r"\s*(?:(?P<handler>[\w:]+)\s*\))?"
)


def axum_routes(content: str) -> Iterator[RouteMatch]:
    """Axum ``.route(...)`` registrations in *content*, one per verb.

    A method router chains verbs (``get(list).post(create)``), so one call site
    yields several matches sharing a path and an offset. The verb scan is bounded
    by the route call's own parens, so a multi-line route is still read.
    """
    for head in _AXUM_ROUTE_HEAD_RE.finditer(content):
        close = match_paren(content, head.start("paren"), quotes='"')
        if close == -1:
            continue  # truncated call; scanning to EOF would credit it every verb
        region = content[head.end() : close]
        for m in _AXUM_METHOD_RE.finditer(region):
            yield RouteMatch(
                verb=m.group("verb").upper(),
                path=head.group("path"),
                receiver=None,
                handler=m.group("handler"),
                offset=head.start(),
                paren_offset=head.start("paren"),
            )
