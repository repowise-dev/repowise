"""Astro import resolution: ESM through the TS resolver, ``/`` paths under ``public/``."""

from __future__ import annotations

import networkx as nx

from repowise.core.ingestion.resolvers import resolve_import
from repowise.core.ingestion.resolvers.context import ResolverContext

_PAGE = "apps/site/src/pages/i.astro"


def _ctx(paths: set[str]) -> ResolverContext:
    return ResolverContext(path_set=paths, stem_map={}, graph=nx.DiGraph(), has_sfc_files=True)


def test_a_root_path_resolves_under_the_nearest_public_folder() -> None:
    ctx = _ctx({_PAGE, "apps/site/public/js/a.js", "public/js/a.js"})
    assert resolve_import("/js/a.js?v=2", _PAGE, "astro", ctx) == "apps/site/public/js/a.js"


def test_a_root_path_falls_back_to_an_outer_public_folder() -> None:
    ctx = _ctx({_PAGE, "public/js/a.js"})
    assert resolve_import("/js/a.js#top", _PAGE, "astro", ctx) == "public/js/a.js"


def test_a_root_path_miss_is_no_node() -> None:
    ctx = _ctx({_PAGE})
    assert resolve_import("/favicon.svg", _PAGE, "astro", ctx) is None
    assert list(ctx.graph.nodes) == []


def test_a_relative_import_still_resolves_through_typescript() -> None:
    ctx = _ctx({_PAGE, "apps/site/src/pages/x.ts", "apps/site/src/pages/Card.astro"})
    assert resolve_import("./x", _PAGE, "astro", ctx) == "apps/site/src/pages/x.ts"
    assert resolve_import("./Card", _PAGE, "astro", ctx) == "apps/site/src/pages/Card.astro"
    assert resolve_import("react", _PAGE, "astro", ctx) == "external:react"


def test_a_root_src_path_falls_back_to_the_project_root() -> None:
    # Vite serves any ``/src/...`` value (an import, a ``<script src>``, a
    # ``<link href>``) from the project root; any other ``/`` path is a public URL only.
    ctx = _ctx({_PAGE, "apps/site/src/scripts/root.ts", "apps/site/js/a.js"})
    root_script = resolve_import("/src/scripts/root.ts", _PAGE, "astro", ctx)
    assert root_script == "apps/site/src/scripts/root.ts"
    assert resolve_import("/js/a.js", _PAGE, "astro", ctx) is None
