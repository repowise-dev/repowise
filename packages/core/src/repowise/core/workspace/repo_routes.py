"""A repository's HTTP routes, stored with every index: the ``http_routes`` table.

Each row is one route a framework registers in the repo: method, path
template, framework, where it is registered, and the function that handles it.
The routes come from the HTTP contract extractor's provider pass (the same
dialects, mounts and ``code_origin`` rule as ``contracts.in_repo``), so this
adds no route recognition of its own. Contracts stay opt-in: this reads HTTP
providers only, matches nothing and writes no ``contracts.json``.

What this adds is the handler. A contract binds a route to the symbol at its
line, which for a route registered in a function body (``r.GET("/x", ...)``
inside ``main``) is the function that registers it, not the one that serves
it. A route row names a handler only when it is the function the registration
names (``ArticleFeed``, ``user.list``) or the one a decorator or annotation
sits on; an inline handler (``app.get('/', function (req, res) {...})``)
leaves it empty, and the row's file and line are where that handler is.

:func:`refresh_route_table` is the one entry point the CLI and the hosted
indexer call, with their own session. On an update it re-reads only when a
changed path can hold a route, or when the stored rows were read by another
version of the extractors.
"""

from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract
    from repowise.core.workspace.repo_index import IndexedSymbol, RepoIndex

_log = logging.getLogger("repowise.workspace.repo_routes")

#: Bumped when this module changes what a row holds; the contracts version
#: covers the extractors underneath (see :func:`routes_fingerprint`).
ROUTE_TABLE_VERSION = 1

