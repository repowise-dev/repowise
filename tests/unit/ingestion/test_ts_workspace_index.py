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
