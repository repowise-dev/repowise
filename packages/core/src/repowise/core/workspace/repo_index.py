"""Read-only index access for the cross-repo hooks.

The hooks run immediately after ingestion writes each repo's
``.repowise/wiki.db`` and, until now, re-derived from raw text what that
database already holds. This is the read side: one connection per repo, opened
once and held across every hook phase.

Read-only in intent, not in mechanism — the shared :func:`.registry.open_repo_db`
runs the same schema check the MCP read path does.

Line ranges here are the *index's*, so a file edited after its repo was
indexed can hand back a span that no longer describes the bytes on disk. A
caller slicing source with these numbers must bound them against the content
it holds — see :func:`.extractors.from_index.symbols_for_content`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger("repowise.workspace.repo_index")

# Node-id prefix ingestion gives an import target it could not resolve inside
# the repo — a third-party package or a sibling workspace repo.
EXTERNAL_PREFIX = "external:"

# How far below a declaration line its symbol may open. Covers a stack of
# decorators or annotations and the blank lines between them. The ceiling: a
# declaration this close above an unrelated *nested* definition binds to it,
# since the index alone cannot tell a decorator run from ordinary code. Reading
# the intervening text would settle it, at the cost of the caller holding the
# file (see :func:`.extractors.from_index._decorators_above`, which does).
_DECLARATION_LOOKAHEAD = 8


@dataclass(frozen=True)
class IndexedSymbol:
    """One ``wiki_symbols`` row: what ingestion parsed, without the body."""

    symbol_id: str  # "<rel_path>::<name>", the ingestion Symbol.id
    name: str
    qualified_name: str
    kind: str
    signature: str
    file_path: str
    start_line: int  # 1-indexed, inclusive
    end_line: int  # 1-indexed, inclusive
    visibility: str
    #: ingestion's language key. Defaults so a caller building one by hand
    #: (the tests, and from_index) need not know about it.
    language: str = ""


@dataclass(frozen=True)
class ExternalImport:
    """An ``imports`` edge from a repo file to an unresolved external target."""

    source_file: str
    external_name: str  # the target with its ``external:`` prefix stripped
    imported_names: tuple[str, ...]


#: What separates a qualifier from the name it qualifies, across the languages
#: whose handler expressions reach :meth:`RepoIndex.symbol_named`.
_QUALIFIER_RE = re.compile(r"::|\.")


class RepoIndex:
    """Read-only accessor over one repo's ``wiki.db``.

    Built by :func:`open_workspace_index`; the caller owns :meth:`close`. Every
    accessor is a pure in-memory read of what :meth:`_load` fetched, so a
    dialect may call one from the worker thread it runs in.
    """

    def __init__(self, alias: str, repo_path: Path, session: AsyncSession, engine: Any) -> None:
        self.alias = alias
        self.repo_path = repo_path
        self._session = session
        self._engine = engine
        self._by_file: dict[str, list[IndexedSymbol]] = {}
        self._by_name: dict[str, list[IndexedSymbol]] = {}
        self._externals: list[ExternalImport] = []

    # -- Loading -----------------------------------------------------------

    async def _load(self, repo_id: str) -> None:
        from sqlalchemy import select

        from repowise.core.persistence.models import GraphEdge, WikiSymbol

        rows = await self._session.execute(
            select(
                WikiSymbol.symbol_id,
                WikiSymbol.name,
                WikiSymbol.qualified_name,
                WikiSymbol.kind,
                WikiSymbol.signature,
                WikiSymbol.file_path,
                WikiSymbol.start_line,
                WikiSymbol.end_line,
                WikiSymbol.visibility,
                WikiSymbol.language,
            ).where(WikiSymbol.repository_id == repo_id)
        )
        for row in rows:
            # By keyword: eight of the ten fields are strings, so a reordered
            # or inserted column would swap values positionally without error.
            self._by_file.setdefault(row.file_path, []).append(
                IndexedSymbol(
                    symbol_id=row.symbol_id,
                    name=row.name,
                    qualified_name=row.qualified_name,
                    kind=row.kind,
                    signature=row.signature,
                    file_path=row.file_path,
                    start_line=row.start_line,
                    end_line=row.end_line,
                    visibility=row.visibility,
                    language=row.language or "",
                )
            )
        # Outermost first, so the first span containing a line is the class and
        # the last is the method.
        for symbols in self._by_file.values():
            symbols.sort(key=lambda s: (s.start_line, -s.end_line))
            for sym in symbols:
                self._by_name.setdefault(sym.name, []).append(sym)

        edges = await self._session.execute(
            select(
                GraphEdge.source_node_id,
                GraphEdge.target_node_id,
                GraphEdge.imported_names_json,
            ).where(
                GraphEdge.repository_id == repo_id,
                GraphEdge.edge_type == "imports",
                GraphEdge.target_node_id.startswith(EXTERNAL_PREFIX),
            )
        )
        for source, target, names_json in edges:
            try:
                names = json.loads(names_json or "[]")
            except ValueError:
                names = []
            if not isinstance(names, list):
                names = []
            self._externals.append(
                ExternalImport(
                    source_file=source,
                    external_name=target[len(EXTERNAL_PREFIX) :],
                    imported_names=tuple(n for n in names if isinstance(n, str)),
                )
            )

    # -- Public API --------------------------------------------------------

    def symbols_for_file(self, rel_path: str) -> list[IndexedSymbol]:
        """Symbols declared in *rel_path* (POSIX, repo-relative), outermost first."""
        return self._by_file.get(rel_path, [])

    def symbol_at(self, rel_path: str, line: int) -> IndexedSymbol | None:
        """The innermost symbol whose span contains *line*, or None."""
        best: IndexedSymbol | None = None
        for sym in self._by_file.get(rel_path, ()):
            if sym.start_line <= line <= sym.end_line and (
                best is None or sym.start_line >= best.start_line
            ):
                best = sym
        return best

    def declared_symbol_at(self, rel_path: str, line: int) -> IndexedSymbol | None:
        """The symbol a declaration on *line* names, or None.

        :meth:`symbol_at` answers "which symbol contains this line", which is
        the wrong question for a declaration that sits *above* its handler: a
        route decorator or annotation is outside the handler's span, because
        the parser takes a symbol's span from the definition node rather than
        the decorated one. So a symbol opening just below *line* is preferred,
        but only when it is nested inside whatever contains *line* — that guard
        is what stops a call on a function's last line from binding to the next
        function down.
        """
        containing = self.symbol_at(rel_path, line)
        # A match *on* a symbol's own declaration line names that symbol, not
        # the first member under it (a gRPC servicer class, say).
        if containing is not None and containing.start_line == line:
            return containing
        following: IndexedSymbol | None = None
        for sym in self._by_file.get(rel_path, ()):
            if line < sym.start_line <= line + _DECLARATION_LOOKAHEAD and (
                following is None or sym.start_line < following.start_line
            ):
                following = sym
        if following is not None and (
            containing is None
            or containing.start_line <= following.start_line <= containing.end_line
        ):
            return following
        return containing

    def symbol_named(self, expression: str) -> IndexedSymbol | None:
        """The one symbol *expression* names in this repo, or None.

        *expression* may be qualified (``OrderHandlers.GetOrder``,
        ``handlers::ping``); the qualifier settles a member name that is
        ambiguous on its own, which is the common case for the handler shape
        this exists for (``Endpoint.HandleAsync``). A name that still resolves to
        more than one symbol is refused rather than guessed: which one the caller
        meant would otherwise be decided by index row order.

        ``::`` separates a qualifier too. Without it a Rust handler written
        ``handlers::ping`` is looked up whole, matches nothing, and the caller
        falls back to a line lookup that binds the route to its router builder.
        """
        parts = _QUALIFIER_RE.split(expression)
        found = self._by_name.get(parts[-1], ())
        if len(found) > 1 and len(parts) > 1:
            tail = parts[-2:]
            found = [
                s for s in found if _QUALIFIER_RE.split(s.qualified_name)[-2:] == tail
            ]
        return found[0] if len(found) == 1 else None

    def external_import_edges(self) -> list[ExternalImport]:
        """Every ``imports`` edge leaving the repo, with the names it consumes."""
        return self._externals

    def public_symbols(self) -> list[IndexedSymbol]:
        """Symbols ingestion marked public, across the whole repo."""
        return [s for syms in self._by_file.values() for s in syms if s.visibility == "public"]

    async def close(self) -> None:
        await self._session.close()
        await self._engine.dispose()


class WorkspaceIndex:
    """The open :class:`RepoIndex` for each workspace repo that has one."""

    def __init__(self, repos: dict[str, RepoIndex]) -> None:
        self._repos = repos

    def get(self, alias: str) -> RepoIndex | None:
        return self._repos.get(alias)

    async def close(self) -> None:
        for repo in self._repos.values():
            try:
                await repo.close()
            except Exception:
                _log.warning("Error closing index for '%s'", repo.alias, exc_info=True)
        self._repos.clear()


async def open_repo_index(alias: str, repo_path: Path) -> RepoIndex | None:
    """Open *repo_path*'s ``wiki.db`` read side, or None when it has no index."""
    from sqlalchemy import select

    from repowise.core.persistence.crud.repository import get_repository_by_path
    from repowise.core.persistence.models import Repository

    from .registry import open_repo_db, repo_db_path

    if not repo_db_path(repo_path).is_file():
        return None
    engine, session_factory = await open_repo_db(repo_path)

    session = session_factory()
    ok = False
    try:
        # A wiki.db normally holds one repository row; prefer the one recorded
        # at this checkout when it holds more, since test runs and moved
        # checkouts leave others behind. Newest first so the fallback is a
        # decision rather than whatever order the scan returns.
        repo = await get_repository_by_path(session, str(repo_path))
        if repo is None:
            repo = (
                await session.execute(
                    select(Repository).order_by(Repository.updated_at.desc()).limit(1)
                )
            ).scalar_one_or_none()
        if repo is None:
            raise LookupError(f"No repository row in {repo_path}")
        index = RepoIndex(alias, repo_path, session, engine)
        await index._load(repo.id)
        ok = True
    finally:
        # BaseException too: a cancellation landing in _load would otherwise
        # leak the engine and its connection.
        if not ok:
            await session.close()
            await engine.dispose()
    return index


async def open_workspace_index(ws_config: Any, workspace_root: Path) -> WorkspaceIndex:
    """Open every indexed repo in the workspace. A repo that fails is skipped."""
    entries = list(ws_config.repos)
    opened = await asyncio.gather(
        *(
            open_repo_index(e.alias, (workspace_root / e.path).resolve())
            for e in entries
        ),
        return_exceptions=True,
    )
    repos: dict[str, RepoIndex] = {}
    for entry, index in zip(entries, opened, strict=True):
        if isinstance(index, BaseException):
            _log.warning("Could not open the index for '%s'", entry.alias, exc_info=index)
        elif index is not None:
            repos[entry.alias] = index
    return WorkspaceIndex(repos)
