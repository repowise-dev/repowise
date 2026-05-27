"""FastAPI ``include_router`` convention edges.

Split out of ``framework_edges.py`` (PR 3.5) — behaviour-preserving move.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext, resolve_import
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
)

if TYPE_CHECKING:
    import networkx as nx


def _add_fastapi_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    """Detect include_router() calls and link app files to router modules."""
    count = 0
    var_to_file: dict[str, str] = {}

    for path, parsed in parsed_files.items():
        for imp in parsed.imports:
            for name in imp.imported_names:
                if name.lower().endswith("router") or name.lower().endswith("app"):
                    resolved = resolve_import(
                        imp.module_path,
                        path,
                        parsed.file_info.language,
                        ctx,
                    )
                    if resolved and resolved in path_set:
                        var_to_file[name] = resolved

    router_re = re.compile(r"(?:include_router|add_api_route)\s*\(\s*(\w+)")
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "python":
            continue
        try:
            source = Path(parsed.file_info.abs_path).read_text(errors="ignore")
        except Exception:
            continue
        for match in router_re.finditer(source):
            var_name = match.group(1)
            target = var_to_file.get(var_name)
            if target and target in path_set and _add_edge_if_new(graph, path, target):
                count += 1
    return count


class _FastApiHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return "fastapi" in dctx.stack_lower or "starlette" in dctx.stack_lower

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_fastapi_edges(graph, parsed_files, ctx, path_set)


HANDLERS: list[FrameworkHandler] = [_FastApiHandler()]
