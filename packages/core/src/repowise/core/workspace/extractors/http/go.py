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

A group handed to a register function (``articles.Register(v1.Group("/articles"))``
in ``main.go``, ``func Register(router *gin.RouterGroup)`` in ``articles/``)
serves that function's routes under the call site's prefix. The repo-wide pass
records each call site under the function it calls, keyed by package directory,
name and argument position, and a route on that parameter takes every prefix its
call sites give: a function registered under two groups serves its routes at
both. A call site handing on its own parameter chains to its own callers. Read
for package-level functions called from production code only; a method's call
site (``h.Register(g)``) names no type, so its routes keep their in-function
path, as does a package whose import path does not end in its directory.
"""

from __future__ import annotations

import posixpath
import re
from itertools import chain
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import (
    GO_VERBLESS,
    HTTP_METHODS,
    go_groups,
    go_match_paren,
    go_routes,
)
from repowise.core.ingestion.framework_routes_chi import route_scopes, title_case_routes
from repowise.core.ingestion.framework_routes_gorilla import gorilla_groups, gorilla_routes
from repowise.core.ingestion.resolvers.go import _read_module_directive

from ..base import line_at
from ..calls import FileStrings, outside_comments
from ..langs import GO
from ..strings import GO_SYNTAX, call_arguments
from .dialect import build_provider_contract
from .mounts import compose_prefix, group_prefixes
from .paths import strip_leading_base_expr

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from repowise.core.ingestion.framework_routes import GroupMatch, RouteMatch
    from repowise.core.ingestion.framework_routes_chi import RouteScope
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext


def _prefix(
    receiver: str, offset: int, scopes: Sequence[RouteScope], groups: Mapping[str, str]
) -> str:
    """The prefix *receiver* serves under at *offset*: its innermost ``Route`` closure, else its group."""
    for scope in reversed(scopes):
        if scope.inner == receiver and scope.start < offset < scope.end:
            return compose_prefix(_prefix(scope.outer, scope.start, scopes, groups), scope.prefix)
    return groups.get(receiver, "")


def _handler(route: RouteMatch) -> str | None:
    """A wrapped handler names the middleware, not the route's own function."""
    return None if route.handler_call else route.handler


def _method(verb: str) -> str | None:
    """The recorded method for *verb*: ``*`` when verbless; gin's ``Any``, OPTIONS and HEAD are not recorded."""
    if verb in GO_VERBLESS:
        return "*"
    return verb if verb in HTTP_METHODS else None


def _unreadable_groups(groups: Sequence[GroupMatch]) -> set[str]:
    """Group variables whose prefix, or an ancestor's, is not a literal."""
    refused = {g.var for g in groups if g.prefix is None}
    grew = True
    while grew:
        before = len(refused)
        refused |= {g.var for g in groups if g.parent in refused}
        grew = len(refused) > before
    return refused


def _expression_path(route: RouteMatch, strings: FileStrings) -> str | None:
    """The path a ``base + "/x"`` argument denotes, or ``None`` when it is not settled."""
    args = call_arguments(strings.content, route.paren_offset)
    text = strings.text(args[0]) if args else None
    if text is None:
        return None
    path, _base = strip_leading_base_expr(text)
    return None if "${" in path else path


# --- Register functions: a router handed in as a parameter -------------------

#: Mount key ``<_ARG><package>\t<function>\t<argument index>\t<call site>``, one
#: per call site so two never collide in the repo-wide merge. ``<package>`` is
#: the caller's directory for a same-package call, else the import path. The
#: value is ``<parent>\t<prefix>``: the prefix the argument carries and, when the
#: argument is the calling function's own parameter, that parameter
#: (``dir|func|index``), whose call sites the prefix composes under.
_ARG = "go-router-arg:"
_SEP = "\t"
_MAX_DEPTH = 16
#: More prefixes than this for one parameter reads as a fan-out this cannot
#: place, so its routes keep their in-function path.
_MAX_PREFIXES = 16

