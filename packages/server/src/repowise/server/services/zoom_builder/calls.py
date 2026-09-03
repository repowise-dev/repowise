"""Project the symbol-level execution graph onto the files that declare it.

The zoom map loads its view with ``include_symbols=False``, which keeps only
``node_type == "file"`` nodes and then keeps only edges whose *both* endpoints
survive. Every ``calls`` / ``dispatches_to`` / ``extends`` / ``implements`` edge
is symbol-to-symbol, so all of them are dropped before the builder ever sees
them and the map can only ever draw imports, uses and co-changes.

Projecting a symbol edge onto ``(file of source, file of target)`` puts them
back. Measured on eight indexed repositories, that is mostly a *relabelling*
rather than new arrows -- a cross-file call almost always rides an import that
was already drawn -- and relabelling is the point: ``calls`` outranks
``imports`` in the verb priority, so a pair that genuinely invokes reads as
invocation instead of as a file-header fact.

Reliability is core's call, not ours: :func:`is_reliable_execution_edge` drops
the ``global_unique`` resolution origin, which is the same bar the coverage tab
and the health engine hold their execution claims to. The two guards below are
the ones projection itself creates.
"""

from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.execution_graph import file_of_symbol, is_reliable_execution_edge
from repowise.core.ingestion.models import EXECUTION_EDGE_TYPES
from repowise.core.persistence.models import GraphEdge


def _ext(path: str) -> str:
    """Lower-case extension of *path* (``a/b/x.py`` -> ``py``), ``""`` if none."""
    base = os.path.basename(path)
    return base.rsplit(".", 1)[1].lower() if "." in base else ""


def keep_projected_edge(src_file: str, tgt_file: str) -> bool:
    """Whether a symbol edge projected onto its two files is a real relation.

    Two guards, both artefacts of the projection rather than of the data:

    * a self-loop is what every intra-file call becomes, and those *outnumber*
      the cross-file ones (12,797 of 24,310 candidates on PowerToys). The canvas
      draws relations between siblings, so a self-loop has nowhere to go.
    * a cross-extension pair is more often a coincidence than a call -- a
      same-named module in two languages, a test naming both ends -- and keeping
      it lets the arrow stitch unrelated packages together.

    No confidence floor. The obvious third guard is inert: the persisted minimum
    confidence for an execution edge is exactly 0.5 on all eight repositories
    measured, so a 0.5 floor drops nothing that the origin filter has not
    already dropped.
    """
    if not src_file or not tgt_file or src_file == tgt_file:
        return False
    return _ext(src_file) == _ext(tgt_file)


async def load_projected_calls(
    session: AsyncSession, repo_id: str, known_files: set[str]
) -> list[tuple[str, str, str]]:
    """``(source file, target file, edge_type)`` for every reliable execution edge.

    Restricted to ``known_files`` so the projection cannot introduce a file the
    view does not know about; the tree is built from the view's nodes and an
    edge to anything else would be dropped downstream anyway.

    Edge type is preserved rather than collapsed, because the verb is the
    payload: :func:`~repowise.server.services.c4_builder.labels.relation_label`
    turns the set of types on a pair into the arrow's word, and the canvas
    filters on that word.
    """
    rows = await session.execute(
        select(
            GraphEdge.source_node_id,
            GraphEdge.target_node_id,
            GraphEdge.edge_type,
            GraphEdge.resolution_origin,
        ).where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.edge_type.in_(sorted(EXECUTION_EDGE_TYPES)),
        )
    )
    projected: list[tuple[str, str, str]] = []
    for source, target, edge_type, origin in rows:
        if not is_reliable_execution_edge(edge_type, origin):
            continue
        src_file = file_of_symbol(source)
        tgt_file = file_of_symbol(target)
        if not keep_projected_edge(src_file, tgt_file):
            continue
        if src_file not in known_files or tgt_file not in known_files:
            continue
        projected.append((src_file, tgt_file, edge_type))
    return projected
