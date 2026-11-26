"""Config, manifest, data and unindexed file changes: patch only when the graph cannot see them.

Each case edits a small TypeScript, Python and Go tree with the manifests,
data files and lockfiles a real commit touches, patches the graph from the
persisted snapshot, and compares nodes and edges, with every attribute,
against a from-scratch build of the same tree. Edits that change something a
graph step reads must fall back to the full build, with the reason recorded.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from repowise.core.pipeline.incremental import build_repo_graph
from repowise.core.pipeline.incremental.config_inputs import (
    _digest,
    _unread_unindexed,
    presence_read,
)
from repowise.core.pipeline.incremental.resolution_cache import (
    load_config_inputs,
    load_resolutions,
    save_resolutions,
)

_PACKAGE = {
    "name": "web",
    "version": "1.0.0",
    "main": "./src/index.ts",
    "exports": {".": "./src/index.ts"},
    "dependencies": {"left-pad": "^1.0.0"},
}
_PYPROJECT = """[project]
name = "tool"
version = "1.0.0"
dependencies = ["httpx>=0.1"]

[project.scripts]
tool = "tool.cli:main"

[tool.ruff]
line-length = 100
"""
_GO_MOD = "module example.com/app\n\ngo 1.22\n\nrequire github.com/x/y v1.0.0\n"
_TREE = {
    "package.json": json.dumps(_PACKAGE, indent=2) + "\n",
    "tsconfig.json": '{"extends": "./configs/base.json", "compilerOptions": {"baseUrl": "."}}\n',
    "configs/base.json": '{"compilerOptions": {"strict": true}}\n',
    "src/index.ts": "import { fmt } from './fmt';\nimport en from './locales/en.json';\nexport const run = () => fmt(en.hi);\n",
    "src/fmt.ts": "export function fmt(s: string) { return s; }\n",
    "src/locales/en.json": '{"hi": "hello"}\n',
    "pyproject.toml": _PYPROJECT,
    "tool/__init__.py": "",
    "tool/cli.py": "from tool.util import helper\n\ndef main():\n    return helper()\n",
    "tool/util.py": "def helper():\n    return 1\n",
    "go.mod": _GO_MOD,
    "cmd/main.go": 'package main\n\nfunc main() { run() }\n\nfunc run() {}\n',
    ".github/workflows/ci.yml": "on: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
    "requirements.txt": "httpx==0.1\n",
    "yarn.lock": "left-pad@^1.0.0:\n  version 1.0.0\n",
    ".gitignore": "dist/\n",
    "jest.e2e.json": '{"setupFiles": ["./src/index.ts"]}\n',
    # Test data and parser grammar sources no graph step reads.
    "cmd/testdata/eval.test": "eval instant at 1m up\n  up 1\n",
    "cmd/parser/expr.y": "%token NUMBER\n%%\nexpr: NUMBER;\n",
    "src/syntax/query.grammar": "@top Query { Number }\n@tokens { Number { @digit+ } }\n",
    # ...though source, a workflow and a local action name them.
    "cmd/gen.go": (
        'package main\n\nimport _ "embed"\n\n//go:generate goyacc -o parser/expr.go parser/expr.y\n\n'
        "//go:embed testdata/eval.test\nvar evalTest string\n"
    ),
    "src/syntax/load.ts": "import grammar from './query.grammar';\nexport const parser = grammar;\n",
    ".github/actions/setup/action.yml": "runs:\n  using: composite\n  steps:\n    - run: ./scripts/test.sh\n      shell: bash\n",
    "scripts/test.sh": "#!/bin/sh\nyarn jest src/fmt.ts\n",
}


def _write(root: Path, files: dict[str, str | None]) -> None:
    for rel, text in files.items():
        path = root / rel
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _norm(value: Any) -> Any:
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(sorted((_norm(v) for v in value), key=repr))
    if isinstance(value, dict):
        return tuple(sorted((k, _norm(v)) for k, v in value.items()))
    return value


def _graph(graph_builder: Any) -> tuple[dict, dict]:
    g = graph_builder.graph()
    return (
        {n: _norm(d) for n, d in g.nodes(data=True)},
        {(u, v): _norm(d) for u, v, d in g.edges(data=True)},
    )


def _snapshot(graph_builder: Any) -> dict[str, list]:
    g = graph_builder.graph()
    return {
        "nodes": [(n, dict(d)) for n, d in g.nodes(data=True)],
        "edges": [(u, v, dict(d)) for u, v, d in g.edges(data=True)],
    }


@pytest.fixture
def indexed(tmp_path: Path) -> tuple[Path, dict[str, list]]:
    tree = tmp_path / "repo"
    _write(tree, _TREE)
    first = build_repo_graph(tree, [], collect_sources=True, cache_resolutions=True)[2]
    return tree, _snapshot(first)


def _update(
    tree: Path, seed: dict[str, list], edits: dict[str, str | None], *, statuses: bool = True
) -> Any:
    """Apply *edits* and patch, passing the diff the update detector would."""
    presence = {rel for rel, text in edits.items() if text is None or not (tree / rel).exists()}
    _write(tree, edits)
    return build_repo_graph(
        tree,
        [],
        collect_sources=True,
        seed_edges=seed,
        patch_requested=True,
        cache_resolutions=True,
        changed_paths=set(edits),
        presence_changed=presence if statuses else None,
    )[2]


def _assert_equals_full(tree: Path, patched: Any, dest: Path) -> None:
    shutil.copytree(tree, dest, ignore=shutil.ignore_patterns(".repowise"))
    full = build_repo_graph(dest, [], collect_sources=True)[2]
    assert _graph(patched) == _graph(full)


def _package(**changes: Any) -> str:
    return json.dumps({**_PACKAGE, **changes}, indent=2) + "\n"


@pytest.mark.parametrize(
    "edits",
    [
        pytest.param({"package.json": _package(version="1.0.1")}, id="package-version-bump"),
        pytest.param(
            {"package.json": _package(dependencies={"left-pad": "^1.3.0"}), "yarn.lock": "left-pad@^1.3.0:\n"},
            id="dependency-bump-with-lockfile",
        ),
        pytest.param({"pyproject.toml": _PYPROJECT.replace('"1.0.0"', '"1.1.0"')}, id="pyproject-version"),
        pytest.param({"pyproject.toml": _PYPROJECT.replace("100", "120")}, id="pyproject-tool-table"),
        pytest.param({"go.mod": _GO_MOD.replace("v1.0.0", "v1.2.0")}, id="go-require-bump"),
        pytest.param({"src/locales/en.json": '{"hi": "hi there"}\n'}, id="imported-data-json"),
        pytest.param({".github/workflows/ci.yml": "on: [push, pull_request]\n"}, id="ci-workflow"),
        pytest.param({"requirements.txt": "httpx==0.2\n"}, id="requirements-txt"),
        pytest.param({"cmd/testdata/eval.test": "eval instant at 2m up\n  up 2\n"}, id="test-data"),
        pytest.param({"cmd/parser/expr.y": "%token NUMBER STRING\n%%\nexpr: NUMBER | STRING;\n"}, id="yacc-grammar"),
        pytest.param({"src/syntax/query.grammar": "@top Query { Number+ }\n"}, id="lezer-grammar"),
        pytest.param(
            {
                ".github/workflows/ci.yml": (
                    "on: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
                    "      - uses: ./.github/actions/setup\n      - run: ./scripts/test.sh src/fmt.ts\n"
                )
            },
            id="ci-workflow-steps",
        ),
        pytest.param(
            {".github/actions/setup/action.yml": "runs:\n  using: composite\n  steps: []\n"}, id="local-action"
        ),
        pytest.param(
            {
                "package.json": _package(version="2.0.0"),
                "yarn.lock": "left-pad@^1.0.0:\n  version 1.0.1\n",
                "src/fmt.ts": "export function fmt(s: string) { return s.trim(); }\n",
            },
            id="release-with-body-edit",
        ),
    ],
)
def test_edits_no_graph_step_reads_are_patched(
    indexed: tuple[Path, dict[str, list]], tmp_path: Path, edits: dict[str, str]
) -> None:
    tree, seed = indexed

    patched = _update(tree, seed, edits)

    assert patched.patch_plan.eligible, patched.patch_plan.reason
    # Framework and dynamic-hint edges are re-derived, never trusted from the seed.
    assert patched.patch_plan.refresh
    _assert_equals_full(tree, patched, tmp_path / "full")


@pytest.mark.parametrize(
    ("edits", "reason"),
    [
        pytest.param({"package.json": _package(main="./src/fmt.ts")}, "package.json: a field", id="package-main"),
        pytest.param(
            {"package.json": _package(exports={".": "./src/fmt.ts"})}, "package.json: a field", id="package-exports"
        ),
        # A new dependency name can mark a framework that loads files by
        # convention, so the digest moves before the tech stack is compared.
        pytest.param(
            {"package.json": _package(dependencies={"express": "^4.0.0"})},
            "package.json: a field",
            id="framework-dependency",
        ),
        pytest.param(
            {"pyproject.toml": _PYPROJECT.replace("tool.cli:main", "tool.util:helper")},
            "pyproject.toml: a field",
            id="pyproject-scripts",
        ),
        pytest.param({"go.mod": _GO_MOD.replace("example.com/app", "example.com/other")}, "go.mod: a field", id="go-module"),
        pytest.param({"package.json": "{not json\n"}, "package.json: a field", id="package-unparsable"),
        # tsconfig extends it, whatever its name.
        pytest.param({"configs/base.json": '{"compilerOptions": {"baseUrl": "src"}}\n'}, "json edits", id="extended-config"),
        pytest.param({"tsconfig.json": '{"compilerOptions": {"baseUrl": "src"}}\n'}, "json edits", id="tsconfig"),
        # The walk now skips cmd/, so the indexed set moves.
        pytest.param(
            {".gitignore": "dist/\ncmd/\n"},
            ".gitignore: it changed which files the walk indexes",
            id="gitignore-moves-index",
        ),
        pytest.param({"requirements-dev.txt": "pytest\n"}, "added or removed", id="unindexed-added"),
        pytest.param({"requirements.txt": None}, "added or removed", id="unindexed-removed"),
        pytest.param(
            {"cmd/testdata/eval.test": None, "cmd/testdata/eval2.test": "eval instant at 1m up\n"},
            "added or removed",
            id="test-data-renamed",
        ),
        pytest.param(
            {".github/workflows/ci.yml": None, ".github/workflows/test.yml": "on: push\n"},
            "file removed",
            id="workflow-renamed",
        ),
        pytest.param({"web/package.json": '{"name": "w"}\n'}, "web/package.json", id="manifest-added"),
        pytest.param({"go.mod": None}, "go.mod: a field", id="go-mod-removed"),
        # The test-runner handler reads any jest./vitest./vite./playwright. json.
        pytest.param({"jest.e2e.json": '{"setupFiles": ["./src/fmt.ts"]}\n'}, "json edits", id="runner-json"),
    ],
)
def test_edits_a_graph_step_reads_fall_back(
    indexed: tuple[Path, dict[str, list]], tmp_path: Path, edits: dict[str, str | None], reason: str
) -> None:
    tree, seed = indexed

    patched = _update(tree, seed, edits)

    assert not patched.patch_plan.eligible
    assert reason in (patched.patch_plan.reason or "")
    _assert_equals_full(tree, patched, tmp_path / "full")


def test_unindexed_edit_without_diff_statuses_falls_back(
    indexed: tuple[Path, dict[str, list]], tmp_path: Path
) -> None:
    tree, seed = indexed

    patched = _update(tree, seed, {"requirements.txt": "httpx==0.2\n"}, statuses=False)

    assert not patched.patch_plan.eligible
    assert "added or removed" in (patched.patch_plan.reason or "")


def test_manifest_edit_without_a_record_falls_back(tmp_path: Path) -> None:
    tree = tmp_path / "repo"
    _write(tree, _TREE)
    first = build_repo_graph(tree, [], collect_sources=True)[2]
    index_dir = tree / ".repowise"
    # A cache written with no record, as the init path writes it.
    save_resolutions(index_dir, first._parsed_files.values(), first.import_resolutions, first._source_map)
    assert load_resolutions(index_dir) and load_config_inputs(index_dir) is None

    patched = _update(tree, _snapshot(first), {"package.json": _package(version="9.9.9")})

    assert not patched.patch_plan.eligible
    assert "no record" in (patched.patch_plan.reason or "")
    _assert_equals_full(tree, patched, tmp_path / "full")


def test_chained_updates_keep_the_record_current(
    indexed: tuple[Path, dict[str, list]], tmp_path: Path
) -> None:
    """Each update records what it read, so the next compares against it."""
    tree, seed = indexed
    first = _update(tree, seed, {"package.json": _package(main="./src/fmt.ts")})
    assert not first.patch_plan.eligible

    second = _update(tree, _snapshot(first), {"package.json": _package(main="./src/fmt.ts", version="3.0.0")})

    assert second.patch_plan.eligible, second.patch_plan.reason
    _assert_equals_full(tree, second, tmp_path / "full")


@pytest.mark.parametrize(
    ("path", "read"),
    [
        ("package.json", True),
        ("packages/a/tsconfig.build.json", True),
        ("app/src/main/resources/application.yml", True),
        ("force-app/main/default/flexipages/Home.flexipage", True),
        ("build.gradle.kts", True),
        ("pom.xml", True),
        ("docs/mkdocs.yml", True),
        ("native/CMakeLists.txt", True),
        ("app/Package.swift", True),
        ("src/Shop/Shop.CSPROJ", True),
        (".github/workflows/ci.yml", False),
        ("src/locales/en.json", False),
        (".gitattributes", False),
        (".repowise/decisions.yaml", False),
        (".repowise/config.yaml", True),
        # The traverser flags an indexed API spec by reading it.
        ("docs/openapi.json", True),
        # The package-root scan looks for it; an edit in place still patches (test_unread_unindexed).
        ("requirements.txt", True),
        ("yarn.lock", False),
        ("docs/logo.png", False),
    ],
)
def test_presence_read(path: str, read: bool) -> None:
    assert presence_read(path) is read


@pytest.mark.parametrize(
    ("path", "unread"),
    [
        ("requirements.txt", True),
        ("package-lock.json", True),
        ("Gemfile.lock", True),
        ("docs/logo.png", True),
        ("promql/testdata/functions.test", True),
        ("promql/parser/generated_parser.y", True),
        ("lezer-promql/src/promql.grammar", True),
        # Live walks read unindexed source of known extensions.
        ("big/Generated.cs", False),
        ("app/models/user.rb", False),
        ("web/generated/http.ts", False),
        ("db/queries.sql.go", False),
        # Cleared only while the indexed set holds still (test_unindexed_edits_no_step_opens_are_patched).
        (".gitignore", False),
        (".repowiseIgnore", False),
        # Prose and data no step opens by extension; API specs are read only when indexed.
        ("data/blob.json", True),
        ("CHANGELOG.md", True),
        ("docs/guide.adoc", True),
        ("api/openapi.json", True),
        ("swagger-private.json", True),
        (".repowise/decisions.yaml", True),
        (".gitattributes", True),
        ("App/Resources/Localizable.xcstrings", True),
        # ...unless a step opens them by name or by directory.
        ("tsconfig.base.json", False),
        ("docs/intro.mdx", False),
        ("app/src/main/resources/application.yml", False),
        ("app/src/main/resources/META-INF/services/com.acme.Spi", False),
        (".repowise/config.yaml", False),
    ],
)
def test_unread_unindexed(path: str, unread: bool) -> None:
    assert _unread_unindexed(path) is unread


def test_digest_tracks_only_what_readers_read(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(_package())
    before = _digest(tmp_path, "package.json")
    (tmp_path / "package.json").write_text(_package(version="5.0.0", description="x"))
    assert _digest(tmp_path, "package.json") == before
    (tmp_path / "package.json").write_text(_package(scripts={"build": "tsc"}))
    assert _digest(tmp_path, "package.json") != before
    assert _digest(tmp_path, "missing/package.json") is None


def test_digest_tracks_dependency_names_not_versions(tmp_path: Path) -> None:
    """A dependency's name says which JS framework loads files by convention."""
    (tmp_path / "package.json").write_text(_package())
    before = _digest(tmp_path, "package.json")
    (tmp_path / "package.json").write_text(_package(dependencies={"left-pad": "^2.0.0"}))
    assert _digest(tmp_path, "package.json") == before
    (tmp_path / "package.json").write_text(_package(devDependencies={"react-email": "6.0.0"}))
    assert _digest(tmp_path, "package.json") != before


