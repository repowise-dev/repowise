"""Apache Camel route builders.

Camel Main, Camel Quarkus and Camel Spring Boot all find ``RouteBuilder``
subclasses on the classpath and instantiate them, so no source names them.
A class extending one of the builder bases, in a file that imports from
``org.apache.camel``, is stamped an entry point, the same way Spring's
servlet initializers are.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext
from .base import DetectionContext, FrameworkHandler
from .spring import _extends_any

if TYPE_CHECKING:
    import networkx as nx

_CAMEL_PACKAGE = "org.apache.camel"
_ROUTE_BUILDER_BASES: frozenset[str] = frozenset(
    {
        "RouteBuilder",
        "EndpointRouteBuilder",
        "RouteConfigurationBuilder",
    }
)


def _imports_camel(parsed: Any) -> bool:
    if parsed.file_info.language not in ("java", "kotlin"):
        return False
    return any(imp.module_path.startswith(_CAMEL_PACKAGE) for imp in parsed.imports)


def _is_route_builder(parsed: Any) -> bool:
    """True if *parsed* declares a Camel route builder the runtime discovers."""
    return _imports_camel(parsed) and _extends_any(parsed, _ROUTE_BUILDER_BASES)


def _add_camel_edges(graph: nx.DiGraph, parsed_files: dict[str, Any]) -> int:
    for path, parsed in parsed_files.items():
        if _is_route_builder(parsed):
            node = graph.nodes.get(path)
            if node is not None:
                node["is_entry_point"] = True
                node["framework_role"] = node.get("framework_role") or "camel_route_builder"
    return 0


class _CamelHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return any(_imports_camel(parsed) for parsed in dctx.parsed_files.values())

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_camel_edges(graph, parsed_files)


HANDLERS: list[FrameworkHandler] = [_CamelHandler()]
