"""Unit tests for ``TsWorkspaceIndex`` — exports-wildcard entry points,
MDX import scan, and the vitest ``include`` glob scanner.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.ts_workspace import (
    build_ts_workspace_index,
    find_mdx_import_targets,
    find_npm_script_entry_targets,
    find_vitest_include_targets,
    get_or_build_ts_index,
)


def _write(repo: Path, rel: str, body: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _ctx(repo: Path, paths: list[str]) -> ResolverContext:
    return ResolverContext(
        path_set=set(paths),
        stem_map={},
        graph=nx.DiGraph(),
        repo_path=repo,
    )


class TestExportsWildcardEntries:
    def test_concrete_export_target_added(self, tmp_path: Path) -> None:
        _write(tmp_path, "package.json", '{"workspaces":["packages/*"]}')
        _write(
            tmp_path,
            "packages/foo/package.json",
            '{"name":"@org/foo","exports":{".":"./src/index.ts"}}',
        )
        _write(tmp_path, "packages/foo/src/index.ts", "export const x = 1;\n")
        ctx = _ctx(tmp_path, ["packages/foo/src/index.ts"])
        index = build_ts_workspace_index(ctx)
        assert "packages/foo/src/index.ts" in index.exports_entry_paths

    def test_wildcard_export_expands_to_all_matching_files(self, tmp_path: Path) -> None:
        _write(tmp_path, "package.json", '{"workspaces":["packages/*"]}')
        _write(
            tmp_path,
            "packages/zod/package.json",
            '{"name":"@org/zod","exports":{"./locales/*":"./src/locales/*.ts"}}',
        )
        for locale in ("ru", "be", "hy"):
            _write(tmp_path, f"packages/zod/src/locales/{locale}.ts", "export {};\n")
        paths = [f"packages/zod/src/locales/{x}.ts" for x in ("ru", "be", "hy")]
        ctx = _ctx(tmp_path, paths)
        index = build_ts_workspace_index(ctx)
        for p in paths:
            assert p in index.exports_entry_paths, p

    def test_main_field_marked_as_entry(self, tmp_path: Path) -> None:
        _write(tmp_path, "package.json", '{"workspaces":["packages/*"]}')
        _write(
            tmp_path,
            "packages/lib/package.json",
            '{"name":"@org/lib","main":"./src/main.ts"}',
        )
        _write(tmp_path, "packages/lib/src/main.ts", "export {};\n")
        ctx = _ctx(tmp_path, ["packages/lib/src/main.ts"])
        index = build_ts_workspace_index(ctx)
        assert "packages/lib/src/main.ts" in index.exports_entry_paths

    def test_index_is_cached(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, [])
        first = get_or_build_ts_index(ctx)
        second = get_or_build_ts_index(ctx)
        assert first is second

    def test_root_workspace_dot(self, tmp_path: Path) -> None:
        """workspaces: ["."] should not crash on Python 3.14+.

        Python 3.14+ rejects pathlib.Path.glob(".") with ValueError.
        Entry-point resolution for the root package is a separate concern.
        """
        _write(
            tmp_path,
            "package.json",
            '{"name":"root","workspaces":["."],"main":"./src/index.ts"}',
        )
        _write(tmp_path, "src/index.ts", "export const x = 1;\n")
        ctx = _ctx(tmp_path, ["src/index.ts"])
        index = build_ts_workspace_index(ctx)
        assert "root" in index.packages


class TestVitestIncludeScanner:
    def test_runtime_tests_glob_matches(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "vitest.config.ts",
            'export default { test: { include: ["runtime-tests/**/*.test.ts"] } };\n',
        )
        _write(tmp_path, "runtime-tests/foo.test.ts", "")
        _write(tmp_path, "runtime-tests/nested/bar.test.ts", "")
        # Non-matching path — must NOT be marked.
        _write(tmp_path, "src/unrelated.ts", "")
        ctx = _ctx(
            tmp_path,
            ["runtime-tests/foo.test.ts", "runtime-tests/nested/bar.test.ts", "src/unrelated.ts"],
        )
        matched = find_vitest_include_targets(ctx)
        assert "runtime-tests/foo.test.ts" in matched
        assert "runtime-tests/nested/bar.test.ts" in matched
        assert "src/unrelated.ts" not in matched


class TestMdxImportScan:
    def test_mdx_import_resolves_to_tsx(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "docs/page.mdx",
            'import Bronze from "../components/bronze";\n\n# Hello\n',
        )
        _write(tmp_path, "components/bronze.tsx", "export default function() {}\n")
        ctx = _ctx(tmp_path, ["components/bronze.tsx", "docs/page.mdx"])
        targets = find_mdx_import_targets(ctx)
        assert "components/bronze.tsx" in targets

    def test_external_import_skipped(self, tmp_path: Path) -> None:
        _write(tmp_path, "docs/page.mdx", 'import React from "react";\n')
        ctx = _ctx(tmp_path, ["docs/page.mdx"])
        targets = find_mdx_import_targets(ctx)
        # ``react`` resolves to an ``external:`` node and must not enter
        # the entry-point set.
        assert not any(t.startswith("external:") for t in targets)


class TestNpmScriptEntryScanner:
    def test_tsx_runner_path_resolves(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "packages/bench/package.json",
            '{"name":"@org/bench","scripts":{"bench":"tsx --conditions src index.ts"}}',
        )
        _write(tmp_path, "packages/bench/index.ts", "")
        ctx = _ctx(tmp_path, ["packages/bench/index.ts"])
        assert "packages/bench/index.ts" in find_npm_script_entry_targets(ctx)

    def test_relative_path_in_root_package(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "package.json",
            '{"scripts":{"build":"bun ./build/build.ts"}}',
        )
        _write(tmp_path, "build/build.ts", "")
        ctx = _ctx(tmp_path, ["build/build.ts"])
        assert "build/build.ts" in find_npm_script_entry_targets(ctx)

    def test_mts_extension_picked_up(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "benchmarks/routers/package.json",
            '{"scripts":{"bench:node":"tsx ./src/bench.mts"}}',
        )
        _write(tmp_path, "benchmarks/routers/src/bench.mts", "")
        ctx = _ctx(tmp_path, ["benchmarks/routers/src/bench.mts"])
        assert "benchmarks/routers/src/bench.mts" in find_npm_script_entry_targets(ctx)

    def test_quoted_glob_expands(self, tmp_path: Path) -> None:
        # Prettier-style glob argument in a root script — the files
        # matched are maintained code even if no static import reaches
        # them.
        _write(
            tmp_path,
            "package.json",
            '{"scripts":{"format":"prettier --check \\"perf-measures/**/*.ts\\""}}',
        )
        _write(tmp_path, "perf-measures/foo.ts", "")
        _write(tmp_path, "perf-measures/nested/bar.ts", "")
        _write(tmp_path, "src/app.ts", "")
        ctx = _ctx(
            tmp_path,
            ["perf-measures/foo.ts", "perf-measures/nested/bar.ts", "src/app.ts"],
        )
        targets = find_npm_script_entry_targets(ctx)
        assert "perf-measures/foo.ts" in targets
        assert "perf-measures/nested/bar.ts" in targets
        assert "src/app.ts" not in targets

    def test_bare_directory_token_expands(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "package.json",
            '{"scripts":{"lint":"eslint src benchmarks"}}',
        )
        _write(tmp_path, "benchmarks/deno/hono.ts", "")
        _write(tmp_path, "src/index.ts", "")
        ctx = _ctx(tmp_path, ["benchmarks/deno/hono.ts", "src/index.ts"])
        targets = find_npm_script_entry_targets(ctx)
        assert "benchmarks/deno/hono.ts" in targets
        assert "src/index.ts" in targets

    def test_experimental_subpackage_marks_all_sources(self, tmp_path: Path) -> None:
        # zod-style ``packages/bench/*`` — runtime-resolved via
        # ``import.meta.resolve`` so no static path appears. The
        # convention-name match rescues the whole sub-package.
        _write(
            tmp_path,
            "packages/bench/package.json",
            '{"name":"@org/benchmarks","private":true,"scripts":{"bench":"tsx index.ts"}}',
        )
        for name in ("index.ts", "array.ts", "boolean.ts"):
            _write(tmp_path, f"packages/bench/{name}", "")
        ctx = _ctx(
            tmp_path,
            ["packages/bench/index.ts", "packages/bench/array.ts", "packages/bench/boolean.ts"],
        )
        targets = find_npm_script_entry_targets(ctx)
        assert "packages/bench/array.ts" in targets
        assert "packages/bench/boolean.ts" in targets

    def test_nested_experimental_dir_inside_package(self, tmp_path: Path) -> None:
        # ``packages/tsc/bench/*.ts`` — package.json at packages/tsc,
        # but ``bench/`` is the experimental segment one level deeper.
        _write(
            tmp_path,
            "packages/tsc/package.json",
            '{"name":"@org/tsc-perf","scripts":{"build":"tsc"}}',
        )
        _write(tmp_path, "packages/tsc/bench/lots-of-objects.ts", "")
        _write(tmp_path, "packages/tsc/src/index.ts", "")
        ctx = _ctx(
            tmp_path,
            ["packages/tsc/bench/lots-of-objects.ts", "packages/tsc/src/index.ts"],
        )
        targets = find_npm_script_entry_targets(ctx)
        assert "packages/tsc/bench/lots-of-objects.ts" in targets
        # The ordinary ``src/index.ts`` is NOT auto-marked — that's
        # handled by the regular exports/main path.
        assert "packages/tsc/src/index.ts" not in targets

    def test_flags_do_not_resolve(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "package.json",
            '{"scripts":{"x":"tsc --noEmit"}}',
        )
        ctx = _ctx(tmp_path, [])
        # No source files referenced — empty set, no crashes from ``-`` tokens.
        assert find_npm_script_entry_targets(ctx) == set()
