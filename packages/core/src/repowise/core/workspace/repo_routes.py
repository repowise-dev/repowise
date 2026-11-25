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

#: Frameworks whose route is a call taking its handler as an argument. There
#: the symbol declared just below the call is never the handler (a decorator
#: or annotation framework is the opposite), so a row binds only a named one.
_CALL_STYLE = frozenset(
    {
        "express", "hono", "koa", "fastify", "elysia", "go", "axum", "javalin",
        "ktor", "sinatra", "grape", "rails", "laravel", "hapi", "django",
        "aspnet-minimal",
    }
)  # fmt: skip
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
            expr = _js_handler_expression(read_file(c.file_path), c.line)
        symbol_id = _handler_symbol(c, framework, expr, index)
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
    c: Contract, framework: str, expr: str | None, index: RepoIndex | None
) -> str | None:
    """The symbol id serving route *c*, or None rather than a guess."""
    if index is None:
        return None
    bound = _symbol_by_id(index, c.symbol_id) if c.symbol_id else None
    if expr:
        name = _QUALIFIER_RE.split(expr)[-1]
        # The contract already bound the name (index-backed dialects, or a
        # handler the repo names once); the line fallback lands elsewhere.
        if bound is not None and bound.name == name:
            return bound.symbol_id
        found = _resolve_named(index, c.file_path, expr, framework)
        return found.symbol_id if found is not None else None
    if bound is None or framework in _CALL_STYLE:
        return None
    # A decorator or annotation names the symbol that opens on or below it;
    # a symbol whose body holds the line is where the route is registered.
    return bound.symbol_id if bound.start_line >= c.line else None


def _symbol_by_id(index: RepoIndex, symbol_id: str) -> IndexedSymbol | None:
    for sym in index.symbols_for_file(symbol_id.split("::", 1)[0]):
        if sym.symbol_id == symbol_id:
            return sym
    return None


def _resolve_named(
    index: RepoIndex, file_path: str, expr: str, framework: str
) -> IndexedSymbol | None:
    """The one symbol *expr* names from *file_path*, nearest scope first.

    The registering file, then for a JavaScript ``mod.fn`` the module ``mod``
    is imported from, then for Go the file's package (its directory), then the
    repo when the name is unique there. More than one candidate at a level is
    no answer.
    """
    parts = _QUALIFIER_RE.split(expr)
    name = parts[-1]
    if len(parts) == 1:
        local = _named_in(index.symbols_for_file(file_path), name)
        if local is not None:
            return local
    if len(parts) > 1 and framework in _JS_ROUTERS | {"fastify"}:
        target = _js_module_file(index, file_path, parts[0])
        if target is not None:
            return _named_in(index.symbols_for_file(target), name)
    if framework == "go":
        package = posixpath.dirname(file_path)
        found = _named_in(
            (
                sym
                for f in index.files()
                if posixpath.dirname(f) == package
                for sym in index.symbols_for_file(f)
            ),
            name,
        )
        if found is not None:
            return found
    return index.symbol_named(expr)


def _named_in(symbols: Iterable[IndexedSymbol], name: str) -> IndexedSymbol | None:
    found = [s for s in symbols if s.name == name]
    return found[0] if len(found) == 1 else None


def _js_module_file(index: RepoIndex, file_path: str, local: str) -> str | None:
    """The indexed file the module bound to *local* in *file_path* is, or None."""
    from repowise.core.ingestion.framework_routes_js import (
        js_imports,
        module_key,
        resolve_module,
    )

    content = _read(index.repo_path / file_path)
    if content is None:
        return None
    spec = js_imports(content).get(local)
    if spec is None or spec[1] != "default":
        return None  # a named import's member is not followed
    module = resolve_module(file_path, spec[0])
    if module is None:
        return None
    matches = [f for f in index.files() if module_key(f) == module]
    return matches[0] if len(matches) == 1 else None


def _js_handler_expression(content: str | None, line: int) -> str | None:
    """The last argument of the router call on *line*, when it names a function.

    Express runs a route's arguments as a middleware chain and the last one
    answers the request, so that is the handler. An inline function, a call
    (``wrap(fn)``) or anything but a name or member chain gives None.
    """
    if not content:
        return None
    from repowise.core.ingestion.framework_routes import express_routes

    from .extractors.base import line_at
    from .extractors.strings import call_arguments

    for route in express_routes(content):
        if line_at(content, route.offset) != line:
            continue
        args = call_arguments(content, route.paren_offset)
        if not args or len(args) < 2:
            return None
        last = args[-1].strip()
        return last if _HANDLER_EXPR_RE.fullmatch(last) else None
    return None


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
    another fingerprint, and returns None when it skipped. An extractor
    failure is logged and leaves the stored rows as they were (None too); a
    database error is the caller's, like every other write in its session.
    """
    from repowise.core.persistence.crud.http_routes import (
        replace_http_routes,
        stamp_routes_fingerprint,
        stored_routes_fingerprint,
    )

    from .repo_index import RepoIndex

    repo_path = Path(repo_path)
    fingerprint = routes_fingerprint(repo_path)
    if changed_paths is not None:
        stored = await stored_routes_fingerprint(session, repo_id)
        if stored == fingerprint and not routes_relevant(changed_paths):
            return None
    index = RepoIndex(repo_path.name, repo_path.resolve(), session, None, repo_id=repo_id)
    await index._load(repo_id)
    try:
        routes = await read_routes(repo_path, index)
    except Exception as exc:
        _log.warning("route table not refreshed: %r", exc)
        # The rows stay; an empty stamp makes the next update read again.
        await stamp_routes_fingerprint(session, repo_id, None)
        return None
    await replace_http_routes(session, repo_id, routes, fingerprint)
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