_FUNC_DECL_RE = re.compile(r"^func\s+(?P<name>\w+)\s*(?:\[[^\]\n]*\]\s*)?\(", re.M)
_IMPORT_RE = re.compile(r"^import\s*(?:\((?P<block>[^)]*)\)|(?P<one>[^\n]+))", re.M)
_IMPORT_SPEC_RE = re.compile(r"(?:(?P<alias>[\w.]+)\s+)?\"(?P<path>[^\"]+)\"")
_CALL_RE = re.compile(r"(?<![\w.])(?:(?P<pkg>\w+)\s*\.\s*)?(?P<func>\w+)\s*(?:\[[^\]\n]*\]\s*)?\(")
_GROUP_ARG_RE = re.compile(
    r"(?P<base>\w+)\s*\.\s*Group\s*\(\s*[\"`](?P<prefix>[^\"`]*)[\"`]\s*(?:,[\s\S]*)?\)"
)
#: A router at the root: ``r := gin.Default()``.
_ENGINE_RE = re.compile(
    r"(?P<var>\w+)\s*:?=\s*(?:gin\s*\.\s*(?:Default|New)|echo\s*\.\s*New"
    r"|chi\s*\.\s*NewRouter|fiber\s*\.\s*New|mux\s*\.\s*NewRouter)\s*\("
)
#: A parameter or field typed as a router: ``rg *gin.RouterGroup``.
_ROUTER_PARAM_RE = re.compile(
    r"(?P<var>\w+)\s+\*?(?:gin\s*\.\s*(?:RouterGroup|Engine|IRouter|IRoutes)"
    r"|echo\s*\.\s*(?:Group|Echo)|chi\s*\.\s*(?:Router|Mux)|fiber\s*\.\s*(?:Router|App)"
    r"|mux\s*\.\s*Router)\b"
)
_KEYWORDS = frozenset({"func", "if", "for", "switch", "return", "go", "defer", "select"})

_Function = tuple[str, list[str], int, int]


def _functions(content: str) -> list[_Function]:
    """``(name, parameter names, body start, body end)`` of each package-level function."""
    out: list[_Function] = []
    for m in _FUNC_DECL_RE.finditer(content):
        close = go_match_paren(content, m.end() - 1)
        brace = content.find("{", close) if close > 0 else -1
        end = go_match_paren(content, brace) if brace > 0 else -1
        if end > 0:
            # `a, b *gin.RouterGroup` names two parameters; a name is a part's first word.
            params = [(p.split() or [""])[0] for p in content[m.end() : close].split(",")]
            out.append((m.group("name"), params, brace, end))
    return out


def _enclosing(functions: Sequence[_Function], offset: int) -> _Function | None:
    return next((f for f in functions if f[2] < offset < f[3]), None)


def _groups_in(text: str) -> list[GroupMatch]:
    return [*go_groups(text), *gorilla_groups(text)]


_Bindings = tuple[dict[str, str], dict[str, "str | None"], set[str]]


class _Scopes:
    """Group bindings as one function sees them: the package's, then its own body's.

    A name reused in two functions (``g := r.Group("/o")`` in one, a parameter
    ``g`` in the next) keeps each function's own binding; read file-wide, the
    parameter would take the other function's group.
    """

    def __init__(self, content: str, functions: Sequence[_Function]) -> None:
        self._content = content
        chars = list(content)
        for _name, _params, start, end in functions:
            chars[start + 1 : end] = " " * (end - start - 1)
        self._package = _groups_in("".join(chars))
        self._memo: dict[int, _Bindings] = {}

    def of(self, fn: _Function | None) -> _Bindings:
        """``(prefixes, parents, refused)`` inside *fn* (``None``: at package level)."""
        key = fn[2] if fn is not None else -1
        if key not in self._memo:
            own = _groups_in(self._content[fn[2] : fn[3]]) if fn is not None else []
            groups = self._package + own
            refused = _unreadable_groups(groups)
            prefixes = group_prefixes(g for g in groups if g.var not in refused)
            self._memo[key] = (prefixes, {g.var: g.parent for g in groups}, refused)
        return self._memo[key]


def _param_index(content: str, fn: _Function | None, name: str) -> int | None:
    """The position of *fn*'s parameter *name*, unless its body rebinds the name."""
    if fn is None or name not in fn[1]:
        return None
    if re.search(rf"(?<![\w.]){re.escape(name)}\s*:=", content[fn[2] : fn[3]]):
        return None
    return fn[1].index(name)


def _imports(content: str) -> dict[str, str]:
    """``package name -> import path``: the alias, else the path's last segment."""
    out: dict[str, str] = {}
    for m in _IMPORT_RE.finditer(content):
        for spec in _IMPORT_SPEC_RE.finditer(m.group("block") or m.group("one") or ""):
            path = spec.group("path")
            out[spec.group("alias") or path.rsplit("/", 1)[-1]] = path
    return out


