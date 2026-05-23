"""SqlIndexStore — default :class:`IndexStore` implementation.

Binds a single :class:`AsyncSession` and delegates every method to the
corresponding function in :mod:`repowise.core.persistence.crud`. There is
no behaviour change relative to calling ``crud.foo(session, ...)``
directly; this class exists so callers can depend on the ABC rather than
on a module-level function namespace, which is what makes alternate
implementations (e.g. dialect-specific or scoped variants) possible.

The class body is intentionally thin — the delegations live in three
domain mixins (``_sql_meta``, ``_sql_analysis``, ``_sql_graph_records``)
so no single file exceeds the project line cap.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from .._interfaces.index_store import IndexStore
from ._sql_analysis import _SqlAnalysisMixin
from ._sql_graph_records import _SqlGraphRecordsMixin
from ._sql_meta import _SqlMetaMixin


class SqlIndexStore(
    _SqlMetaMixin,
    _SqlAnalysisMixin,
    _SqlGraphRecordsMixin,
    IndexStore,
):
    """:class:`IndexStore` backed by :mod:`crud` + an :class:`AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Expose the bound session for callers that need raw SQL access."""
        return self._session


__all__ = ["SqlIndexStore"]
