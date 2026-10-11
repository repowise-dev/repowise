"""IndexStore — aggregate pluggable CRUD facade.

:class:`IndexStore` combines three domain mixins:

- :class:`MetaIndexStore` (repository, page, generation job, webhook, chat)
- :class:`AnalysisIndexStore` (git metadata, dead code, decisions, health, coverage)
- :class:`GraphRecordsIndexStore` (graph_nodes, graph_edges, external_systems, wiki_symbols)

The split is purely a file-size concern — each mixin is in its own module
so no interface file exceeds the project line cap. Callers should depend
on the aggregate :class:`IndexStore`, not on individual mixins.

The OSS default implementation is
:class:`repowise.core.persistence.stores.sql_index_store.SqlIndexStore`,
which binds a single :class:`AsyncSession` and delegates each method to
the existing :mod:`repowise.core.persistence.crud` module. Plugin authors
override individual methods to swap dialects, add scoping (e.g. per-tenant
filtering), or back the store with a remote service.

Behavior is verified by ``tests/unit/persistence/test_interfaces_contract.py``
which runs the same suite against every registered :class:`IndexStore`
implementation.
"""

from __future__ import annotations

from ._analysis import AnalysisIndexStore
from ._graph_records import GraphRecordsIndexStore
from ._meta import MetaIndexStore


class IndexStore(MetaIndexStore, AnalysisIndexStore, GraphRecordsIndexStore):
    """Aggregate pluggable CRUD facade. See module docstring."""


__all__ = ["IndexStore"]
