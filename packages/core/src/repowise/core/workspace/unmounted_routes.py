"""Routes registered by a function nothing mounts: left out of the providers.

A Go repo can keep two copies of a register function, ``InitSkillsRouter`` in
``router/system`` and in ``plugin/auto/router``, with only one wired to the
server. Both declare the same routes, so every caller of those paths linked to
the live copy and the dead one alike.

This reads the stored graph once the index exists. A provider's registering
function is the named function or method whose body holds the route call. It
is unmounted when all of these hold:

* no ``calls`` or ``references`` edge from production code targets it;
* no production file that can reach its package (its own package, or a file
  importing it directly or through other packages) names it, outside its
  own declaration, comments and strings;
* no interface in the repo declares a method of its name (a call through the
  interface reaches every implementation, and names none of them);
* and some production file does call that name, from where it reaches another
  declaration of the name: the call belongs to the other copy.

The last rule is what keeps a register function nobody in the repo names (one a
framework or another repo calls through an interface) in place: only a function
whose name is called, and always for a different copy, is dropped. Without
wiki.db, or with no import edges stored, nothing is dropped.

Go only: a Go package is its directory, every call names its function (no
default export, no ``require('./x')(app)``), and ``main``/``init`` are the only
entry points the runtime calls by itself. Ceiling: a JS/TS router built in a
function needs the default-export and module-call shapes read first; the same
graph test would then apply.
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING

from .extractors.langs import GO

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Sequence

    from .contracts import Contract
    from .repo_index import IndexedSymbol, RepoIndex

#: The ``extraction_stats`` key counting providers left out as unmounted.
UNMOUNTED_PROVIDERS_STAT = "http_providers_unmounted"

_REGISTERING_KINDS = frozenset({"function", "method"})
#: Called by the Go runtime itself, never by name.
_ENTRY_POINTS = frozenset({"main", "init"})


async def drop_unmounted_providers(
    contracts: list[Contract],
    repo_index: RepoIndex | None,
    files: Iterable[tuple[str, str, str]],
) -> tuple[list[Contract], int]:
    """*contracts* without the Go HTTP providers an unmounted function registers,
    and how many were left out. *files* are the walked ``(path, suffix, content)``."""
    if repo_index is None or repo_index.session is None:
        return contracts, 0
    registering = _registering_symbols(contracts, repo_index)
    if not registering:
        return contracts, 0
    go_files = {path: content for path, suffix, content in files if suffix in GO}
    graph = await _read_graph(repo_index, {s.symbol_id for s in registering.values()})
    if graph is None:
        return contracts, 0
    imports, called = graph
    dead = await _unmounted(registering, repo_index, go_files, imports, called)
    if not dead:
        return contracts, 0
    kept = [c for c in contracts if id(c) not in dead]
    return kept, len(contracts) - len(kept)


def _registering_symbols(
    contracts: Sequence[Contract], repo_index: RepoIndex
) -> dict[int, IndexedSymbol]:
    """``id(contract) -> the function whose body registers it``, Go HTTP providers only."""
    out: dict[int, IndexedSymbol] = {}
    for c in contracts:
        if c.contract_type != "http" or c.role != "provider" or not c.line:
            continue
        if posixpath.splitext(c.file_path)[1].lower() not in GO:
            continue
        sym = repo_index.symbol_at(c.file_path, c.line)
        if (
            sym is not None
            and sym.kind in _REGISTERING_KINDS
            and sym.start_line < c.line
            and sym.name not in _ENTRY_POINTS
        ):
            out[id(c)] = sym
    return out


async def _read_graph(
    repo_index: RepoIndex, targets: Collection[str]
) -> tuple[dict[str, set[str]], set[tuple[str, str]]] | None:
    """``(importers by imported Go file, callers of *targets*)``; ``None`` when
    the graph cannot be read or holds no Go import edge."""
    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.core.persistence.models import GraphEdge

    session = repo_index.session
    importers: dict[str, set[str]] = {}
    callers: set[tuple[str, str]] = set()
    try:
        rows = await session.execute(
            select(GraphEdge.source_node_id, GraphEdge.target_node_id).where(
                GraphEdge.repository_id == repo_index.repo_id,
                GraphEdge.edge_type == "imports",
                GraphEdge.source_node_id.like("%.go"),
                GraphEdge.target_node_id.like("%.go"),
            )
        )
        for source, target in rows:
            importers.setdefault(target, set()).add(source)
        wanted = sorted(targets)
        for i in range(0, len(wanted), 400):
            rows = await session.execute(
                select(GraphEdge.source_node_id, GraphEdge.target_node_id).where(
                    GraphEdge.repository_id == repo_index.repo_id,
                    GraphEdge.edge_type.in_(("calls", "references")),
                    GraphEdge.target_node_id.in_(wanted[i : i + 400]),
                )
            )
            callers.update((source, target) for source, target in rows)
    except (SQLAlchemyError, AttributeError, TypeError):
        return None  # a stand-in session, or an index without a graph
    if not importers:
        return None
    return importers, callers


class _Reach:
    """Which files can reach a Go package: the package's own, and every importer up the chain."""

    def __init__(self, importers: dict[str, set[str]], go_files: Collection[str]) -> None:
        self._importers = importers
        self._by_dir: dict[str, list[str]] = {}
        for path in go_files:
            self._by_dir.setdefault(posixpath.dirname(path), []).append(path)
        self._memo: dict[str, frozenset[str]] = {}

    def reachers(self, directory: str) -> frozenset[str]:
        if directory not in self._memo:
            seen = set(self._by_dir.get(directory, ()))
            stack = list(seen)
            while stack:
                for source in self._importers.get(stack.pop(), ()):
                    if source not in seen:
                        seen.add(source)
                        stack.append(source)
            self._memo[directory] = frozenset(seen)
        return self._memo[directory]