def _root(
    receiver: str, offset: int, scopes: Sequence[RouteScope], parents: Mapping[str, str | None]
) -> str:
    """The variable *receiver*'s ``Route`` closures and groups start from at *offset*."""
    for scope in reversed(scopes):
        if scope.inner == receiver and scope.start < offset < scope.end:
            return _root(scope.outer, scope.start, scopes, parents)
    seen: set[str] = set()
    while (parent := parents.get(receiver)) is not None and receiver not in seen:
        seen.add(receiver)
        receiver = parent
    return receiver


def _package_key(ctx: ScanContext, directory: str, import_path: str | None) -> str | None:
    """The package a call names: ``=<directory>`` when it is placed in the repo,
    ``~<import path>`` (matched to a directory by its tail) when no ``go.mod``
    holds the caller, ``None`` when the import is another module's.

    A same-package call names the caller's directory; an import path is placed
    through the ``go.mod`` holding the caller. Ceiling: a monorepo's sibling
    module (or a ``replace``) is not placed, so its routes keep their
    in-function path; ``resolvers.go.read_go_modules`` would place it.
    """
    if import_path is None:
        return "=" + directory
    module = _module(ctx, directory)
    if module is None:
        return "~" + import_path
    module_dir, module_path = module
    if import_path == module_path or import_path.startswith(module_path + "/"):
        rest = import_path[len(module_path) :].lstrip("/")
        return "=" + posixpath.join(module_dir, rest).rstrip("/")
    return None  # another module's package: a dependency, or a sibling this does not place


def _module(ctx: ScanContext, directory: str) -> tuple[str, str] | None:
    """``(directory, module path)`` of the ``go.mod`` nearest above *directory*."""
    if ctx.repo_root is None:
        return None
    while True:
        go_mod = ctx.repo_root / directory / "go.mod"
        if go_mod.is_file():
            module_path = _read_module_directive(go_mod)
            return (directory, module_path) if module_path else None
        if not directory:
            return None
        directory = posixpath.dirname(directory)


def _same_package(site_package: str, directory: str) -> bool:
    """Whether a call site's package (see :func:`_package_key`) is the one at *directory*."""
    kind, name = site_package[:1], site_package[1:]
    if kind == "=":
        return name == directory
    return bool(directory) and name.endswith("/" + directory)


#: One call site handing a router on: ``(package, function, argument index, parent, prefix)``.
_Site = tuple[str, str, int, str, str]


def router_sites(mounts: Mapping[str, str]) -> list[_Site]:
    """The register-function call sites :func:`_router_args` recorded in *mounts*."""
    out: list[_Site] = []
    for key, value in mounts.items():
        if key.startswith(_ARG):
            package, func, idx, _site = key[len(_ARG) :].split(_SEP, 3)
            parent, _, prefix = value.partition(_SEP)
            out.append((package, func, int(idx), parent, prefix))
    return out


class _FanOutError(Exception):
    """More prefixes reach one parameter than :data:`_MAX_PREFIXES`."""


def served_prefixes(
    sites: Sequence[_Site], directory: str, func: str, index: int
) -> list[str] | None:
    """Every prefix the call sites of *func*'s parameter *index* (in the package
    at *directory*) hand it, sorted; ``None`` when no call site hands it one, or
    when more than :data:`_MAX_PREFIXES` reach it at any level of the chain."""
    try:
        return _prefixes(sites, (directory, func, index), frozenset()) or None
    except _FanOutError:
        return None


def _prefixes(
    sites: Sequence[_Site], param: tuple[str, str, int], stack: frozenset[tuple[str, str, int]]
) -> list[str]:
    """:func:`served_prefixes` for *param*, *stack* holding the parameters above it.
    A call site inside the chain it extends (a function registering itself) adds nothing."""
    directory, func, index = param
    stack = stack | {param}
    out: set[str] = set()
    for package, name, idx, parent, prefix in sites:
        if name != func or idx != index or not _same_package(package, directory):
            continue
        outer: list[str] = []
        if parent:
            p_dir, p_func, p_idx = parent.split("|")
            up = (p_dir, p_func, int(p_idx))
            if up in stack:
                continue  # a cycle: the call site serves no path of its own
            if len(stack) < _MAX_DEPTH:
                outer = _prefixes(sites, up, stack)
        if outer:
            out.update(compose_prefix(o, prefix) for o in outer)
        else:
            out.add(prefix)  # called from an unmounted function: its own segment only
        if len(out) > _MAX_PREFIXES:
            raise _FanOutError
    return sorted(out)


