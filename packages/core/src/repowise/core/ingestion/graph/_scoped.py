"""Scoped (patch) build support for :class:`GraphBuilder`.

A full ``build()`` re-resolves every file's imports, calls and heritage, which
is the repo-proportional cost of every update. When an edit left a file's
cross-file surface unchanged, nothing outside that file can resolve differently,
so the graph can start from the last fully resolved snapshot and re-resolve only
the edited files. Global passes still run and can add or merge their edge types.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..models import REGISTRATION_HINT


class _ScopedGraphHost(Protocol):
    _graph: Any
    _parsed_files: dict[str, Any]


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

    def seed_graph_snapshot(self: _ScopedGraphHost, snapshot: dict[str, Any]) -> int:
        """Seed the exact last-built graph; return its edge count.

        Current parse results own the file/symbol attributes. Snapshot values
        fill only attributes the parser does not provide, while all edge
        attributes (including non-column resolver facts) are restored exactly.
        """
        graph = self._graph
        for node_id, attrs in snapshot.get("nodes", ()):
            if node_id not in graph:
                graph.add_node(node_id, **attrs)
                continue
            node = graph.nodes[node_id]
            for key, value in attrs.items():
                node.setdefault(key, value)
        added = 0
        for source, target, attrs in snapshot.get("edges", ()):
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
