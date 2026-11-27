"""Vite ``import.meta.glob`` and ``new URL(..., import.meta.url)`` are imports.

A glob call loads every file its patterns match, so each gets an edge from the
caller and is reachable; ``!`` patterns remove files, and only indexed files
match. ``new URL('./x', import.meta.url)`` loads the file beside the caller.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.resolvers.ts_workspace import _vitest_glob_to_regex
from repowise.core.ingestion.tsconfig_resolver import wire_tsconfig_resolver


def _build(root: Path, files: dict[str, str]):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    builder = GraphBuilder(root)
    parser = ASTParser()
    source_map: dict[str, bytes] = {}
    for fi in FileTraverser(root).traverse():
        src = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, src))
        source_map[fi.path] = src
    builder.set_source_map(source_map)
    wire_tsconfig_resolver(builder, root)
    builder.build()
    graph = builder.graph()
    report = DeadCodeAnalyzer(
        graph, {}, parsed_files=builder._parsed_files, source_map=source_map, repo_root=root
    ).analyze({})
    return graph, report


def _imports_from(graph, source: str) -> dict[str, list[str]]:
    return {
        v: d.get("imported_names")
        for _, v, d in graph.out_edges(source, data=True)
        if d.get("edge_type") == "imports"
    }


def _unreachable(report) -> set[str]:
    return {f.file_path for f in report.findings if f.kind == DeadCodeKind.UNREACHABLE_FILE}


def test_literal_glob_edges_every_match_and_keeps_them_reachable(tmp_path: Path) -> None:
    graph, report = _build(
        tmp_path,
        {
            "src/main.ts": (
                "import { load } from './loader';\nload();\n"
            ),
            "src/loader.ts": (
                "export function load() {\n"
                "  return import.meta.glob('./pages/*.ts');\n"
                "}\n"
            ),
            "src/pages/home.ts": "export const title = 'home';\n",
            "src/pages/about.ts": "export const title = 'about';\n",
            "src/pages/nested/deep.ts": "export const title = 'deep';\n",
            "src/orphan.ts": "export const x = 1;\n",
        },
    )
    edges = _imports_from(graph, "src/loader.ts")
    assert edges["src/pages/home.ts"] == ["*"]
    assert edges["src/pages/about.ts"] == ["*"]
    # ``*`` stays inside one directory.
    assert "src/pages/nested/deep.ts" not in edges
    unreachable = _unreachable(report)
    assert "src/pages/home.ts" not in unreachable
    assert "src/pages/about.ts" not in unreachable
    assert "src/orphan.ts" in unreachable


def test_array_negation_options_and_braces(tmp_path: Path) -> None:
    graph, _ = _build(
        tmp_path,
        {
            "src/registry.ts": (
                "export const mods = import.meta.glob(\n"
                "  ['./mods/**/*.{ts,js}', '!**/skip.ts', '!./mods/legacy/*'],\n"
                "  { eager: true, import: 'setup' },\n"
                ");\n"
                "export const raw = import.meta.glob('./docs/*.md.ts', { query: '?raw' });\n"
            ),
            "src/mods/a.ts": "export function setup() {}\n",
            "src/mods/sub/b.js": "export function setup() {}\n",
            "src/mods/skip.ts": "export function setup() {}\n",
            "src/mods/legacy/old.ts": "export function setup() {}\n",
            "src/docs/intro.md.ts": "export const text = 'x';\n",
        },
    )
    edges = _imports_from(graph, "src/registry.ts")
    assert edges["src/mods/a.ts"] == ["setup"]
    assert edges["src/mods/sub/b.js"] == ["setup"]
    assert "src/mods/skip.ts" not in edges
    assert "src/mods/legacy/old.ts" not in edges
    # Loaded as text: the file is used, none of its exports are.
    assert edges["src/docs/intro.md.ts"] == []


@pytest.mark.parametrize(
    ("root_prefix", "markers"),
    [
        pytest.param("", {}, id="repo-root"),
        pytest.param(
            "apps/web/", {"package.json": "{}", "apps/web/package.json": "{}"}, id="package-root"
        ),
        pytest.param(
            "apps/web/",
            {
                "apps/web/vite.config.mts": "export default { build: { root: './dist' } };\n",
                "apps/web/src/package.json": "{}",
            },
            id="config-directory",
        ),
        pytest.param(
            "apps/web/site/",
            {
                "vite.config.ts": "export default {};\n",
                "apps/web/vite.config.ts": "export default defineConfig({ root: './site' });\n",
            },
            id="configured-root",
        ),
    ],
)
def test_root_relative_glob(tmp_path: Path, root_prefix: str, markers: dict[str, str]) -> None:
    other_pages = "src/pages" if root_prefix else "apps/web/src/pages"
    graph, _ = _build(
        tmp_path,
        {
            **markers,
            "apps/web/src/main.ts": (
                "export const pages = import.meta.glob("
                "['/src/pages/*.ts', '!/src/pages/skip.ts', '/../outside/*.ts']);\n"
            ),
            f"{root_prefix}src/pages/home.ts": "export const title = 'home';\n",
            f"{root_prefix}src/pages/skip.ts": "export const title = 'skip';\n",
            f"{other_pages}/other.ts": "export const title = 'other';\n",
            "apps/outside/leak.ts": "export const title = 'outside';\n",
            "apps/web/outside/leak.ts": "export const title = 'outside';\n",
        },
    )
    assert _imports_from(graph, "apps/web/src/main.ts") == {
        f"{root_prefix}src/pages/home.ts": ["*"]
    }


def test_alias_rooted_glob_resolves_through_tsconfig_paths(tmp_path: Path) -> None:
    graph, report = _build(
        tmp_path,
        {
            "tsconfig.json": json.dumps(
                {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}
            ),
            "src/use-announcements.ts": (
                "export const all = import.meta.glob('@/announcements/*.ts', { eager: true });\n"
            ),
            "src/announcements/2024-01-01.ts": "export default { title: 'a' };\n",
            "src/announcements/2024-02-01.ts": "export default { title: 'b' };\n",
            "other/announcements/2024-03-01.ts": "export default { title: 'c' };\n",
        },
    )
    edges = _imports_from(graph, "src/use-announcements.ts")
    assert set(edges) == {"src/announcements/2024-01-01.ts", "src/announcements/2024-02-01.ts"}
    assert "src/announcements/2024-01-01.ts" not in _unreachable(report)


def test_nuxt_alias_glob_reaches_the_components(tmp_path: Path) -> None:
    c = "app/components/Domain/Announcement/Announcements"
    graph, report = _build(
        tmp_path,
        {
            "package.json": json.dumps({"dependencies": {"nuxt": "^4.1.0"}}),
            "nuxt.config.ts": "export default defineNuxtConfig({});\n",
            "app/app.vue": "<template><div /></template>\n",
            "app/composables/use-announcements.ts": (
                "const all = import.meta.glob('~/components/Domain/Announcement/"
                "Announcements/*.vue');\n"
                "export function useAnnouncements() { return all; }\n"
            ),
            f"{c}/2024-06-01.vue": "<template><p>a</p></template>\n",
            f"{c}/2024-09-01.vue": "<template><p>b</p></template>\n",
        },
    )
    edges = _imports_from(graph, "app/composables/use-announcements.ts")
    assert set(edges) == {f"{c}/2024-06-01.vue", f"{c}/2024-09-01.vue"}
    assert not _unreachable(report) & set(edges)


def test_template_pattern_and_base_option(tmp_path: Path) -> None:
    graph, _ = _build(
        tmp_path,
        {
            "src/index.ts": (
                "export const a = import.meta.glob(`./views/*.ts`);\n"
                "export const b = import.meta.glob(['./*.ts', '!./skip.ts'], { base: './widgets' });\n"
                "export const c = import.meta.glob(`./${dir}/*.ts`);\n"
            ),
            "src/views/home.ts": "export const v = 1;\n",
            "src/widgets/button.ts": "export const w = 1;\n",
            "src/widgets/skip.ts": "export const s = 1;\n",
        },
    )
    edges = _imports_from(graph, "src/index.ts")
    assert set(edges) == {"src/views/home.ts", "src/widgets/button.ts"}


def test_glob_never_matches_the_importer_or_unindexed_files(tmp_path: Path) -> None:
    graph, _ = _build(
        tmp_path,
        {
            ".gitignore": "src/generated/\n",
            "src/all.ts": "export const all = import.meta.glob(['./*.ts', './generated/*.ts']);\n",
            "src/one.ts": "export const one = 1;\n",
            "src/generated/two.ts": "export const two = 2;\n",
        },
    )
    edges = _imports_from(graph, "src/all.ts")
    assert set(edges) == {"src/one.ts"}


def test_new_url_with_import_meta_url_edges_the_relative_file(tmp_path: Path) -> None:
    graph, report = _build(
        tmp_path,
        {
            "config/index.mjs": (
                "const plugin = new URL('./plugins/rule.mjs', import.meta.url);\n"
                "const pkg = new URL('some-package/x.js', import.meta.url);\n"
                "export default { plugin, pkg };\n"
            ),
            "config/plugins/rule.mjs": "export const rule = 1;\n",
        },
    )
    edges = _imports_from(graph, "config/index.mjs")
    assert edges == {"config/plugins/rule.mjs": []}
    assert "config/plugins/rule.mjs" not in _unreachable(report)


def test_glob_regex_braces() -> None:
    regex = _vitest_glob_to_regex("src/**/*.{test,spec}.{ts,tsx}")
    assert regex.match("src/a/b.test.ts")
    assert regex.match("src/b.spec.tsx")
    assert not regex.match("src/b.unit.ts")
    # An unbalanced or comma-less brace is a literal.
    assert _vitest_glob_to_regex("a/{b}.ts").match("a/{b}.ts")
