"""What an empty callers/callees/used_by list actually rests on.

An empty list from the call graph reads the same whether the resolver bound
almost every call site in that language or guessed at most of them. These
helpers attach the basis next to the zero: how many call edges the language
has, and how large a share of them came from a strategy the resolution
vocabulary ranks as a guess.

The one thing the index cannot say is how many call sites the resolver failed
to bind at all: no such count is persisted, and re-parsing on a tool path is
not an option. The note text says so plainly rather than invent a rate.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import GraphEdge, GraphNode

# Resolution origins the vocabulary in `ingestion/models.py::ResolutionOrigin`
# annotates at or below 0.75 confidence. Each binds a name to a symbol that
# merely exists somewhere in the repo, so the edge is a guess, not a fact.
# NULL is not in here: it means the row predates the vocabulary, which is
# unknown rather than weak, so it counts in the denominator and not as a guess.
GUESSED_ORIGINS: frozenset[str] = frozenset(
    {
        "global_unique",
        "receiver_global",
        "receiver_typed_global",
        "receiver_field_global",
        "receiver_framework_global",
        "return_type_global",
    }
)

RESOLVED_NOTE = (
    "Call sites the resolver could not bind are not counted, so an empty list "
    "means no resolved edge reaches this symbol, not proof that nothing calls it."
)

NO_EDGES_NOTE = (
    "No call edges were resolved for this language, so an empty list carries no information."
)

# One aggregate query per repo per index commit, and nothing more: the whole
# per-language grouping is cached under a key that moves when the index does.
_CACHE: dict[tuple[str, str], dict[str, dict[str | None, int]]] = {}
_CACHE_MAX = 32
# Targets of one call resolve concurrently; the lock keeps a cold cache to one query.
_LOCK = asyncio.Lock()


def reset_cache() -> None:
    """Drop every cached grouping. For tests and for a re-index in-process."""
    _CACHE.clear()


async def _grouping(
    session: AsyncSession, repo_id: str, cache_key: str
) -> dict[str, dict[str | None, int]]:
    """Call-edge counts for this repo, by source-node language then origin."""
    key = (repo_id, cache_key)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    async with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached
        return await _query_grouping(session, key)


async def _query_grouping(
    session: AsyncSession, key: tuple[str, str]
) -> dict[str, dict[str | None, int]]:
    repo_id = key[0]
    stmt = (
        select(GraphNode.language, GraphEdge.resolution_origin, func.count(GraphEdge.id))
        .join(
            GraphNode,
            (GraphNode.repository_id == GraphEdge.repository_id)
            & (GraphNode.node_id == GraphEdge.source_node_id),
        )
        .where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.edge_type == "calls",
        )
        .group_by(GraphNode.language, GraphEdge.resolution_origin)
    )
    grouping: dict[str, dict[str | None, int]] = {}
    for language, origin, count in await session.execute(stmt):
        grouping.setdefault(language or "", {})[origin] = int(count or 0)

    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = grouping
    return grouping


def _entry(language: str, origins: dict[str | None, int]) -> dict[str, Any]:
    total = sum(origins.values())
    if not total:
        return {
            "language": language,
            "resolved_call_edges": 0,
            "guessed_share": None,
            "unresolved_call_sites": None,
            "note": NO_EDGES_NOTE,
        }
    guessed = sum(n for origin, n in origins.items() if origin in GUESSED_ORIGINS)
    return {
        "language": language,
        "resolved_call_edges": total,
        "guessed_share": round(guessed / total, 2),
        "unresolved_call_sites": None,
        "note": RESOLVED_NOTE,
    }


async def call_resolution_basis(
    session: AsyncSession, repo_id: str, language: str | None, *, cache_key: str
) -> dict[str, Any]:
    """Basis for one language's empty call-graph list."""
    grouping = await _grouping(session, repo_id, cache_key)
    return _entry(language or "", grouping.get(language or "", {}))


async def call_resolution_bases(
    session: AsyncSession, repo_id: str, *, cache_key: str
) -> list[dict[str, Any]]:
    """One basis entry per language that has call edges in this repo."""
    grouping = await _grouping(session, repo_id, cache_key)
    return [_entry(lang, origins) for lang, origins in sorted(grouping.items()) if origins]


def basis_cache_key(repository: Any) -> str:
    """A key that moves whenever the index does, cheapest field first."""
    head = getattr(repository, "head_commit", None)
    if head:
        return str(head)
    updated = getattr(repository, "updated_at", None)
    return str(updated) if updated else "unknown"
