# packages/core/src/repowise/core/ingestion/graph/builder.py
"""GraphBuilder — constructs the directed dependency graph from ParsedFiles."""
from __future__ import annotations

import networkx as nx
from pathlib import Path

from ._edges import EdgesMixin
from ._metrics import MetricsMixin
from ._resolve import ResolveMixin
from ._serialize import SerializeMixin
from ._rehydrate import RehydrateMixin
from ..type_ref_resolution import resolve_type_refs
from ..test_reach.registration_detector import detect_registrations


class GraphBuilder(
    MetricsMixin, ResolveMixin, EdgesMixin, SerializeMixin, RehydrateMixin
):
    """Build a dependency graph from a collection of ParsedFile objects."""

    def __init__(self, repo_root: Path | None = None):
        self.repo_root = repo_root
        self.graph: nx.DiGraph = nx.DiGraph()

    def build(self, parsed_files: list) -> nx.DiGraph:
        """Construye el grafo a partir de los ficheros parseados."""
        for pf in parsed_files:
            self.add_file(pf)
        # Resolución de tipos, imports, etc.
        resolve_type_refs(self.graph)
        # Aristas de framework (FastAPI, etc.)
        self.add_framework_edges()
        # Aristas de registro (nuevo).
        if self.repo_root is not None:
            self.add_registration_edges(self.repo_root)
        return self.graph

    # ... (métodos existentes: add_file, add_framework_edges, etc.)

    def add_registration_edges(self, repo_root: Path) -> int:
        """Añade aristas ``registration`` al grafo a partir del detector."""
        regs = detect_registrations(repo_root)
        count = 0
        for registrar, registered_modules in regs.items():
            for reg in registered_modules:
                if registrar in self.graph and reg in self.graph:
                    if not self.graph.has_edge(registrar, reg):
                        self.graph.add_edge(
                            registrar,
                            reg,
                            edge_type="registration",
                            confidence=0.9,
                        )
                        count += 1
        return count
