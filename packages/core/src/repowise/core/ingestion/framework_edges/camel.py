"""Apache Camel route builders.

Camel Main, Camel Quarkus and Camel Spring Boot discover ``RouteBuilder``
subclasses on the classpath and instantiate them, so no source names the
subclass.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import DetectionContext, FrameworkHandler
from .spring import _extends_any

if TYPE_CHECKING:
    import networkx as nx

    from ..resolvers import ResolverContext

_ROUTE_BUILDER_BASES: frozenset[str] = frozenset({
    "RouteBuilder",
    "EndpointRouteBuilder",
    "RouteConfigurationBuilder",
})


class _CamelHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return any(
            imp.module_path.startswith("org.apache.camel")
            for p in dctx.parsed_files.values()
            if p.file_info.language in ("java", "kotlin")
            for imp in p.imports
        )

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        for path, parsed in parsed_files.items():
            if _extends_any(parsed, _ROUTE_BUILDER_BASES):
                node = graph.nodes.get(path)
                if node is not None:
                    node["is_entry_point"] = True
                    node["framework_role"] = "camel_route_builder"
        return 0


HANDLERS: list[FrameworkHandler] = [_CamelHandler()]
