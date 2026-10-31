# packages/core/src/repowise/core/ingestion/graph/builder.py
"""GraphBuilder — constructs the directed dependency graph from ParsedFiles."""
from __future__ import annotations

from pathlib import Path

import networkx as nx

from ._edges import EdgesMixin
from ._metrics import MetricsMixin
from ._rehydrate import RehydrateMixin
from ._resolve import ResolveMixin
from ._serialize import SerializeMixin
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
        """Construye el grafo a partir de los ficheros parseados.

        Firma original intacta (P3). Si se necesita ``repo_root`` para la
        detección de registros, se obtiene de ``self.repo_root`` — el
        llamador debe pasarlo al constructor, tal como estaba previsto.
        """
        for pf in parsed_files:
            self.add_file(pf)

        resolve_type_refs(self.graph)
        self.add_framework_edges()

        if self.repo_root is not None:
            self.add_registration_edges(self.repo_root)

        return self.graph

    # ... métodos existentes (add_file, add_framework_edges, ...) sin cambios

    def add_registration_edges(self, repo_root: Path) -> int:
        """Añade aristas ``registration`` usando el grafo como resolución.

        Se invoca *después* de que las aristas ``imports`` estén resueltas,
        para que el detector pueda apoyarse en ellas.
        """
        regs = detect_registrations(repo_root, graph=self.graph)
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
