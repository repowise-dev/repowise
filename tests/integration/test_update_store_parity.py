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
    _assert_centrality_matches(control, incremental)


def _centrality(repo: Path) -> dict[str, dict[str, tuple]]:
    with closing(sqlite3.connect(repo / ".repowise" / "wiki.db")) as conn:
        return {
            table: {
                row[0]: row[1:]
                for row in conn.execute(f"SELECT node_id, pagerank, betweenness FROM {table}")
            }
            for table in ("graph_nodes", "graph_metrics")
        }


def _assert_centrality_matches(control: Path, incremental: Path) -> None:
    """Centrality equals init's within the persist gate's float tolerance.

    The update writes these through the gate, which keeps a stored value that
    differs from the fresh one by no more than ``_CENTRALITY_ATOL``.
    """
    from repowise.core.persistence.crud.graph import _CENTRALITY_ATOL

    expected = _centrality(control)
    actual = _centrality(incremental)
    for table, rows in expected.items():
        assert rows.keys() == actual[table].keys(), table
        assert any(value[0] for value in rows.values()), f"{table} must carry pagerank"
        for node_id, values in rows.items():
            assert actual[table][node_id] == pytest.approx(values, rel=0, abs=_CENTRALITY_ATOL), (
                table,
                node_id,
            )


def _kg_graph(repo: Path) -> tuple[str, list[str]]:
    kg = json.loads((repo / ".repowise" / "knowledge-graph.json").read_text(encoding="utf-8"))
    nodes = sorted(json.dumps(n, sort_keys=True) for n in kg["nodes"] + kg["edges"])
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
    fingerprint, before = _kg_graph(incremental)

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

    expected_fingerprint, expected = _kg_graph(control)
    assert expected_fingerprint == fingerprint, "fixture must keep the graph's shape"
    assert expected != before, "fixture must move the changed file's node"
    assert _kg_graph(incremental) == (fingerprint, expected)


def test_update_after_a_force_push_matches_init(tmp_path: Path, monkeypatch) -> None:
    """History rewritten under a warm commit cache: the update still writes
    what a fresh init at the new head writes. A commit limit of 2 sends the
    walk below the window, so the deep-region cache is exercised too."""
    incremental = tmp_path / "incremental" / "repo"
    _seed(incremental)
    _advance(incremental)
    runner = CliRunner()
    monkeypatch.setenv(
        "REPOWISE_DB_URL", f"sqlite+aiosqlite:///{incremental / '.repowise' / 'wiki.db'}"
    )
    _invoke(runner, incremental, *_INIT, "--commit-limit", "2")
    assert (incremental / ".repowise" / "commit_deep_cache.json").is_file()

    # Force push: drop the last two commits and land a different one.
    _git(incremental, "reset", "-q", "--hard", "HEAD~2")
    _write(incremental, {"src/app/caller.py": _A["src/app/caller.py"] + "\n# rewritten\n"})
    _commit(incremental, "fix: caller rewritten after the force push")
    _invoke(runner, incremental, "update", "--index-only", "--no-workspace", "--no-agents")

    control = tmp_path / "control" / "repo"
    shutil.copytree(incremental, control, ignore=shutil.ignore_patterns(".repowise"))
    monkeypatch.setenv(
        "REPOWISE_DB_URL", f"sqlite+aiosqlite:///{control / '.repowise' / 'wiki.db'}"
    )
    _invoke(runner, control, *_INIT, "--commit-limit", "2")

    expected, actual = _store(control), _store(incremental)
    bad = [table for table, rows in expected.items() if actual[table] != rows]
    assert not bad, bad


_REGISTRAR = {
    "src/app/__init__.py": "",
    "src/app/registry.py": (
        "_RULES = []\n\n\ndef rule(fn):\n    _RULES.append(fn)\n    return fn\n\n\n"
        "def run(value):\n    return [check(value) for check in _RULES]\n"
    ),
    "src/app/checks_a.py": (
        "from app.registry import rule\n\n\n@rule\ndef check_a(value):\n    return value + 1\n"
    ),
    "src/app/checks_b.py": (
        "from app.registry import rule\nfrom app.util import helper\n\n\n"
        "@rule\ndef check_b(value):\n    return helper(value)\n"
    ),
    "src/app/util.py": "def helper(value):\n    return value + 1\n",
    "pyproject.toml": "[project]\nname = 'registrar'\n",
    ".gitignore": ".repowise/\n",
}


def _graph_update(repo: Path) -> dict:
    state = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
    return state["graph_update"]


def test_editing_one_decorated_file_keeps_the_graph_patchable(
    tmp_path: Path, monkeypatch
) -> None:
    """A decorator that registers two files' functions: an update that
    reconciles one of them keeps the decorator's edge into the other, so the
    store matches the graph and the next update patches from its snapshot."""
    incremental = tmp_path / "incremental" / "repo"
    incremental.mkdir(parents=True)
    _write(incremental, _REGISTRAR)
    _git(incremental, "init", "-q")
    _commit(incremental, "seed")
    runner = CliRunner()
    monkeypatch.setenv(
        "REPOWISE_DB_URL", f"sqlite+aiosqlite:///{incremental / '.repowise' / 'wiki.db'}"
    )
    _invoke(runner, incremental, *_INIT)

    # A util.py edit reconciles its caller check_b but not the decorator's file;
    # a checks_a.py edit reconciles the decorator's file but not check_b.
    util = _REGISTRAR["src/app/util.py"]
    edits = [
        ("src/app/util.py", util.replace("+ 1", "+ 2")),
        ("src/app/checks_a.py", _REGISTRAR["src/app/checks_a.py"].replace("+ 1", "+ 5")),
        ("src/app/util.py", util.replace("+ 1", "+ 3")),
    ]
    for path, text in edits:
        _write(incremental, {path: text})
        _commit(incremental, f"chore: edit {path}")
        _invoke(runner, incremental, "update", "--index-only", "--no-workspace", "--no-agents")
        assert _graph_update(incremental)["mode"] == "patch", (path, _graph_update(incremental))

    control = tmp_path / "control" / "repo"
    shutil.copytree(incremental, control, ignore=shutil.ignore_patterns(".repowise"))
    monkeypatch.setenv(
        "REPOWISE_DB_URL", f"sqlite+aiosqlite:///{control / '.repowise' / 'wiki.db'}"
    )
    _invoke(runner, control, *_INIT)

    expected = _store(control)["graph_edges"]
    registrations = {
        edge[:2] for edge in expected if edge[5] == "decorator_registration"
    }
    assert registrations == {
        ("src/app/registry.py::rule", "src/app/checks_a.py::check_a"),
        ("src/app/registry.py::rule", "src/app/checks_b.py::check_b"),
    }, "fixture must register both checks"
    assert _store(incremental)["graph_edges"] == expected
