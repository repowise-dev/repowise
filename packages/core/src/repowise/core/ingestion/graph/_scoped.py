"""Scoped (patch) build support for :class:`GraphBuilder`.

A full ``build()`` re-resolves every file's imports, calls and heritage, which
is the repo-proportional cost of every update. When an edit left a file's
cross-file surface unchanged, nothing outside that file can resolve differently,
so the graph can start from the last fully resolved snapshot and re-resolve only
the edited files. Global passes still run and can add or merge their edge types.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any, Protocol

from ..models import REGISTRATION_HINT, is_dynamic_edge

# Stamped on nodes by passes every build runs over every file (warmups,
# always-run detection), so a seeded value could only be stale.
_REBUILT_NODE_ATTRS = frozenset({"package_export", "always_run_reason"})
# Stamped on a re-resolved file's own node by the type-reference pass, which
# adds to whatever is there.
_SCOPE_REBUILT_NODE_ATTRS = frozenset({"local_type_uses"})
# Edge types only the framework pass writes; dynamic hints are matched by prefix.
_FRAMEWORK_EDGE_TYPES = frozenset({"framework", "framework_binds"})


def _seed_nodes(
    graph: Any, nodes: Any, drop: set[str], rescoped: set[str]
) -> set[str]:
    """Merge the snapshot's nodes into *graph*; return the ids left out.

    Left out: nodes of deleted files, and nodes of a re-resolved file that its
    current parse no longer declares.
    """
    dropped: set[str] = set()
    for node_id, attrs in nodes:
        owner = attrs.get("file_path")
        gone = node_id in drop or owner in drop
        if gone or (node_id not in graph and owner in rescoped):
            dropped.add(node_id)
        elif node_id not in graph:
            graph.add_node(node_id, **attrs)
        else:
            _fill_node_attrs(graph.nodes[node_id], attrs, node_id in rescoped or owner in rescoped)
    return dropped


def _fill_node_attrs(node: dict[str, Any], attrs: dict[str, Any], rescoped: bool) -> None:
    """Fill the attributes the parse does not set and this build will not stamp again."""
    rebuilt = _REBUILT_NODE_ATTRS | _SCOPE_REBUILT_NODE_ATTRS if rescoped else _REBUILT_NODE_ATTRS
    for key, value in attrs.items():
        if key not in rebuilt:
            node.setdefault(key, value)


class _ScopedGraphHost(Protocol):
    _graph: Any
    _parsed_files: dict[str, Any]
    _stashed_co_changes: list[tuple[str, str, dict[str, Any]]]


class ScopedBuildMixin:
    """Seed a graph with a full snapshot and resolve only files in scope."""

    def _scoped_items(
        self: _ScopedGraphHost, scope: frozenset[str] | None
    ) -> list[tuple[str, Any]]:
        """``(path, parsed)`` pairs to resolve, in insertion order like a full build."""
        parsed_files = self._parsed_files
        if scope is None:
            return list(parsed_files.items())
        return [(p, pf) for p, pf in parsed_files.items() if p in scope]

    def seed_graph_snapshot(
        self: _ScopedGraphHost,
        snapshot: dict[str, Any],
        drop_paths: Collection[str] = (),
        scope: Collection[str] = (),
    ) -> int:
        """Seed the exact last-built graph; return its edge count.

        Current parse results own the file/symbol attributes. Snapshot values
        fill only attributes the parser does not provide and this build will
        not stamp again, while all edge attributes (including non-column
        resolver facts) are restored exactly. Nodes of the deleted files in
        *drop_paths*, symbols the files in *scope* no longer declare, and the
        edges of both are skipped.
        """
        graph = self._graph
        dropped = _seed_nodes(graph, snapshot.get("nodes", ()), set(drop_paths), set(scope))
        added = 0
        for source, target, attrs in snapshot.get("edges", ()):
            if source in dropped or target in dropped:
                continue
            if source not in graph:
                graph.add_node(source, language="external", symbol_count=0, has_error=False)
            if target not in graph:
                graph.add_node(target, language="external", symbol_count=0, has_error=False)
            graph.add_edge(source, target, **attrs)
            added += 1
        return added

    def _scope_owned_nodes(self: _ScopedGraphHost, scope: frozenset[str]) -> set[str]:
        """Node ids whose outgoing resolution belongs to one of the changed files."""
        owned: set[str] = set()
        for path in scope:
            parsed = self._parsed_files.get(path)
            if parsed is None:
                continue
            owned.add(path)
            owned.add(f"{path}::__module__")
            owned.update(sym.id for sym in parsed.symbols)
        return owned

    @staticmethod
    def _clear_call_resolution(data: dict[str, Any]) -> None:
        """Remove resolver attributes merged onto a structural ``has_method`` edge."""
        data.clear()
        data["edge_type"] = "has_method"

    @classmethod
    def _should_remove_scoped_edge(
        cls,
        source: str,
        data: dict[str, Any],
        owned: set[str],
    ) -> bool:
        """Whether a seeded edge must be regenerated for the changed-file scope."""
        edge_type = data.get("edge_type")
        if edge_type in {"method_implements", "dispatches_to"}:
            return False
        if data.get("hint_source") == REGISTRATION_HINT or source not in owned:
            return False
        if edge_type == "defines":
            return False
        if edge_type == "has_method":
            if data.get("resolution_origin") or data.get("call_lines"):
                cls._clear_call_resolution(data)
            return False
        return True

    def _clear_scope_edges(self: _ScopedGraphHost, scope: frozenset[str]) -> None:
        """Drop changed-file edges, preserving only their bare structural links."""
        graph = self._graph
        owned = self._scope_owned_nodes(scope)

        graph.remove_edges_from(
            (source, target)
            for source, target, data in list(graph.edges(data=True))
            if self._should_remove_scoped_edge(source, data, owned)
        )

    def _seed_import_targets(
        self: _ScopedGraphHost,
        import_targets: dict[str, set[str]],
        scope: frozenset[str],
    ) -> None:
        """Fill unchanged files' import-like target sets from the seeded graph."""
        graph = self._graph
        for path in self._parsed_files:
            if path in scope:
                continue
            import_targets[path] = {
                target
                for target, data in graph[path].items()
                if data.get("edge_type") == "imports"
                or (
                    data.get("edge_type") == "type_use"
                    and not data.get("no_scope_widening")
                )
            }

    def _clear_global_pass_edges(self: _ScopedGraphHost) -> None:
        """Drop seeded edges the whole-graph passes of a refreshing patch re-derive.

        Registration and override-dispatch edges are rebuilt by
        :meth:`build`; framework and dynamic-hint edges by the synthetic edge
        passes that run after it. Removing them first leaves those passes the
        same graph a full build gives them. ``co_changes`` edges are set aside
        the same way and put back by :meth:`restore_co_change_edges` once the
        synthetic passes are done: a full build adds them last, and only
        between files nothing else links.
        """
        graph = self._graph
        stale: list[tuple[str, str]] = []
        stashed: list[tuple[str, str, dict[str, Any]]] = []
        for source, target, data in graph.edges(data=True):
            edge_type = data.get("edge_type")
            if edge_type == "co_changes":
                stashed.append((source, target, dict(data)))
                stale.append((source, target))
            elif (
                edge_type == "dispatches_to"
                or edge_type in _FRAMEWORK_EDGE_TYPES
                or is_dynamic_edge(edge_type)
                or data.get("hint_source") == REGISTRATION_HINT
            ):
                stale.append((source, target))
        graph.remove_edges_from(stale)
        self._stashed_co_changes = stashed

    def _clear_dispatch_edges(self: _ScopedGraphHost) -> None:
        """Drop every seeded ``dispatches_to`` edge; :meth:`build` re-derives them all."""
        graph = self._graph
        graph.remove_edges_from(
            [(u, v) for u, v, d in graph.edges(data=True) if d.get("edge_type") == "dispatches_to"]
        )

    def restore_co_change_edges(self: _ScopedGraphHost) -> None:
        """Put back the set-aside ``co_changes`` edges between still-unlinked files."""
        graph = self._graph
        for source, target, data in getattr(self, "_stashed_co_changes", ()):
            if source not in graph or target not in graph:
                continue
            if graph.has_edge(source, target) or graph.has_edge(target, source):
                continue
            graph.add_edge(source, target, **data)
        self._stashed_co_changes = []

    def _drop_unreferenced_seed_nodes(self: _ScopedGraphHost) -> None:
        """Remove seeded nodes no file declares and no remaining edge reaches.

        Called once the edges this patch re-derives are cleared: an external
        module or anchor node that only those edges reached is re-created by
        whichever pass still needs it, as a full build would create it.
        """
        graph = self._graph
        # A file the walk found but could not parse keeps its earlier rows.
        parsed = set(self._parsed_files) | set(getattr(self, "traversed_file_paths", ()))
        graph.remove_nodes_from(
            [
                node
                for node in graph
                if graph.degree(node) == 0
                and node not in parsed
                and graph.nodes[node].get("file_path") not in parsed
            ]
        )
