"""Pluggable persistence interfaces.

Three abstract storage contracts live here so callers can swap storage
backends without touching call sites:

- :class:`IndexStore` — SQL-backed CRUD facade over the relational model
  (repositories, pages, decisions, git metadata, health, dead code, etc.).
- :class:`GraphStore` — query + persistence surface for the code graph
  (nodes, edges, in-memory traversal, batch upserts).
- :class:`JobStore` — checkpoint/resume state for long-running pipeline runs.

The OSS defaults live in :mod:`repowise.core.persistence.stores`:
``SqlIndexStore``, ``InProcessGraphStore``, ``SqlJobStore``.

These ABCs intentionally describe *what* is stored, not *how*. An
implementation may wrap any SQL dialect, a remote service, or an
in-memory mock — anything that honors the method signatures is a valid
plugin. The in-tree contract tests in
``tests/unit/persistence/test_interfaces_contract.py`` exercise the
shared behavior against every registered implementation.
"""

from __future__ import annotations

from .graph_store import GraphStore
from .index_store import IndexStore
from .job_store import JobRecord, JobState, JobStore

__all__ = [
    "GraphStore",
    "IndexStore",
    "JobRecord",
    "JobState",
    "JobStore",
]
