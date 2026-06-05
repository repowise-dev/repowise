"""Unit tests for the single pruned filesystem scan behind the ts_workspace
finders (perf: 12+ unpruned rglob walks → one os.walk that skips
node_modules / dot-dirs).

Destination: tests/unit/ingestion/test_ts_workspace_scan.py
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.resolvers.context import ResolverContext  # adjust import at apply time
from repowise.core.ingestion.resolvers.ts_workspace import (
    find_mdx_import_targets,
    find_vitest_include_targets,
)


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _ctx(root: Path, path_set: set[str]) -> ResolverContext:
    return ResolverContext(
        path_set=path_set,
        stem_map={},
        graph=None,
        repo_path=root,
    )


class TestPrunedScan:
    def test_mdx_in_node_modules_ignored(self, tmp_path: Path) -> None:
        _write(tmp_path, "docs/page.mdx", "import { B } from '../src/b'\n")
        _write(tmp_path, "node_modules/pkg/readme.mdx", "import { X } from '../../src/c'\n")
        _write(tmp_path, "src/b.ts", "export const B = 1\n")
        _write(tmp_path, "src/c.ts", "export const X = 1\n")
        ctx = _ctx(tmp_path, {"src/b.ts", "src/c.ts", "docs/page.mdx"})
        targets = find_mdx_import_targets(ctx)
        assert "src/b.ts" in targets, targets
        assert "src/c.ts" not in targets, targets

    def test_mdx_in_hidden_dir_ignored(self, tmp_path: Path) -> None:
        _write(tmp_path, ".cache/stale.mdx", "import { B } from '../src/b'\n")
        _write(tmp_path, "src/b.ts", "export const B = 1\n")
        ctx = _ctx(tmp_path, {"src/b.ts"})
        targets = find_mdx_import_targets(ctx)
        assert targets == set(), targets

    def test_vitest_config_in_node_modules_ignored(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "vitest.config.ts",
            "export default { test: { include: ['runtime-tests/**/*.ts'] } }\n",
        )
        _write(
            tmp_path,
            "node_modules/dep/vitest.config.ts",
            "export default { test: { include: ['poison/**/*.ts'] } }\n",
        )
        path_set = {"runtime-tests/a.ts", "poison/b.ts"}
        ctx = _ctx(tmp_path, path_set)
        targets = find_vitest_include_targets(ctx)
        assert "runtime-tests/a.ts" in targets, targets
        # the node_modules config's include glob resolves relative to its own
        # dir, so even pre-refactor it could not match; the point here is the
        # walk no longer descends at all.
        assert "poison/b.ts" not in targets, targets

    def test_root_configs_still_found(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "packages/app/vitest.config.mts",
            "export default { test: { include: ['src/**/*.spec.ts'] } }\n",
        )
        path_set = {"packages/app/src/x.spec.ts", "packages/app/src/x.ts"}
        ctx = _ctx(tmp_path, path_set)
        targets = find_vitest_include_targets(ctx)
        assert "packages/app/src/x.spec.ts" in targets, targets
        assert "packages/app/src/x.ts" not in targets, targets
