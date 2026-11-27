"""Nuxt aliases and auto-imports, end to end through the graph.

``~/`` and ``@/`` resolve to the source directory and ``~~/`` / ``@@/`` to the
project root. Component tags and composable/util calls written without an
import bind to the one file Nuxt would load, so reachability, unused exports,
call edges and test links see them. A repository that is not a Nuxt project
(no ``nuxt`` dependency) resolves exactly as before.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.framework_facts import convention_load_reasons
from repowise.core.ingestion.resolvers.nuxt import component_name

_APP_FILES = {
    "frontend/nuxt.config.ts": """\
export default defineNuxtConfig({
  components: [{ path: "~/components", pathPrefix: false }],
  future: { compatibilityVersion: 4 },
});
""",
    "frontend/tsconfig.json": '{ "extends": "./.nuxt/tsconfig.json" }\n',
    "frontend/app/pages/index.vue": """\
<template>
  <div>
    <RecipeCard :title="titleCase(name)" />
    <LazyStatsCards />
    <base-dialog />
  </div>
</template>

<script setup lang="ts">
import { useUser } from "~/composables/use-user";
import Editor from "@/components/Domain/Recipe/RecipeEditor.vue";
const user = useUser();
const toast = useToast();
const name = "x";
</script>
""",
    "frontend/app/components/Domain/Recipe/RecipeCard.vue": "<template><p>card</p></template>\n",
    "frontend/app/components/Domain/Recipe/RecipeEditor.vue": "<template><p>ed</p></template>\n",
    "frontend/app/components/global/StatsCards.vue": "<template><p>stats</p></template>\n",
    "frontend/app/components/global/BaseDialog.vue": "<template><p>dialog</p></template>\n",
    "frontend/app/components/Orphan.vue": "<template><p>orphan</p></template>\n",
    "frontend/app/composables/use-user.ts": "export function useUser() { return 1; }\n",
    "frontend/app/composables/use-toast.ts": (
        "export function useToast() { return 2; }\nexport function neverUsed() { return 3; }\n"
    ),
    "frontend/app/composables/use-utils.ts": "export function titleCase(s: string) { return s; }\n",
    # Nested: Nuxt does not auto-import it, so a bare call does not bind.
    "frontend/app/composables/api/use-nested.ts": "export function useNested() { return 4; }\n",
    "frontend/app/composables/use-toast.test.ts": """\
import { useToast } from "~/composables/use-toast";
test("toast", () => { useToast(); });
""",
    "frontend/app/pages/shadow.vue": """\
<script setup lang="ts">
function useToast() { return 0; }
useToast();
useNested();
</script>
""",
    "frontend/app/pages/param.vue": """\