#: Frameworks whose route is an annotation, attribute or decorator on its
#: handler, or a file route's exported function. Only these may bind the symbol
#: the contract found at the route's line; anywhere else that symbol is the
#: function registering the route (``main``), so a row binds only a handler the
#: registration names. A framework missing here loses a handler, never gains a
#: wrong one.
_DECLARATIVE = frozenset(
    {
        "spring", "jaxrs", "micronaut", "aspnet", "azure-functions", "nestjs",
        "apex-rest", "rust-attr", "next-app", "next-pages",
    }
)  # fmt: skip
#: Python decorators sit outside the function's span, so there the handler
#: opens below the route line; a symbol holding the line registers the route.
_PYTHON_DECORATORS = frozenset({"fastapi", "flask"})
#: JavaScript router frameworks whose dialect names no handler: the row reads
#: the call's last argument itself (see :func:`_js_handler_expression`).
_JS_ROUTERS = frozenset({"express", "hono", "koa", "elysia"})
_HANDLER_EXPR_RE = re.compile(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*")
_QUALIFIER_RE = re.compile(r"::|\.")


@dataclass(frozen=True)
class Route:
    """One stored route.

    ``path`` is the normalized template the contract matcher compares
    (``/users/{param}``), with any mount prefix the extractor could stitch on.
    ``handler`` is the expression the registration names, as written;
    ``handler_symbol_id`` the ``wiki_symbols`` id it resolves to, or None when
    the handler is inline or cannot be resolved without guessing.
    """

    method: str
    path: str
    framework: str
    file_path: str
    line: int
    handler_symbol_id: str | None = None
    handler: str | None = None
    service: str | None = None

    @property
    def key(self) -> tuple[str, str, str, int]:
        return (self.method, self.path, self.file_path, self.line)


def routes_from_contracts(
    contracts: Iterable[Contract],
    index: RepoIndex | None,
    read_file: Any = None,
) -> list[Route]:
    """The route rows of *contracts*' HTTP providers, handlers resolved in *index*.

    Pure apart from *read_file* (``rel_path -> text | None``), which the JS
    router rows read their call's arguments through; None skips that read.
    One row per (method, path, file, line), in file and line order.
    """
    rows: dict[tuple[str, str, str, int], Route] = {}
    go_walk = _GoIndexWalk(index) if index is not None else None
    for c in contracts:
        if c.contract_type != "http" or c.role != "provider" or c.line is None:
            continue
        meta = c.meta or {}
        method, path = meta.get("method"), meta.get("path")
        if not method or not path:
            continue
        framework = str(meta.get("framework") or "")
        expr = meta.get("handler")
        if expr is None and framework in _JS_ROUTERS and read_file is not None:
            expr = _js_handler_expression(read_file(c.file_path), c.line, str(path))
        symbol_id = _handler_symbol(c, framework, expr, index, go_walk)
        route = Route(
            method=str(method),
            path=str(path),
            framework=framework,
            file_path=c.file_path,
            line=c.line,
            handler_symbol_id=symbol_id,
            handler=expr,
            service=c.service,
        )
        rows.setdefault(route.key, route)
    return sorted(rows.values(), key=lambda r: (r.file_path, r.line, r.method, r.path))


def _handler_symbol(
    c: Contract,
    framework: str,
    expr: str | None,
    index: RepoIndex | None,
    go_walk: _GoIndexWalk | None = None,
) -> str | None:
    """The symbol id serving route *c*, or None rather than a guess."""
    if index is None:
        return None
    bound = _symbol_by_id(index, c.symbol_id) if c.symbol_id else None
    if expr:
        if framework == "go" and go_walk is not None:
            # Asked first: a local named like a type (``EmailApi := api.Group.EmailTest``)
            # is the method it holds, not the type its name matches.
            method = go_walk.method_at(c.file_path, c.line, _QUALIFIER_RE.split(expr))
            if method is not None:
                return method.symbol_id
        # The contract may already have bound the name (index-backed dialects,
        # or a handler the repo names once); its line fallback lands elsewhere.
        if bound is not None and _names(bound, expr):
            return bound.symbol_id
        found = _resolve_named(index, c.file_path, expr, framework, go_walk)
        return found.symbol_id if found is not None else None
    if bound is None:
        return None
    if framework in _PYTHON_DECORATORS:
        return bound.symbol_id if bound.start_line >= c.line else None
    # An annotation stack is inside the span on the JVM and .NET, so the
    # symbol holding the line is the annotated one.
    return bound.symbol_id if framework in _DECLARATIVE else None


def _names(sym: IndexedSymbol, expr: str) -> bool:
    """Whether *sym* is what *expr* spells: its name, and a qualifier its own."""
    parts = _QUALIFIER_RE.split(expr)
    if sym.name != parts[-1]:
        return False
    return len(parts) == 1 or _QUALIFIER_RE.split(sym.qualified_name)[-2:] == parts[-2:]


def _symbol_by_id(index: RepoIndex, symbol_id: str) -> IndexedSymbol | None:
    for sym in index.symbols_for_file(symbol_id.split("::", 1)[0]):
        if sym.symbol_id == symbol_id:
            return sym
    return None


def _resolve_named(
    index: RepoIndex,
    file_path: str,
    expr: str,
    framework: str,
    go_walk: _GoIndexWalk | None = None,
) -> IndexedSymbol | None:
    """The one symbol *expr* names from *file_path*, nearest scope first.

    The registering file, then for JavaScript the relative module the name or
    its qualifier is imported from, for Go the file's package (its directory)
    or an imported package directory, and elsewhere the repo when the name is
    unique there. More than one candidate at a level is no answer.
    """
    parts = _QUALIFIER_RE.split(expr)
    name = parts[-1]
    if len(parts) == 1:
        local = _named_in(index.symbols_for_file(file_path), name)
        if local is not None:
            return local
    if framework in _JS_ROUTERS:
        # Only what a relative import names is followed: `mod.fn` off a whole
        # module, or `fn` imported by name. A package or aliased spec is no answer.
        imported = _js_import(index, file_path, parts[0])
        if imported is None:
            return None
        target, exported = imported
        if len(parts) == 1:
            return (
                None
                if exported == "default"
                else _named_in(index.symbols_for_file(target), exported)
            )
        return _named_in(index.symbols_for_file(target), name) if exported == "default" else None
    if framework == "go":
        # A bare name is the file's package (its directory); ``pkg.Fn`` must
        # come from the directory *file_path* imports as ``pkg``.
        if len(parts) == 1:
            package = posixpath.dirname(file_path)
            return _named_in(_in_directories(index, lambda d: d == package), name)
        if len(parts) == 2:
            package = go_walk.imported_dir(file_path, parts[0]) if go_walk is not None else None
            if package is None:
                return None
            return _named_in(_in_directories(index, lambda d: d == package), name)
        return None
    found = index.symbol_named(expr)
    # ``symbol_named`` returns a lone candidate whatever its qualifier says.
    return found if found is not None and _names(found, expr) else None


class _GoIndexWalk:
    """:class:`GoSelectorTypes` read through the stored symbols and the files on disk.

    Built on the first Go route that asks. An import is followed into the
    repository through its ``go.mod`` modules, so an aliased import
    (``api "…/server/api/v1"``) reaches its directory.
    """

    def __init__(self, index: RepoIndex) -> None:
        self._index = index
        self._types: Any = None
        self._modules: tuple[tuple[str, str], ...] | None = None
        self._dirs: dict[str, list[str]] = {}

    def _walk(self) -> Any:
        if self._types is None:
            from repowise.core.ingestion.languages.go_selector_types import GoSelectorTypes

            for f in self._index.files():
                if f.endswith(".go"):
                    self._dirs.setdefault(posixpath.dirname(f), []).append(f)
            self._types = GoSelectorTypes(
                self._package_files, self._text, self.imported_dir, self._method_of
            )
        return self._types

    def method_at(self, file_path: str, line: int, parts: list[str]) -> IndexedSymbol | None:
        return self._walk().method_at(file_path, line, parts)

    def _package_files(self, package: str) -> list[str]:
        # A package variable or type is a stored symbol, so its file is listed.
        return self._dirs.get(package, [])

    def _text(self, rel_path: str) -> str:
        return _read(self._index.repo_path / rel_path) or ""

    def imported_dir(self, file_path: str, local: str) -> str | None:
        imported = self._walk().facts(file_path).imports.get(local)
        if imported is None:
            return None
        if self._modules is None:
            from repowise.core.ingestion.resolvers.go import read_go_modules

            self._modules = read_go_modules(self._index.repo_path)
        for module_dir, module in self._modules:  # longest module path first
            if imported == module or imported.startswith(module + "/"):
                rest = imported[len(module) :].strip("/")
                package = posixpath.join(module_dir, rest) if module_dir else rest
                return package if package in self._dirs else None
        return None

    def _method_of(self, package: str, type_name: str, method: str) -> IndexedSymbol | None:
        found = [
            sym
            for f in self._dirs.get(package, ())
            for sym in self._index.symbols_for_file(f)
            if sym.name == method
            and sym.kind == "method"
            and _QUALIFIER_RE.split(sym.qualified_name)[-2:] == [type_name, method]
        ]
        return found[0] if len(found) == 1 else None


def _in_directories(index: RepoIndex, keep: Any) -> Iterable[IndexedSymbol]:
    return (
        sym
        for f in index.files()
        if keep(posixpath.dirname(f))
        for sym in index.symbols_for_file(f)
    )


def _named_in(symbols: Iterable[IndexedSymbol], name: str) -> IndexedSymbol | None:
    found = [s for s in symbols if s.name == name]
    return found[0] if len(found) == 1 else None


def _js_import(index: RepoIndex, file_path: str, local: str) -> tuple[str, str] | None:
    """``(indexed file, exported name)`` *local* is imported as in *file_path*, or None."""
    from repowise.core.ingestion.framework_routes_js import (
        js_imports,
        module_key,
        resolve_module,
    )

    content = _read(index.repo_path / file_path)
    if content is None:
        return None
    spec = js_imports(content).get(local)
    module = resolve_module(file_path, spec[0]) if spec is not None else None
    if spec is None or module is None:
        return None
    matches = [f for f in index.files() if module_key(f) == module]
    return (matches[0], spec[1]) if len(matches) == 1 else None


def _js_handler_expression(content: str | None, line: int, path: str) -> str | None:
    """The last argument of the router call on *line*, when it names a function.

    Express runs a route's arguments as a middleware chain and the last one
    answers the request, so that is the handler. An inline function, a call
    (``wrap(fn)``) or anything but a name or member chain gives None. Two calls
    on one line are told apart by *path*, the contract's (mounted) path.
    """
    if not content:
        return None
    from repowise.core.ingestion.framework_routes import express_routes

    from .extractors.base import line_at
    from .extractors.http.paths import normalize_http_path
    from .extractors.strings import call_arguments

    on_line = [r for r in express_routes(content) if line_at(content, r.offset) == line]
    if len(on_line) > 1:
        on_line = [r for r in on_line if r.path and path.endswith(normalize_http_path(r.path))]
    if len(on_line) != 1:
        return None
    args = call_arguments(content, on_line[0].paren_offset)
    if args and not args[-1]:
        args = args[:-1]  # a trailing comma
    if not args or len(args) < 2:
        return None
    last = args[-1].strip()
    return last if _HANDLER_EXPR_RE.fullmatch(last) else None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Reading the repo and storing the table
# ---------------------------------------------------------------------------


def _route_config() -> Any:
    """HTTP only: the route table needs no gRPC, socket, topic or data reads."""
    from .config import ContractConfig

    return ContractConfig(
        detect_grpc=False,
        detect_socket=False,
        detect_topics=False,
        detect_data=False,
        detect_code_api=False,
    )


def routes_fingerprint(repo_path: Path) -> str:
    """What the stored rows were read with: extractor versions and the repo's excludes."""
    from .contracts import CONTRACTS_VERSION
    from .repo_contracts import _repo_exclude_patterns

    raw = json.dumps(
        [ROUTE_TABLE_VERSION, CONTRACTS_VERSION, _repo_exclude_patterns(repo_path)]
    ).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def routes_relevant(paths: Iterable[str]) -> bool:
    """Whether any of *paths* can change the route table (a false yes costs one read)."""
    from .repo_contracts import _relevance

    relevant = _relevance(_route_config())
    return any(relevant(p) for p in paths)


async def read_routes(repo_path: Path, index: RepoIndex | None) -> list[Route]:
    """Every route *repo_path* registers, handlers resolved in *index*.

    Raises on an extractor failure; nothing is written here.
    """
    import asyncio

    from .code_api import CodeSurface
    from .contracts import _extract_one_repo, _RepoExtraction
    from .extractors.base import make_exclude_predicate
    from .extractors.masstransit import MessageTypeIndex
    from .repo_contracts import _repo_exclude_patterns, _with_repo_excludes
    from .repo_index import WorkspaceIndex

    repo_path = Path(repo_path).resolve()
    alias = repo_path.name
    config = _route_config()
    exclude = _with_repo_excludes(
        make_exclude_predicate(tuple(config.exclude_globs)), _repo_exclude_patterns(repo_path)
    )
    extraction = _RepoExtraction(
        contract_config=config,
        exclude=exclude,
        code_surface=CodeSurface(),
        message_types=MessageTypeIndex(),
        boundaries_by_repo=None,
        # Not closed here: the index belongs to the caller's session.
        workspace_index=WorkspaceIndex({alias: index} if index is not None else {}),
    )
    contracts, _stats = await _extract_one_repo(extraction, alias, repo_path)
    cache: dict[str, str | None] = {}

    def read_file(rel: str) -> str | None:
        if rel not in cache:
            cache[rel] = _read(repo_path / rel)
        return cache[rel]

    return await asyncio.to_thread(routes_from_contracts, contracts, index, read_file)


async def refresh_route_table(
    session: Any,
    repo_id: str,
    repo_path: Path | str,
    *,
    changed_paths: Iterable[str] | None = None,
) -> int | None:
    """Re-read *repo_path*'s routes into ``http_routes``; returns the rows written.

    *changed_paths* None (an init) always reads. Given (an update), it reads
    only when one of them can hold a route or the stored rows were read with
    another fingerprint (an empty list reads on a fingerprint change alone),
    and returns None when it skipped. A missing checkout reads nothing. A
    failed read is logged, keeps the stored rows and clears the stamp so the
    next update reads again (None too). The write is one savepoint: a database
    error leaves the rows as they were and is the caller's.
    """
    from repowise.core.persistence.crud.http_routes import (
        replace_http_routes,
        stamp_routes_fingerprint,
        stored_routes_fingerprint,
    )

    from .repo_index import RepoIndex

    repo_path = Path(repo_path)
    if not repo_path.is_dir():
        return None
    fingerprint = routes_fingerprint(repo_path)
    if changed_paths is not None:
        stored = await stored_routes_fingerprint(session, repo_id)
        if stored == fingerprint and not routes_relevant(changed_paths):
            return None
    try:
        index = RepoIndex(repo_path.name, repo_path.resolve(), session, None, repo_id=repo_id)
        await index._load(repo_id)
        routes = await read_routes(repo_path, index)
    except Exception as exc:
        _log.warning("route table not refreshed: %r", exc)
        async with session.begin_nested():
            await stamp_routes_fingerprint(session, repo_id, None)
        return None
    try:
        async with session.begin_nested():
            await replace_http_routes(session, repo_id, routes, fingerprint)
    except Exception:
        # The savepoint put the old rows and their stamp back; clear the stamp
        # so the next update reads again rather than trusting them.
        async with session.begin_nested():
            await stamp_routes_fingerprint(session, repo_id, None)
        raise
    return len(routes)


__all__ = [
    "ROUTE_TABLE_VERSION",
    "Route",
    "read_routes",
    "refresh_route_table",
    "routes_fingerprint",
    "routes_from_contracts",
    "routes_relevant",
]
