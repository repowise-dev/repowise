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


def _next_top_level_comma(args: str, *, hash_comments: bool = False) -> int:
    """Index just past the first comma separating *args*, or -1."""
    for i, c, depth in scan_code(args, hash_comments=hash_comments):
        if c == "," and depth == 0:
            return i + 1
    return -1


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


# What binds a variable to a router object, per framework. Hono, Fastify, Koa
# and Elysia all serve routes through Express's ``<var>.get('/path', handler)``
# DSL, so ``express_routes`` already matches their call sites; only the binding
# ever told them apart, and each consumer spelled that out for Express alone
# (``_EXPRESS_RECEIVER_RE`` graph-side, ``_ROUTER_BIND_RE`` contract-side, with
# different reach). One table, so a route is attributed to the framework that
# serves it rather than to whichever recogniser claimed the variable first.
_JS_ROUTER_CTORS: tuple[tuple[str, str], ...] = (
    # express(), express.Router(), require('express').Router(), bare Router().
    # The dotted prefix is `[\w$.]*` so a router built off a nested namespace
    # (`services.http.Router()`) still binds, as it did before this table.
    ("express", r"(?:[\w$.]*\s*\.\s*)?Router\s*\(|express\s*\("),
    ("hono", r"new\s+Hono\s*[(<]"),
    ("fastify", r"[Ff]astify\s*\("),
    ("koa", r"new\s+[Kk]oa\s*\("),
    ("elysia", r"new\s+Elysia\s*[(<]"),
)

_JS_ROUTER_BIND_RE = re.compile(
    r"(?<![\w$])(?P<var>[\w$]+)\s*(?::[^=;\n]+)?=\s*(?:"
    + "|".join(f"(?P<{name}>{alt})" for name, alt in _JS_ROUTER_CTORS)
    + r")"
)

#: Names conventionally holding a router where no binding is in scope, because
#: the framework instance is created in another file.
JS_DEFAULT_ROUTER_NAMES = frozenset({"app", "router"})


def js_router_bindings(content: str) -> dict[str, str]:
    """Every variable bound to a router in *content*, mapped to its framework."""
    out: dict[str, str] = {}
    for m in _JS_ROUTER_BIND_RE.finditer(content):
        for name, _alt in _JS_ROUTER_CTORS:
            if m.group(name) is not None:
                out[m.group("var")] = name
                break
    return out


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
# Shared scanning
# ---------------------------------------------------------------------------


def scan_code(
    text: str,
    start: int = 0,
    *,
    quotes: str = "\"'`",
    hash_comments: bool = False,
    text_blocks: bool = False,
) -> Iterator[tuple[int, str, int]]:
    """``(index, char, paren_depth)`` over *text*, skipping strings and comments.

    Route calls run across lines and carry commented-out arguments, so a scanner
    that reads a comment as code is worse than one that reads a line: an
    apostrophe in ``// won't work`` opens a string that never closes.

    *quotes* is what opens a string literal — Rust must pass ``'"'``, because its
    ``'`` is a lifetime. *hash_comments* adds PHP's ``#``, excluding the ``#[``
    of an attribute. *text_blocks* adds Java's ``\"\"\"``, which pairwise quote
    matching otherwise reads as an empty string followed by an unterminated one,
    swallowing the rest of the file at the first quote inside the block.
    """
    depth = 0
    i, n = start, len(text)
    while i < n:
        c = text[i]
        if text_blocks and text[i : i + 3] == '"""':
            end = text.find('"""', i + 3)
            i = n if end == -1 else end + 3
            continue
        if c in quotes:
            i += 1
            while i < n and text[i] != c:
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            i = text.find("\n", i)
            if i == -1:
                return
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            if end == -1:
                return
            i = end + 2
            continue
        if hash_comments and c == "#" and text[i + 1 : i + 2] != "[":
            i = text.find("\n", i)
            if i == -1:
                return
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        yield i, c, depth
        i += 1


def match_paren(
    text: str, open_idx: int, *, quotes: str = "\"'`", hash_comments: bool = False
) -> int:
    """Index of the ``)`` closing the ``(`` at *open_idx*, or -1."""
    for i, c, depth in scan_code(text, open_idx, quotes=quotes, hash_comments=hash_comments):
        if c == ")" and depth == 0:
            return i
    return -1


# ---------------------------------------------------------------------------
# Go — gin / echo / chi routers and stdlib net/http
# ---------------------------------------------------------------------------

# `HandleFunc` precedes `Handle` so the longer name wins the alternation.
_GO_VERBS = "GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD|Any|HandleFunc|Handle"

