"""Svelte end-to-end extraction: symbols, imports, calls, health, dead code."""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers import ResolverContext, resolve_import

_COMPONENT = b"""<script lang="ts">
  import Child from './Child.svelte';
  import { fmt } from '$lib/fmt';

  export let count: number = 0;

  /** Bump the counter. */
  export function inc() {
    if (count > 10) {
      count = 0;
    } else {
      count += 1;
    }
  }
</script>

<button on:click={inc}>{fmt(count)}</button>
<Child {count} />
<Child count={0} />
"""


def _file(path: str = "src/lib/Counter.svelte") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language="svelte",
        size_bytes=len(_COMPONENT),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


@pytest.fixture(scope="module")
def parser() -> ASTParser:
    return ASTParser()


@pytest.fixture(scope="module")
def parsed(parser: ASTParser):
    return parser.parse_file(_file(), _COMPONENT)


class TestSymbols:
    def test_the_file_itself_becomes_a_component_symbol(self, parsed) -> None:
        # Nothing in a .svelte source names the component — the filename does.
        component = [s for s in parsed.symbols if s.name == "Counter"]
        assert len(component) == 1
        assert component[0].kind == "class"
        assert component[0].start_line == 1

    def test_script_functions_are_extracted(self, parsed) -> None:
        assert "inc" in {s.name for s in parsed.symbols}

    def test_symbol_lines_point_at_the_original_file(self, parsed) -> None:
        inc = next(s for s in parsed.symbols if s.name == "inc")
        # `export function inc()` is on line 8 of the component source.
        assert inc.start_line == 8

    def test_route_sigil_is_stripped_from_the_component_name(self, parser) -> None:
        result = parser.parse_file(_file("src/routes/+page.svelte"), _COMPONENT)
        assert "page" in {s.name for s in result.symbols}

    def test_no_parse_errors_on_a_well_formed_component(self, parsed) -> None:
        assert parsed.parse_errors == []


class TestImports:
    def test_relative_component_import(self, parsed) -> None:
        child = next(i for i in parsed.imports if i.module_path.endswith("Child.svelte"))
        assert child.is_relative
        assert child.imported_names == ["Child"]

    def test_named_bindings_are_extracted(self, parsed) -> None:
        fmt = next(i for i in parsed.imports if i.module_path == "$lib/fmt")
        assert fmt.imported_names == ["fmt"]
        assert [b.local_name for b in fmt.bindings] == ["fmt"]


class TestCalls:
    def test_script_calls_are_extracted(self, parsed) -> None:
        assert "fmt" in {c.target_name for c in parsed.calls}

    def test_markup_component_tags_become_calls(self, parsed) -> None:
        # <Child /> is how Svelte instantiates Child — the JSX analogue.
        child_calls = [c for c in parsed.calls if c.target_name == "Child"]
        assert len(child_calls) == 2
        assert {c.line for c in child_calls} == {18, 19}

    def test_calls_are_attributed_to_the_component(self, parsed) -> None:
        child = next(c for c in parsed.calls if c.target_name == "Child")
        assert child.caller_symbol_id is not None
        assert child.caller_symbol_id.endswith("::Counter")

    def test_runes_are_not_call_targets(self, parser) -> None:
        src = b"<script>\n  let n = $state(0);\n  let d = $derived(n * 2);\n</script>\n"
        result = parser.parse_file(_file(), src)
        targets = {c.target_name for c in result.calls}
        assert "$state" not in targets
        assert "$derived" not in targets


class TestSvelteKitLibAlias:
    """$lib is declared only in the generated .svelte-kit tsconfig, which is
    gitignored — without explicit handling every $lib import goes external."""

    def _ctx(self, paths: set[str]) -> ResolverContext:
        import networkx as nx

        return ResolverContext(path_set=paths, stem_map={}, graph=nx.DiGraph())

    def test_lib_alias_resolves_to_src_lib(self) -> None:
        ctx = self._ctx({"svelte.config.js", "src/lib/fmt.ts", "src/routes/+page.svelte"})
        assert (
            resolve_import("$lib/fmt", "src/routes/+page.svelte", "svelte", ctx) == "src/lib/fmt.ts"
        )

    def test_lib_alias_resolves_a_component(self) -> None:
        ctx = self._ctx({"svelte.config.js", "src/lib/Button.svelte", "src/routes/+page.svelte"})
        assert (
            resolve_import("$lib/Button.svelte", "src/routes/+page.svelte", "svelte", ctx)
            == "src/lib/Button.svelte"
        )

    def test_lib_alias_resolves_an_index_barrel(self) -> None:
        ctx = self._ctx({"svelte.config.js", "src/lib/ui/index.ts", "src/routes/+page.svelte"})
        assert (
            resolve_import("$lib/ui", "src/routes/+page.svelte", "svelte", ctx)
            == "src/lib/ui/index.ts"
        )

    def test_monorepo_picks_the_nearest_project(self) -> None:
        ctx = self._ctx(
            {
                "apps/web/svelte.config.js",
                "apps/web/src/lib/fmt.ts",
                "apps/docs/svelte.config.js",
                "apps/docs/src/lib/fmt.ts",
                "apps/docs/src/routes/+page.svelte",
            }
        )
        assert (
            resolve_import("$lib/fmt", "apps/docs/src/routes/+page.svelte", "svelte", ctx)
            == "apps/docs/src/lib/fmt.ts"
        )

    def test_app_virtual_modules_stay_external(self) -> None:
        # $app/* is synthesised by the framework — it has no file in the repo.
        ctx = self._ctx({"svelte.config.js", "src/routes/+page.svelte"})
        resolved = resolve_import("$app/navigation", "src/routes/+page.svelte", "svelte", ctx)
        assert resolved == "external:$app/navigation"


