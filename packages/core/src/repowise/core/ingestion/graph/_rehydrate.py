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
    "is_reachability_root",
    "always_run_reason",
    "kind",
    "name",
    "qualified_name",
    "file_path",
    "start_line",
    "end_line",
    "visibility",
    "signature",
    "docstring",
    "parent_symbol_id",
)

# Symbol attributes ``graph_nodes`` has no column for. They come from the parse
# alone, so a rehydrated graph takes them back from the re-parse
# (:meth:`RehydrateMixin.restore_parse_only_attrs`); without them the
# refactoring detectors lose the ``@Override`` / ``override`` exemptions.
_PARSE_ONLY_SYMBOL_ATTRS = ("decorators", "modifiers")


def _restore_spawn_lines(graph: Any, parsed: Any) -> None:
    """Mark the call lines of *parsed* that hand a coroutine to a task scheduler.

    ``graph_edges`` stores call lines but not which of them spawn, so a
    rehydrated ``calls`` edge takes them back from the re-parse: the caller's
    spawned call to the callee's name at a line the edge already records.
    """
    module = f"{parsed.file_info.path}::__module__"
    for call in parsed.calls:
        caller = call.caller_symbol_id or module
        if not call.spawned or caller not in graph:
            continue
        for callee, data in graph[caller].items():
            if (
                data.get("edge_type") == "calls"
                and call.line in (data.get("call_lines") or ())
                and graph.nodes[callee].get("name") == call.target_name
            ):
                lines = data.setdefault("spawn_lines", [])
                if call.line not in lines:
                    lines.append(call.line)
                    lines.sort()


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
            attrs = {key: node[key] for key in _NODE_ATTR_KEYS if node.get(key) is not None}
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
            # Cohesion provenance — cycle detection drops these edges, so a
            # rehydrated graph must carry the mark or it re-reports every
            # cohesive package as an import cycle.
            hint_source = edge.get("hint_source")
            if hint_source:
                edge_attrs["hint_source"] = hint_source
            resolution_origin = edge.get("resolution_origin")
            if resolution_origin:
                edge_attrs["resolution_origin"] = resolution_origin
            call_lines = edge.get("call_lines")
            if call_lines:
                edge_attrs["call_lines"] = list(call_lines)
            supplied_props = edge.get("supplied_props")
            if supplied_props is not None:
                edge_attrs["supplied_props"] = frozenset(supplied_props)
            if edge.get("type_only"):
                edge_attrs["type_only"] = True
            if edge.get("deferred"):
                edge_attrs["deferred"] = True
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

    def restore_parse_only_attrs(self, parsed_files: Iterable[Any]) -> None:
        """Stamp the parse-only symbol attributes back onto rehydrated nodes,
        and the spawned call lines back onto their edges.

        The first symbol under an id keeps its values, as in ``add_file``,
        where the first declared overload keeps the node.
        """
        graph = self._graph  # type: ignore[attr-defined]
        for parsed in parsed_files:
            for sym in parsed.symbols:
                node = graph.nodes.get(sym.id)
                if node is None:
                    continue
                for key in _PARSE_ONLY_SYMBOL_ATTRS:
                    node.setdefault(key, getattr(sym, key))
            _restore_spawn_lines(graph, parsed)
