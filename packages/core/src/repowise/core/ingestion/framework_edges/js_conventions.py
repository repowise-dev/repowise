"""Files a JS framework loads by directory convention, anchored to the framework.

A React Email preview under ``emails/``, a Next.js ``pages/`` route, a Remix
``app/routes/`` module, a Nuxt page or plugin, a SvelteKit hook, an Astro page
and an Expo Router screen are loaded by the framework from where they sit, so
no file imports them. Each ``package.json`` that depends on one of these
frameworks (:data:`~..framework_facts.JS_FRAMEWORKS`) gets its entry globs
anchored to the framework's ``framework:`` node, relative to the manifest's
directory, the way a TYPO3 or Laravel app's convention files are. Dead-code
liveness then sees the file, and what it imports, as reached.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..framework_facts import js_frameworks, npm_manifests
from ..models import TS_JS_LANGUAGES
from ..resolvers import ResolverContext
from .base import DetectionContext, FrameworkHandler, add_entry_edges

if TYPE_CHECKING:
    import networkx as nx

class _JsConventionHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        if dctx.ctx.repo_path is None:
            return False
        return any(p.file_info.language in TS_JS_LANGUAGES for p in dctx.parsed_files.values())

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        if ctx.repo_path is None:
            return 0
        js_paths = [p for p, parsed in parsed_files.items() if parsed.file_info.language in TS_JS_LANGUAGES]
        count = 0
        for manifest in npm_manifests(ctx.repo_path, js_paths):
            for facts, _pkg in js_frameworks(manifest, path_set):
                count += add_entry_edges(graph, facts, manifest.rel_dir, path_set)
        return count


HANDLERS: list[FrameworkHandler] = [_JsConventionHandler()]