def test_manifest_edit_the_diff_missed_falls_back(
    indexed: tuple[Path, dict[str, list]], tmp_path: Path
) -> None:
    """A manifest edited outside the update's diff must not count as seen."""
    tree, seed = indexed
    (tree / "go.mod").write_text(_GO_MOD.replace("example.com/app", "example.com/other"))

    patched = _update(tree, seed, {"src/fmt.ts": "export function fmt(s: string) { return s + '!'; }\n"})

    assert not patched.patch_plan.eligible
    assert "go.mod: a field" in (patched.patch_plan.reason or "")
    _assert_equals_full(tree, patched, tmp_path / "full")


def test_package_json_raw_text_reader_is_part_of_the_view(tmp_path: Path) -> None:
    """The test-runner handler scans package.json text, dropped fields included."""
    web = {
        "name": "web",
        "jest": {"setupFiles": ["./setup.ts"]},
        "devDependencies": {"left-pad": "^1.0.0", "@acme/root": "file:../.."},
    }
    tree = tmp_path / "repo"
    _write(
        tree,
        {
            "package.json": json.dumps({"name": "mono", "private": True, "workspaces": ["packages/*"]}) + "\n",
            "packages/web/package.json": json.dumps(web, indent=2) + "\n",
            "packages/web/setup.ts": "export const ready = true;\n",
            "packages/web/a.test.ts": "import { ready } from './setup';\ntest('a', () => ready);\n",
            "other/b.test.ts": "test('b', () => 1);\n",
        },
    )
    first = build_repo_graph(tree, [], collect_sources=True, cache_resolutions=True)[2]
    web["devDependencies"] = {"left-pad": "^1.0.0"}

    patched = _update(tree, _snapshot(first), {"packages/web/package.json": json.dumps(web, indent=2) + "\n"})

    assert not patched.patch_plan.eligible
    assert "a field" in (patched.patch_plan.reason or "")
    _assert_equals_full(tree, patched, tmp_path / "full")


