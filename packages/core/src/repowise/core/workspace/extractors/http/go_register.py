"""Go register functions: routes declared on a router group a function is handed.

``articles.Register(v1.Group("/articles"))`` in ``main.go`` and
``func Register(router *gin.RouterGroup) { router.GET("/feed", h) }`` in
``articles/`` serve ``/api/articles/feed``; the function alone says ``/feed``.

The repo-wide mount pass (:func:`router_args`) records each call handing a
router to a package-level function: the package's directory (placed through
the ``go.mod`` holding the caller), the function, the argument position and the
prefix the argument carries. A call site handing on the calling function's own
parameter names that parameter as its parent, so prefixes chain up to where a
group is built. :func:`served_paths` then puts a route hanging off such a
parameter under every prefix its call sites give: a function registered under
two groups serves its routes at both.

Never a guessed path. A route keeps its in-function path when the chain cannot
be read to its top: a method's call site (``h.Register(g)``, which names no type
to key by), a caller that is itself never handed a router, a package in another
module (a dependency, or a monorepo sibling: ``resolvers.go.read_go_modules``
would place one), a dot import, more than :data:`_MAX_PREFIXES` prefixes, or a
chain deeper than :data:`_MAX_DEPTH`. A call site in test code adds nothing, and
a call site that only cycles back into the chain serves no path of its own.
Group variables are read in the function that binds them; ceiling: a parameter
rebound anywhere in its function (``g := ...``) counts as rebound throughout.
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING

from repowise.core.ingestion.framework_routes import go_groups, go_match_paren
from repowise.core.ingestion.framework_routes_chi import route_scopes
from repowise.core.ingestion.framework_routes_gorilla import gorilla_groups
from repowise.core.ingestion.resolvers.go import _read_module_directive

from ..strings import call_arguments
from .mounts import compose_prefix, group_prefixes

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from repowise.core.ingestion.framework_routes import GroupMatch, RouteMatch
    from repowise.core.ingestion.framework_routes_chi import RouteScope

    from ..base import ScanContext

#: Mount key ``<_ARG><package dir>\t<function>\t<argument index>\t<call site>``,
#: one per call site so two never collide in the repo-wide merge. The value is
#: ``<parent>\t<prefix>``: the prefix the argument carries and, when it is the
#: calling function's own parameter, that parameter (``dir|func|index``).
_ARG = "go-router-arg:"
_SEP = "\t"
_MAX_DEPTH = 16
_MAX_PREFIXES = 16
#: Parameters read for one route at most; a wider call graph is refused.
_MAX_EXPANSIONS = 256

_FUNC_DECL_RE = re.compile(
    r"^func\s+(?P<recv>\([^()\n]*\)\s*)?(?P<name>\w+)\s*(?:\[[^\]\n]*\]\s*)?\(", re.M
)
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

#: ``(name, parameter names, body start, body end)``; a method's name is None.
_Function = tuple["str | None", list[str], int, int]
#: ``(prefixes, parents, refused)`` of the groups one function sees.
_Bindings = tuple[dict[str, str], dict[str, "str | None"], set[str]]
#: ``(package dir, function, argument index, parent, prefix)``.
_Site = tuple[str, str, int, str, str]
_Param = tuple[str, str, int]


def route_prefix(
    receiver: str, offset: int, scopes: Sequence[RouteScope], groups: Mapping[str, str]
) -> str:
    """The prefix *receiver* serves under at *offset*: its innermost ``Route`` closure, else its group."""
    for scope in reversed(scopes):
        if scope.inner == receiver and scope.start < offset < scope.end:
            outer = route_prefix(scope.outer, scope.start, scopes, groups)
            return compose_prefix(outer, scope.prefix)
    return groups.get(receiver, "")


def unreadable_groups(groups: Sequence[GroupMatch]) -> set[str]:
    """Group variables whose prefix, or an ancestor's, is not a literal."""
    refused = {g.var for g in groups if g.prefix is None}
    grew = True
    while grew:
        before = len(refused)
        refused |= {g.var for g in groups if g.parent in refused}
        grew = len(refused) > before
    return refused


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


