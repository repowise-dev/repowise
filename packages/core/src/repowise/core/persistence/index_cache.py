"""In-process answers kept per (database, repository, index stamp).

A read model folded from a whole repository's rows is the same answer until an
index run rewrites those rows, so it is computed once per index state and kept
in a small LRU. The stamp is what moves when an index run lands: the repository
row and the file metrics written beside the graph rows. One statement, so a
cached answer costs one read.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import HealthFileMetric, Repository

__all__ = ["IndexStampCache", "index_stamp"]


async def index_stamp(session: AsyncSession, repo_id: str) -> tuple[Any, ...]:
    """What moves when an index run rewrites the graph and the health rows."""
    repo = Repository.id == repo_id
    metrics = HealthFileMetric.repository_id == repo_id
    columns = (
        select(Repository.head_commit).where(repo),
        select(Repository.updated_at).where(repo),
        select(Repository.graph_edges_parser_fingerprint).where(repo),
        select(func.max(HealthFileMetric.updated_at)).where(metrics),
        select(func.count()).select_from(HealthFileMetric).where(metrics),
    )
    row = (await session.execute(select(*(c.scalar_subquery() for c in columns)))).one()
    return tuple(row)


class IndexStampCache:
    """A bounded LRU whose keys carry the database, repository and index stamp."""

    def __init__(self, size: int) -> None:
        self._size = size
        self._entries: OrderedDict[tuple[Any, ...], Any] = OrderedDict()

    async def key(self, session: AsyncSession, repo_id: str, *parts: Any) -> tuple[Any, ...]:
        bind = str(session.bind.url) if session.bind is not None else None
        return (bind, repo_id, await index_stamp(session, repo_id), *parts)

    def __contains__(self, key: tuple[Any, ...]) -> bool:
        return key in self._entries

    def get(self, key: tuple[Any, ...]) -> Any:
        """The kept answer (marked recently used); ``KeyError`` when absent."""
        value = self._entries[key]
        self._entries.move_to_end(key)
        return value

    def drop_repo(self, repo_id: str) -> None:
        """Forget every answer kept for *repo_id*, for a caller that keeps one
        state per repository: an older index state is never asked for again."""
        for key in [k for k in self._entries if k[1] == repo_id]:
            del self._entries[key]

    def drop_repo_except(self, repo_id: str, prefix: tuple[Any, ...]) -> None:
        """Forget *repo_id*'s answers whose key does not start with *prefix*:
        older states go, variants of the current state (a trailing key part)
        stay."""
        n = len(prefix)
        for key in [k for k in self._entries if k[1] == repo_id and k[:n] != prefix]:
            del self._entries[key]

    def remember(self, key: tuple[Any, ...], value: Any) -> Any:
        self._entries[key] = value
        while len(self._entries) > self._size:
            self._entries.popitem(last=False)
        return value

    def forget(self, key: tuple[Any, ...]) -> None:
        """Drop one kept answer, if present."""
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()