def _router_args(ctx: ScanContext) -> dict[str, str]:
    """The mount entries for each call in *ctx* handing a router to a package-level function."""
    from repowise.core.code_origin import path_origin
    from repowise.core.workspace.contracts import _NOT_CONTRACT_ORIGINS

    # The collect pass holds no stored origin, so the path decides (the
    # code_origin fallback): a test registering routes at its own prefix serves none.
    if path_origin(ctx.rel_path) in _NOT_CONTRACT_ORIGINS:
        return {}
    content = ctx.content
    engines = {m.group("var") for m in _ENGINE_RE.finditer(content)}
    # A bare name is a router when the file binds it to one; a group call always is.
    routers = (
        {g.var for g in _groups_in(content)}
        | engines
        | {m.group("var") for m in _ROUTER_PARAM_RE.finditer(content)}
    )
    if not routers and "Group(" not in content:
        return {}
    functions: list[_Function] = []
    scopes: _Scopes | None = None
    imports = _imports(content)
    directory = posixpath.dirname(ctx.rel_path)
    out: dict[str, str] = {}
    for m in _CALL_RE.finditer(content):
        pkg, func = m.group("pkg"), m.group("func")
        if func in _KEYWORDS or (pkg is not None and pkg not in imports):
            continue  # a method on a value: its type is not read here
        if pkg is None and content[max(0, m.start() - 8) : m.start()].rstrip().endswith("func"):
            continue  # a declaration, not a call
        found: list[tuple[int, str, re.Match[str] | None]] = []
        for index, raw in enumerate(call_arguments(content, m.end() - 1) or []):
            arg = raw.strip()
            group = _GROUP_ARG_RE.fullmatch(arg)
            base = group.group("base") if group else arg if arg in routers else None
            if base is not None:
                found.append((index, base, group))
        if not found or ctx.in_comment(m.start()) or ctx.in_string(m.start()):
            continue
        package = _package_key(ctx, directory, imports.get(pkg) if pkg else None)
        if package is None:
            continue
        if scopes is None:
            functions = _functions(content)
            scopes = _Scopes(content, functions)
        enclosing = _enclosing(functions, m.start())
        prefixes, parents, refused = scopes.of(enclosing)
        for index, base, group in found:
            if base in refused:
                continue
            root = _root(base, m.start(), (), parents)
            param = _param_index(content, enclosing, root)
            if enclosing is not None and param is not None:
                parent = f"{directory}|{enclosing[0]}|{param}"
            elif base in prefixes or root in engines:
                parent = ""
            else:
                continue  # not a router this file binds
            prefix = prefixes.get(base, "")
            if group:
                prefix = compose_prefix(prefix, group.group("prefix"))
            key = _SEP.join((package, func, str(index), f"{ctx.rel_path}:{m.start()}"))
            out[_ARG + key] = parent + _SEP + prefix
    return out


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
        return _router_args(ctx)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        groups = [*go_groups(content), *gorilla_groups(content)]
        refused = _unreadable_groups(groups)
        prefixes = group_prefixes(g for g in groups if g.var not in refused)
        scopes = route_scopes(content)
        strings = FileStrings(content, GO_SYNTAX)
        routes = chain(go_routes(content), title_case_routes(content), gorilla_routes(content))
        sites = router_sites(ctx.mounts)
        directory = posixpath.dirname(ctx.rel_path)
        # Only a package some call site hands a router to needs its functions read.
        mounted = any(_same_package(site[0], directory) for site in sites)
        functions = _functions(content) if mounted else []
        scoped = _Scopes(content, functions) if functions else None
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
            served = None
            if scoped is not None:
                served = _served_paths(
                    content, route, path, scopes, scoped, functions, sites, directory
                )
            if served is None:
                prefix = _prefix(route.receiver or "", route.offset, scopes, prefixes)
                served = [compose_prefix(prefix, path)]
            for path_raw in served:
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


def _served_paths(
    content: str,
    route: RouteMatch,
    path: str,
    scopes: Sequence[RouteScope],
    scoped: _Scopes,
    functions: Sequence[_Function],
    sites: Sequence[_Site],
    directory: str,
) -> list[str] | None:
    """*path* under its own function's groups, under each prefix that function's
    callers hand the router it hangs off; ``None`` when it hangs off no
    parameter a call site hands a router."""
    enclosing = _enclosing(functions, route.offset)
    if enclosing is None or not route.receiver:
        return None
    prefixes, parents, _refused = scoped.of(enclosing)
    root = _root(route.receiver, route.offset, scopes, parents)
    param = _param_index(content, enclosing, root)
    served = served_prefixes(sites, directory, enclosing[0], param) if param is not None else None
    if served is None:
        return None
    local = compose_prefix(_prefix(route.receiver, route.offset, scopes, prefixes), path)
    return [compose_prefix(p, local) for p in served]
