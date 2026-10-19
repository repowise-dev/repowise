"""The index's file centrality, for a change-health pass that sees only changed files.

Centrality gates read how many files use a file. An isolated pass counts only
the changed files, so the same code scores differently there than in the full
index; feeding both sides the index's counts keeps the two readings in step.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...ids import SYMBOL_SEP, is_external
from ...ingestion.cohesion import records_no_use
from ...persistence.models import GraphEdge

#: History, not code: the edge kind the in-memory dependents count leaves out.
_NOT_A_USE = ("co_changes",)


async def index_dependents(session: AsyncSession, repository_id: str) -> dict[str, int]:
    """``{file: distinct files that use it}``, counted as the health engine counts."""
    rows = await session.execute(
        select(GraphEdge.source_node_id, GraphEdge.target_node_id, GraphEdge.hint_source)
        .where(
            GraphEdge.repository_id == repository_id,
            GraphEdge.edge_type.not_in(_NOT_A_USE),
        )
        .distinct()
    )
    return dependents_counts(rows.all())


def dependents_counts(edges: Iterable[tuple[str, str, str | None]]) -> dict[str, int]:
    """Distinct using files per file from ``(source, target, hint)`` edges.

    Symbols, externals, self-edges and edges that record no use are left out.
    """
    users: dict[str, set[str]] = {}
    for source, target, hint in edges:
        if source == target or not _is_file(source) or not _is_file(target):
            continue
        if records_no_use({"hint_source": hint}, source, target):
            continue
        users.setdefault(target, set()).add(source)
    return {target: len(sources) for target, sources in users.items()}


def _is_file(node_id: str) -> bool:
    return SYMBOL_SEP not in node_id and not is_external(node_id)
