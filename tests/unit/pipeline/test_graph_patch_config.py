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
        # A framework dependency changes the tech stack and so the framework edges.
        pytest.param(
            {"package.json": _package(dependencies={"express": "^4.0.0"})},
            "tech stack changed",
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
        pytest.param({".gitignore": "dist/\nbuild/\n"}, ".gitignore: the graph build reads", id="gitignore"),
        pytest.param({"requirements-dev.txt": "pytest\n"}, "added or removed", id="unindexed-added"),
        pytest.param({"requirements.txt": None}, "added or removed", id="unindexed-removed"),
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
        (".github/workflows/ci.yml", False),
        ("src/locales/en.json", False),
        ("requirements.txt", False),
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
        # Live walks read unindexed source of known extensions.
        ("big/Generated.cs", False),
        ("app/models/user.rb", False),
        ("data/blob.json", False),
        (".gitignore", False),
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
