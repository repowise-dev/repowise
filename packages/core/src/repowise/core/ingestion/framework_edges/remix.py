"""Remix / SvelteKit / Astro loader/action filesystem-convention edges.

These frameworks load files by filesystem convention — Remix's
``routes/*.tsx`` with ``loader``/``action`` exports, SvelteKit's
``+page.ts``/``+server.ts``, Astro's ``src/pages/*``. Phase 1's
``_NEVER_FLAG_PATTERNS`` already exempts the convention files
themselves; this module emits edges from each convention file to its
intra-repo imports so consumed helpers (``~/utils/db``,
``$lib/server/auth``) inherit reachability.
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
)

if TYPE_CHECKING:
    import networkx as nx


_REMIX_ROUTE_RE = re.compile(r"(?:^|/)(?:app/)?routes/")
_SVELTE_ROUTE_RE = re.compile(r"/\+(?:page|layout|server|error)\.(ts|tsx|js|mjs)$")
_ASTRO_PAGE_RE = re.compile(r"(?:^|/)src/pages/")


def _is_convention_route(path: str) -> bool:
    if _SVELTE_ROUTE_RE.search(path):
        return True
    if _REMIX_ROUTE_RE.search(path) and any(
        path.endswith(ext) for ext in (".ts", ".tsx", ".js", ".jsx")
    ):
        return True
    if _ASTRO_PAGE_RE.search(path):
        return True
    return False


class _ConventionRouteHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        if any(tok in dctx.stack_lower for tok in ("remix", "sveltekit", "astro")):
            return True
        for path, parsed in dctx.parsed_files.items():
            if parsed.file_info.language not in ("typescript", "javascript"):
                continue
            if _is_convention_route(path):
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
            if not _is_convention_route(path):
                continue
            for target in _build_ts_var_to_file(parsed, path, ctx, path_set).values():
                if target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1
        return count


HANDLERS: list[FrameworkHandler] = [_ConventionRouteHandler()]
