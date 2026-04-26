"""Unit tests for TS resolver SFC extension probing and workspace packages."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.ts_workspace import (
    build_workspace_map,
    resolve_via_workspaces,
)
from repowise.core.ingestion.resolvers.typescript import resolve_ts_js_import


def _ctx(repo: Path, paths: list[str], has_sfc: bool = False) -> ResolverContext:
    path_set = set(paths)
    return ResolverContext(
        path_set=path_set,
        stem_map={},
        graph=nx.DiGraph(),
        repo_path=repo,
        has_sfc_files=has_sfc
        or any(p.endswith((".vue", ".svelte", ".astro")) for p in path_set),
    )


class TestSfcExtensions:
    def test_vue_extension_resolved_when_sfc_present(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["src/App.vue", "src/main.ts"])
        result = resolve_ts_js_import("./App", "src/main.ts", ctx)
        assert result == "src/App.vue"

    def test_svelte_extension(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["src/Widget.svelte", "src/main.ts"])
        result = resolve_ts_js_import("./Widget", "src/main.ts", ctx)
        assert result == "src/Widget.svelte"

    def test_pure_ts_repo_skips_sfc_probe(self, tmp_path: Path) -> None:
        # No SFC file present → has_sfc_files=False → resolver should NOT
        # match a hypothetical .vue path. We don't index App.vue here; the
        # resolver should return None rather than fishing for SFC files.
        ctx = _ctx(tmp_path, ["src/foo.ts"])
        assert ctx.has_sfc_files is False
        result = resolve_ts_js_import("./missing", "src/foo.ts", ctx)
        assert result is None


class TestWorkspaceMap:
    def test_workspaces_array_form(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}))
        pkg_a = tmp_path / "packages" / "a"
        pkg_a.mkdir(parents=True)
        (pkg_a / "package.json").write_text(json.dumps({"name": "@org/a"}))
        mapping = build_workspace_map(tmp_path)
        assert mapping == {"@org/a": "packages/a"}

    def test_workspaces_object_form(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"workspaces": {"packages": ["libs/*"]}})
        )
        pkg = tmp_path / "libs" / "core"
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text(json.dumps({"name": "@org/core"}))
        mapping = build_workspace_map(tmp_path)
        assert mapping == {"@org/core": "libs/core"}

    def test_empty_when_no_root_package(self, tmp_path: Path) -> None:
        assert build_workspace_map(tmp_path) == {}


class TestWorkspaceResolution:
    def test_resolves_workspace_subpath(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}))
        pkg = tmp_path / "packages" / "core"
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text(json.dumps({"name": "@org/core"}))
        ctx = _ctx(tmp_path, ["packages/core/src/index.ts"])
        result = resolve_via_workspaces("@org/core/src/index", ctx)
        assert result == "packages/core/src/index.ts"

    def test_resolves_workspace_index(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}))
        pkg = tmp_path / "packages" / "core"
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text(json.dumps({"name": "@org/core"}))
        ctx = _ctx(tmp_path, ["packages/core/index.ts"])
        result = resolve_via_workspaces("@org/core", ctx)
        assert result == "packages/core/index.ts"

    def test_external_when_no_match(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["packages/core/index.ts"])
        # No package.json → no workspaces → returns None (resolver itself
        # would then return external:).
        assert resolve_via_workspaces("@unknown/pkg", ctx) is None
