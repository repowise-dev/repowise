"""Where execution starts, read from the stored index: one answer for every reader of it.

Three stored facts say code runs from outside the import graph:

* a file node :func:`~repowise.core.entry_candidacy.is_reachability_root`
  holds (entry points, and files a runner or loader starts);
* a file a ``framework:`` anchor points at (a framework loads it by
  convention), which dead code also counts as reached;
* a function an HTTP route names as its handler (``http_routes``), which the
  framework calls when the route is hit.

Package usage walks the import graph from these files, and security reach
walks the execution graph from them and the handlers, so both start from the
same places dead code treats as reached.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from repowise.core.entry_candidacy import is_reachability_root
from repowise.core.ids import FrameworkId, file_path_of, parse
from repowise.core.ingestion.symbol_identity import file_of_symbol


@dataclass(frozen=True, slots=True)
class StoredRoots:
    """The stored roots of one repository.

    *files* are file roots (the root flag or a framework anchor);
    *route_handlers* are the ``http_routes`` handler symbol ids.
    """

    files: frozenset[str] = frozenset()
    route_handlers: frozenset[str] = frozenset()

    @property
    def all_files(self) -> frozenset[str]:
        """*files* plus each file holding a route handler."""
        return self.files | {file_of_symbol(h) for h in self.route_handlers}


async def load_stored_roots(session: Any, repository_id: str) -> StoredRoots:
    """The repository's :class:`StoredRoots`, from ``graph_nodes``, ``graph_edges`` and ``http_routes``."""
    from repowise.core.persistence.crud.http_routes import list_http_routes
    from repowise.core.persistence.models import GraphEdge, GraphNode

    nodes = await session.execute(
        select(GraphNode.node_id, GraphNode.is_entry_point, GraphNode.is_reachability_root).where(
            GraphNode.repository_id == repository_id, GraphNode.node_type == "file"
        )
    )
    files = {row.node_id for row in nodes if is_reachability_root(row._mapping)}
    anchored = await session.execute(
        select(GraphEdge.source_node_id, GraphEdge.target_node_id).where(
            GraphEdge.repository_id == repository_id,
            GraphEdge.source_node_id.like("framework:%"),
        )
    )
    for source, target in anchored:
        # Only an edge to a file anchors it; a binding into a symbol says that
        # symbol runs, not that the framework loads the file.
        if isinstance(parse(source), FrameworkId) and file_path_of(target) == target:
            files.add(target)
    handlers = {
        r.handler_symbol_id
        for r in await list_http_routes(session, repository_id)
        if r.handler_symbol_id
    }
    return StoredRoots(files=frozenset(files), route_handlers=frozenset(handlers))


__all__ = ["StoredRoots", "load_stored_roots"]
