"""Next.js App Router framework edges.

In the App Router, every file matching ``app/**/{page,layout,route,
middleware,template,default,error,loading,not-found,global-error}.
{ts,tsx,js,jsx}`` is loaded by the Next.js runtime via filesystem
convention — never imported by any other source file. Phase 1's
``_NEVER_FLAG_PATTERNS`` already exempts those files from
``unreachable_file`` flagging, but they're still invisible to the
import graph, so any module they consume (helpers, layout configs,
component libraries) reads as unreachable.

This module emits ``framework`` edges from every recognised App-Router
file to whatever it imports inside the repo, so the consumed modules
inherit reachability through the router files (which themselves are
exempt). The router files act as roots of an implicit framework
subgraph the static parser can't see on its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..framework_routes import next_app_router_file as _is_app_router_file
from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    _build_ts_var_to_file,
)

if TYPE_CHECKING:
    import networkx as nx


class _NextAppRouterHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        if any("next" in tok for tok in dctx.stack_lower):
            return True
        # Cheap presence check: any app-router file in the parsed set.
        for path, parsed in dctx.parsed_files.items():
            if parsed.file_info.language not in ("typescript", "javascript"):
                continue
            if _is_app_router_file(path):
                return True
        return False

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
            if not _is_app_router_file(path):
                continue
            # Edge from the router file to every intra-repo module it
            # imports — those modules now reach a never-flagged file.
            for target in _build_ts_var_to_file(parsed, path, ctx, path_set).values():
                if target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1
        return count


HANDLERS: list[FrameworkHandler] = [_NextAppRouterHandler()]