class TestNodeSubpathImports:
    """package.json "imports" (#lib/*) — the other alias style SvelteKit apps
    use. svelte.dev uses it, and without it eight live components read as
    unreachable."""

    def _ctx(self, tmp_path, pkg: dict, paths: set[str]):
        import json

        import networkx as nx

        from repowise.core.ingestion.resolvers import ResolverContext

        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        for rel in paths:
            target = tmp_path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")
        return ResolverContext(
            path_set=set(paths) | {"package.json"},
            stem_map={},
            graph=nx.DiGraph(),
            repo_path=tmp_path,
        )

    def test_wildcard_alias_resolves_a_component(self, tmp_path) -> None:
        ctx = self._ctx(
            tmp_path,
            {"imports": {"#lib/*": "./src/lib/*"}},
            {"src/lib/components/Modal.svelte", "src/routes/Page.svelte"},
        )
        assert (
            resolve_import(
                "#lib/components/Modal.svelte", "src/routes/Page.svelte", "svelte", ctx
            )
            == "src/lib/components/Modal.svelte"
        )

    def test_wildcard_alias_probes_extensions(self, tmp_path) -> None:
        ctx = self._ctx(
            tmp_path,
            {"imports": {"#lib/*": "./src/lib/*"}},
            {"src/lib/utils.ts", "src/routes/Page.svelte"},
        )
        assert (
            resolve_import("#lib/utils", "src/routes/Page.svelte", "svelte", ctx)
            == "src/lib/utils.ts"
        )

    def test_exact_alias_key(self, tmp_path) -> None:
        ctx = self._ctx(
            tmp_path,
            {"imports": {"#config": "./src/config.ts"}},
            {"src/config.ts", "src/routes/Page.svelte"},
        )
        assert (
            resolve_import("#config", "src/routes/Page.svelte", "svelte", ctx) == "src/config.ts"
        )

    def test_conditional_target_is_flattened(self, tmp_path) -> None:
        ctx = self._ctx(
            tmp_path,
            {"imports": {"#lib/*": {"import": "./src/lib/*", "default": "./dist/*"}}},
            {"src/lib/api.ts", "src/routes/Page.svelte"},
        )
        assert (
            resolve_import("#lib/api", "src/routes/Page.svelte", "svelte", ctx) == "src/lib/api.ts"
        )

    def test_unmatched_alias_falls_through_to_external(self, tmp_path) -> None:
        ctx = self._ctx(
            tmp_path,
            {"imports": {"#lib/*": "./src/lib/*"}},
            {"src/routes/Page.svelte"},
        )
        assert (
            resolve_import("#nope/x", "src/routes/Page.svelte", "svelte", ctx)
            == "external:#nope/x"
        )

    def test_typescript_gets_the_same_treatment(self, tmp_path) -> None:
        # The "imports" field is plain Node — not Svelte-specific.
        ctx = self._ctx(
            tmp_path,
            {"imports": {"#lib/*": "./src/lib/*"}},
            {"src/lib/db.ts", "src/server.ts"},
        )
        assert resolve_import("#lib/db", "src/server.ts", "typescript", ctx) == "src/lib/db.ts"


class TestCodeHealth:
    def test_complexity_is_measured_on_the_script_block(self) -> None:
        from repowise.core.analysis.health.complexity.walker import walk_file

        result = walk_file("/repo/src/lib/Counter.svelte", "svelte", _COMPONENT)
        inc = next(f for f in result.functions if f.name == "inc")
        # one if/else branch on top of the base path
        assert inc.ccn == 2
        assert inc.start_line == 8

    def test_perf_dialect_is_registered(self) -> None:
        from repowise.core.analysis.health.perf.dialects import PERF_DIALECTS

        assert "svelte" in PERF_DIALECTS

    def test_dataflow_dialect_is_registered(self) -> None:
        from repowise.core.analysis.health.dataflow.dialects import DEFUSE_DIALECTS

        assert "svelte" in DEFUSE_DIALECTS


class TestDeadCode:
    def test_component_props_are_never_flagged_as_unused_exports(self) -> None:
        # `export let count` is set by the parent as a markup attribute, never
        # imported by name, so flagging it would be a guaranteed false positive.
        from repowise.core.analysis.dead_code.analyzer import _non_importable_kinds

        skipped = _non_importable_kinds("svelte")
        assert {"constant", "variable", "function", "class"} <= skipped

    def test_typescript_is_unaffected(self) -> None:
        from repowise.core.analysis.dead_code.analyzer import _non_importable_kinds

        assert "function" not in _non_importable_kinds("typescript")
