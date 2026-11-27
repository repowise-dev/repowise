"""Astro import resolution: ESM through the TS resolver, ``/`` paths under ``public/``."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.framework_facts.js import js_frameworks
from repowise.core.ingestion.framework_facts.npm import npm_manifests
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


def test_a_relative_markup_ref_falls_back_to_public() -> None:
    # ``href="favicon.ico"`` arrives as ``./favicon.ico``; a sibling file still wins.
    layout = "src/layouts/Base.astro"
    ctx = _ctx({layout, "public/favicon.ico", "public/js/a.js", "public/a.js", "src/layouts/a.js"})
    assert resolve_import("./favicon.ico", layout, "astro", ctx) == "public/favicon.ico"
    assert resolve_import("./js/a.js", layout, "astro", ctx) == "public/js/a.js"
    assert resolve_import("./a.js", layout, "astro", ctx) == "src/layouts/a.js"


def test_custom_astro_directories(tmp_path: Path) -> None:
    app = tmp_path / "apps/site"
    app.mkdir(parents=True)
    (app / "package.json").write_text('{"dependencies": {"astro": "*"}}')
    (app / "astro.config.mjs").write_text(
        "export default defineConfig({ srcDir: './app', publicDir: './static' })"
    )
    page = "apps/site/app/pages/index.astro"
    paths = {
        page,
        "apps/site/app/middleware.ts",
        "apps/site/app/scripts/root.ts",
        "apps/site/static/js/a.js",
        "apps/site/public/js/a.js",
        "apps/site/src/pages/old.astro",
    }
    ctx = _ctx(paths)
    ctx.repo_path = tmp_path
    assert resolve_import("/js/a.js?v=2", page, "astro", ctx) == "apps/site/static/js/a.js"
    assert resolve_import("/app/scripts/root.ts", page, "astro", ctx) == (
        "apps/site/app/scripts/root.ts"
    )
    ctx.path_set.add("apps/site/src/lib.js")
    assert resolve_import("/src/lib.js", page, "astro", ctx) == "apps/site/src/lib.js"
    manifest = npm_manifests(tmp_path, paths)[0]
    facts, _ = js_frameworks(manifest, paths)[0]
    entries = set(facts.entry_files(manifest.rel_dir, paths))
    assert page in entries
    assert "apps/site/app/middleware.ts" in entries
    assert "apps/site/src/pages/old.astro" not in entries


def test_default_astro_directories_with_manifest(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"dependencies": {"astro": "*"}}')
    page = "src/pages/index.astro"
    paths = {page, "public/a.js", "src/scripts/root.ts"}
    ctx = _ctx(paths)
    ctx.repo_path = tmp_path
    assert resolve_import("/a.js", page, "astro", ctx) == "public/a.js"
    assert resolve_import("/src/scripts/root.ts", page, "astro", ctx) == "src/scripts/root.ts"
    manifest = npm_manifests(tmp_path, paths)[0]
    facts, _ = js_frameworks(manifest, paths)[0]
    assert page in facts.entry_files(manifest.rel_dir, paths)