# r.GET("/users", GetUsers). The receiver may be empty on an inline chain. The
# handler may be wrapped (`api.RequireSession(h)`) or a composite literal
# (`healthHandler{}`). The two keywords that can open one are excluded: they name
# no symbol, and `r.GET("/x", func(c *gin.Context) {...})` otherwise yields the
# handler `func`.
_GO_ROUTE_RE = re.compile(
    rf"(?P<receiver>\w*)\s*\.\s*(?P<verb>{_GO_VERBS})\s*(?P<paren>\()"
    r"""\s*(?P<q>["'])(?P<path>[^"']*)(?P=q)"""
    r"(?:\s*,\s*(?P<handler>(?!(?:func|struct)\b)[A-Za-z_][\w.]*)\s*(?P<after>[,)({]))?"
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
        close = match_paren(content, m.start("paren"), hash_comments=True)
        if close == -1:
            continue
        args = content[m.end() : close]
        path = _LARAVEL_PATH_RE.match(args)
        # Only past the first top-level comma: the path expression can itself
        # contain `X::class` or a quoted 'word@word', neither of which is the
        # handler (`Route::get(trans('Contact@us'), [PageController::class, ...])`).
        second = _next_top_level_comma(args, hash_comments=True)
        handler = _LARAVEL_HANDLER_RE.search(args, second) if second != -1 else None
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
        # Only verbs the method router itself chains. A verb nested in another
        # call's arguments is not one: `get(h).with_state(state.options())`
        # would otherwise fabricate an OPTIONS route.
        for i, _c, depth in scan_code(region, quotes='"'):
            if depth != 0:
                continue
            m = _AXUM_METHOD_RE.match(region, i)
            if m is None:
                continue
            yield RouteMatch(
                verb=m.group("verb").upper(),
                path=head.group("path"),
                receiver=None,
                handler=m.group("handler"),
                offset=head.start(),
                paren_offset=head.start("paren"),
            )


# ---------------------------------------------------------------------------
# Django — urls.py
# ---------------------------------------------------------------------------

# path("users/<int:pk>/", views.detail) / re_path(r"^users/$", View.as_view()) /
# path("api/", include("api.urls")) — one call shape, split by the handler below.
# A URLconf entry names no verb (the view chooses), so every match is `*`.
_DJANGO_ENTRY_RE = re.compile(
    r"(?<![\w.])(?P<verb>re_path|path|url)\s*(?P<paren>\()"
    r"""\s*(?:r|rb|b)?(?P<q>['"])(?P<path>[^'"]*)(?P=q)\s*,\s*"""
    r"(?P<handler>[\w.]+)\s*(?P<after>[,)(])"
)

_DJANGO_INCLUDE_ARG_RE = re.compile(
    r"""\s*(?:(?P<q>['"])(?P<mod>[^'"]+)(?P=q)|(?P<expr>[\w.]+))"""
)


def _django_path(raw: str, verb: str) -> str:
    """A URLconf pattern as a path.

    Both spellings of a capture become ``{name}``: ``path()``'s
    ``<converter:name>`` and ``re_path()``'s ``(?P<name>...)``, whose anchors go
    with it. ``normalize_http_path`` reads neither, so a path left as written
    would carry the converter into the contract id.
    """
    if verb == "path":
        return re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", raw)
    raw = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"{\1}", raw)
    return raw.strip("^$")


def django_routes(content: str) -> Iterator[RouteMatch]:
    """URLconf entries in *content* that name a view.

    ``verb`` is always ``*``; ``handler`` is the view expression as written. An
    ``include(...)`` entry is not a route — see :func:`django_includes`.
    """
    for m in _DJANGO_ENTRY_RE.finditer(content):
        handler = m.group("handler")
        if handler == "include":
            continue
        yield RouteMatch(
            verb="*",
            path=_django_path(m.group("path"), m.group("verb")),
            receiver=None,
            handler=handler,
            offset=m.start(),
            paren_offset=m.start("paren"),
            # `path("x/", login_required(view))` names a decorator, not the view.
            handler_call=m.group("after") == "(",
        )


def django_includes(content: str) -> Iterator[tuple[str, str]]:
    """``(prefix, module)`` per ``include(...)`` entry — dotted as written."""
    for m in _DJANGO_ENTRY_RE.finditer(content):
        if m.group("handler") != "include":
            continue
        arg = _DJANGO_INCLUDE_ARG_RE.match(content, m.end("after"))
        module = (arg.group("mod") or arg.group("expr")) if arg else None
        if module:
            yield _django_path(m.group("path"), m.group("verb")), module


# ---------------------------------------------------------------------------
# JAX-RS — Jakarta EE, Quarkus, Jersey, RESTEasy, Dropwizard
# ---------------------------------------------------------------------------

# A JAX-RS route is two annotations: @GET names the verb, an optional @Path
# names the sub-path, and the class's own @Path is the prefix. The graph side
# recognised these as substrings only, to stamp a role; nothing read the paths.
_JAXRS_VERBS = "GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS"

# `@PathParam` is not a match: the `(` is required.
_JAXRS_PATH_RE = re.compile(r"""@Path\s*\(\s*(?:value\s*=\s*)?["'](?P<path>[^"']*)["']""")

_JAXRS_VERB_RE = re.compile(rf"@(?P<verb>{_JAXRS_VERBS})\b")

