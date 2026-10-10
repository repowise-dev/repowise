"""Files a runtime loads by path for a test: Alembic migrations and a VS Code extension's entry."""

from __future__ import annotations

import json
from types import SimpleNamespace

import networkx as nx

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


def test_code_running_alembic_commands_reaches_every_migration() -> None:
    graph, parsed, ctx = _ctx(
        {
            "db/alembic/env.py": "from alembic import context\n",
            "db/alembic/versions/0001_init.py": "def upgrade(): ...\n",
            "db/alembic/versions/0002_more.py": "def upgrade(): ...\n",
            "tests/test_migrate.py": "from alembic import command\ncommand.upgrade(cfg, 'head')\n",
            "tests/test_other.py": "import alembic_utils\n",
            "app/db.py": "import alembic.command\n",
            # A versions directory with no env.py beside it is not a script directory.
            "app/versions/v1.py": "x = 1\n",
        }
    )
    assert _add_runner_edges(graph, parsed, ctx, ctx.path_set) == 6
    scripts = {"db/alembic/env.py", "db/alembic/versions/0001_init.py", "db/alembic/versions/0002_more.py"}
    assert _edges(graph, ALEMBIC_RUNNER_HINT) == {
        (runner, s) for runner in ("tests/test_migrate.py", "app/db.py") for s in scripts
    }


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
        },
        repo_path=tmp_path,
    )
    assert _add_host_edges(graph, parsed, ctx, ctx.path_set) == 1
    assert _edges(graph, VSCODE_HOST_HINT) == {
        ("ext/src/test/extension.test.ts", "ext/src/extension.ts")
    }
