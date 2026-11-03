# packages/core/src/repowise/core/ingestion/graph/builder.py
"""GraphBuilder — constructs the directed dependency graph from ParsedFiles."""
from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
from typing import Any

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

    def build(
        self,
        parsed_files: list,
        progress: Any = None,
        scope: Collection[str] | None = None,
    ) -> nx.DiGraph:
        """Construye el grafo a partir de los ficheros parseados.

        Firma original intacta (P3): ``parsed_files``, ``progress`` y
        ``scope`` se aceptan tal cual, y ``repo_root`` se obtiene
        internamente de ``self.repo_root`` para la detección de registros.

        * ``progress``: callback opcional invocado tras procesar cada
          fichero (acepta ``(i, total)`` o ``(i,)``).
        * ``scope``: si se indica, sólo se procesan los ficheros cuyo
          ``path`` (o ``rel_path``) esté en el conjunto.
        """
        # Filtrado por scope, preservando el orden original.
        files = list(parsed_files)
        if scope is not None:
            allowed = set(scope)
            files = [
                pf
                for pf in files
                if self._pf_path(pf) in allowed
            ]

        total = len(files)
        for index, pf in enumerate(files, start=1):
            self.add_file(pf)
            if progress is not None:
                self._report(progress, index, total)

        resolve_type_refs(self.graph)
        self.add_framework_edges()

        if self.repo_root is not None:
            self.add_registration_edges(self.repo_root)

        return self.graph

    @staticmethod
    def _pf_path(pf: Any) -> str | None:
        """Extrae la ruta de un ParsedFile (``path`` o ``rel_path``)."""
        for attr in ("rel_path", "path"):
            value = getattr(pf, attr, None)
            if value is not None:
                return str(value)
        return None

    @staticmethod
    def _report(progress: Any, index: int, total: int) -> None:
        """Invoca *progress* tolerando firmas ``(i, total)`` o ``(i,)``."""
        try:
            progress(index, total)
        except TypeError:
            progress(index)

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
