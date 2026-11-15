"""An update from commit A to B lands on the graph a fresh init at B builds."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.helpers import release_update_lock
from repowise.cli.main import cli

_INIT = (
    "init",
    "--embedder",
    "mock",
    "--no-editor-setup",
    "--no-hook",
    "--no-agents",
    "--no-codex",
    "--no-claude-md",
    "--no-cost-tracking",
    "--no-workspace",
    "--no-prose",
    "-y",
)


def _git(repo: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Graph Parity",
        "GIT_AUTHOR_EMAIL": "graph-parity@example.invalid",
        "GIT_COMMITTER_NAME": "Graph Parity",
        "GIT_COMMITTER_EMAIL": "graph-parity@example.invalid",
    }
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _invoke(runner: CliRunner, repo: Path, *args: str) -> None:
    try:
        result = runner.invoke(cli, [*args, str(repo)], catch_exceptions=False)
        assert result.exit_code == 0, result.output
    finally:
        release_update_lock(repo)


def _commit(repo: Path, files: tuple[str, ...], message: str) -> None:
    for rel in files:
        path = repo / rel
        # A comment line is valid in every fixture format except rst, where it is text.
        path.write_text(path.read_text(encoding="utf-8") + f"# {message}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _seed(repo: Path) -> None:
    repo.mkdir(parents=True)
    files = {
        "src/a.py": "def a():\n    return 1\n",
        "src/b.py": "def b():\n    return 2\n",
        "tests/test_a.py": "def test_a():\n    assert True\n",
        ".github/workflows/ci.yaml": "name: ci\non: push\n",
        "pyproject.toml": "[project]\nname = 'demo'\n",
        "docs/guide.rst": "Guide\n=====\n",
    }
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    # A code file co-changing with a workflow, a manifest and a doc page the
    # graph has no node for; the test file is linked to src only by history.
    for n in range(3):
        _commit(
            repo,
            ("src/a.py", "tests/test_a.py", ".github/workflows/ci.yaml", "docs/guide.rst"),
            f"a {n}",
        )
        _commit(repo, (".github/workflows/ci.yaml", "pyproject.toml"), f"ci {n}")


def _graph(repo: Path) -> dict[str, list[tuple]]:
    db = repo / ".repowise" / "wiki.db"
    with closing(sqlite3.connect(db)) as conn:
        return {
            "nodes": conn.execute(
                "SELECT node_id, node_type, community_id FROM graph_nodes ORDER BY node_id"
            ).fetchall(),
            "co_changes": conn.execute(
                "SELECT source_node_id, target_node_id FROM graph_edges "
                "WHERE edge_type = 'co_changes' ORDER BY 1, 2"
            ).fetchall(),
        }


def test_update_graph_matches_init_at_the_same_commit(tmp_path: Path, monkeypatch) -> None:
    incremental = tmp_path / "incremental" / "repo"
    _seed(incremental)
    runner = CliRunner()
    monkeypatch.setenv(
        "REPOWISE_DB_URL", f"sqlite+aiosqlite:///{incremental / '.repowise' / 'wiki.db'}"
    )
    _invoke(runner, incremental, *_INIT)

    for n in range(2):
        _commit(incremental, ("src/b.py", ".github/workflows/ci.yaml", "docs/guide.rst"), f"b {n}")
    _invoke(runner, incremental, "update", "--index-only", "--no-workspace", "--no-agents")

    control = tmp_path / "control" / "repo"
    shutil.copytree(incremental, control, ignore=shutil.ignore_patterns(".repowise"))
    monkeypatch.setenv(
        "REPOWISE_DB_URL", f"sqlite+aiosqlite:///{control / '.repowise' / 'wiki.db'}"
    )
    _invoke(runner, control, *_INIT)

    expected = _graph(control)
    assert "docs/guide.rst" not in {node for node, *_ in expected["nodes"]}
    assert expected["co_changes"], "fixture must produce co-change edges"
    assert _graph(incremental) == expected
