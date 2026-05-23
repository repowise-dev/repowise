"""Default storage implementations.

This subpackage holds the OSS defaults for the three pluggable
persistence ABCs in :mod:`repowise.core.persistence._interfaces`:

- :class:`SqlIndexStore` wraps :mod:`repowise.core.persistence.crud`
  behind the :class:`IndexStore` ABC.
- :class:`InProcessGraphStore` wraps
  :class:`repowise.core.ingestion.graph.GraphBuilder` behind the
  :class:`GraphStore` ABC.
- :class:`SqlJobStore` backs the :class:`JobStore` ABC with the
  ``pipeline_jobs`` table.

Other implementations (test doubles, remote-backed plugins, scoped
wrappers) live outside this subpackage and depend only on the ABCs.
"""

from __future__ import annotations

from .in_process_graph_store import InProcessGraphStore
from .sql_index_store import SqlIndexStore
from .sql_job_store import SqlJobStore

__all__ = [
    "InProcessGraphStore",
    "SqlIndexStore",
    "SqlJobStore",
]
