"""``repowise update`` on a directory that is not a git repository."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli


def _nodes(db: Path) -> set[str]:
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute("SELECT node_id FROM graph_nodes WHERE node_type = 'file'").fetchall()
    return {r[0] for r in rows}


def _symbols(db: Path) -> set[str]:
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute("SELECT name FROM wiki_symbols").fetchall()
    return {r[0] for r in rows}


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e.x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e.x",
    }
    out = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, env=env, text=True
    )
    return out.stdout.strip()


def _state(root: Path) -> dict:
    return json.loads((root / ".repowise" / "state.json").read_text(encoding="utf-8"))


def _baseline(root: Path) -> Path:
    return root / ".repowise" / "content_baseline.json"


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).write_text(text, encoding="utf-8")


@pytest.fixture
def plain_env(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "plain"
    (root / "pkg").mkdir(parents=True)
    _write(root, "pkg/__init__.py", "")
    _write(root, "pkg/a.py", "def alpha():\n    return 1\n")
    _write(root, "pkg/b.py", "from pkg.a import alpha\n\n\ndef beta():\n    return alpha()\n")
    db = root / ".repowise" / "wiki.db"
    db.parent.mkdir(parents=True)
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("REPOWISE_SKIP_EDITOR_SETUP", "1")
    # A parent checkout would make the directory part of its git repository.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    return root


@pytest.fixture
def plain_dir(plain_env: Path) -> Path:
    r = CliRunner().invoke(cli, ["init", str(plain_env), "--index-only"], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    assert _baseline(plain_env).exists()
    return plain_env


def _update(root: Path, *args: str, catch: bool = False):
    return CliRunner().invoke(cli, ["update", str(root), *args], catch_exceptions=catch)


def test_update_refreshes_a_plain_directory(plain_dir: Path) -> None:
    db = plain_dir / ".repowise" / "wiki.db"
    assert {"pkg/a.py", "pkg/b.py"} <= _nodes(db)

    _write(plain_dir, "pkg/a.py", "def gamma():\n    return 3\n")
    (plain_dir / "pkg" / "b.py").unlink()
    _write(plain_dir, "pkg/c.py", "def delta():\n    return 4\n")

    r = _update(plain_dir, "--index-only")

    assert r.exit_code == 0, r.output
    nodes = _nodes(db)
    assert "pkg/c.py" in nodes
    assert "pkg/b.py" not in nodes
    symbols = _symbols(db)
    assert {"gamma", "delta"} <= symbols
    assert not symbols & {"alpha", "beta"}
    assert _state(plain_dir)["last_sync_commit"] is None

    again = _update(plain_dir, "--index-only")
    assert again.exit_code == 0, again.output
    assert "Already up to date" in again.output


def test_an_unchanged_plain_directory_is_up_to_date(plain_dir: Path) -> None:
    r = _update(plain_dir, "--index-only")

    assert r.exit_code == 0, r.output
    assert "Already up to date" in r.output


def test_a_commit_made_after_a_plain_index_takes_over(plain_dir: Path) -> None:
    """The first update after ``git init`` and a commit records it and drops the baseline."""
    _git(plain_dir, "init", "-q")
    _git(plain_dir, "add", "pkg")
    _git(plain_dir, "commit", "-qm", "first")
    _write(plain_dir, "pkg/c.py", "def delta():\n    return 4\n")

    r = _update(plain_dir, "--index-only")

    assert r.exit_code == 0, r.output
    assert "delta" in _symbols(plain_dir / ".repowise" / "wiki.db")
    assert _state(plain_dir)["last_sync_commit"] == _git(plain_dir, "rev-parse", "HEAD")
    assert not _baseline(plain_dir).exists()


def test_a_plain_index_with_no_baseline_names_the_fix(plain_dir: Path) -> None:
    _baseline(plain_dir).unlink()

    r = _update(plain_dir, "--index-only", catch=True)

    assert r.exit_code != 0
    assert "content baseline" in " ".join(r.output.split())


def test_docs_update_refreshes_a_plain_directory(plain_env: Path) -> None:
    r = CliRunner().invoke(
        cli, ["init", str(plain_env), "--provider", "mock", "--yes"], catch_exceptions=False
    )
    assert r.exit_code == 0, r.output
    before = json.loads(_baseline(plain_env).read_text(encoding="utf-8"))["files"]
    _write(plain_env, "pkg/c.py", "def delta():\n    return 4\n")

    r = _update(plain_env, "--provider", "mock")

    assert r.exit_code == 0, r.output
    assert "pkg/c.py" in _nodes(plain_env / ".repowise" / "wiki.db")
    after = json.loads(_baseline(plain_env).read_text(encoding="utf-8"))["files"]
    assert set(after) == set(before) | {"pkg/c.py"}


def test_an_index_only_run_leaves_model_pages_their_change(plain_env: Path) -> None:
    """Model pages still owe a change an index-only run indexed, as a lagging docs pointer would."""
    r = CliRunner().invoke(
        cli, ["init", str(plain_env), "--provider", "mock", "--yes"], catch_exceptions=False
    )
    assert r.exit_code == 0, r.output
    _write(plain_env, "pkg/a.py", "def alpha2():\n    return 1\n")

    assert _update(plain_env, "--index-only").exit_code == 0
    # The lock is released at process exit; both runs share this process.
    from repowise.core.update_lock import release_update_lock

    release_update_lock(plain_env)
    docs = _update(plain_env, "--provider", "mock")

    assert docs.exit_code == 0, docs.output
    assert "Already up to date" not in docs.output
    assert "pkg/a.py" in docs.output
