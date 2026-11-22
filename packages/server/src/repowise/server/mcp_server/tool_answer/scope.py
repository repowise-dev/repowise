"""A get_answer ``scope``: one or several path prefixes, read against the index.

Agents pass several directories in one scope ("pkg/maps pkg/datapath",
"a/, b/") and directories the index does not hold. Read as one literal prefix,
both match nothing and the reply could only say "no wiki hits". Here a scope
becomes the union of its prefixes, written canonically as the prefixes joined
by ``,`` (what :func:`~repowise.server.mcp_server._page_paths.in_scope` and the
search path filter read), and a prefix no indexed file lives under is named
back to the caller with the existing directories nearest to it.
"""

from __future__ import annotations

import difflib
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

from sqlalchemy import func, or_, select, union

from repowise.core.persistence.crud import indexed_file
from repowise.core.persistence.models import GraphNode, Page
from repowise.server.mcp_server.tool_answer.synthesis import _normalize_scope

#: Joins the prefixes of a canonical multi-prefix scope.
SCOPE_SEPARATOR = ","

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


def _on_disk(root: Path | None, prefix: str) -> bool:
    """Whether *prefix* is a path inside the checkout. Documents are served from
    disk (see documents.py), so a directory of them holds no graph row or page."""
    if root is None:
        return False
    try:
        return (root / prefix).resolve().is_relative_to(root.resolve()) and (
            root / prefix
        ).exists()
    except OSError:
        return False


async def _holds_files(session: Any, repo_id: Any, prefix: str, root: Path | None) -> bool:
    graph, pages = _indexed_paths(repo_id)
    for query, column in ((graph, GraphNode.node_id), (pages, Page.target_path)):
        if await session.scalar(query.where(_under(column, prefix)).limit(1)) is not None:
            return True
    return _on_disk(root, prefix)


async def _split_pieces(session: Any, repo_id: Any, raw: str, root: Path | None) -> list[str]:
    """The prefixes *raw* names. Commas always separate; whitespace separates
    unless the whole piece is itself an indexed path ("My Docs/")."""
    prefixes: list[str] = []
    for piece in raw.split(SCOPE_SEPARATOR):
        norm = _normalize_scope(piece)
        if norm is None:
            continue
        if any(ch.isspace() for ch in norm) and not await _holds_files(session, repo_id, norm, root):
            prefixes.extend(n for word in norm.split() if (n := _normalize_scope(word)))
        else:
            prefixes.append(norm)
    return list(dict.fromkeys(prefixes))


def _deepest_ancestor(parts: list[str], dir_files: Counter) -> str:
    """The deepest indexed directory above a missing path; ``""`` for the root."""
    for i in range(len(parts) - 1, 0, -1):
        if (ancestor := "/".join(parts[:i])) in dir_files:
            return ancestor
    return ""


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


def _nearest(missing: list[str], dir_files: Counter) -> list[str]:
    """Existing directories near each missing prefix.

    A directory named like the missing one's last segment comes first, inside
    the deepest existing ancestor before elsewhere, then shallowest and
    largest. Without one, the largest directories directly inside that ancestor.
    """
    by_name: dict[str, list[str]] = {}
    for d in dir_files:
        by_name.setdefault(d.rsplit("/", 1)[-1].lower(), []).append(d)
    picked: list[str] = []
    for m in missing:
        parts = m.split("/")
        ancestor = _deepest_ancestor(parts, dir_files)
        inside = f"{ancestor}/" if ancestor else ""
        found = sorted(
            _named_like(parts[-1].lower(), by_name),
            key=lambda d: (not d.startswith(inside), d.count("/"), -dir_files[d], d),
        ) or _children(inside, dir_files)
        picked.extend(d for d in found if d not in picked)
    return [f"{d}/" for d in picked[:_NEAREST_DIRS]]


async def _directories(session: Any, repo_id: Any) -> Counter:
    """Every directory holding indexed files, with how many files it holds."""
    rows = await session.execute(union(*_indexed_paths(repo_id)))
    counts: Counter = Counter()
    for (path,) in rows:
        parts = path.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            counts["/".join(parts[:i])] += 1
    return counts


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
    found, missing = [], []
    # One indexed query per prefix: a scope names a handful, never a list to batch.
    for p in prefixes:
        (found if await _holds_files(session, repo_id, p, root) else missing).append(p)
    if found:
        return ScopeResolution(SCOPE_SEPARATOR.join(_outermost(found)), missing, [])
    dir_files = await _directories(session, repo_id)
    if not dir_files:
        return ScopeResolution(SCOPE_SEPARATOR.join(_outermost(prefixes)), [], [])
    return ScopeResolution(None, missing, _nearest(missing, dir_files))
