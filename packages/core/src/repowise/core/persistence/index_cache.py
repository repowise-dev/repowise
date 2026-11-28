"""In-process answers kept per (database, repository, index stamp).

A read model folded from a whole repository's rows is the same answer until an
index run rewrites those rows, so it is computed once per index state and kept
in a small LRU. The stamp is what moves when an index run lands: the repository
row and the file metrics written beside the graph rows. One statement, so a
cached answer costs one read.
"""

from __future__ import annotations

import weakref
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import HealthFileMetric, Repository

__all__ = ["IndexStampCache", "drop_repo_everywhere", "index_stamp"]

# Every live cache, so one call can forget a repository in all of them.
_INSTANCES: weakref.WeakSet[IndexStampCache] = weakref.WeakSet()


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
    """A bounded LRU whose keys carry the database, repository and index stamp.

    *size* caps the entry count. A cache of large answers can also pass
    *weigh* (an answer's size in any unit) and *budget* (the most it keeps in
    that unit): least recently used entries go until the total fits, except
    the newest, which is always kept so an oversized answer is still served
    from cache to the requests that follow it.
    """

    def __init__(
        self,
        size: int,
        *,
        weigh: Callable[[Any], int] | None = None,
        budget: int | None = None,
    ) -> None:
        self._size = size
        self._weigh = weigh
        self._budget = budget
        self._entries: OrderedDict[tuple[Any, ...], Any] = OrderedDict()
        self._weights: dict[tuple[Any, ...], int] = {}
        self._total = 0
        _INSTANCES.add(self)

    async def key(self, session: AsyncSession, repo_id: str, *parts: Any) -> tuple[Any, ...]:
        bind = str(session.bind.url) if session.bind is not None else None
        return (bind, repo_id, await index_stamp(session, repo_id), *parts)

    def __contains__(self, key: tuple[Any, ...]) -> bool:
        return key in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def weight(self) -> int:
        """The summed weight of the kept answers (0 without *weigh*)."""
        return self._total

    def get(self, key: tuple[Any, ...]) -> Any:
        """The kept answer (marked recently used); ``KeyError`` when absent."""
        value = self._entries[key]
        self._entries.move_to_end(key)
        return value

    def drop_repo(self, repo_id: str) -> None:
        """Forget every answer kept for *repo_id*, for a caller that keeps one
        state per repository: an older index state is never asked for again."""
        for key in [k for k in self._entries if k[1] == repo_id]:
            self._pop(key)

    def drop_repo_except(self, repo_id: str, prefix: tuple[Any, ...]) -> None:
        """Forget *repo_id*'s answers whose key does not start with *prefix*:
        older states go, variants of the current state (a trailing key part)
        stay."""
        n = len(prefix)
        for key in [k for k in self._entries if k[1] == repo_id and k[:n] != prefix]:
            self._pop(key)

    def remember(self, key: tuple[Any, ...], value: Any) -> Any:
        weight = self._weigh(value) if self._weigh is not None else 0
        self._pop(key)
        self._entries[key] = value
        if weight:
            self._weights[key] = weight
            self._total += weight
        while len(self._entries) > self._size or (
            self._budget is not None and self._total > self._budget and len(self._entries) > 1
        ):
            self._pop(next(iter(self._entries)))
        return value

    def forget(self, key: tuple[Any, ...]) -> None:
        """Drop one kept answer, if present."""
        self._pop(key)

    def clear(self) -> None:
        self._entries.clear()
        self._weights.clear()
        self._total = 0

    def _pop(self, key: tuple[Any, ...]) -> None:
        self._entries.pop(key, None)
        self._total -= self._weights.pop(key, 0)


def drop_repo_everywhere(repo_id: str) -> None:
    """Forget *repo_id*'s answers in every :class:`IndexStampCache` in the process."""
    for cache in list(_INSTANCES):
        cache.drop_repo(repo_id)
