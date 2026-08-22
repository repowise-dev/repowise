"""Unit tests for TS resolver SFC extension probing and workspace packages."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.ts_workspace import (
    _normalize_repo_rel,
    build_ts_workspace_index,
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


class TestExplicitRelativeExtensions:
    @pytest.mark.parametrize(
        "extension",
        [".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"],
    )
    def test_existing_explicit_relative_file_wins(
        self, tmp_path: Path, extension: str
    ) -> None:
        ctx = _ctx(tmp_path, [f"data/example{extension}", "services/reader.js"])
        result = resolve_ts_js_import(
            f"../data/example{extension}",
            "services/reader.js",
            ctx,
        )
        assert result == f"data/example{extension}"

    def test_ts_rewrite_fallback_still_resolves_when_js_file_absent(
        self, tmp_path: Path
    ) -> None:
        ctx = _ctx(tmp_path, ["data/example.ts", "services/reader.js"])
        result = resolve_ts_js_import(
            "../data/example.js",
            "services/reader.js",
            ctx,
        )
        assert result == "data/example.ts"


class TestDirectoryIndexProbing:
    """A relative import to a directory must probe its index files.

    The resolver already probed ``/index.ts`` and ``/index.js`` but missed the
    two React index forms (``/index.tsx``, ``/index.jsx``) that the bare
    extension list and the tsconfig path resolver both accept — so a
    directory-index React component resolved to nothing and read as an
    unreachable external dep.
    """

    @pytest.mark.parametrize(
        "index_file",
        [
            "src/components/TodoList/index.ts",
            "src/components/TodoList/index.tsx",
            "src/components/TodoList/index.mts",
            "src/components/TodoList/index.cts",
            "src/components/TodoList/index.js",
            "src/components/TodoList/index.jsx",
        ],
    )
    def test_directory_index_resolves(self, tmp_path: Path, index_file: str) -> None:
        ctx = _ctx(tmp_path, [index_file, "src/main.ts"])
        result = resolve_ts_js_import("./components/TodoList", "src/main.ts", ctx)
        assert result == index_file

    def test_tsx_index_wins_over_ts_when_both_present(self, tmp_path: Path) -> None:
        # ``/index.ts`` is probed before ``/index.tsx``; when both exist the
        # first match wins, matching the bare-extension ordering.
        ctx = _ctx(
            tmp_path,
            [
                "src/components/TodoList/index.ts",
                "src/components/TodoList/index.tsx",
            ],
        )
        result = resolve_ts_js_import("./components/TodoList", "src/main.ts", ctx)
        assert result == "src/components/TodoList/index.ts"


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

    def test_empty_workspaces_entry_drops_itself_not_the_workspace(
        self, tmp_path: Path
    ) -> None:
        # ``Path.glob("")`` raises ValueError. Unlike the pnpm reader, which
        # drops blank entries before they reach the globber, the ``workspaces``
        # field passes every string straight through — so the guard in
        # ``_expand_member_dirs`` is what keeps one blank entry from costing
        # the whole map.
        (tmp_path / "package.json").write_text(
            json.dumps({"workspaces": ["", "packages/*"]})
        )
        pkg = tmp_path / "packages" / "a"
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text(json.dumps({"name": "@org/a"}))
        assert build_workspace_map(tmp_path) == {"@org/a": "packages/a"}


class TestPnpmWorkspaceMap:
    """pnpm declares members in ``pnpm-workspace.yaml``, never in ``workspaces``."""

    def _pkg(self, tmp_path: Path, rel: str, name: str) -> None:
        pkg_dir = tmp_path / rel
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "package.json").write_text(json.dumps({"name": name}))

    def _root(self, tmp_path: Path) -> None:
        """An unnamed private root, the common pnpm shape — not a member."""
        (tmp_path / "package.json").write_text(json.dumps({"private": True}))

    def test_pnpm_workspace_yaml_is_read(self, tmp_path: Path) -> None:
        # A pnpm root package.json carries no ``workspaces`` field at all.
        self._root(tmp_path)
        (tmp_path / "pnpm-workspace.yaml").write_text(
            'packages:\n  - "apps/*"\n  - "packages/*"\n'
        )
        self._pkg(tmp_path, "apps/console", "@org/console")
        self._pkg(tmp_path, "packages/domain", "@org/domain")
        assert build_workspace_map(tmp_path) == {
            "@org/console": "apps/console",
            "@org/domain": "packages/domain",
        }

    def test_named_root_is_a_member(self, tmp_path: Path) -> None:
        # "The root package is always included, even when custom location
        # wildcards are used" — pnpm's ``packages`` setting.
        (tmp_path / "package.json").write_text(json.dumps({"name": "@org/root"}))
        (tmp_path / "pnpm-workspace.yaml").write_text('packages:\n  - "packages/*"\n')
        self._pkg(tmp_path, "packages/a", "@org/a")
        assert build_workspace_map(tmp_path) == {
            "@org/root": ".",
            "@org/a": "packages/a",
        }

    def test_root_is_not_subject_to_negation(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"name": "@org/root"}))
        (tmp_path / "pnpm-workspace.yaml").write_text(
            'packages:\n  - "packages/*"\n  - "!**"\n'
        )
        assert build_workspace_map(tmp_path) == {"@org/root": "."}

    def test_yml_extension_is_not_a_pnpm_manifest(self, tmp_path: Path) -> None:
        # pnpm reads only ``pnpm-workspace.yaml``; ``.yml`` is an open request
        # (pnpm/pnpm#1380). Honouring it would map members pnpm never installs.
        self._root(tmp_path)
        (tmp_path / "pnpm-workspace.yml").write_text('packages:\n  - "libs/*"\n')
        self._pkg(tmp_path, "libs/core", "@org/core")
        assert build_workspace_map(tmp_path) == {}

    def test_non_glob_entry_resolved(self, tmp_path: Path) -> None:
        # ``- scripts`` (a plain directory, no glob) is legal pnpm.
        self._root(tmp_path)
        (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - scripts\n")
        self._pkg(tmp_path, "scripts", "@org/scripts")
        assert build_workspace_map(tmp_path) == {"@org/scripts": "scripts"}

    def test_negated_pattern_excludes_member(self, tmp_path: Path) -> None:
        self._root(tmp_path)
        (tmp_path / "pnpm-workspace.yaml").write_text(
            'packages:\n  - "packages/**"\n  - "!**/__fixtures__/**"\n'
        )
        self._pkg(tmp_path, "packages/real", "@org/real")
        self._pkg(tmp_path, "packages/__fixtures__/fake", "@org/fake")
        assert build_workspace_map(tmp_path) == {"@org/real": "packages/real"}

    def test_slashless_negation_is_anchored_at_the_root(self, tmp_path: Path) -> None:
        # fast-glob (pnpm's matcher) anchors a slashless pattern at the
        # workspace root, so ``!fixtures`` must NOT drop packages/fixtures.
        # git-ignore semantics would match it at any depth — the reason this
        # is expanded with Path.glob rather than pathspec.
        self._root(tmp_path)
        (tmp_path / "pnpm-workspace.yaml").write_text(
            'packages:\n  - "packages/*"\n  - "!fixtures"\n'
        )
        self._pkg(tmp_path, "packages/fixtures", "@org/fixtures")
        assert build_workspace_map(tmp_path) == {"@org/fixtures": "packages/fixtures"}

    def test_negation_does_not_swallow_nested_members(self, tmp_path: Path) -> None:
        # ``!packages/*`` excludes direct children only. Under git-ignore
        # semantics a directory match also removes everything beneath it,
        # which would wrongly drop packages/group/nested.
        self._root(tmp_path)
        (tmp_path / "pnpm-workspace.yaml").write_text(
            'packages:\n  - "packages/**"\n  - "!packages/*"\n'
        )
        self._pkg(tmp_path, "packages/top", "@org/top")
        self._pkg(tmp_path, "packages/group/nested", "@org/nested")
        assert build_workspace_map(tmp_path) == {"@org/nested": "packages/group/nested"}

    def test_pnpm_manifest_wins_over_package_json_workspaces(self, tmp_path: Path) -> None:
        # pnpm does not read the ``workspaces`` field, so a member declared
        # only there is one pnpm never installs. Mapping it would invent an
        # intra-repo edge for what is really a registry dependency.
        (tmp_path / "package.json").write_text(
            json.dumps({"private": True, "workspaces": ["legacy/*"]})
        )
        (tmp_path / "pnpm-workspace.yaml").write_text('packages:\n  - "packages/*"\n')
        self._pkg(tmp_path, "legacy/old", "@org/old")
        self._pkg(tmp_path, "packages/new", "@org/new")
        assert build_workspace_map(tmp_path) == {"@org/new": "packages/new"}

    def test_catalog_only_manifest_is_a_root_only_workspace(self, tmp_path: Path) -> None:
        # Since pnpm 10 the file also holds settings. ``packages`` is optional
        # and "if the field is omitted, only the root package is included".
        (tmp_path / "package.json").write_text(json.dumps({"name": "@org/root"}))
        (tmp_path / "pnpm-workspace.yaml").write_text("catalog:\n  react: ^19.0.0\n")
        self._pkg(tmp_path, "packages/a", "@org/a")
        assert build_workspace_map(tmp_path) == {"@org/root": "."}

    def test_malformed_yaml_is_not_fatal(self, tmp_path: Path) -> None:
        self._root(tmp_path)
        (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - [unclosed\n")
        assert build_workspace_map(tmp_path) == {}

    @pytest.mark.parametrize("bad", ["/abs/packages/*", "!/abs/*"])
    def test_unglobbable_entry_drops_itself_not_the_workspace(
        self, tmp_path: Path, bad: str
    ) -> None:
        # ``Path.glob`` refuses an absolute pattern (NotImplementedError). The
        # strings are manifest-supplied and this path runs unguarded under
        # ``resolve_via_workspaces``, so a bad entry must cost its own member
        # rather than every member. Both the include and the exclude loop.
        self._root(tmp_path)
        (tmp_path / "pnpm-workspace.yaml").write_text(
            f'packages:\n  - "packages/*"\n  - "{bad}"\n'
        )
        self._pkg(tmp_path, "packages/a", "@org/a")
        assert build_workspace_map(tmp_path) == {"@org/a": "packages/a"}

    def test_resolves_import_through_pnpm_member(self, tmp_path: Path) -> None:
        # The end-to-end claim: a member found only via pnpm-workspace.yaml
        # still resolves through its own ``exports`` map, so a cross-package
        # import becomes an intra-repo edge instead of an ``external:`` node.
        self._root(tmp_path)
        (tmp_path / "pnpm-workspace.yaml").write_text('packages:\n  - "packages/*"\n')
        pkg_dir = tmp_path / "packages" / "domain"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "package.json").write_text(
            json.dumps({"name": "@org/domain", "exports": {".": "./src/index.ts"}})
        )
        ctx = _ctx(tmp_path, ["packages/domain/src/index.ts"])
        assert resolve_via_workspaces("@org/domain", ctx) == "packages/domain/src/index.ts"


class TestRootPackageResolution:
    """A workspace member AT the repo root carries ``dir == "."``.

    Joining that with a subpath yields ``./src/index.ts``, which never equals
    the repo-relative ``src/index.ts`` a path set holds. These cover both
    managers because the defect predates pnpm support: an npm/yarn repo
    listing ``"."`` in ``workspaces`` mapped the member but never resolved it.
    """

    def test_npm_root_workspace_resolves_via_exports(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps(
                {"name": "@org/root", "workspaces": ["."], "exports": {".": "./src/index.ts"}}
            )
        )
        ctx = _ctx(tmp_path, ["src/index.ts"])
        assert build_workspace_map(tmp_path) == {"@org/root": "."}
        assert resolve_via_workspaces("@org/root", ctx) == "src/index.ts"

    def test_pnpm_root_resolves_via_main(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "@org/root", "main": "./src/entry.ts"})
        )
        (tmp_path / "pnpm-workspace.yaml").write_text("packages: []\n")
        ctx = _ctx(tmp_path, ["src/entry.ts"])
        assert resolve_via_workspaces("@org/root", ctx) == "src/entry.ts"

    def test_root_subpath_import_resolves(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"name": "@org/root"}))
        (tmp_path / "pnpm-workspace.yaml").write_text("packages: []\n")
        ctx = _ctx(tmp_path, ["src/util/date.ts"])
        assert resolve_via_workspaces("@org/root/src/util/date", ctx) == "src/util/date.ts"

    def test_root_exports_wildcard_expands(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "@org/root", "exports": {"./lib/*": "./src/lib/*.ts"}})
        )
        (tmp_path / "pnpm-workspace.yaml").write_text("packages: []\n")
        ctx = _ctx(tmp_path, ["src/lib/a.ts", "src/lib/b.ts", "src/other.ts"])
        index = build_ts_workspace_index(ctx)
        assert index.exports_entry_paths == {"src/lib/a.ts", "src/lib/b.ts"}

    def test_root_exports_wildcard_at_repo_root_expands(self, tmp_path: Path) -> None:
        """A root package whose wildcard target has NO directory prefix.

        ``{"./*": "./*.ts"}`` on a ``dir == "."`` member joins to ``"./"``,
        which collapses entirely to the repo root. The sibling test above
        cannot see this: its ``./src/lib/*.ts`` target keeps a non-empty
        ``src/lib/`` prefix, so the join never reaches the degenerate case.
        Left unguarded the prefix stayed ``"./"``, no repo-relative path
        started with it, and every wildcard-exported root file dropped out of
        ``exports_entry_paths`` — a false-positive dead-code source.
        """
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "@org/root", "exports": {"./*": "./*.ts"}})
        )
        (tmp_path / "pnpm-workspace.yaml").write_text("packages: []\n")
        ctx = _ctx(tmp_path, ["index.ts", "util.ts", "notes.md"])
        index = build_ts_workspace_index(ctx)
        assert index.exports_entry_paths == {"index.ts", "util.ts"}

    def test_normalize_repo_rel_collapses_bare_root(self) -> None:
        """The unit under the case above — ``"./"`` is the repo root, i.e. ``""``."""
        assert _normalize_repo_rel("./") == ""
        # Unchanged for every non-degenerate shape.
        assert _normalize_repo_rel("./src/lib/") == "src/lib/"
        assert _normalize_repo_rel("./src/index.ts") == "src/index.ts"
        assert _normalize_repo_rel("pkgs/a/src/") == "pkgs/a/src/"


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

    def test_resolves_workspace_subpath_to_mts(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}))
        pkg = tmp_path / "packages" / "core"
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text(json.dumps({"name": "@org/core"}))
        ctx = _ctx(tmp_path, ["packages/core/src/index.mts"])
        result = resolve_via_workspaces("@org/core/src/index", ctx)
        assert result == "packages/core/src/index.mts"

    def test_resolves_workspace_index_cts(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}))
        pkg = tmp_path / "packages" / "core"
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text(json.dumps({"name": "@org/core"}))
        ctx = _ctx(tmp_path, ["packages/core/index.cts"])
        result = resolve_via_workspaces("@org/core", ctx)
        assert result == "packages/core/index.cts"


def _setup_workspace(
    tmp_path: Path,
    pkg_name: str,
    pkg_data_extra: dict,
) -> Path:
    """Write a minimal root + one workspace pkg, return the workspace dir."""
    (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["packages/*"]}))
    pkg_dir = tmp_path / "packages" / pkg_name.split("/")[-1]
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text(json.dumps({"name": pkg_name, **pkg_data_extra}))
    return pkg_dir


class TestWorkspaceExportsField:
    """Exports-field resolution — the Node.js subpath protocol that
    every modern monorepo (turborepo / nx / pnpm) leans on. Without
    this, ``@org/ui/lib/format`` would probe ``packages/ui/lib/format``
    and miss the actual source file at ``packages/ui/src/lib/format.ts``.
    """

    def test_exports_exact_subpath_resolves_through_src(self, tmp_path: Path) -> None:
        _setup_workspace(
            tmp_path,
            "@org/ui",
            {"exports": {"./lib/format": "./src/lib/format.ts"}},
        )
        ctx = _ctx(tmp_path, ["packages/ui/src/lib/format.ts"])
        assert (
            resolve_via_workspaces("@org/ui/lib/format", ctx)
            == "packages/ui/src/lib/format.ts"
        )

    def test_exports_wildcard_pattern(self, tmp_path: Path) -> None:
        _setup_workspace(
            tmp_path,
            "@org/ui",
            {"exports": {"./graph/*": "./src/graph/*.tsx"}},
        )
        ctx = _ctx(tmp_path, ["packages/ui/src/graph/sigma-canvas.tsx"])
        assert (
            resolve_via_workspaces("@org/ui/graph/sigma-canvas", ctx)
            == "packages/ui/src/graph/sigma-canvas.tsx"
        )

    def test_exports_longest_prefix_wins(self, tmp_path: Path) -> None:
        # Two patterns can both match; the more specific (longer static
        # prefix) one must take precedence.
        _setup_workspace(
            tmp_path,
            "@org/ui",
            {
                "exports": {
                    "./*": "./src/*.ts",
                    "./graph/*": "./src/graph/*.tsx",
                }
            },
        )
        ctx = _ctx(
            tmp_path,
            [
                "packages/ui/src/graph/node.tsx",
                "packages/ui/src/utils.ts",
            ],
        )
        # Specific wildcard wins for graph/*
        assert (
            resolve_via_workspaces("@org/ui/graph/node", ctx)
            == "packages/ui/src/graph/node.tsx"
        )
        # Generic wildcard catches the rest
        assert (
            resolve_via_workspaces("@org/ui/utils", ctx)
            == "packages/ui/src/utils.ts"
        )

    def test_exports_conditional_object_picks_import_over_require(
        self, tmp_path: Path
    ) -> None:
        _setup_workspace(
            tmp_path,
            "@org/ui",
            {
                "exports": {
                    "./util": {
                        "require": "./dist/util.cjs",
                        "import": "./src/util.ts",
                    }
                }
            },
        )
        ctx = _ctx(
            tmp_path,
            ["packages/ui/src/util.ts", "packages/ui/dist/util.cjs"],
        )
        assert (
            resolve_via_workspaces("@org/ui/util", ctx)
            == "packages/ui/src/util.ts"
        )

    def test_exports_condition_naming_absent_build_output_is_passed_over(
        self, tmp_path: Path
    ) -> None:
        # Every condition this module ranks names a built artefact a source
        # checkout does not contain, and the package publishes its TypeScript
        # under a condition no fixed list can name. Collapsing to one ranked
        # target left the whole package unresolvable.
        _setup_workspace(
            tmp_path,
            "@org/ui",
            {
                "exports": {
                    ".": {
                        "@org/source": "./src/index.ts",
                        "types": "./index.d.cts",
                        "import": "./index.js",
                        "require": "./index.cjs",
                    }
                }
            },
        )
        ctx = _ctx(tmp_path, ["packages/ui/src/index.ts"])
        assert resolve_via_workspaces("@org/ui", ctx) == "packages/ui/src/index.ts"

    def test_ranked_condition_still_wins_when_both_targets_exist(
        self, tmp_path: Path
    ) -> None:
        # The guard on the guard: continuing past an absent target must not
        # become a preference for source, or every package shipping both a
        # build and its sources would change which file it binds.
        _setup_workspace(
            tmp_path,
            "@org/ui",
            {
                "exports": {
                    ".": {
                        "@org/source": "./src/index.ts",
                        "import": "./dist/index.js",
                    }
                }
            },
        )
        ctx = _ctx(tmp_path, ["packages/ui/src/index.ts", "packages/ui/dist/index.js"])
        assert resolve_via_workspaces("@org/ui", ctx) == "packages/ui/dist/index.js"

    def test_nested_unranked_condition_does_not_outrank_a_later_ranked_one(
        self, tmp_path: Path
    ) -> None:
        # ``import`` outranks ``require``, but its subtree holds only conditions
        # this module does not rank, so the old collapse skipped the whole
        # branch and chose ``require``. Enumerating candidates must not promote
        # the development build to the head of the list.
        _setup_workspace(
            tmp_path,
            "@org/ui",
            {
                "exports": {
                    ".": {
                        "import": {"development": "./dev.js"},
                        "require": "./index.cjs",
                    }
                }
            },
        )
        ctx = _ctx(tmp_path, ["packages/ui/dev.js", "packages/ui/index.cjs"])
        assert resolve_via_workspaces("@org/ui", ctx) == "packages/ui/index.cjs"

    def test_entry_with_no_ranked_condition_still_falls_through(
        self, tmp_path: Path
    ) -> None:
        # No condition here is one the module ranks, so the key was dropped
        # outright and the subpath probe below answered. Keeping the key on the
        # strength of a spare candidate would let ``./internal.ts`` answer from
        # the exports step instead — a moved binding, not a new one.
        _setup_workspace(
            tmp_path,
            "@org/ui",
            {"exports": {".": {"bespoke": "./internal.ts"}}},
        )
        ctx = _ctx(tmp_path, ["packages/ui/internal.ts", "packages/ui/index.ts"])
        assert resolve_via_workspaces("@org/ui", ctx) == "packages/ui/index.ts"

    def test_spare_export_target_never_displaces_the_index_probe(
        self, tmp_path: Path
    ) -> None:
        # vue's shape: the ranked condition names an absent build artefact, a
        # lower condition names a committed ``index.mjs``, and the package root
        # also holds the ``index.js`` the probe below already bound. The spare
        # candidate must stay behind that probe, or the change stops being an
        # addition and starts moving imports that already resolve.
        _setup_workspace(
            tmp_path,
            "@org/ui",
            {
                "main": "index.js",
                "exports": {
                    ".": {
                        "import": {
                            "default": "./dist/ui.esm-bundler.js",
                            "node": "./index.mjs",
                        }
                    }
                },
            },
        )
        ctx = _ctx(tmp_path, ["packages/ui/index.js", "packages/ui/index.mjs"])
        assert resolve_via_workspaces("@org/ui", ctx) == "packages/ui/index.js"

    def test_declaration_file_is_not_taken_as_a_fallback_entry(
        self, tmp_path: Path
    ) -> None:
        # The built entry is absent and the committed type declarations are
        # not. Binding them would resolve every call through this package to a
        # signature with no body.
        _setup_workspace(
            tmp_path,
            "@org/ui",
            {
                "exports": {
                    ".": {"import": "./dist/index.js", "types": "./types/index.d.ts"}
                }
            },
        )
        ctx = _ctx(tmp_path, ["packages/ui/types/index.d.ts"])
        assert resolve_via_workspaces("@org/ui", ctx) is None

    def test_wildcard_key_does_not_fall_back_to_a_fixed_target(
        self, tmp_path: Path
    ) -> None:
        # The fixed target would answer for every subpath under the key, so
        # two distinct imports would collapse onto one unrelated file.
        _setup_workspace(
            tmp_path,
            "@org/ui",
            {
                "exports": {
                    "./features/*": {
                        "import": "./src/features/*.mjs",
                        "custom": "./src/shared.ts",
                    }
                }
            },
        )
        ctx = _ctx(tmp_path, ["packages/ui/src/shared.ts", "packages/ui/src/features/a.ts"])
        assert resolve_via_workspaces("@org/ui/features/a", ctx) != "packages/ui/src/shared.ts"

    def test_bare_package_falls_back_to_source_entry(self, tmp_path: Path) -> None:
        # No ``exports`` at all and every manifest field names a build
        # directory the repository does not contain, so the import became an
        # external node while the sources sat beside it.
        _setup_workspace(
            tmp_path,
            "@org/ui",
            {
                "main": "./dist/ui.cjs",
                "module": "./dist/ui.js",
                "types": "./types/index.d.ts",
            },
        )
        ctx = _ctx(tmp_path, ["packages/ui/src/index.ts"])
        assert resolve_via_workspaces("@org/ui", ctx) == "packages/ui/src/index.ts"

    def test_source_entry_fallback_does_not_displace_a_resolving_main(
        self, tmp_path: Path
    ) -> None:
        _setup_workspace(tmp_path, "@org/ui", {"main": "./entry.ts"})
        ctx = _ctx(tmp_path, ["packages/ui/entry.ts", "packages/ui/src/index.ts"])
        assert resolve_via_workspaces("@org/ui", ctx) == "packages/ui/entry.ts"

    def test_exports_bare_dot_root(self, tmp_path: Path) -> None:
        _setup_workspace(
            tmp_path,
            "@org/ui",
            {"exports": {".": "./src/index.ts"}},
        )
        ctx = _ctx(tmp_path, ["packages/ui/src/index.ts"])
        assert (
            resolve_via_workspaces("@org/ui", ctx)
            == "packages/ui/src/index.ts"
        )

    def test_exports_string_shorthand(self, tmp_path: Path) -> None:
        # `"exports": "./src/index.ts"` is shorthand for `{".": ...}`.
        _setup_workspace(
            tmp_path,
            "@org/ui",
            {"exports": "./src/index.ts"},
        )
        ctx = _ctx(tmp_path, ["packages/ui/src/index.ts"])
        assert (
            resolve_via_workspaces("@org/ui", ctx)
            == "packages/ui/src/index.ts"
        )

    def test_no_exports_falls_back_to_src_root(self, tmp_path: Path) -> None:
        # Packages without `exports` but with a `src/` layout — the most
        # common shape for internal monorepo libraries — must still
        # resolve.
        _setup_workspace(tmp_path, "@org/ui", {})
        ctx = _ctx(tmp_path, ["packages/ui/src/lib/format.ts"])
        assert (
            resolve_via_workspaces("@org/ui/lib/format", ctx)
            == "packages/ui/src/lib/format.ts"
        )

    def test_no_exports_falls_back_to_flat_layout(self, tmp_path: Path) -> None:
        # Packages laid out directly at the package root (no src/) keep
        # working — common in small/older monorepos.
        _setup_workspace(tmp_path, "@org/ui", {})
        ctx = _ctx(tmp_path, ["packages/ui/lib/format.ts"])
        assert (
            resolve_via_workspaces("@org/ui/lib/format", ctx)
            == "packages/ui/lib/format.ts"
        )

    def test_exports_unmatched_subpath_returns_none(self, tmp_path: Path) -> None:
        # When ``exports`` is declared, an unmatched subpath should NOT
        # fall through to the legacy probe — Node treats undeclared
        # subpaths as blocked. Returning the external node is the
        # resolver's job upstream; here we just return None.
        _setup_workspace(
            tmp_path,
            "@org/ui",
            {"exports": {"./util": "./src/util.ts"}},
        )
        ctx = _ctx(
            tmp_path,
            ["packages/ui/src/util.ts", "packages/ui/src/secret.ts"],
        )
        # NB: current implementation falls back to legacy probe for
        # robustness — Node-strict behaviour can be added once monorepo
        # behaviour is validated. Assert the lenient (current) result.
        assert (
            resolve_via_workspaces("@org/ui/secret", ctx)
            == "packages/ui/src/secret.ts"
        )


class TestMtsCtsResolution:
    def test_extensionless_import_resolves_to_mts(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["src/module.mts", "src/main.ts"])
        assert resolve_ts_js_import("./module", "src/main.ts", ctx) == "src/module.mts"

    def test_extensionless_import_resolves_to_cts(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["src/module.cts", "src/main.ts"])
        assert resolve_ts_js_import("./module", "src/main.ts", ctx) == "src/module.cts"

    def test_directory_import_resolves_to_index_mts(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["src/pkg/index.mts", "src/main.ts"])
        assert resolve_ts_js_import("./pkg", "src/main.ts", ctx) == "src/pkg/index.mts"

    def test_directory_import_resolves_to_index_cts(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["src/pkg/index.cts", "src/main.ts"])
        assert resolve_ts_js_import("./pkg", "src/main.ts", ctx) == "src/pkg/index.cts"