def _functions(content: str) -> list[_Function]:
    """Each package-level function and method, as :data:`_Function`."""
    out: list[_Function] = []
    for m in _FUNC_DECL_RE.finditer(content):
        close = go_match_paren(content, m.end() - 1)
        brace = content.find("{", close) if close > 0 else -1
        end = go_match_paren(content, brace) if brace > 0 else -1
        if end > 0:
            # `a, b *gin.RouterGroup` names two parameters; a name is a part's first word.
            params = [(p.split() or [""])[0] for p in call_arguments(content, m.end() - 1) or []]
            out.append((None if m.group("recv") else m.group("name"), params, brace, end))
    return out


def _enclosing(functions: Sequence[_Function], offset: int) -> _Function | None:
    return next((f for f in functions if f[2] < offset < f[3]), None)


def _groups_in(text: str) -> list[GroupMatch]:
    return [*go_groups(text), *gorilla_groups(text)]


class _Scopes:
    """Group bindings as one function sees them: the package's, then its own body's.

    A name reused in two functions (``g := r.Group("/o")`` in one, a parameter
    ``g`` in the next) keeps each function's own binding, and a parameter hides a
    package-level group of its name.
    """

    def __init__(self, content: str, functions: Sequence[_Function]) -> None:
        self._content = content
        chars = list(content)
        for _name, _params, start, end in functions:
            chars[start + 1 : end] = " " * (end - start - 1)
        self._package = _groups_in("".join(chars))
        self._memo: dict[int, _Bindings] = {}

    def of(self, fn: _Function | None) -> _Bindings:
        key = fn[2] if fn is not None else -1
        if key not in self._memo:
            params = set(fn[1]) if fn is not None else set()
            own = _groups_in(self._content[fn[2] : fn[3]]) if fn is not None else []
            groups = [g for g in self._package if g.var not in params] + own
            refused = unreadable_groups(groups)
            prefixes = group_prefixes(g for g in groups if g.var not in refused)
            self._memo[key] = (prefixes, {g.var: g.parent for g in groups}, refused)
        return self._memo[key]


def _param_index(content: str, fn: _Function | None, name: str) -> int | None:
    """The position of function *fn*'s parameter *name*, unless its body rebinds
    the name (``g := ...``, ``g, err := ...``, ``var g``)."""
    if fn is None or fn[0] is None or name not in fn[1]:
        return None
    n = re.escape(name)
    rebinds = rf"\bvar\s+{n}\b|(?<![\w.])(?:\w+\s*,\s*)*{n}(?:\s*,\s*\w+)*\s*:="
    if re.search(rebinds, content[fn[2] : fn[3]]):
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


def _package_dir(ctx: ScanContext, directory: str, import_path: str | None) -> str | None:
    """The directory of the package a call names: the caller's own for a
    same-package call, else the import placed through the caller's ``go.mod``;
    ``None`` for an import that ``go.mod`` does not place."""
    if import_path is None:
        return directory
    module = _module(ctx, directory)
    if module is None:
        return None
    module_dir, module_path = module
    if import_path != module_path and not import_path.startswith(module_path + "/"):
        return None
    rest = import_path[len(module_path) :].lstrip("/")
    return posixpath.join(module_dir, rest).rstrip("/")


# --- collect: the call sites ---------------------------------------------------


def router_args(ctx: ScanContext) -> dict[str, str]:
    """The mount entries for each call in *ctx* handing a router to a package-level function."""
    from repowise.core.code_origin import path_origin
    from repowise.core.workspace.contracts import _NOT_CONTRACT_ORIGINS

    # The collect pass holds no stored origin, so the path decides (the
    # code_origin fallback): a test registering routes at its own prefix serves none.
    if path_origin(ctx.rel_path) in _NOT_CONTRACT_ORIGINS:
        return {}
    reader = _CallReader(ctx)
    if not reader.routers and "Group(" not in ctx.content:
        return {}
    out: dict[str, str] = {}
    for m in _CALL_RE.finditer(ctx.content):
        out.update(reader.entries(m))
    return out


