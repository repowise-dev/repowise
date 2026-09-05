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
    """One router-group binding — ``var v1 = api.MapGroup("/v1")``.

    ``prefix`` is ``None`` when the call declares one this cannot read, which is
    not the same as declaring none: a consumer must refuse the routes under such
    a group rather than serve them at the group's own prefix.
    """

    var: str
    parent: str | None
    prefix: str | None


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
    ``<converter:name>``, which is Flask's rule and shares its rewrite, and
    ``re_path()``'s ``(?P<name>...)``, whose anchors go with it.
    ``normalize_http_path`` reads neither, so a path left as written would carry
    the converter into the contract id.
    """
    if verb == "path":
        return flask_path(raw)
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

# A named export opening its own line. Only whitespace may precede it, so a
# commented-out handler declares nothing. Shared with Remix, which reads the
# same declaration for a different set of names.
_EXPORT_HEAD = r"^[^\S\n]*(?P<export>export)\s+(?:async\s+)?(?:function\s+|const\s+|let\s+|var\s+)"


def _exported_names(content: str, pattern: re.Pattern[str]) -> list[tuple[str, int]]:
    """``(name, offset)`` per exported handler, first declaration of each.

    *offset* is the ``export`` keyword, which is where the declaration starts
    and so the line a contract binds to.
    """
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for m in pattern.finditer(content):
        name = m.group("name")
        if name not in seen:
            seen.add(name)
            out.append((name, m.start("export")))
    return out


# The App Router takes the verb from the exported name and the path from the
# file's location, so a route handler's text holds no path literal at all.
_NEXT_HANDLER_RE = re.compile(
    _EXPORT_HEAD + r"(?P<name>GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b",
    re.MULTILINE,
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
    return _exported_names(content, _NEXT_HANDLER_RE)


# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------

# @app.route("/users/<int:id>", methods=["GET", "POST"]) plus Flask 2's
# verb-named shortcuts. `route` carries its verbs in the call rather than in the
# decorator name, so the verb is read from the argument text for that spelling.
# The decorator must open its own line: only whitespace may precede it, so a
# commented-out route and one written in a trailing comment are both inert.
_FLASK_ROUTE_RE = re.compile(
    r"^[^\S\n]*(?P<at>@)(?P<receiver>\w+)\s*\.\s*"
    r"(?P<verb>route|get|post|put|patch|delete)\s*(?P<paren>\()"
    r"""\s*(?:r|rb|b)?(?P<q>['"])(?P<path>[^'"]*)(?P=q)""",
    re.MULTILINE,
)

# `methods=["GET", "POST"]`, in either bracket style Flask accepts.
_FLASK_METHODS_RE = re.compile(r"methods\s*=\s*[\[(](?P<verbs>[^\])]*)[\])]")
_FLASK_METHOD_LITERAL_RE = re.compile(r"""['"](\w+)['"]""")

# The keyword itself, whatever its value. Seeing one the literal pattern above
# could not read means the route serves verbs this file does not spell.
_FLASK_METHODS_KW_RE = re.compile(r"\bmethods\s*=")

# app.register_blueprint(bp, url_prefix="/users"). The receiver is captured
# because Flask 2 lets a blueprint register another blueprint under itself.
_FLASK_REGISTER_RE = re.compile(
    r"(?:(?P<parent>\w+)\s*\.\s*)?register_blueprint\s*(?P<paren>\()\s*(?P<var>\w[\w.]*)"
)
_FLASK_URL_PREFIX_RE = re.compile(r"""url_prefix\s*=\s*['"](?P<prefix>[^'"]*)['"]""")

# The keyword itself, whatever its value: one the literal pattern above could
# not read mounts the blueprint somewhere this file does not spell.
_FLASK_URL_PREFIX_KW_RE = re.compile(r"\burl_prefix\s*=")

# A converter, with or without arguments: `<int:id>`, `<id>`, `<path:rest>`,
# `<string(minlength=2):code>`.
_FLASK_CONVERTER_RE = re.compile(r"<(?:[^<>]*:)?(\w+)>")

# The decorated function. Only further decorators, comments and blank lines may
# sit between the route call and it; anything else means the decorator was not
# on a function, and the next `def` in the file belongs to somebody else.
_FLASK_DEF_RE = re.compile(r"^[^\S\n]*(?:async[^\S\n]+)?def[^\S\n]+(?P<name>\w+)", re.MULTILINE)
_FLASK_BETWEEN_RE = re.compile(r"(?:[^\S\n]*(?:@[^\n]*|#[^\n]*)?\n)*[^\S\n]*")

# A docstring, so a route in a usage example is not read as a route. Flask's own
# helpers document themselves with `@app.route("/uploads/<path:name>")` blocks,
# and those endpoints do not exist anywhere.
_TRIPLE_QUOTE_RE = re.compile(r'"""|\'\'\'')


def _docstring_spans(content: str) -> list[tuple[int, int]]:
    """``(start, end)`` of every triple-quoted string in *content*, in order.

    A span is closed by its own delimiter, so a ``'''`` written inside a
    ``\"\"\"`` block does not end it. An unterminated opener ends the walk rather
    than swallowing the rest of the file.
    """
    spans: list[tuple[int, int]] = []
    pos = 0
    while (m := _TRIPLE_QUOTE_RE.search(content, pos)) is not None:
        close = content.find(m.group(), m.end())
        if close == -1:
            break
        pos = close + 3
        spans.append((m.start(), pos))
    return spans


def flask_path(raw: str) -> str:
    """A Flask rule as a path: every converter spelling becomes ``{name}``.

    ``normalize_http_path`` reads ``<...>`` as ordinary text and would carry the
    converter into the contract id, so the rewrite happens here, as it does for
    Django's ``<converter:name>``.
    """
    return _FLASK_CONVERTER_RE.sub(r"{\1}", raw)


def _flask_handler(content: str, after: int) -> str | None:
    """The name of the function decorated by a route call ending at *after*."""
    m = _FLASK_DEF_RE.search(content, after)
    if m is None or not _FLASK_BETWEEN_RE.fullmatch(content, after, m.start()):
        return None
    return m.group("name")


def _flask_verbs(args: str, verb: str) -> list[str]:
    """The methods a route decorator declares, in the order written.

    Empty when the call names its methods through a variable
    (``methods=HTTP_METHODS``): the route serves verbs the file does not spell,
    and defaulting to GET would publish one of them as the whole endpoint.
    """
    if verb != "route":
        return [verb.upper()]
    m = _FLASK_METHODS_RE.search(args)
    if m is None:
        # Flask serves GET when `methods=` is absent.
        return [] if _FLASK_METHODS_KW_RE.search(args) else ["GET"]
    return [v.upper() for v in _FLASK_METHOD_LITERAL_RE.findall(m.group("verbs"))]


def _flask_call_end(content: str, paren_offset: int) -> int:
    """Index just past the call opened at *paren_offset*, or the text's end."""
    close = match_paren(content, paren_offset, quotes="\"'", hash_comments=True)
    return len(content) if close == -1 else close + 1


def flask_routes(content: str) -> Iterator[RouteMatch]:
    """Route decorators in *content*, one match per declared method.

    ``receiver`` is the decorated variable as written; whether it holds an app or
    a blueprint is the consumer's question, because the answer needs the file's
    bindings. ``handler`` is the function directly below. A decorator inside a
    docstring is documentation, not a route.
    """
    matches = list(_FLASK_ROUTE_RE.finditer(content))
    # Paid for only once a candidate exists, and walked alongside the matches:
    # both are in ascending offset order, so neither is rescanned.
    spans = _docstring_spans(content) if matches else []
    span = 0
    for m in matches:
        at = m.start("at")
        while span < len(spans) and spans[span][1] <= at:
            span += 1
        if span < len(spans) and spans[span][0] < at:
            continue
        paren_offset = m.start("paren")
        end = _flask_call_end(content, paren_offset)
        handler = _flask_handler(content, end)
        path = flask_path(m.group("path"))
        for verb in _flask_verbs(content[paren_offset:end], m.group("verb").lower()):
            yield RouteMatch(
                verb=verb,
                path=path,
                receiver=m.group("receiver"),
                handler=handler,
                offset=at,
                paren_offset=paren_offset,
            )


def flask_blueprints(content: str) -> Iterator[GroupMatch]:
    """``register_blueprint(...)`` mounts in *content*.

    ``var`` is the registered expression as written, dotted where the call names
    one (``views.bp``): the graph consumer resolves its head against the file's
    imports, the contract consumer keys the prefix on its final segment.
    ``prefix`` is empty when the call carries no ``url_prefix=`` and ``None``
    when it carries one this cannot read (``url_prefix=PREFIX``), which is where
    the blueprint is mounted and the routes on it are not served.
    """
    if "register_blueprint" not in content:
        return
    for m in _FLASK_REGISTER_RE.finditer(content):
        paren_offset = m.start("paren")
        args = content[paren_offset : _flask_call_end(content, paren_offset)]
        pm = _FLASK_URL_PREFIX_RE.search(args)
        if pm is None and _FLASK_URL_PREFIX_KW_RE.search(args):
            prefix: str | None = None
        else:
            prefix = pm.group("prefix") if pm else ""
        yield GroupMatch(
            var=m.group("var"),
            parent=_opt(m.group("parent")),
            prefix=prefix,
        )


# ---------------------------------------------------------------------------
# Micronaut
# ---------------------------------------------------------------------------

# `@Controller("/users")` names the class prefix and `@Get("/{id}")` the
# sub-path: the two-level shape JAX-RS spells with two annotations, written
# with the verb in the annotation's own name. The graph side recognised
# `@Controller` as a substring only, to stamp a role; nothing read the paths.
_MICRONAUT_VERBS = "Get|Post|Put|Delete|Patch|Head|Options"

_MICRONAUT_VERB_RE = re.compile(rf"@(?P<verb>{_MICRONAUT_VERBS})\b")
_MICRONAUT_CONTROLLER_RE = re.compile(r"@Controller\b")

# A declarative HTTP client, written with the same verb annotations as a
# controller: its routes are calls out, not endpoints served.
_MICRONAUT_CLIENT_RE = re.compile(r"@Client\b")

# An argument list follows, or the annotation stands alone.
_MICRONAUT_OPEN_RE = re.compile(r"\s*\(")

# The path argument: positional, or named `uri =` (Micronaut's spelling) or
# `value =` (its alias).
_MICRONAUT_ARG_RE = re.compile(r"""\s*\(\s*(?:(?:uri|value)\s*=\s*)?["'](?P<path>[^"']*)["']""")

# The first argument's keyword, when it has one. An annotation whose arguments
# are all named and none of them a path key carries no path at all, so
# `@Get(produces = ...)` serves the class prefix exactly as a bare `@Get` does.
_MICRONAUT_NAMED_ARG_RE = re.compile(r"\(\s*\w+\s*=")

# A keyword that does name the path: seeing one this could not read means the
# route has a path and it is not the class prefix.
_MICRONAUT_PATH_KEY_RE = re.compile(r"\b(?:uri|uris|value)\s*=")

# A type declaration, unanchored because a Micronaut controller is routinely
# written on one line (`@Controller("/hi") public class Hi {}`), which a
# line-anchored match reads as no declaration at all. Java's `record` and `enum`
# count: both carry controller annotations, and a spelling left out here is a
# controller the graph reads as an ordinary file.
_MICRONAUT_TYPE_RE = re.compile(r"\b(?:class|interface|object|record|enum)\s+\w+")

# An RFC 6570 query or fragment expansion. Micronaut route templates carry
# their query parameters in the path string (`/list{?args*}`), where
# `normalize_http_path` reads them as a path parameter rather than dropping
# them the way it drops a plain `?query`.
_MICRONAUT_QUERY_EXPANSION_RE = re.compile(r"\{[?#&][^}]*\}")

# The annotated method's name: the first identifier opening a parameter list
# that is not itself an annotation, so an annotation run carrying calls of its
# own (`@Produces(...)`, a nested `@Schema(...)`) is stepped over.
_MICRONAUT_METHOD_RE = re.compile(r"(?<![@\w])(?P<name>\w+)\s*\(")


def micronaut_annotations(content: str) -> set[int]:
    """Offsets of every ``@`` written as code in *content*.

    An annotation quoted in a doc comment or a string is documentation, not a
    route: the guides ship whole controllers inside asciidoc snippets, and the
    routes in them are served by nothing.

    The scan reads the whole file, so a consumer wanting more than one of the
    three readers below computes this once and hands it to each of them.
    """
    return {i for i, c, _ in scan_code(content, quotes='"', text_blocks=True) if c == "@"}


def micronaut_path(raw: str) -> str:
    """A Micronaut route template as a path, without its query expansion."""
    return _MICRONAUT_QUERY_EXPANSION_RE.sub("", raw)


def _micronaut_annotation_path(content: str, at: int) -> str | None:
    """The path the annotation ending at *at* declares.

    ``""`` when it names no path, which Micronaut serves at the class prefix:
    a bare ``@Get``, and equally ``@Get(produces = ...)``, whose arguments are
    all named and none of them the path. ``None`` when it does name one this
    cannot read: ``uris = {…}`` names several and a constant names none, and in
    neither case is the class prefix on its own the route.
    """
    opened = _MICRONAUT_OPEN_RE.match(content, at)
    if opened is None:
        return ""
    m = _MICRONAUT_ARG_RE.match(content, at)
    if m is not None:
        return m.group("path")
    close = match_paren(content, opened.end() - 1, quotes='"')
    args = content[opened.end() - 1 : len(content) if close == -1 else close]
    if _MICRONAUT_PATH_KEY_RE.search(args) or not _MICRONAUT_NAMED_ARG_RE.match(args):
        return None
    return ""


def _micronaut_annotated_types(
    content: str, pattern: re.Pattern[str], annotations: set[int] | None = None
) -> Iterator[tuple[int, str | None]]:
    """``(offset, path)`` per type-level match of *pattern*, ascending.

    The declaration scan is JAX-RS's: an annotation run reaching a type is the
    same shape in both. ``path`` is ``None`` when the annotation carries an
    argument this cannot read.
    """
    code = micronaut_annotations(content) if annotations is None else annotations
    for m in pattern.finditer(content):
        if m.start() not in code:
            continue
        end = _jaxrs_decl_end(content, m.start())
        if not _MICRONAUT_TYPE_RE.search(content, m.end(), end):
            continue
        yield m.start(), _micronaut_annotation_path(content, m.end())


def micronaut_class_paths(
    content: str, annotations: set[int] | None = None
) -> list[tuple[int, str | None]]:
    """``(offset, prefix)`` per type-level ``@Controller``, ascending.

    A controller whose prefix is a constant keeps its entry with a ``None``
    prefix. It is still a controller, which is what the graph reads, and the
    routes under it are refused by the contract consumer rather than published
    at a path nothing serves.
    """
    if "@Controller" not in content:
        return []
    return [
        (offset, None if path is None else micronaut_path(path).rstrip("/"))
        for offset, path in _micronaut_annotated_types(
            content, _MICRONAUT_CONTROLLER_RE, annotations
        )
    ]


def micronaut_client_types(content: str, annotations: set[int] | None = None) -> list[int]:
    """Offsets of every type-level ``@Client``, ascending.

    A declarative client declares the endpoints it calls with the annotations a
    controller declares the ones it serves with, so the two are told apart only
    by which of them the verb annotation sits under.
    """
    if "@Client" not in content:
        return []
    return [
        offset
        for offset, _path in _micronaut_annotated_types(content, _MICRONAUT_CLIENT_RE, annotations)
    ]


def _micronaut_route_path(content: str, at: int) -> str | None:
    """The sub-path an annotation ending at *at* declares, as a path."""
    raw = _micronaut_annotation_path(content, at)
    return None if raw is None else micronaut_path(raw)


def micronaut_routes(content: str, annotations: set[int] | None = None) -> Iterator[RouteMatch]:
    """Verb-annotated controller methods in *content*.

    ``path`` is the method's own sub-path, ``""`` when the annotation carries
    none and the class prefix is the whole route; the prefix is stitched on by
    the consumer, the only side that knows how to compose one. ``handler`` is
    the method the annotation is written above.
    """
    code = micronaut_annotations(content) if annotations is None else annotations
    for m in _MICRONAUT_VERB_RE.finditer(content):
        if m.start() not in code:
            continue
        end = _jaxrs_decl_end(content, m.start())
        name = _MICRONAUT_METHOD_RE.search(content, m.end(), end)
        yield RouteMatch(
            verb=m.group("verb").upper(),
            path=_micronaut_route_path(content, m.end()),
            receiver=None,
            handler=name.group("name") if name is not None else None,
            offset=m.start(),
            paren_offset=m.start(),
        )


# ---------------------------------------------------------------------------
# Remix
# ---------------------------------------------------------------------------

# Remix serves what it finds under `app/routes/`, so no route call exists to
# read: the path is the file's name read as a grammar and the verb is the name
# of each exported handler. `app/` is optional because the app directory is
# configurable and a repo that moves it still writes `routes/` under it.
_REMIX_ROUTES_DIR_RE = re.compile(r"(?:^|/)(?:app/)?routes/")

REMIX_ROUTE_EXTS: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx")

# A test written beside the route it exercises. `routes/` holds both.
_REMIX_TEST_RE = re.compile(r"\.(?:test|spec)\.")

# `loader` answers a GET. `action` answers every verb that is not a GET and the
# file never says which, so the route is recorded with the unknown-verb marker
# the URLconf dialects use rather than a guessed POST.
_REMIX_EXPORT_RE = re.compile(_EXPORT_HEAD + r"(?P<name>loader|action)\b", re.MULTILINE)

#: The HTTP method each exported handler answers.
REMIX_HANDLER_VERBS: dict[str, str] = {"loader": "GET", "action": "*"}


def remix_route_file(rel_path: str) -> bool:
    """True when *rel_path* is a route Remix loads by filesystem convention."""
    if not _REMIX_ROUTES_DIR_RE.search(rel_path):
        return False
    name = rel_path.rsplit("/", 1)[-1]
    if _REMIX_TEST_RE.search(name):
        return False
    return name.endswith(REMIX_ROUTE_EXTS)


def _remix_strip_ext(name: str) -> str:
    for ext in REMIX_ROUTE_EXTS:
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


#: Written before a character the route name escaped, so the grammar below can
#: tell ``.`` from ``[.]`` without a second pass over the name.
_REMIX_ESCAPE_MARK = "\x00"


def _remix_split(part: str) -> list[str]:
    """The segments of one dotted route name, escapes marked.

    ``.`` separates segments, and inside ``[...]`` every character is literal:
    that is how a route serves a file name (``sitemap[.]xml``). The escape
    covers the bracketed characters and no more, so each one is marked rather
    than the segment holding it: ``$id[.]json`` is still a parameter.
    """
    segments: list[str] = []
    current = ""
    in_escape = False
    for ch in part:
        if ch == "[" and not in_escape:
            in_escape = True
        elif ch == "]" and in_escape:
            in_escape = False
        elif ch == "." and not in_escape:
            segments.append(current)
            current = ""
        else:
            current += _REMIX_ESCAPE_MARK + ch if in_escape else ch
    segments.append(current)
    return segments


def _remix_chars(segment: str) -> list[tuple[str, bool]]:
    """``(character, escaped)`` pairs, reading the marker back off *segment*."""
    out: list[tuple[str, bool]] = []
    marked = False
    for ch in segment:
        if ch == _REMIX_ESCAPE_MARK and not marked:
            marked = True
            continue
        out.append((ch, marked))
        marked = False
    return out


def _remix_segment_path(segment: str) -> str | None:
    """The URL segment a route-name segment names, or None when it names none.

    Every rule reads unescaped characters only: a ``$`` or a ``_`` written
    inside brackets is part of the name the route serves, not grammar.
    """
    chars = _remix_chars(segment)
    if chars and chars[0] == ("_", False):
        # `_index` and every other pathless layout: nesting, not a URL segment.
        return None
    # A trailing underscore opts the route out of its parent's layout, which
    # changes what renders and not what is served.
    while chars and chars[-1] == ("_", False):
        chars.pop()
    if len(chars) > 1 and chars[0] == ("(", False) and chars[-1] == (")", False):
        # Optional. Recorded in its present form: the absent form is a second
        # path, and emitting both would publish an endpoint per combination.
        chars = chars[1:-1]
    if chars == [("$", False)]:
        return "*"
    text = "".join(ch for ch, _esc in chars)
    if chars and chars[0] == ("$", False):
        return ":" + text[1:]
    return text or None


def remix_route_path(rel_path: str) -> str | None:
    """The URL the Remix route file *rel_path* serves, else None.

    Directories separate segments exactly as ``.`` does, so the nested form
    (``routes/users/$id.tsx``) and the flat form (``routes/users.$id.tsx``)
    read alike. A directory whose file is named ``route`` carries the route's
    name itself, and a trailing ``index`` is the parent's index route.
    """
    if not remix_route_file(rel_path):
        return None
    end = 0
    for m in _REMIX_ROUTES_DIR_RE.finditer(rel_path):
        end = m.end()
    parts = rel_path[end:].split("/")
    parts[-1] = _remix_strip_ext(parts[-1])
    if len(parts) > 1 and parts[-1] == "route":
        parts.pop()
    raw: list[str] = []
    for part in parts:
        raw.extend(_remix_split(part))
    if raw and raw[-1] == "index":
        raw.pop()
    segments = [seg for text in raw if (seg := _remix_segment_path(text)) is not None]
    return "/" + "/".join(segments)


def remix_route_verbs(content: str) -> list[tuple[str, int]]:
    """``(verb, offset)`` per exported route handler, in declaration order."""
    return [
        (REMIX_HANDLER_VERBS[name], offset)
        for name, offset in _exported_names(content, _REMIX_EXPORT_RE)
    ]