def _declaration_re(name: str) -> re.Pattern[str]:
    return re.compile(rf"\bfunc\s*(?:\([^()]*\)\s*)?{re.escape(name)}\b")


def _mentions(name: str, masked: dict[str, str]) -> set[str]:
    """Files naming *name* other than in a ``func`` declaration of it."""
    word = re.compile(rf"(?<![\w]){re.escape(name)}\b")
    decl = _declaration_re(name)
    out: set[str] = set()
    for path, text in masked.items():
        if name not in text:
            continue
        declared = {m.end() for m in decl.finditer(text)}
        if any(m.end() not in declared for m in word.finditer(text)):
            out.add(path)
    return out


def _interface_method(name: str, repo_index: RepoIndex) -> bool:
    """Whether an interface in the repo declares a method *name*."""
    for sym in repo_index.symbols_named(name):
        parent, sep, _ = sym.symbol_id.rpartition("::")
        if sep and any(
            s.symbol_id == parent and s.kind == "interface"
            for s in repo_index.symbols_for_file(sym.file_path)
        ):
            return True
    return False


async def _unmounted(
    registering: dict[int, IndexedSymbol],
    repo_index: RepoIndex,
    go_files: dict[str, str],
    importers: dict[str, set[str]],
    called: set[tuple[str, str]],
) -> set[int]:
    """The ``id(contract)`` of each provider whose registering function is unmounted."""
    from repowise.core.ingestion.symbol_identity import file_of_symbol

    from .contracts import _NOT_CONTRACT_ORIGINS, stored_origins
    from .extractors.base import _comment_mask

    names = {s.name for s in registering.values()}
    candidates = [p for p, text in go_files.items() if any(n in text for n in names)]
    origins = await stored_origins(candidates, repo_index) if candidates else {}
    production = {p for p in candidates if origins.get(p) not in _NOT_CONTRACT_ORIGINS}
    # A test calling a register function does not mount it.
    caller_files = {file_of_symbol(source) for source, _ in called}
    caller_origins = await stored_origins(caller_files, repo_index) if caller_files else {}
    called_targets = {
        target
        for source, target in called
        if caller_origins.get(file_of_symbol(source)) not in _NOT_CONTRACT_ORIGINS
    }
    masked = {p: _comment_mask(".go", go_files[p], strings=True) or go_files[p] for p in production}
    reach = _Reach(importers, go_files)
    verdicts: dict[str, bool] = {}
    dead: set[int] = set()
    for key, sym in registering.items():
        if sym.symbol_id not in verdicts:
            verdicts[sym.symbol_id] = sym.symbol_id not in called_targets and _is_dead_copy(
                sym, repo_index, masked, reach
            )
        if verdicts[sym.symbol_id]:
            dead.add(key)
    return dead


def _is_dead_copy(
    sym: IndexedSymbol, repo_index: RepoIndex, masked: dict[str, str], reach: _Reach
) -> bool:
    """Whether *sym*'s name is called only from where another declaration of it is reached."""
    mentions = _mentions(sym.name, masked)
    own = reach.reachers(posixpath.dirname(sym.file_path))
    if not mentions or mentions & own or _interface_method(sym.name, repo_index):
        return False
    others = {
        posixpath.dirname(s.file_path)
        for s in repo_index.symbols_named(sym.name)
        if s.symbol_id != sym.symbol_id
        and s.kind in _REGISTERING_KINDS
        and posixpath.splitext(s.file_path)[1].lower() in GO
    }
    return any(mentions & reach.reachers(d) for d in others)


__all__ = ["UNMOUNTED_PROVIDERS_STAT", "drop_unmounted_providers"]
