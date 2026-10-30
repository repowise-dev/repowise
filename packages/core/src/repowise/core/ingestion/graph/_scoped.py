"""Scoped (patch) build support for :class:`GraphBuilder`.

A full ``build()`` re-resolves every file's imports, calls and heritage, which
is the repo-proportional cost of every update. When an edit left a file's
cross-file surface unchanged, nothing outside that file can resolve differently,
so the graph can start from the last fully resolved snapshot and re-resolve only
the edited files. Global passes still run and can add or merge their edge types.
"""

from __future__ import annotations

from typing import Any

from ..models import REGISTRATION_HINT


class ScopedBuildMixin:
    """Seed a graph with a full snapshot and resolve only files in scope."""

    def _scoped_items(self, scope: frozenset[str] | None) -> list[tuple[str, Any]]:
        """``(path, parsed)`` pairs to resolve, in insertion order like a full build."""
        parsed_files = self._parsed_files  # type: ignore[attr-defined]
        if scope is None:
            return list(parsed_files.items())
        return [(p, pf) for p, pf in parsed_files.items() if p in scope]

    def seed_graph_snapshot(self, snapshot: dict[str, Any]) -> int:
        """Seed the exact last-built graph; return its edge count.

        Current parse results own the file/symbol attributes. Snapshot values
        fill only attributes the parser does not provide, while all edge
        attributes (including non-column resolver facts) are restored exactly.
        """
        graph = self._graph  # type: ignore[attr-defined]
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

    def _clear_scope_edges(self, scope: frozenset[str]) -> None:
        """Drop changed-file edges, preserving only their bare structural links."""
        graph = self._graph  # type: ignore[attr-defined]
        parsed_files = self._parsed_files  # type: ignore[attr-defined]
        owned: set[str] = set()
        for path in scope:
            parsed = parsed_files.get(path)
            if parsed is None:
                continue
            owned.add(path)
            owned.update(sym.id for sym in parsed.symbols)

        stale: list[tuple[str, str]] = []
        # These relations depend only on the unchanged public surface (method
        # sets, heritage and signatures), not on method bodies. Keep them in a
        # patch; a surface change takes the full-build path.
        stable_global = {"method_implements", "dispatches_to"}
        for source, target, data in list(graph.edges(data=True)):
            edge_type = data.get("edge_type")
            if edge_type in stable_global or data.get("hint_source") == REGISTRATION_HINT:
                continue
            if source in owned:
                if edge_type == "defines":
                    continue
                if edge_type == "has_method":
                    # Calls from a class to one of its methods merge call-site
                    # attributes onto the structural class -> method edge.
                    # Keep that link, but clear the old call resolution.
                    if data.get("resolution_origin") or data.get("call_lines"):
                        data.clear()
                        data["edge_type"] = "has_method"
                    continue
                stale.append((source, target))
        graph.remove_edges_from(stale)

    def _seed_import_targets(self, import_targets: dict[str, set[str]], scope: frozenset[str]) -> None:
        """Fill unchanged files' import-like target sets from the seeded graph."""
        graph = self._graph  # type: ignore[attr-defined]
        for path in self._parsed_files:  # type: ignore[attr-defined]
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
