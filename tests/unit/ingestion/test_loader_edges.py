"""Files a runtime loads by path for a test: Alembic migrations and a VS Code extension's entry."""

from __future__ import annotations

import json
from types import SimpleNamespace

import networkx as nx
import pytest

from repowise.core.ingestion.framework_edges.alembic_runner import (
    ALEMBIC_RUNNER_HINT,
    _add_runner_edges,
)
from repowise.core.ingestion.framework_edges.vscode_extension import (
    VSCODE_HOST_HINT,
    _add_host_edges,
)
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.test_paths import is_test_related_path


def _ctx(sources: dict[str, str], repo_path=None) -> tuple[nx.DiGraph, dict, ResolverContext]:
    graph = nx.DiGraph()
    graph.add_nodes_from(sources)
    parsed = {
        p: SimpleNamespace(file_info=SimpleNamespace(is_test=is_test_related_path(p), abs_path=p))
        for p in sources
    }
    ctx = ResolverContext(
        path_set=set(sources),
        stem_map={},
        graph=graph,
        repo_path=repo_path,
        source_map={p: s.encode() for p, s in sources.items()},
    )
    return graph, parsed, ctx


def _edges(graph: nx.DiGraph, hint: str) -> set[tuple[str, str]]:
    return {(s, t) for s, t, d in graph.edges(data=True) if d.get("hint_source") == hint}


_SCRIPTS = {
    "db/alembic/env.py": "from alembic import context\n",
    "db/alembic/versions/0001_init.py": "from alembic import op\ndef upgrade(): ...\n",
    "db/alembic/versions/0002_more.py": "def upgrade(): ...\n",
    # A versions directory with no env.py beside it is not a script directory.
    "app/versions/v1.py": "x = 1\n",
}
_LINKED = {
    "db/alembic/env.py",
    "db/alembic/versions/0001_init.py",
    "db/alembic/versions/0002_more.py",
}


@pytest.mark.parametrize(
    ("runner", "linked"),
    [
        ("from alembic import command\ncommand.upgrade(cfg, 'head')\n", True),
        ("from alembic import (\n    command,\n)\n", True),
        ("import alembic.config\nalembic.config.main(argv=['upgrade', 'head'])\n", True),
        ("import alembic\nalembic.command.upgrade(cfg, 'head')\n", True),
        ("subprocess.run(['alembic', 'upgrade', 'head'])\n", True),
        ("os.system('alembic upgrade head')\n", True),
        ("from tests.db import migrate  # alembic underneath\n", False),
        ('"""Runs alembic in CI."""\nx = 1\n', False),
        ("import alembic_utils\n", False),
    ],
)
def test_code_naming_alembic_reaches_every_migration(runner: str, linked: bool) -> None:
    graph, parsed, ctx = _ctx({**_SCRIPTS, "tests/test_migrate.py": runner})
    _add_runner_edges(graph, parsed, ctx, ctx.path_set)
    expected = {("tests/test_migrate.py", s) for s in _LINKED} if linked else set()
    assert _edges(graph, ALEMBIC_RUNNER_HINT) == expected


def test_a_helper_running_alembic_passes_it_on_through_its_importers() -> None:
    sources = {**_SCRIPTS, "tests/db.py": "from alembic import command\n", "tests/t.py": "x\n"}
    graph, parsed, ctx = _ctx(sources)
    _add_runner_edges(graph, parsed, ctx, ctx.path_set)
    assert {s for s, _ in _edges(graph, ALEMBIC_RUNNER_HINT)} == {"tests/db.py"}


def test_a_migration_outside_a_script_directory_gets_no_edge() -> None:
    graph, parsed, ctx = _ctx({**_SCRIPTS, "tests/test_m.py": "import alembic\n"})
    _add_runner_edges(graph, parsed, ctx, ctx.path_set)
    assert {t for _, t in _edges(graph, ALEMBIC_RUNNER_HINT)} == _LINKED


def test_an_extension_test_reaches_the_entry_its_package_declares(tmp_path) -> None:
    manifest = {"name": "ext", "main": "./dist/extension.js", "engines": {"vscode": "^1.90.0"}}
    (tmp_path / "ext").mkdir()
    (tmp_path / "ext" / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "package.json").write_text('{"main": "index.js"}', encoding="utf-8")
    graph, parsed, ctx = _ctx(
        {
            "ext/src/extension.ts": "export function activate() {}\n",
            "ext/src/test/extension.test.ts": 'import * as vscode from "vscode";\n',
            "ext/webview/src/app.test.ts": 'import { render } from "./app";\n',
            "lib/index.js": "module.exports = {}\n",
            "lib/index.test.js": 'const vscode = require("vscode");\n',
            "ext/src/test/bare.test.ts": 'import "vscode";\n',
        },
        repo_path=tmp_path,
    )
    assert _add_host_edges(graph, parsed, ctx, ctx.path_set) == 2
    assert _edges(graph, VSCODE_HOST_HINT) == {
        ("ext/src/test/extension.test.ts", "ext/src/extension.ts"),
        ("ext/src/test/bare.test.ts", "ext/src/extension.ts"),
    }


def test_an_entry_built_from_no_known_source_gives_no_edge(tmp_path) -> None:
    manifest = {"main": "./out/bundle.js", "engines": {"vscode": "^1.90.0"}}
    (tmp_path / "ext").mkdir()
    (tmp_path / "ext" / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    graph, parsed, ctx = _ctx(
        {
            "ext/lib/activate.ts": "export function activate() {}\n",
            "ext/src/test/extension.test.ts": 'import * as vscode from "vscode";\n',
        },
        repo_path=tmp_path,
    )
    # No edge, so a change to the extension's code keeps its full run.
    assert _add_host_edges(graph, parsed, ctx, ctx.path_set) == 0
