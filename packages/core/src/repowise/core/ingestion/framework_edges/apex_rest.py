"""Salesforce Apex REST route handlers.

The platform calls a ``@RestResource`` class's ``@Http*`` methods, so each one
is bound from its file's module symbol, as Spring binds a ``@GetMapping`` method.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..framework_routes import apex_rest_routes
from .base import DetectionContext, FrameworkHandler, add_symbol_edge, source_text

if TYPE_CHECKING:
    import networkx as nx

    from ..resolvers import ResolverContext


class _ApexRestHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return any(p.file_info.language == "apex" for p in dctx.parsed_files.values())

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        count = 0
        for path, parsed in parsed_files.items():
            if parsed.file_info.language != "apex":
                continue
            text = source_text(path, parsed, ctx.source_map or {})
            methods = [s for s in parsed.symbols if s.kind == "method"]
            for route in apex_rest_routes(text):
                line = text.count("\n", 0, route.offset) + 1
                # An Apex method's span starts at its annotations.
                holders = [s for s in methods if s.start_line <= line <= s.end_line]
                if holders:
                    handler = max(holders, key=lambda s: s.start_line)
                    count += add_symbol_edge(graph, f"{path}::__module__", handler.id)
        return count


HANDLERS: list[FrameworkHandler] = [_ApexRestHandler()]
