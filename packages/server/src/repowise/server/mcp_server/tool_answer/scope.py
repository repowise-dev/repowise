"""A get_answer ``scope``: one or several path prefixes, read against the index.

Agents pass several directories in one scope ("pkg/maps pkg/datapath",
"a/, b/") and directories the index does not hold. Read as one literal prefix,
both match nothing and the reply could only say "no wiki hits". Here a scope
becomes the union of its prefixes, written canonically as the prefixes joined
by :data:`~repowise.server.mcp_server._page_paths.SCOPE_SEPARATOR` (what
``in_scope`` and the search path filter read), and a prefix no indexed file
lives under is named back to the caller with the existing directories nearest
to it.
"""

from __future__ import annotations

import asyncio
import difflib
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

from sqlalchemy import func, or_, select, union

from repowise.core.persistence.crud import indexed_file
from repowise.core.persistence.models import GraphNode, Page
from repowise.server.mcp_server._line_hits import _git_paths
from repowise.server.mcp_server._page_paths import SCOPE_SEPARATOR
from repowise.server.mcp_server.tool_answer.documents import _is_document
from repowise.server.mcp_server.tool_answer.synthesis import _normalize_scope

#: Directories a scope that matched nothing is pointed at.
_NEAREST_DIRS = 5


class ScopeResolution(NamedTuple):
    # Canonical scope over the prefixes the index holds; None when unscoped or
    # when no prefix matched.
    scope: str | None
    # Prefixes no indexed file lives under, as the caller wrote them.
    missing: list[str]
    # Existing directories near the missing prefixes, filled only when no
    # prefix matched (the call then has nothing to search).
    nearest: list[str]


def scope_label(scope: str) -> str:
    """A canonical scope as a reply names it: ``pkg/a/, pkg/b/``."""
    return ", ".join(f"{p}/" for p in scope.split(SCOPE_SEPARATOR))


def _outermost(prefixes: list[str]) -> list[str]:
    """*prefixes* sorted, without any that another one already holds."""
    kept: list[str] = []
    for p in sorted(set(prefixes)):
        if not any(f"{p}/".startswith(f"{k}/") for k in kept):
            kept.append(p)
    return kept


def _under(column: Any, prefix: str) -> Any:
    """*column* is *prefix* or a path under it as a directory, exact case."""
    under = f"{prefix}/"
    return or_(column == prefix, func.substr(column, 1, len(under)) == under)


def _indexed_paths(repo_id: Any) -> tuple[Any, Any]:
    """The two places a file the index read is written: its graph row, and the
    wiki page retrieval serves (a page's path is what a scope filters)."""
    return (
        select(GraphNode.node_id.label("path")).where(
            GraphNode.repository_id == repo_id, indexed_file()
        ),
        select(Page.target_path.label("path")).where(
            Page.repository_id == repo_id, Page.page_type == "file_page"
        ),
    )


def _documents_under(root: Path | None, prefix: str) -> bool:
    """Whether git tracks a document under *prefix*. Documents are served from
    the checkout (documents.py), so their directory may hold no graph row or
    page; an untracked or ignored directory is not one the answer reads."""
    if root is None:
        return False
    listed = _git_paths(root, ["ls-files", "-z", "--", prefix], 10, (0,))
    return any(_is_document(p) for p in listed or ())


async def _holds_files(session: Any, repo_id: Any, prefix: str, root: Path | None) -> bool:
    graph, pages = _indexed_paths(repo_id)
    for query, column in ((graph, GraphNode.node_id), (pages, Page.target_path)):
        if await session.scalar(query.where(_under(column, prefix)).limit(1)) is not None:
            return True
    return await asyncio.to_thread(_documents_under, root, prefix)


async def _split_pieces(session: Any, repo_id: Any, raw: str, root: Path | None) -> list[str]:
    """The prefixes *raw* names. Commas and whitespace separate, unless the
    piece they would split is itself an indexed path ("My Docs", "a,b")."""

    async def split(piece: str, sep: str | None) -> list[str]:
        norm = _normalize_scope(piece)
        if norm is None:
            return []
        parts = norm.split(sep)
        if len(parts) == 1 or await _holds_files(session, repo_id, norm, root):
            return [norm]
        return [n for part in parts if (n := _normalize_scope(part))]

    prefixes: list[str] = []
    for piece in await split(raw, ","):
        for p in await split(piece, None):
            prefixes.append(p)
    return list(dict.fromkeys(prefixes))


