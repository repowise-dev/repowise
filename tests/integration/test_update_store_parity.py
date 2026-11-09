"""An update from commit A to B leaves the store a fresh init at B writes.

The fixture's A..B range edits, adds, removes and renames files, lands a bug
fix, drops the only import of a package, moves a function an unchanged
file calls, and adds a file under a directory the traverser skips. Each table
below is compared row for row, minus the columns an update deliberately
carries forward; those are listed with the reason.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest
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

# Row identity, plus the columns an update carries forward on files it did not
# re-index: ownership, age and authorship need the full history walk (see
# DECAY_REFRESH_KEYS), so they move only on a full index or the file's next change.
_GIT_METADATA_SKIPPED = {
    "id",
    "repository_id",
    "created_at",
    "updated_at",
    "age_days",
    "top_authors_json",
    "avg_commit_size",
    "recent_owner_name",
    "recent_owner_commit_pct",
    "is_stable",
}

_A = {
    "src/app/__init__.py": "",
    "src/app/util.py": (
        "def helper(x):\n"
        "    return x + 1\n"
        "\n"
        "\n"
        "def scale(x):\n"
        "    total = 0\n"
        "    for i in range(x):\n"
        "        total += i\n"
        "    return total\n"
    ),
    "src/app/caller.py": ("from app.util import helper\n\n\ndef run(x):\n    return helper(x)\n"),
    "src/app/legacy.py": (
        "import xml.dom.minidom\n"
        "\n"
        "\n"
        "def parse(text):\n"
        "    return xml.dom.minidom.parseString(text)\n"
    ),
    "src/app/names.py": "def label(x):\n    return str(x)\n",
    "tests/test_util.py": (
        "from app.util import helper\n\n\ndef test_helper():\n    assert helper(1) == 2\n"
    ),
    "pyproject.toml": "[project]\nname = 'parity'\n",
}


def _git(repo: Path, *args: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Store Parity",
        "GIT_AUTHOR_EMAIL": "store-parity@example.invalid",
        "GIT_COMMITTER_NAME": "Store Parity",
        "GIT_COMMITTER_EMAIL": "store-parity@example.invalid",
    }
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _write(repo: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _seed(repo: Path) -> None:
    repo.mkdir(parents=True)
    _write(repo, _A)
    _git(repo, "init", "-q")
    _commit(repo, "seed")
    _write(repo, {"src/app/util.py": _A["src/app/util.py"].replace("x + 1", "x + 2")})
    _commit(repo, "feat: widen helper")


def _advance(repo: Path) -> None:
    """Commit A..B: a fix, a move, an add, a removal and a rename."""
    _write(repo, {"src/app/util.py": _A["src/app/util.py"].replace("x + 2", "x + 1")})
    _commit(repo, "fix: helper returned the wrong value")
    # helper moves to a new file that util.py re-exports; caller.py is
    # untouched, but its call now resolves to the new file.
    _write(
        repo,
        {
            "src/app/helpers.py": "def helper(x):\n    return x + 1\n",
            "src/app/util.py": (
                "from app.helpers import helper\n"
                "\n\ndef scale(x):\n    total = 0\n    for i in range(x):\n"
                "        total += i\n    return total\n"
            ),
        },
    )
    _commit(repo, "refactor: move helper")
    # The traverser skips fixtures/ dirs; a full index still gives the file
    # its git history.
    _write(repo, {"src/app/fixtures/sample.py": "def sample():\n    return 1\n"})
    (repo / "src/app/legacy.py").unlink()
    _git(repo, "mv", "src/app/names.py", "src/app/labels.py")
    _commit(repo, "chore: drop legacy parser, rename names")


def _invoke(runner: CliRunner, repo: Path, *args: str) -> None:
    try:
        result = runner.invoke(cli, [*args, str(repo)], catch_exceptions=False)
        assert result.exit_code == 0, result.output
    finally:
        release_update_lock(repo)


def _rows(conn: sqlite3.Connection, sql: str) -> list[tuple]:
    return sorted(conn.execute(sql).fetchall(), key=repr)


def _git_metadata(conn: sqlite3.Connection) -> list[tuple]:
    columns = [
        row[1]
        for row in conn.execute("PRAGMA table_info(git_metadata)")
        if row[1] not in _GIT_METADATA_SKIPPED
    ]
    return _rows(conn, f"SELECT {', '.join(columns)} FROM git_metadata")


def _kg_summaries(repo: Path) -> list[tuple]:
    kg = json.loads((repo / ".repowise" / "knowledge-graph.json").read_text(encoding="utf-8"))
    return sorted((n["id"], n.get("summary", "")) for n in kg["nodes"])


def _store(repo: Path) -> dict[str, list[tuple]]:
    db = repo / ".repowise" / "wiki.db"
    with closing(sqlite3.connect(db)) as conn:
        return {
            "kg_summaries": _kg_summaries(repo),
            "graph_nodes": _rows(
                conn,
                "SELECT node_id, node_type, community_id, community_meta_json, "
                "file_path, start_line, end_line FROM graph_nodes",
            ),
            "graph_edges": _rows(
                conn,
                "SELECT source_node_id, target_node_id, edge_type, imported_names_json, "
                "confidence, hint_source, resolution_origin, call_lines_json "
                "FROM graph_edges",
            ),
            "graph_metrics": _rows(conn, "SELECT node_id, community_id FROM graph_metrics"),
            "fix_events": _rows(
                conn,
                "SELECT fix_sha, file_path, shape_kind, old_ranges_json, "
                "inducing_shas_json, symbol_ids_json, attribution FROM fix_events",
            ),
            "git_metadata": _git_metadata(conn),
            # Open rows only: an update keeps resolved plans as payoff history.
            "refactoring_suggestions": _rows(
                conn,
                "SELECT public_id, file_path, target_symbol FROM refactoring_suggestions "
                "WHERE status = 'open'",
            ),
            "health_findings": _rows(
                conn,
                "SELECT public_id, file_path, biomarker_type, function_name FROM health_findings "
                "WHERE status = 'open'",
            ),
        }


@pytest.mark.parametrize("update_args", [("--index-only",), ()], ids=["index-only", "docs"])
def test_update_store_matches_init_at_the_same_commit(
    tmp_path: Path, monkeypatch, update_args: tuple[str, ...]
) -> None:
    incremental = tmp_path / "incremental" / "repo"
    _seed(incremental)
    runner = CliRunner()
    monkeypatch.setenv(
        "REPOWISE_DB_URL", f"sqlite+aiosqlite:///{incremental / '.repowise' / 'wiki.db'}"
    )
    _invoke(runner, incremental, *_INIT)
    util = "file:src/app/util.py"
    stale_util = dict(_kg_summaries(incremental))[util]

    _advance(incremental)
    _invoke(runner, incremental, "update", *update_args, "--no-workspace", "--no-agents")

    control = tmp_path / "control" / "repo"
    shutil.copytree(incremental, control, ignore=shutil.ignore_patterns(".repowise"))
    monkeypatch.setenv(
        "REPOWISE_DB_URL", f"sqlite+aiosqlite:///{control / '.repowise' / 'wiki.db'}"
    )
    _invoke(runner, control, *_INIT)

    expected = _store(control)
    assert expected["fix_events"], "fixture must produce a fix event"
    assert ("src/app/caller.py::run", "src/app/helpers.py::helper", "calls") in {
        edge[:3] for edge in expected["graph_edges"]
    }, "fixture must move the call target of an unchanged file"
    assert "helper" in stale_util and dict(expected["kg_summaries"])[util] != stale_util, (
        "fixture must change a file's floor summary"
    )
    with closing(sqlite3.connect(control / ".repowise" / "wiki.db")) as conn:
        assert conn.execute(
            "SELECT commit_count_total FROM git_metadata WHERE file_path = ?",
            ("src/app/fixtures/sample.py",),
        ).fetchone() == (1,), "fixture must index a file the traverser skips"
    actual = _store(incremental)
    bad = [table for table, rows in expected.items() if actual[table] != rows]
    assert not bad, bad


def _kg_file_nodes(repo: Path) -> tuple[str, list[tuple]]:
    kg = json.loads((repo / ".repowise" / "knowledge-graph.json").read_text(encoding="utf-8"))
    fields = ("summary", "symbolCount", "lineCount", "complexity")
    nodes = sorted(
        (n["id"], *(n.get(f) for f in fields)) for n in kg["nodes"] if n["id"].startswith("file:")
    )
    state = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
    return state["knowledge_graph"]["fingerprint"], nodes


def test_same_shape_edit_refreshes_the_changed_file_nodes(tmp_path: Path, monkeypatch) -> None:
    """An edit that keeps the graph's shape skips the KG rebuild; the changed
    file's node still matches what a fresh init writes."""
    incremental = tmp_path / "incremental" / "repo"
    _seed(incremental)
    runner = CliRunner()
    monkeypatch.setenv(
        "REPOWISE_DB_URL", f"sqlite+aiosqlite:///{incremental / '.repowise' / 'wiki.db'}"
    )
    _invoke(runner, incremental, *_INIT)
    fingerprint, before = _kg_file_nodes(incremental)

    # Renamed in place: the same nodes, edges and communities, a new top symbol.
    _write(incremental, {"src/app/names.py": "def title(x):\n    # renamed\n    return str(x)\n"})
    _commit(incremental, "refactor: rename label")
    _invoke(runner, incremental, "update", "--index-only", "--no-workspace", "--no-agents")

    control = tmp_path / "control" / "repo"
    shutil.copytree(incremental, control, ignore=shutil.ignore_patterns(".repowise"))
    monkeypatch.setenv(
        "REPOWISE_DB_URL", f"sqlite+aiosqlite:///{control / '.repowise' / 'wiki.db'}"
    )
    _invoke(runner, control, *_INIT)

    expected_fingerprint, expected = _kg_file_nodes(control)
    assert expected_fingerprint == fingerprint, "fixture must keep the graph's shape"
    assert expected != before, "fixture must move the changed file's node"
    assert _kg_file_nodes(incremental) == (fingerprint, expected)