def test_a_failing_config_check_falls_back(
    indexed: tuple[Path, dict[str, list]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.pipeline.incremental import config_inputs

    def boom(**_kwargs: Any) -> Any:
        raise OSError("disk went away")

    monkeypatch.setattr(config_inputs, "plan_non_source_changes", boom)
    tree, seed = indexed

    patched = _update(tree, seed, {"package.json": _package(version="1.0.1")})

    assert not patched.patch_plan.eligible
    assert "config check failed" in (patched.patch_plan.reason or "")
    _assert_equals_full(tree, patched, tmp_path / "full")


def test_a_failed_save_leaves_no_older_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from repowise.core.pipeline.incremental import resolution_cache

    index_dir = tmp_path / ".repowise"
    index_dir.mkdir()
    save_resolutions(index_dir, [], {}, {}, config_inputs={"files": {}, "stack": []})
    assert load_config_inputs(index_dir) is not None

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("file in use")

    monkeypatch.setattr(resolution_cache, "dump_sealed_pickle", fail)
    save_resolutions(index_dir, [], {}, {}, config_inputs={"files": {}, "stack": ["Go"]})

    assert load_config_inputs(index_dir) is None


# --- files the walk does not index ---------------------------------------------

_CMAKE = "cmake_minimum_required(VERSION 3.20)\nproject(native C)\nadd_library(core core.c)\n"
# Over the traverser's 500 KB ceiling for files no parser reads.
_BIG_MARKDOWN = "# Changelog\n\n" + "- fixed a thing that was broken before\n" * 16_000
_BIG_SPEC = {"openapi": "3.0.0", "paths": {f"/r{i}": {"get": {}} for i in range(30_000)}}
_UNINDEXED_TREE = {
    **_TREE,
    ".gitattributes": "* text=auto\n",
    ".repowiseIgnore": "dist/\n",
    ".repowise/decisions.yaml": "decisions: []\n",
    ".repowise/config.yaml": "sql_dialect: postgres\n",
    # Gitignored (dist/) and oversized files.
    "dist/gen.py": "def made():\n    return 1\n",
    "dist/notes.md": "# Notes\n",
    "dist/App/Resources/Localizable.xcstrings": '{"strings": {}}\n',
    "CHANGELOG.md": _BIG_MARKDOWN,
    "api/openapi.json": json.dumps(_BIG_SPEC) + "\n",
    # A C library its CMakeLists.txt builds.
    "native/CMakeLists.txt": _CMAKE,
    "native/core.h": "int core(void);\n",
    "native/core.c": '#include "core.h"\nint core(void) { return 1; }\n',
    "native/extra.c": '#include "core.h"\nint extra(void) { return core(); }\n',
}


@pytest.fixture
def unindexed(tmp_path: Path) -> tuple[Path, dict[str, list]]:
    tree = tmp_path / "repo"
    _write(tree, _UNINDEXED_TREE)
    first = build_repo_graph(tree, [], collect_sources=True, cache_resolutions=True)[2]
    indexed = set(first._parsed_files)
    assert not {"CHANGELOG.md", "api/openapi.json", "dist/notes.md", "native/CMakeLists.txt"} & indexed
    return tree, _snapshot(first)


@pytest.mark.parametrize(
    "edits",
    [
        pytest.param({".gitattributes": "* text=auto eol=lf\n"}, id="gitattributes"),
        pytest.param({".repowise/decisions.yaml": "decisions:\n  - id: d1\n"}, id="repowise-decisions"),
        # Ignores a directory that holds nothing the walk indexed.
        pytest.param({".gitignore": "dist/\nbuild/\n"}, id="gitignore-same-index"),
        pytest.param({".repowiseIgnore": "dist/\nbuild/\n"}, id="repowiseignore-same-index"),
        pytest.param({"CHANGELOG.md": "# Changelog\n\n- one more\n" + _BIG_MARKDOWN}, id="oversized-markdown"),
        pytest.param(
            {"api/openapi.json": json.dumps({**_BIG_SPEC, "info": {"title": "x"}}) + "\n"}, id="oversized-openapi"
        ),
        pytest.param({"dist/notes.md": "# Notes\n\nMore.\n"}, id="gitignored-markdown"),
        pytest.param(
            {"dist/App/Resources/Localizable.xcstrings": '{"strings": {"hi": {}}}\n'}, id="strings-under-resources"
        ),
        # Comments and commands the CMake reader skips.
        pytest.param(
            {"native/CMakeLists.txt": "# build the core\n" + _CMAKE + 'message(STATUS "core")\n'}, id="cmake-comment"
        ),
        pytest.param(
            {
                ".gitattributes": "*.c text\n",
                "CHANGELOG.md": "# Changelog\n\n- release\n" + _BIG_MARKDOWN,
                "src/fmt.ts": "export function fmt(s: string) { return s.trim(); }\n",
            },
            id="release-with-body-edit",
        ),
    ],
)
def test_unindexed_edits_no_step_opens_are_patched(
    unindexed: tuple[Path, dict[str, list]], tmp_path: Path, edits: dict[str, str]
) -> None:
    tree, seed = unindexed

    patched = _update(tree, seed, edits)

    assert patched.patch_plan.eligible, patched.patch_plan.reason
    assert patched.patch_plan.refresh
    _assert_equals_full(tree, patched, tmp_path / "full")


@pytest.mark.parametrize(
    ("edits", "reason"),
    [
        pytest.param(
            {".repowise/config.yaml": "sql_dialect: mysql\n"},
            ".repowise/config.yaml: the graph build reads",
            id="repowise-config",
        ),
        # Live walks open unindexed source, whatever made the walk skip it.
        pytest.param(
            {"dist/gen.py": "def made():\n    return 2\n"}, "dist/gen.py: the graph build reads", id="unindexed-source"
        ),
        pytest.param(
            {"native/CMakeLists.txt": _CMAKE.replace("core.c)", "core.c extra.c)")},
            "native/CMakeLists.txt: a field",
            id="cmake-target-sources",
        ),
        pytest.param(
            {"native/CMakeLists.txt": _CMAKE + "target_include_directories(core PUBLIC include)\n"},
            "native/CMakeLists.txt: a field",
            id="cmake-include-dirs",
        ),
        pytest.param(
            {".gitignore": "dist/\nnative/\n"},
            ".gitignore: it changed which files the walk indexes",
            id="gitignore-drops-indexed-files",
        ),
        pytest.param(
            {".repowiseIgnore": "dist/\nnative/\n"},
            ".repowiseIgnore: it changed which files the walk indexes",
            id="repowiseignore-drops-indexed-files",
        ),
    ],
)
def test_unindexed_edits_a_step_opens_fall_back(
    unindexed: tuple[Path, dict[str, list]], tmp_path: Path, edits: dict[str, str], reason: str
) -> None:
    tree, seed = unindexed

    patched = _update(tree, seed, edits)

    assert not patched.patch_plan.eligible
    assert reason in (patched.patch_plan.reason or "")
    _assert_equals_full(tree, patched, tmp_path / "full")


def test_cmake_target_edit_changes_the_graph(unindexed: tuple[Path, dict[str, list]], tmp_path: Path) -> None:
    """Why a target edit must not patch: the full build stamps the newly compiled file."""
    tree, seed = unindexed
    before = dict(seed["nodes"])["native/extra.c"].get("build_listed")

    patched = _update(tree, seed, {"native/CMakeLists.txt": _CMAKE.replace("core.c)", "core.c extra.c)")})

    assert not before
    assert patched.graph().nodes["native/extra.c"].get("build_listed")


def test_cmake_digest_ignores_the_checkout_path(tmp_path: Path) -> None:
    for name in ("a", "b"):
        _write(tmp_path / name, {"CMakeLists.txt": _CMAKE})
    assert _digest(tmp_path / "a", "CMakeLists.txt") == _digest(tmp_path / "b", "CMakeLists.txt")
    (tmp_path / "b" / "CMakeLists.txt").write_text(_CMAKE.replace("core.c)", "core.c extra.c)"))
    assert _digest(tmp_path / "a", "CMakeLists.txt") != _digest(tmp_path / "b", "CMakeLists.txt")


# --- build outputs a package.json names ---------------------------------------

# The TypeScript resolver reads a subpath export into gitignored build output
# as the source it was built from, and a checked-in build as itself.
_BUILDS_TREE = {
    "package.json": json.dumps({"name": "root", "private": True, "workspaces": ["packages/*"]}) + "\n",
    "packages/lib/package.json": json.dumps(
        {"name": "@acme/lib", "version": "1.0.0", "exports": {"./x": "./dist/impl/x.js"}}
    )
    + "\n",
    "packages/lib/src/impl/x.ts": "export function f() { return 1; }\n",
    "packages/lib/dist/impl/x.js": "exports.f = function () { return 1; };\n",
    "packages/app/package.json": json.dumps({"name": "app", "dependencies": {"@acme/lib": "*"}}) + "\n",
    "packages/app/src/main.ts": "import { f } from '@acme/lib/x';\nexport const x = f();\n",
    "packages/app/src/other.ts": "export const y = 1;\n",
    ".gitignore": "dist/\n",
}
_MAIN = "packages/app/src/main.ts"


@pytest.fixture
def builds(tmp_path: Path) -> tuple[Path, dict[str, list]]:
    import subprocess

    tree = tmp_path / "repo"
    _write(tree, _BUILDS_TREE)
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
    first = build_repo_graph(tree, [], collect_sources=True, cache_resolutions=True)[2]
    assert first.graph().has_edge(_MAIN, "packages/lib/src/impl/x.ts")
    return tree, _snapshot(first)


def test_gitignore_that_checks_a_build_in_falls_back(
    builds: tuple[Path, dict[str, list]], tmp_path: Path
) -> None:
    """Un-ignoring dist/ leaves the indexed set alone but moves the import to the build."""
    tree, seed = builds

    patched = _update(tree, seed, {".gitignore": "# nothing ignored\n"})

    assert not patched.patch_plan.eligible
    assert "changed whether it is checked in" in (patched.patch_plan.reason or "")
    assert not patched.graph().has_edge(_MAIN, "packages/lib/src/impl/x.ts")
    _assert_equals_full(tree, patched, tmp_path / "full")


def test_gitignore_that_leaves_builds_alone_is_patched(
    builds: tuple[Path, dict[str, list]], tmp_path: Path
) -> None:
    tree, seed = builds

    patched = _update(tree, seed, {".gitignore": "dist/\ncoverage/\n"})

    assert patched.patch_plan.eligible, patched.patch_plan.reason
    _assert_equals_full(tree, patched, tmp_path / "full")


def test_a_local_build_the_diff_never_names_falls_back(
    builds: tuple[Path, dict[str, list]], tmp_path: Path
) -> None:
    """With dist/ not ignored, a build that appears on disk is checked in from then on."""
    tree, seed = builds
    (tree / ".gitignore").write_text("# nothing ignored\n")
    (tree / "packages/lib/dist/impl/x.js").unlink()
    first = _update(tree, seed, {".gitignore": "# nothing ignored\n"})
    (tree / "packages/lib/dist/impl/x.js").write_text("exports.f = function () { return 2; };\n")

    patched = _update(tree, _snapshot(first), {"packages/app/src/other.ts": "export const y = 2;\n"})

    assert not patched.patch_plan.eligible
    assert "changed whether it is checked in" in (patched.patch_plan.reason or "")
    _assert_equals_full(tree, patched, tmp_path / "full")


def test_a_failed_record_with_checked_in_builds_falls_back(
    builds: tuple[Path, dict[str, list]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When this run cannot record its inputs, builds the last record holds still count."""
    from repowise.core.pipeline.incremental import config_inputs

    tree, seed = builds
    first = _update(tree, seed, {".gitignore": "# nothing ignored\n"})
    assert load_config_inputs(tree / ".repowise")["builds"] == ["packages/lib/dist/impl/x.js"]
    monkeypatch.setattr(config_inputs, "record_inputs", lambda *_a, **_k: None)

    patched = _update(tree, _snapshot(first), {"packages/app/src/other.ts": "export const y = 2;\n"})

    assert not patched.patch_plan.eligible
    assert "no record of which build outputs" in (patched.patch_plan.reason or "")


def test_gitignore_edit_without_a_builds_record_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record written before builds were recorded cannot clear a .gitignore edit."""
    from repowise.core.pipeline.incremental import config_inputs

    real = config_inputs.record_inputs

    def without_builds(*args: Any, **kwargs: Any) -> Any:
        record = real(*args, **kwargs)
        record.pop("builds")
        return record

    tree = tmp_path / "repo"
    _write(tree, _TREE)
    monkeypatch.setattr(config_inputs, "record_inputs", without_builds)
    first = build_repo_graph(tree, [], collect_sources=True, cache_resolutions=True)[2]
    monkeypatch.undo()
    assert "builds" not in (load_config_inputs(tree / ".repowise") or {"builds": 1})

    patched = _update(tree, _snapshot(first), {".gitignore": "dist/\ncoverage/\n"})

    assert not patched.patch_plan.eligible
    assert "no record of which build outputs" in (patched.patch_plan.reason or "")


_REINDEXED = "detected changes include unparsed files"
_INDEX_MOVED = ".repowiseIgnore: it changed which files the walk indexes"
_NOTHING = "coverage/\n"
_LIB_DIST = "packages/lib/dist/\n"
_LIB_SRC = "packages/lib/src/\n"
_META_DIR = "packages/meta/\n"
_META = {"name": "@acme/meta", "version": "1.0.0"}


@pytest.mark.parametrize(
    ("gitignore", "before", "after", "reason"),
    [
        # dist/ gitignored: the import reads the source the build came from.
        pytest.param("dist/\n", _NOTHING, _LIB_DIST, None, id="ignores-gitignored-build"),
        # dist/ checked in: the import reads the build, which the walk never indexes.
        pytest.param("", _NOTHING, _LIB_DIST, None, id="ignores-checked-in-build"),
        pytest.param("", _LIB_DIST, _NOTHING, None, id="unignores-checked-in-build"),
        # ...unless a negation hands the build to the walk.
        pytest.param("", _NOTHING, "!" + _LIB_DIST, _REINDEXED, id="reincludes-build"),
        # A package directory holding only its manifest.
        pytest.param("dist/\n", _NOTHING, _META_DIR, _INDEX_MOVED, id="ignores-manifest-only"),
        # The walk stops, or starts, indexing the package's source.
        pytest.param("dist/\n", _NOTHING, _LIB_SRC, _INDEX_MOVED, id="ignores-source"),
        pytest.param("dist/\n", _LIB_SRC, _NOTHING, _REINDEXED, id="unignores-source"),
    ],
)
def test_repowiseignore_edit_over_package_builds(
    tmp_path: Path, gitignore: str, before: str, after: str, reason: str | None
) -> None:
    """``.repowiseIgnore`` is read only by the walk; ``git check-ignore`` never sees it."""
    import subprocess

    tree = tmp_path / "repo"
    _write(
        tree,
        {
            **_BUILDS_TREE,
            "packages/meta/package.json": json.dumps(_META) + "\n",
            ".gitignore": gitignore or "# nothing ignored\n",
            ".repowiseIgnore": before,
        },
    )
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
    first = build_repo_graph(tree, [], collect_sources=True, cache_resolutions=True)[2]

    patched = _update(tree, _snapshot(first), {".repowiseIgnore": after})

    if reason is None:
        assert patched.patch_plan.eligible, patched.patch_plan.reason
    else:
        assert not patched.patch_plan.eligible
        assert reason in (patched.patch_plan.reason or "")
    _assert_equals_full(tree, patched, tmp_path / "full")


# --- files parsed without their grammar ---------------------------------------


@pytest.fixture
def no_go_grammar(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Parse in process with the Go grammar unavailable, as after a failed download."""
    from repowise.core.ingestion import parser as parser_mod
    from repowise.core.pipeline.phases import ingestion

    real = parser_mod._get_language
    monkeypatch.setattr(parser_mod, "_get_language", lambda tag: None if tag == "go" else real(tag))

    def in_process(to_parse: list, **_kwargs: Any) -> list:
        parser = parser_mod.ASTParser()
        return [parser.parse_file(fi, source) for _idx, (fi, source), _hash in to_parse]

    monkeypatch.setattr(ingestion, "parse_guarded", in_process)
    return monkeypatch


def test_unchanged_file_without_its_grammar_is_not_a_change(
    no_go_grammar: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """It re-parses every run, but the same bytes still without a grammar are the last parse."""
    tree = tmp_path / "repo"
    _write(tree, _TREE)
    first = build_repo_graph(tree, [], collect_sources=True, cache_resolutions=True)[2]
    assert first._parsed_files["cmd/main.go"].grammar_missing

    patched = _update(tree, _snapshot(first), {"src/fmt.ts": "export function fmt(s: string) { return s + '!'; }\n"})

    assert patched.patch_plan.eligible, patched.patch_plan.reason
    assert "cmd/main.go" not in patched.patch_plan.scope
    _assert_equals_full(tree, patched, tmp_path / "full")


def test_grammar_that_loads_again_rebuilds(no_go_grammar: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A file that now parses with its grammar has a new parse: the graph is rebuilt."""
    tree = tmp_path / "repo"
    _write(tree, _TREE)
    first = build_repo_graph(tree, [], collect_sources=True, cache_resolutions=True)[2]
    no_go_grammar.undo()

    patched = _update(tree, _snapshot(first), {"src/fmt.ts": "export function fmt(s: string) { return s + '!'; }\n"})

    assert not patched.patch_plan.eligible
    assert not patched._parsed_files["cmd/main.go"].grammar_missing
    _assert_equals_full(tree, patched, tmp_path / "full")


def test_edited_file_without_its_grammar_falls_back(
    no_go_grammar: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tree = tmp_path / "repo"
    _write(tree, _TREE)
    first = build_repo_graph(tree, [], collect_sources=True, cache_resolutions=True)[2]

    patched = _update(tree, _snapshot(first), {"cmd/main.go": "package main\n\nfunc main() {}\n"})

    assert not patched.patch_plan.eligible
    assert "grammar unavailable" in (patched.patch_plan.reason or "")
    _assert_equals_full(tree, patched, tmp_path / "full")
