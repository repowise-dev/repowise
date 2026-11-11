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

from repowise.core.ingestion.resolution_basis import guessed_share
from repowise.core.persistence.models import GraphEdge, GraphNode

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
    return {
        "language": language,
        "resolved_call_edges": total,
        "guessed_share": round(guessed_share(origins) or 0.0, 2),
        "unresolved_call_sites": None,
        "note": RESOLVED_NOTE,
    }


async def call_resolution_basis(
    session: AsyncSession, repo_id: str, language: str | None, *, cache_key: str
) -> dict[str, Any]:
    """Basis for one language's empty call-graph list."""
    grouping = await _grouping(session, repo_id, cache_key)
    return _entry(language or "", grouping.get(language or "", {}))


def same_file_basis(users: int) -> dict[str, Any]:
    """Basis for an empty ``used_by`` whose only users share the symbol's file.

    The resolver-coverage figures answer "could a user have been missed"; here
    users were found, so the zero says only where they are. A use that is not
    a call (a subclass, a reference) is served under ``relations``.
    """
    uses = "1 use" if users == 1 else f"{users} uses"
    return {
        "note": f'{uses}, all in this file; include=["callers"] lists them in callers or relations.'
    }


async def call_resolution_bases(
    session: AsyncSession, repo_id: str, *, cache_key: str
) -> list[dict[str, Any]]:
    """One basis entry per language that has call edges in this repo."""
    grouping = await _grouping(session, repo_id, cache_key)
    return [_entry(lang, origins) for lang, origins in sorted(grouping.items()) if origins]
