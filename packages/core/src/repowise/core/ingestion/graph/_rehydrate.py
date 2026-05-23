"""Rehydrate a :class:`GraphBuilder` from persisted graph rows.

After a ``--mode fast`` index the in-memory :class:`GraphBuilder` is gone, but
the structural graph it produced is fully persisted (``graph_nodes`` /
``graph_edges``) together with the materialized centrality snapshot
(``graph_metrics``). When upgrading a fast index to full (``repowise update
--full``) we need a builder to drive doc generation — but re-resolving imports,
calls, and heritage would redo the most expensive part of ingestion for no
benefit, since the answer is already on disk.

:meth:`RehydrateMixin.from_persisted` reconstructs the NetworkX graph from those
rows and loads the file-level metric snapshot via
:meth:`MetricsMixin.load_metrics_from_sql`, so PageRank / betweenness /
community / degree are served straight from SQL with **no NetworkX recompute**
and **no resolution pass**. The result is metric- and traversal-equivalent to
the originally-built graph (proven in ``tests/unit/ingestion``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Node-dict keys that map onto NetworkX node attributes. ``node_id`` is the key
# itself (handled separately) and is excluded here. ``None`` values are dropped
# so file nodes don't carry empty symbol columns.
_NODE_ATTR_KEYS = (
    "node_type",
    "language",
    "symbol_count",
    "has_error",
    "is_test",
    "is_entry_point",
    "kind",
    "name",
    "qualified_name",
    "file_path",
    "start_line",
    "end_line",
    "visibility",
    "signature",
    "parent_symbol_id",
)


class RehydrateMixin:
    """Construct a :class:`GraphBuilder` from persisted rows instead of ASTs."""

    @classmethod
    def from_persisted(
        cls,
        nodes: Iterable[Mapping[str, Any]],
        edges: Iterable[Mapping[str, Any]],
        metrics: Mapping[str, Mapping[str, Any]] | None = None,
        *,
        repo_path: Path | str | None = None,
    ) -> Any:
        """Rebuild a finalized builder from persisted nodes/edges/metrics.

        *nodes* and *edges* are sequences of plain dicts as returned by
        ``persistence.get_all_graph_nodes`` / ``get_all_graph_edges``. *metrics*
        is the ``graph_metrics`` snapshot (``node_id → metrics``); when supplied
        it pre-fills the file-level metric caches so no centrality kernel runs.

        The returned builder has ``_built = True`` — it is ready for traversal
        and generation; calling :meth:`build` again is neither needed nor done.
        """
        builder = cls(repo_path=repo_path)  # type: ignore[call-arg]
        graph = builder._graph

        node_count = 0
        for node in nodes:
            node_id = node.get("node_id")
            if node_id is None:
                continue
            attrs = {
                key: node[key]
                for key in _NODE_ATTR_KEYS
                if node.get(key) is not None
            }
            # ``parent_symbol_id`` is persisted under that name but the live
            # graph uses ``parent_name`` (see GraphBuilder.add_file).
            if "parent_symbol_id" in attrs:
                attrs["parent_name"] = attrs.pop("parent_symbol_id")
            graph.add_node(node_id, **attrs)
            node_count += 1

        edge_count = 0
        for edge in edges:
            source = edge.get("source_node_id")
            target = edge.get("target_node_id")
            if source is None or target is None:
                continue
            edge_attrs: dict[str, Any] = {
                "edge_type": edge.get("edge_type", "imports"),
                "confidence": edge.get("confidence", 1.0),
            }
            imported_names = edge.get("imported_names")
            if imported_names:
                edge_attrs["imported_names"] = list(imported_names)
            graph.add_edge(source, target, **edge_attrs)
            edge_count += 1

        builder._built = True
        if metrics:
            builder.load_metrics_from_sql(dict(metrics))

        log.info(
            "graph.rehydrated_from_sql",
            nodes=node_count,
            edges=edge_count,
            metrics=len(metrics) if metrics else 0,
        )
        return builder