class _CallReader:
    """One file's calls that hand a router on."""

    def __init__(self, ctx: ScanContext) -> None:
        content = ctx.content
        self.ctx = ctx
        self.engines = {m.group("var") for m in _ENGINE_RE.finditer(content)}
        # A bare name is a router when the file binds it to one; a group call always is.
        self.routers = (
            {g.var for g in _groups_in(content)}
            | self.engines
            | {m.group("var") for m in _ROUTER_PARAM_RE.finditer(content)}
        )
        self.imports = _imports(content)
        self.directory = posixpath.dirname(ctx.rel_path)
        self._read: tuple[list[_Function], _Scopes, list[RouteScope]] | None = None

    def _file(self) -> tuple[list[_Function], _Scopes, list[RouteScope]]:
        if self._read is None:
            functions = _functions(self.ctx.content)
            self._read = (
                functions,
                _Scopes(self.ctx.content, functions),
                route_scopes(self.ctx.content),
            )
        return self._read

    def _arguments(self, m: re.Match[str]) -> list[tuple[int, str, re.Match[str] | None]]:
        """``(index, base, group call)`` for each argument that is a router."""
        found = []
        for index, raw in enumerate(call_arguments(self.ctx.content, m.end() - 1) or []):
            arg = raw.strip()
            group = _GROUP_ARG_RE.fullmatch(arg)
            base = group.group("base") if group else arg if arg in self.routers else None
            if base is not None:
                found.append((index, base, group))
        return found

    def _callee(self, m: re.Match[str]) -> str | None:
        """The package directory of the function *m* calls, or None when it is not one."""
        pkg, func = m.group("pkg"), m.group("func")
        if func in _KEYWORDS or (pkg is not None and pkg not in self.imports):
            return None  # a method on a value: its type is not read here
        content = self.ctx.content
        if pkg is None and content[max(0, m.start() - 8) : m.start()].rstrip().endswith("func"):
            return None  # a declaration, not a call
        return _package_dir(self.ctx, self.directory, self.imports.get(pkg) if pkg else None)

    def entries(self, m: re.Match[str]) -> dict[str, str]:
        found = self._arguments(m)
        if not found or self.ctx.in_comment(m.start()) or self.ctx.in_string(m.start()):
            return {}
        package = self._callee(m)
        if package is None:
            return {}
        functions, scopes, closures = self._file()
        enclosing = _enclosing(functions, m.start())
        bindings = scopes.of(enclosing)
        out: dict[str, str] = {}
        for index, base, group in found:
            value = self._value(base, group, m.start(), enclosing, bindings, closures)
            if value is not None:
                site = f"{self.ctx.rel_path}:{m.start()}"
                out[_ARG + _SEP.join((package, m.group("func"), str(index), site))] = value
        return out

    def _value(
        self,
        base: str,
        group: re.Match[str] | None,
        offset: int,
        enclosing: _Function | None,
        bindings: _Bindings,
        closures: Sequence[RouteScope],
    ) -> str | None:
        """The mount value for one router argument, or None when it is not one this reads."""
        prefixes, parents, refused = bindings
        if base in refused:
            return None
        root = _root(base, offset, closures, parents)
        param = _param_index(self.ctx.content, enclosing, root)
        if param is not None and enclosing is not None:
            parent = f"{self.directory}|{enclosing[0]}|{param}"
        elif base in prefixes or root in self.engines:
            parent = ""
        else:
            return None  # not a router this file binds
        prefix = route_prefix(base, offset, closures, prefixes)
        if group:
            prefix = compose_prefix(prefix, group.group("prefix"))
        return parent + _SEP + prefix


# --- extract: the served paths -------------------------------------------------