_JAXRS_TYPE_RE = re.compile(
    r"^[^\S\n]*(?:public|final|abstract|open|internal|sealed|data|\s)*"
    r"(?:class|interface)\s+\w+",
    re.MULTILINE,
)


def _jaxrs_decl_end(content: str, start: int) -> int:
    """End of the declaration annotated at *start* — its ``{`` or ``;``.

    Scanned rather than matched: an annotation run carries parens of its own
    (``@Produces(MediaType.APPLICATION_JSON)``) and swagger nests them several
    deep, so anything that stops at the first paren stops inside the run.
    """
    for i, c, depth in scan_code(content, start, quotes='"', text_blocks=True):
        if depth == 0 and c in ";{":
            return i
    return len(content)


def jaxrs_class_paths(content: str) -> list[tuple[int, str]]:
    """``(offset, prefix)`` per type-level ``@Path``, ascending.

    A class and its methods share the annotation, so the only thing separating a
    prefix from a sub-path is which declaration the annotation run reaches.
    """
    types = [m.start() for m in _JAXRS_TYPE_RE.finditer(content)]
    out: list[tuple[int, str]] = []
    for m in _JAXRS_PATH_RE.finditer(content):
        end = _jaxrs_decl_end(content, m.start())
        if any(m.start() < t < end for t in types):
            out.append((m.start(), m.group("path").rstrip("/")))
    return out


def jaxrs_routes(content: str) -> Iterator[RouteMatch]:
    """Verb-annotated resource methods in *content*.

    ``path`` is the method's own ``@Path`` or ``""``; the class prefix is
    stitched on by the consumer, the only side that knows how to compose one.
    """
    for m in _JAXRS_VERB_RE.finditer(content):
        end = _jaxrs_decl_end(content, m.start())
        sub = _JAXRS_PATH_RE.search(content, m.end(), end)
        yield RouteMatch(
            verb=m.group("verb").upper(),
            path=sub.group("path") if sub else "",
            receiver=None,
            handler=None,
            offset=m.start(),
            paren_offset=m.start(),
        )


# ---------------------------------------------------------------------------
# Next.js App Router
# ---------------------------------------------------------------------------

#: App Router files loaded by filesystem convention rather than by import.
NEXT_APP_ROUTER_BASENAMES: frozenset[str] = frozenset({
    "page", "layout", "route", "middleware", "template", "default",
    "error", "loading", "not-found", "global-error", "forbidden",
    "unauthorized", "instrumentation",
})
NEXT_APP_ROUTER_EXTS: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".mjs")

_NEXT_APP_DIR_RE = re.compile(r"(?:^|/)app/")

# Segments naming no URL segment: route group `(marketing)`, parallel route
# `@modal`, private folder `_lib`.
_NEXT_INERT_SEG_RE = re.compile(r"\(.*\)|@.*|_.*")

# `[id]`, `[...slug]`, `[[...slug]]` -> `{id}` / `{slug}`.
_NEXT_DYNAMIC_SEG_RE = re.compile(r"^\[+\.{0,3}(?P<name>[^\]]+)\]+$")

# The App Router takes the verb from the exported name and the path from the
# file's location, so a route handler's text holds no path literal at all.
_NEXT_HANDLER_RE = re.compile(
    r"export\s+(?:async\s+)?(?:function\s+|const\s+|let\s+|var\s+)"
    r"(?P<verb>GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b"
)


def next_app_router_file(rel_path: str) -> bool:
    """True when *rel_path* is loaded by App Router filesystem convention."""
    if not _NEXT_APP_DIR_RE.search(rel_path):
        return False
    name = rel_path.rsplit("/", 1)[-1]
    for ext in NEXT_APP_ROUTER_EXTS:
        if name.endswith(ext) and name[: -len(ext)] in NEXT_APP_ROUTER_BASENAMES:
            return True
    return False


def next_route_path(rel_path: str) -> str | None:
    """The URL an ``app/**/route.ts`` handler serves, else None.

    Only ``route.*`` is an endpoint; ``page``/``layout`` and the rest render UI
    and publish no API surface.
    """
    if not next_app_router_file(rel_path):
        return None
    parts = rel_path.split("/")
    if parts[-1].rsplit(".", 1)[0] != "route":
        return None
    app_at = len(parts) - 2 - parts[-2::-1].index("app")  # the nearest `app/`
    segments: list[str] = []
    for seg in parts[app_at + 1 : -1]:
        if _NEXT_INERT_SEG_RE.fullmatch(seg):
            continue
        dyn = _NEXT_DYNAMIC_SEG_RE.match(seg)
        segments.append("{" + dyn.group("name") + "}" if dyn else seg)
    return "/" + "/".join(segments)


def next_route_verbs(content: str) -> list[tuple[str, int]]:
    """``(verb, offset)`` per exported handler, in declaration order."""
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for m in _NEXT_HANDLER_RE.finditer(content):
        verb = m.group("verb")
        if verb not in seen:
            seen.add(verb)
            out.append((verb, m.start("verb")))
    return out
