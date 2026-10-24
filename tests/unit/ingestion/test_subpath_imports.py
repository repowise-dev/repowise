"""Unit tests for Node.js ``package.json`` ``"imports"`` (``#`` subpath) resolution."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.subpath_imports import resolve_subpath_import


def _ctx(repo: Path, paths: list[str]) -> ResolverContext:
    return ResolverContext(
        path_set=set(paths),
        stem_map={},
        graph=nx.DiGraph(),
        repo_path=repo,
    )


def test_subpath_import_resolves_dot_directory_and_src_targets(tmp_path: Path) -> None:
    pkg_dir = tmp_path / "packages" / "gen"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text(
        json.dumps(
            {
                "name": "@acme/gen",
                "imports": {
                    "#gen/*": "./.generated/*.js",
                    "#utils/*": "./src/utils/*.ts",
                },
            }
        ),
        encoding="utf-8",
    )
    ctx = _ctx(
        tmp_path,
        [
            "packages/gen/.generated/util.js",
            "packages/gen/src/utils/math.ts",
            "packages/gen/src/a.ts",
        ],
    )
    assert (
        resolve_subpath_import("#gen/util", "packages/gen/src/a.ts", ctx)
        == "packages/gen/.generated/util.js"
    )
    assert (
        resolve_subpath_import("#utils/math", "packages/gen/src/a.ts", ctx)
        == "packages/gen/src/utils/math.ts"
    )