def router_sites(mounts: Mapping[str, str]) -> list[_Site]:
    """The register-function call sites :func:`router_args` recorded in *mounts*."""
    out: list[_Site] = []
    for key, value in mounts.items():
        if key.startswith(_ARG):
            package, func, idx, _site = key[len(_ARG) :].split(_SEP, 3)
            parent, _, prefix = value.partition(_SEP)
            out.append((package, func, int(idx), parent, prefix))
    return out


class _RefusedError(Exception):
    """A chain this does not place: too wide, too deep, or under an unmounted caller."""


def served_prefixes(
    sites: Sequence[_Site], directory: str, func: str, index: int
) -> list[str] | None:
    """Every prefix the call sites of *func*'s parameter *index* (in the package
    at *directory*) hand it, sorted; ``None`` when none does or the chain is refused."""
    try:
        return _prefixes(sites, (directory, func, index), frozenset(), [_MAX_EXPANSIONS]) or None
    except _RefusedError:
        return None


def _prefixes(
    sites: Sequence[_Site], param: _Param, stack: frozenset[_Param], budget: list[int]
) -> list[str] | None:
    """*param*'s prefixes: ``[]`` when no call site hands it a router, ``None``
    when every call site only cycles back into the chain (*stack*)."""
    if len(stack) >= _MAX_DEPTH or budget[0] <= 0:
        raise _RefusedError
    budget[0] -= 1
    stack = stack | {param}
    found = [(parent, prefix) for *site, parent, prefix in sites if tuple(site) == param]
    out: set[str] = set()
    for parent, prefix in found:
        out.update(_site_prefixes(sites, parent, prefix, stack, budget))
        if len(out) > _MAX_PREFIXES:
            raise _RefusedError
    return sorted(out) if out or not found else None


def _site_prefixes(
    sites: Sequence[_Site], parent: str, prefix: str, stack: frozenset[_Param], budget: list[int]
) -> list[str]:
    """The prefixes one call site gives: its own, under its parent's."""
    if not parent:
        return [prefix]
    p_dir, p_func, p_idx = parent.split("|")
    up = (p_dir, p_func, int(p_idx))
    outer = None if up in stack else _prefixes(sites, up, stack, budget)
    if outer is None:
        return []  # a cycle: the call site serves no path of its own
    if not outer:
        raise _RefusedError  # its caller is never handed a router: the prefix above is unknown
    return [compose_prefix(o, prefix) for o in outer]


class RegisterPaths:
    """Served paths for one file's routes on a handed-in router."""

    def __init__(self, content: str, directory: str, sites: list[_Site]) -> None:
        self._content = content
        self._directory = directory
        self._sites = sites
        self._functions = _functions(content)
        self._scopes = _Scopes(content, self._functions)

    @classmethod
    def for_file(cls, ctx: ScanContext) -> RegisterPaths | None:
        """None unless some call site hands a router to this file's package."""
        directory = posixpath.dirname(ctx.rel_path)
        sites = router_sites(ctx.mounts)
        if not any(site[0] == directory for site in sites):
            return None
        return cls(ctx.content, directory, sites)

    def paths(
        self, route: RouteMatch, path: str, closures: Sequence[RouteScope]
    ) -> list[str] | None:
        """*path* under its own function's groups, under each prefix that
        function's callers hand the router it hangs off; ``None`` when it hangs
        off no parameter a call site hands a router."""
        enclosing = _enclosing(self._functions, route.offset)
        if enclosing is None or not route.receiver:
            return None
        prefixes, parents, _refused = self._scopes.of(enclosing)
        root = _root(route.receiver, route.offset, closures, parents)
        param = _param_index(self._content, enclosing, root)
        if param is None or enclosing[0] is None:
            return None
        served = served_prefixes(self._sites, self._directory, enclosing[0], param)
        if served is None:
            return None
        local = compose_prefix(route_prefix(route.receiver, route.offset, closures, prefixes), path)
        return [compose_prefix(p, local) for p in served]
