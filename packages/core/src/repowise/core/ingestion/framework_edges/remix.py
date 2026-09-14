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

from ..framework_routes import remix_route_file
from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    _build_ts_var_to_file,
)

if TYPE_CHECKING:
    import networkx as nx


_SVELTE_ROUTE_RE = re.compile(r"/\+(?:page|layout|server|error)[.\w]*\.(ts|tsx|js|mjs|svelte)$")
_ASTRO_PAGE_RE = re.compile(r"(?:^|/)src/pages/")

# SvelteKit's +page.svelte / +layout.svelte are convention routes in their own
# right, so components count here alongside their .ts/.js siblings.
_ROUTE_LANGUAGES = ("typescript", "javascript", "svelte")


def _is_convention_route(path: str) -> bool:
    if _SVELTE_ROUTE_RE.search(path):
        return True
    if remix_route_file(path):
        return True
    return bool(_ASTRO_PAGE_RE.search(path))


class _ConventionRouteHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        if any(tok in dctx.stack_lower for tok in ("remix", "sveltekit", "astro")):
            return True
        for path, parsed in dctx.parsed_files.items():
            if parsed.file_info.language not in _ROUTE_LANGUAGES:
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
            if parsed.file_info.language not in _ROUTE_LANGUAGES:
                continue
            if not _is_convention_route(path):
                continue
            for target in _build_ts_var_to_file(parsed, path, ctx, path_set).values():
                if target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1
        return count


HANDLERS: list[FrameworkHandler] = [_ConventionRouteHandler()]