def _named_like(tail: str, by_name: dict[str, list[str]]) -> list[str]:
    """Directories whose name holds *tail*, sits inside it, or spells it closely."""
    names = {
        n for n in by_name if len(tail) >= 3 and (tail in n or (len(n) >= 3 and n in tail))
    }
    names.update(difflib.get_close_matches(tail, list(by_name), n=_NEAREST_DIRS, cutoff=0.8))
    return [d for n in names for d in by_name[n]]


def _children(inside: str, dir_files: Counter) -> list[str]:
    """The directories directly in *inside* (``""`` for the root), largest first."""
    depth = inside.count("/")
    kids = (d for d in dir_files if d.startswith(inside) and d.count("/") == depth)
    return sorted(kids, key=lambda d: (-dir_files[d], d))


def _nearest(missing: str, ancestor: str, dir_files: Counter) -> list[str]:
    """Directories inside *ancestor* near *missing*: those named like its last
    segment, shallowest and largest first; without one, the largest directories
    directly inside *ancestor*. *dir_files* holds the directories under it."""
    by_name: dict[str, list[str]] = {}
    for d in dir_files:
        by_name.setdefault(d.rsplit("/", 1)[-1].lower(), []).append(d)
    inside = f"{ancestor}/" if ancestor else ""
    named = _named_like(missing.rsplit("/", 1)[-1].lower(), by_name)
    return sorted(named, key=lambda d: (d.count("/"), -dir_files[d], d)) or _children(
        inside, dir_files
    )


async def _deepest_ancestor(session: Any, repo_id: Any, missing: str) -> str:
    """The deepest indexed directory above *missing*; ``""`` for the root."""
    parts = missing.split("/")
    for i in range(len(parts) - 1, 0, -1):
        if await _holds_files(session, repo_id, ancestor := "/".join(parts[:i]), None):
            return ancestor
    return ""


async def _directories(session: Any, repo_id: Any, ancestor: str) -> Counter:
    """The directories holding indexed files under *ancestor* (every one for
    ``""``), with how many files each holds. Read only on a miss, and only
    under the deepest indexed parent, so a deep miss reads a subtree."""
    graph, pages = _indexed_paths(repo_id)
    if ancestor:
        graph = graph.where(_under(GraphNode.node_id, ancestor))
        pages = pages.where(_under(Page.target_path, ancestor))
    counts: Counter = Counter()
    for (path,) in await session.execute(union(graph, pages)):
        parts = path.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            counts["/".join(parts[:i])] += 1
    return counts


async def _suggestions(session: Any, repo_id: Any, missing: list[str]) -> list[str] | None:
    """The nearest indexed directories to *missing*; None when the index holds
    no directory at all, so it cannot judge a prefix."""
    picked: list[str] = []
    for m in missing:
        ancestor = await _deepest_ancestor(session, repo_id, m)
        dir_files = await _directories(session, repo_id, ancestor)
        if not dir_files and not ancestor:
            return None
        picked.extend(d for d in _nearest(m, ancestor, dir_files) if d not in picked)
    return [f"{d}/" for d in picked[:_NEAREST_DIRS]]


async def resolve_scope(
    session: Any, repo_id: Any, raw: str | None, root: Path | None = None
) -> ScopeResolution:
    """*raw* as the union of the prefixes it names that the index or the
    checkout at *root* holds.

    A repository whose graph holds no file rows cannot judge a prefix, so its
    scope is kept as written.
    """
    if _normalize_scope(raw) is None:
        return ScopeResolution(None, [], [])
    prefixes = await _split_pieces(session, repo_id, raw or "", root)
    if not prefixes:
        # Separators only (","): no directory named, read as "" and "." are.
        return ScopeResolution(None, [], [])
    found, missing = [], []
    # One indexed query per prefix: a scope names a handful, never a list to batch.
    for p in prefixes:
        (found if await _holds_files(session, repo_id, p, root) else missing).append(p)
    if found:
        return ScopeResolution(SCOPE_SEPARATOR.join(_outermost(found)), missing, [])
    nearest = await _suggestions(session, repo_id, missing)
    if nearest is None:
        return ScopeResolution(SCOPE_SEPARATOR.join(_outermost(prefixes)), [], [])
    return ScopeResolution(None, missing, nearest)
