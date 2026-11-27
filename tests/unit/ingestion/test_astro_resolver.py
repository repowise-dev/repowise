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


def test_a_relative_markup_ref_falls_back_to_public() -> None:
    # ``href="favicon.ico"`` arrives as ``./favicon.ico``; a sibling file still wins.
    layout = "src/layouts/Base.astro"
    ctx = _ctx({layout, "public/favicon.ico", "public/js/a.js", "public/a.js", "src/layouts/a.js"})
    assert resolve_import("./favicon.ico", layout, "astro", ctx) == "public/favicon.ico"
    assert resolve_import("./js/a.js", layout, "astro", ctx) == "public/js/a.js"
    assert resolve_import("./a.js", layout, "astro", ctx) == "src/layouts/a.js"


# --- srcDir and publicDir (#6451) ------------------------------------------------


def _astro_manifest(config: str | None):
    from dataclasses import replace

    from repowise.core.ingestion.framework_facts import parse_npm_manifest

    manifest = parse_npm_manifest({"dependencies": {"astro": "^5.0.0"}})
    assert manifest is not None
    return manifest if config is None else replace(manifest, configs={"astro.config.mjs": config})


def test_the_default_src_dir_anchors_src_pages() -> None:
    from repowise.core.ingestion.framework_facts import js_frameworks

    ((facts, _pkg),) = js_frameworks(_astro_manifest(None))
    paths = ["src/pages/index.astro", "src/middleware.ts", "app/pages/index.astro"]
    assert facts.entry_files("", paths) == ["src/middleware.ts", "src/pages/index.astro"]


def test_a_custom_src_dir_anchors_its_pages_and_middleware() -> None:
    from repowise.core.ingestion.framework_facts import js_frameworks

    config = "export default defineConfig({ srcDir: './app' })"
    ((facts, _pkg),) = js_frameworks(_astro_manifest(config))
    paths = [
        "app/pages/index.astro",
        "app/middleware.ts",
        "app/content.config.ts",
        "src/pages/old.astro",
    ]
    assert facts.entry_files("", paths) == [
        "app/content.config.ts",
        "app/middleware.ts",
        "app/pages/index.astro",
    ]


def test_a_src_dir_outside_the_app_keeps_the_default() -> None:
    from repowise.core.ingestion.framework_facts import js_frameworks

    ((facts, _pkg),) = js_frameworks(_astro_manifest("defineConfig({ srcDir: '../shared' })"))
    assert facts.entry_files("", ["src/pages/a.astro"]) == ["src/pages/a.astro"]


def test_the_src_dir_is_read_from_the_config_on_disk(tmp_path) -> None:
    from repowise.core.ingestion.framework_facts import convention_load_reasons

    (tmp_path / "package.json").write_text(
        '{"dependencies": {"astro": "^5.0.0"}}', encoding="utf-8"
    )
    (tmp_path / "astro.config.mjs").write_text(
        "export default defineConfig({ srcDir: 'app' })", encoding="utf-8"
    )
    assert set(
        convention_load_reasons(tmp_path, ["app/pages/index.astro", "src/pages/a.astro"])
    ) == {"app/pages/index.astro"}


def test_a_root_path_resolves_under_a_custom_public_dir() -> None:
    page = "apps/site/app/pages/i.astro"
    config = b"export default defineConfig({ srcDir: './app', publicDir: './static' })"
    ctx = _ctx(
        {
            page,
            "apps/site/astro.config.mjs",
            "apps/site/static/favicon.svg",
            "apps/site/public/favicon.svg",
        }
    )
    ctx.source_map = {"apps/site/astro.config.mjs": config}
    assert resolve_import("/favicon.svg", page, "astro", ctx) == "apps/site/static/favicon.svg"


def test_an_app_without_public_dir_still_uses_public() -> None:
    page = "apps/site/src/pages/i.astro"
    ctx = _ctx({page, "apps/site/astro.config.mjs", "apps/site/public/favicon.svg"})
    ctx.source_map = {
        "apps/site/astro.config.mjs": b"export default defineConfig({ srcDir: 'src' })"
    }
    assert resolve_import("/favicon.svg", page, "astro", ctx) == "apps/site/public/favicon.svg"
