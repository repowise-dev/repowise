"""tRPC procedure-registry framework edges.

A typical tRPC router declares procedures via a name-keyed object
literal: ``router({ getUser: publicProcedure.query(getUserHandler), …
})``. The handler is bound by identifier; tRPC consumers reach it
through the client-side procedure name, so no static caller appears in
the import graph. This module emits framework edges from the router
declaration site to each handler's file.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    _build_ts_var_to_file,
    read_text,
)

if TYPE_CHECKING:
    import networkx as nx


# Matches ``<chain>.query(handler)`` / ``.mutation(handler)`` /
# ``.subscription(handler)`` where the argument is a bare identifier.
_TRPC_PROC_RE = re.compile(
    r"""\.\s*(?:query|mutation|subscription)\s*\(\s*
        ([A-Za-z_$][\w$]*)
        \s*\)""",
    re.VERBOSE,
)


def _has_trpc_imports(parsed_files: dict[str, Any]) -> bool:
    for parsed in parsed_files.values():
        if parsed.file_info.language not in ("typescript", "javascript"):
            continue
        for imp in parsed.imports:
            if "@trpc/" in imp.module_path or imp.module_path == "trpc":
                return True
    return False


class _TrpcHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        if "trpc" in dctx.stack_lower:
            return True
        return _has_trpc_imports(dctx.parsed_files)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        count = 0
        for path, parsed in parsed_files.items():
            if parsed.file_info.language not in ("typescript", "javascript"):
                continue
            text = read_text(parsed)
            if not text or "@trpc/" not in text and "publicProcedure" not in text \
                    and "protectedProcedure" not in text:
                continue
            var_to_file = _build_ts_var_to_file(parsed, path, ctx, path_set)
            if not var_to_file:
                continue
            for m in _TRPC_PROC_RE.finditer(text):
                handler_name = m.group(1)
                target = var_to_file.get(handler_name)
                if target and target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1
        return count


HANDLERS: list[FrameworkHandler] = [_TrpcHandler()]
