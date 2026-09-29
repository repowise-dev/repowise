"""Manifest-declared entry points (tier 0), end to end.

Each fixture is a small repo on disk driven through the real traverser, parser,
GraphBuilder (whose warmups stamp the TS manifest entries) and KG curation, so
the three places the answer lands are checked together: ``FileInfo`` (what the
entry-point list ranks), the graph node (what dead code reads) and
``project.entry_points`` (what the overview shows).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core.analysis.kg_curation import curate_knowledge_graph
from repowise.core.analysis.knowledge_graph import build_knowledge_graph_skeleton
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.ts_workspace import (
    checked_in_builds,
    manifest_entry_paths,
)
from repowise.core.ingestion.traverser import FileTraverser

_PARSER = ASTParser()


def _write(repo: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return repo


def _index(repo: Path) -> SimpleNamespace:
    traverser = FileTraverser(repo)
    infos = list(traverser.traverse())
    parser = _PARSER
    builder = GraphBuilder(repo)
    parsed = []
    for fi in infos:
        pf = parser.parse_file(fi, Path(fi.abs_path).read_bytes())
        builder.add_file(pf)
        parsed.append(pf)
    graph = builder.build()
    structure = traverser.get_repo_structure(infos)
    skeleton = build_knowledge_graph_skeleton(
        parsed_files=parsed,
        graph_builder=builder,
        repo_structure=structure,
        tech_stack=[],
        external_systems=[],
    )
    kg = curate_knowledge_graph(
        skeleton,
        parsed_files=parsed,
        graph_builder=builder,
        repo_structure=structure,
        community_info=builder.community_info(),
        enabled=True,
    )
    return SimpleNamespace(
        info={pf.file_info.path: pf.file_info for pf in parsed},
        graph=graph,
        project=kg.project,
    )


def _declared(idx: SimpleNamespace) -> set[str]:
    return {p for p, fi in idx.info.items() if fi.is_manifest_entry}


@pytest.fixture
def pnpm_bin_dist_to_src(tmp_path: Path) -> Path:
    """A pnpm package publishing a built CLI; only its sources are checked in."""
    return _write(
        tmp_path,
        {
            "pnpm-workspace.yaml": "packages:\n  - 'packages/**'\n",
            "package.json": json.dumps({"name": "root", "private": True}),
            "packages/tools/cli/package.json": json.dumps(
                {
                    "name": "@acme/cli",
                    "bin": {"acme": "./dist/cli.js", "acme-dev": "./dist/dev.mjs"},
                    "main": "./dist/index.js",
                }
            ),
            "packages/tools/cli/src/cli.ts": "import { run } from './index';\nrun();\n",
            "packages/tools/cli/src/dev.ts": "import { run } from './index';\nrun();\n",
            "packages/tools/cli/src/index.ts": "export function run() {}\n",
            "packages/keyring/src/entry.ts": "export class Entry {}\n",
            "harness/run.mjs": "console.log('bench');\n",
        },
    )


def test_pnpm_bin_dist_to_src(pnpm_bin_dist_to_src: Path) -> None:
    idx = _index(pnpm_bin_dist_to_src)
    declared = {
        "packages/tools/cli/src/cli.ts",
        "packages/tools/cli/src/dev.ts",
        "packages/tools/cli/src/index.ts",
    }
    assert _declared(idx) == declared
    for path in declared:
        assert idx.info[path].is_entry_point
        assert idx.graph.nodes[path]["is_entry_point"]
    # Declared entries lead, the deep glue ``index.ts`` included; the stem
    # guesses (``run.mjs``, a keyring ``entry.ts``) follow.
    assert set(idx.project["entry_points"][:3]) == declared
    assert idx.project["entry_points"][2] == "packages/tools/cli/src/index.ts"
    assert "harness/run.mjs" in idx.project["entry_points"][3:]


def test_npm_exports_only_dist(tmp_path: Path) -> None:
    """``exports["."]`` pointing at build output only; ``types`` is skipped."""
    repo = _write(
        tmp_path,
        {
            "package.json": json.dumps(
                {
                    "name": "lib",
                    "exports": {
                        ".": {
                            "types": "./dist/index.d.ts",
                            "import": "./dist/index.mjs",
                            "require": "./dist/index.cjs",
                        },
                        "./utils": "./dist/utils.mjs",
                    },
                }
            ),
            "src/index.ts": "export * from './utils';\n",
            "src/utils.ts": "export const u = 1;\n",
        },
    )
    idx = _index(repo)
    # Only "." is where the package starts; a subpath export is not.
    assert _declared(idx) == {"src/index.ts"}
    assert idx.project["entry_points"][0] == "src/index.ts"


def test_exports_conditions_object_is_the_root_export() -> None:
    data = {"exports": {"import": "./build/main.mjs", "types": "./build/main.d.ts"}}
    assert manifest_entry_paths(".", data, {"src/main.ts"}) == {"src/main.ts"}


def test_pyproject_scripts_and_dist_init(tmp_path: Path) -> None:
    repo = _write(
        tmp_path,
        {
            "pyproject.toml": (
                '[project]\nname = "my-tool"\n[project.scripts]\nmytool = "my_tool.cli:main"\n'
            ),
            "src/my_tool/__init__.py": "from .core import thing\n",
            "src/my_tool/cli.py": "def main():\n    pass\n",
            "src/my_tool/core.py": "def thing():\n    pass\n",
            "run.py": "print('x')\n",
        },
    )
    idx = _index(repo)
    assert _declared(idx) == {"src/my_tool/cli.py", "src/my_tool/__init__.py"}
    assert idx.graph.nodes["src/my_tool/__init__.py"]["is_entry_point"]
    assert idx.project["entry_points"][:2] == ["src/my_tool/cli.py", "src/my_tool/__init__.py"]
    # ``run.py`` keeps its flag (dead-code exemption) but ranks after both.
    assert idx.info["run.py"].is_entry_point
    assert idx.project["entry_points"].index("run.py") == 2


def test_committed_dist_no_remap(tmp_path: Path) -> None:
    """A checked-in build is the entry itself, so the source is not named."""
    repo = _write(
        tmp_path,
        {
            "package.json": json.dumps({"name": "shipped", "main": "./dist/index.js"}),
            "dist/index.js": "module.exports = {};\n",
            "src/index.ts": "export {};\n",
        },
    )
    idx = _index(repo)
    assert _declared(idx) == set()


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_gitignored_build_is_remapped(tmp_path: Path) -> None:
    """A local, gitignored build is not the checkout: map it to its source.

    One batched check answers for every package: the sibling package's
    committed ``lib`` build still counts as checked in.
    """
    repo = _write(
        tmp_path,
        {
            ".gitignore": "dist/\n",
            "a/package.json": "{}",
            "a/dist/index.js": "module.exports = {};\n",
            "a/src/index.ts": "export {};\n",
            "b/lib/main.js": "module.exports = {};\n",
            "b/src/main.ts": "export {};\n",
        },
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    manifests = [("a", {"main": "./dist/index.js"}), ("b", {"main": "./lib/main.js"})]
    path_set = {"a/src/index.ts", "b/src/main.ts"}
    checked_in = checked_in_builds(repo, manifests, path_set)
    assert checked_in == {"b/lib/main.js"}
    assert manifest_entry_paths("a", manifests[0][1], path_set, checked_in) == {"a/src/index.ts"}
    assert manifest_entry_paths("b", manifests[1][1], path_set, checked_in) == set()


def test_build_target_escaping_the_repo_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "outside.js").write_text("x", encoding="utf-8")
    repo = _write(tmp_path / "repo", {"src/x.ts": "export {};\n"})
    manifests = [(".", {"main": "dist/../../outside.js"})]
    assert checked_in_builds(repo, manifests, {"src/x.ts"}) == frozenset()
    assert manifest_entry_paths(".", manifests[0][1], {"src/x.ts"}) == set()


def test_fixture_pkg_under_tests(tmp_path: Path) -> None:
    """Test and example packages' bins stay dead-code exempt but are never surfaced."""
    repo = _write(
        tmp_path,
        {
            "tests/e2e/pkg/package.json": json.dumps({"name": "fx", "bin": "./tool.js"}),
            "tests/e2e/pkg/tool.js": "console.log(1);\n",
            "examples/demo/package.json": json.dumps({"name": "demo", "main": "./start.js"}),
            "examples/demo/start.js": "console.log(1);\n",
            "src/main.py": "print('x')\n",
        },
    )
    idx = _index(repo)
    for hidden in ("tests/e2e/pkg/tool.js", "examples/demo/start.js"):
        assert idx.info[hidden].is_manifest_entry
        assert idx.graph.nodes[hidden]["is_entry_point"]
        assert hidden not in idx.project["entry_candidates"]
    assert idx.project["entry_points"] == ["src/main.py"]