<script setup lang="ts">
const fmt = (titleCase: (s: string) => string) => titleCase("a");
const img = RecipeCard();
</script>
""",
    "frontend/server/api/ping.ts": "export default () => useToast();\n",
}


def _write(root: Path, files: dict[str, str], nuxt_dep: bool) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    deps = {"nuxt": "^4.1.0"} if nuxt_dep else {"vue": "^3.5.0"}
    (root / "frontend/package.json").write_text(
        json.dumps({"dependencies": deps}), encoding="utf-8"
    )


def _build(root: Path):
    builder = GraphBuilder(root)
    parser = ASTParser()
    source_map: dict[str, bytes] = {}
    for fi in FileTraverser(root).traverse():
        src = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, src))
        source_map[fi.path] = src
    builder.set_source_map(source_map)
    builder.build()
    graph = builder.graph()
    report = DeadCodeAnalyzer(
        graph, {}, parsed_files=builder._parsed_files, source_map=source_map, repo_root=root
    ).analyze({})
    return builder, graph, report


def _imports(graph) -> set[tuple[str, str]]:
    return {(u, v) for u, v, d in graph.edges(data=True) if d.get("edge_type") == "imports"}


def _calls(graph) -> set[tuple[str, str]]:
    return {(u, v) for u, v, d in graph.edges(data=True) if d.get("edge_type") == "calls"}


@pytest.fixture
def nuxt_app(tmp_path: Path):
    _write(tmp_path, _APP_FILES, nuxt_dep=True)
    return _build(tmp_path)


_PAGE = "frontend/app/pages/index.vue"
_C = "frontend/app/components"


def test_aliases_resolve_to_the_source_dir(nuxt_app) -> None:
    _, graph, _ = nuxt_app
    edges = _imports(graph)
    assert (_PAGE, "frontend/app/composables/use-user.ts") in edges
    assert (_PAGE, f"{_C}/Domain/Recipe/RecipeEditor.vue") in edges
    assert not any(v.startswith("external:~") or v.startswith("external:@/") for _, v in edges)


def test_component_tags_bind_to_their_files(nuxt_app) -> None:
    _, graph, _ = nuxt_app
    edges = _imports(graph)
    assert (_PAGE, f"{_C}/Domain/Recipe/RecipeCard.vue") in edges
    assert (_PAGE, f"{_C}/global/StatsCards.vue") in edges  # <LazyStatsCards>
    assert (_PAGE, f"{_C}/global/BaseDialog.vue") in edges  # <base-dialog>


def test_composable_and_util_calls_bind_with_their_names(nuxt_app) -> None:
    _, graph, _ = nuxt_app
    toast = graph.edges[_PAGE, "frontend/app/composables/use-toast.ts"]
    assert toast["imported_names"] == ["useToast"]
    assert (_PAGE, "frontend/app/composables/use-utils.ts") in _imports(graph)


def test_auto_imports_reach_the_call_graph(nuxt_app) -> None:
    _, graph, _ = nuxt_app
    calls = _calls(graph)
    for callee in (
        "frontend/app/composables/use-toast.ts::useToast",
        f"{_C}/global/StatsCards.vue::StatsCards",  # <LazyStatsCards>
        f"{_C}/Domain/Recipe/RecipeCard.vue::RecipeCard",
    ):
        assert any(u.startswith(_PAGE) and v == callee for u, v in calls), callee


def test_dead_code_keeps_auto_imported_files_and_exports(nuxt_app) -> None:
    _, _, report = nuxt_app
    files = {f.file_path for f in report.findings if f.kind.value == "unreachable_file"}
    exports = {f.symbol_name for f in report.findings if f.kind.value == "unused_export"}
    for live in (
        "RecipeCard.vue",
        "StatsCards.vue",
        "BaseDialog.vue",
        "use-toast.ts",
        "use-utils.ts",
    ):
        assert not any(p.endswith(live) for p in files), live
    assert not exports & {"useToast", "titleCase", "useUser"}
    # True positives stay: nothing names them.
    assert f"{_C}/Orphan.vue" in files
    assert "neverUsed" in exports


def test_no_binding_where_nuxt_would_not_import(nuxt_app) -> None:
    _, graph, _ = nuxt_app
    edges = _imports(graph)
    # A local declaration shadows the auto-import; nested composables are not scanned.
    assert ("frontend/app/pages/shadow.vue", "frontend/app/composables/use-toast.ts") not in edges
    assert (
        "frontend/app/pages/shadow.vue",
        "frontend/app/composables/api/use-nested.ts",
    ) not in edges
    # A parameter shadows a util; a script call is not a component tag.
    assert ("frontend/app/pages/param.vue", "frontend/app/composables/use-utils.ts") not in edges
    assert ("frontend/app/pages/param.vue", f"{_C}/Domain/Recipe/RecipeCard.vue") not in edges
    # server/ belongs to Nitro, which has its own auto-imports.
    assert ("frontend/server/api/ping.ts", "frontend/app/composables/use-toast.ts") not in edges


def test_test_file_links_through_the_alias(nuxt_app) -> None:
    _, graph, _ = nuxt_app
    assert (
        "frontend/app/composables/use-toast.test.ts",
        "frontend/app/composables/use-toast.ts",
    ) in _imports(graph)


def test_auto_imports_stay_off_the_parsed_file(nuxt_app) -> None:
    builder, _, _ = nuxt_app
    parsed = builder._parsed_files[_PAGE]
    assert not any(imp.raw_statement.startswith("// nuxt") for imp in parsed.imports)
    assert len(builder.import_resolutions[_PAGE]) == len(parsed.imports)


def test_without_a_nuxt_dependency_nothing_changes(tmp_path: Path) -> None:
    _write(tmp_path, _APP_FILES, nuxt_dep=False)
    builder, graph, _ = _build(tmp_path)
    assert builder._implicit_imports == {}
    edges = _imports(graph)
    assert (_PAGE, f"{_C}/Domain/Recipe/RecipeCard.vue") not in edges
    assert (_PAGE, "frontend/app/composables/use-user.ts") not in edges


def test_nuxt3_root_src_dir_and_alias_to_root(tmp_path: Path) -> None:
    files = {
        "nuxt.config.ts": "export default defineNuxtConfig({});\n",
        "pages/index.vue": (
            '<script setup lang="ts">\nimport { a } from "~/lib/a";\n'
            'import { b } from "~~/shared/b";\nconst x = a() + b();\n</script>\n'
        ),
        "lib/a.ts": "export function a() { return 1; }\n",
        "shared/b.ts": "export function b() { return 2; }\n",
        "components/base/BaseButton.vue": "<template><b /></template>\n",
        "components/form/Input.vue": "<template><i /></template>\n",
        "app.vue": "<template><BaseButton /><FormInput /></template>\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    (tmp_path / "package.json").write_text(
        '{"devDependencies": {"nuxt": "^3.12.0"}}', encoding="utf-8"
    )
    _, graph, _ = _build(tmp_path)
    edges = _imports(graph)
    assert ("pages/index.vue", "lib/a.ts") in edges
    assert ("pages/index.vue", "shared/b.ts") in edges
    # pathPrefix on (the default): directory segments prefix the name, deduplicated.
    assert ("app.vue", "components/base/BaseButton.vue") in edges
    assert ("app.vue", "components/form/Input.vue") in edges


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def test_src_dir_roots_aliases_and_auto_imports(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        {
            "package.json": '{"devDependencies": {"nuxt": "^3.12.0"}}',
            "nuxt.config.ts": 'export default defineNuxtConfig({ srcDir: "src" });\n',
            "src/pages/index.vue": (
                '<script setup lang="ts">\nimport { a } from "~/lib/a";\nconst x = a() + useB();\n'
                "</script>\n<template><RecipeCard /></template>\n"
            ),
            "src/lib/a.ts": "export function a() { return 1; }\n",
            "src/composables/use-b.ts": "export function useB() { return 2; }\n",
            "src/components/RecipeCard.vue": "<template><p /></template>\n",
        },
    )
    _, graph, _ = _build(tmp_path)
    edges = _imports(graph)
    assert ("src/pages/index.vue", "src/lib/a.ts") in edges
    assert ("src/pages/index.vue", "src/composables/use-b.ts") in edges
    assert ("src/pages/index.vue", "src/components/RecipeCard.vue") in edges


def test_a_config_takes_the_dependency_of_the_manifest_above_it(tmp_path: Path) -> None:
    """``web/nuxt.config.ts`` with ``nuxt`` declared only in the root package.json."""
    _write_tree(
        tmp_path,
        {
            "package.json": '{"devDependencies": {"nuxt": "^3.12.0"}}',
            "web/nuxt.config.ts": "export default defineNuxtConfig({});\n",
            "web/pages/index.vue": (
                '<script setup lang="ts">\nimport { a } from "~/lib/a";\nconst x = a();\n</script>\n'
            ),
            "web/lib/a.ts": "export function a() { return 1; }\n",
        },
    )
    _, graph, _ = _build(tmp_path)
    assert ("web/pages/index.vue", "web/lib/a.ts") in _imports(graph)


def test_a_nuxt_module_repo_binds_nothing(tmp_path: Path) -> None:
    """``nuxt`` as a devDependency with no ``nuxt.config`` is a module, not an app."""
    _write_tree(
        tmp_path,
        {
            "package.json": '{"devDependencies": {"nuxt": "^3.12.0"}}',
            "src/runtime/plugin.ts": "export default () => useThing();\n",
            "composables/use-thing.ts": "export function useThing() { return 1; }\n",
        },
    )
    builder, _, _ = _build(tmp_path)
    assert builder._implicit_imports == {}


@pytest.mark.parametrize(
    ("rel", "prefix", "expected"),
    [
        ("base/BaseButton.vue", True, "BaseButton"),
        ("form/Input.vue", True, "FormInput"),
        ("form/input/index.vue", True, "FormInput"),
        ("some-dir/my-comp.client.vue", True, "SomeDirMyComp"),
        ("Domain/Recipe/RecipeCard.vue", True, "DomainRecipeCard"),
        ("Domain/Recipe/RecipeCard.vue", False, "RecipeCard"),
        ("Domain/Recipe/index.vue", False, "Recipe"),
    ],
)
def test_component_names_follow_nuxt(rel: str, prefix: bool, expected: str) -> None:
    assert component_name(rel, prefix) == expected


def test_nuxt_dir_pages_override(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        {
            "package.json": '{"devDependencies": {"nuxt": "^3.12.0"}}',
            "nuxt.config.ts": "export default defineNuxtConfig({ dir: { pages: 'routes' } });\n",
            "routes/index.vue": "<template><h1>Home</h1></template>\n",
            "pages/ignored.vue": "<template><h1>Ignored</h1></template>\n",
        },
    )
    builder, _graph, _report = _build(tmp_path)
    builder.add_framework_edges([])
    assert builder.graph().has_edge("framework:nuxt", "routes/index.vue")
    assert not builder.graph().has_edge("framework:nuxt", "pages/ignored.vue")
    reasons = convention_load_reasons(tmp_path, ["routes/index.vue", "pages/ignored.vue"])
    assert "routes/index.vue" in reasons
    assert "pages/ignored.vue" not in reasons


def test_nuxt_server_dir_override(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        {
            "package.json": '{"devDependencies": {"nuxt": "^3.12.0"}}',
            "nuxt.config.ts": "export default defineNuxtConfig({ serverDir: 'custom-server' });\n",
            "custom-server/api/ping.ts": "export default () => 'pong';\n",
            "server/api/ignored.ts": "export default () => 'ignored';\n",
        },
    )
    builder, _graph, _ = _build(tmp_path)
    builder.add_framework_edges([])
    assert builder.graph().has_edge("framework:nuxt", "custom-server/api/ping.ts")
    assert not builder.graph().has_edge("framework:nuxt", "server/api/ignored.ts")


def test_nuxt_extends_layer(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        {
            "app/package.json": '{"devDependencies": {"nuxt": "^3.12.0"}}',
            "app/nuxt.config.ts": "export default defineNuxtConfig({ extends: ['../base'] });\n",
            "app/pages/index.vue": "<template><h1>App</h1></template>\n",
            "base/pages/layer.vue": "<template><h1>Layer</h1></template>\n",
            "base/components/BaseCard.vue": "<template><p>card</p></template>\n",
            "stray/pages/stray.vue": "<template><h1>Stray</h1></template>\n",
        },
    )
    builder, _graph, _ = _build(tmp_path)
    builder.add_framework_edges([])
    assert builder.graph().has_edge("framework:nuxt", "app/pages/index.vue")
    assert builder.graph().has_edge("framework:nuxt", "base/pages/layer.vue")
    assert not builder.graph().has_edge("framework:nuxt", "stray/pages/stray.vue")
    reasons = convention_load_reasons(
        tmp_path,
        [
            "app/pages/index.vue",
            "base/components/BaseCard.vue",
            "stray/pages/stray.vue",
        ],
    )
    assert "base/components/BaseCard.vue" in reasons
    assert "stray/pages/stray.vue" not in reasons


def test_nuxt_extends_layer_server_routes_default_src(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        {
            "app/package.json": '{"devDependencies": {"nuxt": "^3.12.0"}}',
            "app/nuxt.config.ts": "export default defineNuxtConfig({ extends: ['../base'] });\n",
            "app/pages/index.vue": "<template><h1>App</h1></template>\n",
            "base/server/api/layer_route.ts": "export default () => 'layer';\n",
        },
    )
    builder, _graph, _ = _build(tmp_path)
    builder.add_framework_edges([])
    assert builder.graph().has_edge("framework:nuxt", "base/server/api/layer_route.ts")
    reasons = convention_load_reasons(tmp_path, ["app/pages/index.vue", "base/server/api/layer_route.ts"])
    assert "base/server/api/layer_route.ts" in reasons


def test_nuxt_extends_repo_root_layer(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        {
            "apps/web/package.json": '{"devDependencies": {"nuxt": "^3.12.0"}}',
            "apps/web/nuxt.config.ts": "export default defineNuxtConfig({ extends: ['../..'] });\n",
            "apps/web/pages/index.vue": "<template><h1>Web</h1></template>\n",
            "pages/root.vue": "<template><h1>Root</h1></template>\n",
        },
    )
    builder, _graph, _ = _build(tmp_path)
    builder.add_framework_edges([])
    assert builder.graph().has_edge("framework:nuxt", "pages/root.vue")
    reasons = convention_load_reasons(tmp_path, ["apps/web/pages/index.vue", "pages/root.vue"])
    assert "pages/root.vue" in reasons


def test_nuxt_layer_server_routes_with_custom_server_dir(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        {
            "app/package.json": '{"devDependencies": {"nuxt": "^3.12.0"}}',
            "app/nuxt.config.ts": (
                "export default defineNuxtConfig({ serverDir: 'custom-server', extends: ['../base'] });\n"
            ),
            "app/custom-server/api/app.ts": "export default () => 'app';\n",
            "base/server/api/layer.ts": "export default () => 'layer';\n",
            "base/custom-server/api/wrong.ts": "export default () => 'wrong';\n",
        },
    )
    builder, _graph, _ = _build(tmp_path)
    builder.add_framework_edges([])
    assert builder.graph().has_edge("framework:nuxt", "app/custom-server/api/app.ts")
    assert builder.graph().has_edge("framework:nuxt", "base/server/api/layer.ts")
    assert not builder.graph().has_edge("framework:nuxt", "base/custom-server/api/wrong.ts")

