"""Dashboard stale rows say what git knows: commits since, and moved files.

Real ``git``: the count must come from the same anchor as the stored staleness
score, so a stubbed subprocess would only test the plumbing.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.server.mcp_server.tool_why.dashboard import _stale_row_currency

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_BORN = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
_LATER = datetime(2026, 1, 3, 12, 0, tzinfo=UTC)
_ACCEPTED = datetime(2026, 1, 4, 12, 0, tzinfo=UTC)


def _git(repo: Path, *args: str, when: datetime | None = None) -> str:
    env = dict(os.environ)
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when.isoformat()
    out = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True, env=env
    )
    return out.stdout.strip()


def _commit(repo: Path, path: str, content: str, when: datetime) -> None:
    (repo / path).write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"touch {path}", when=when)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _commit(root, "a.py", "a = 0\n", _T0)
    _commit(root, "b.py", "b = 0\n", _T0)
    return root


def _decision(files: list[str], created_at: datetime | None = _BORN) -> SimpleNamespace:
    return SimpleNamespace(
        affected_files_json=json.dumps(files),
        evidence_commits_json="[]",
        created_at=created_at,
    )


def test_counts_commits_since_birth_over_the_whole_scope(repo: Path) -> None:
    # The row serves only a.py, but the record governs both files and the
    # stored score counts both, so a change to b.py is part of why it is stale.
    _commit(repo, "b.py", "b = 1\n", _LATER)
    extra = _stale_row_currency(repo, _decision(["a.py", "b.py"]), ["a.py"])
    assert extra == {"commits_since": 1}


def test_a_later_reviewed_grant_moves_the_anchor(repo: Path) -> None:
    _commit(repo, "a.py", "a = 1\n", _LATER)
    extra = _stale_row_currency(repo, _decision(["a.py"]), ["a.py"], accepted_at=_ACCEPTED)
    assert extra == {"commits_since": 0}


def test_an_unscoped_record_gets_no_count(repo: Path) -> None:
    # With no paths, rev-list would count the whole repository.
    _commit(repo, "a.py", "a = 1\n", _LATER)
    assert _stale_row_currency(repo, _decision([]), []) == {}


def test_reports_served_files_that_are_gone(repo: Path) -> None:
    extra = _stale_row_currency(repo, _decision(["a.py", "gone.py"]), ["a.py", "gone.py"])
    assert extra["moved_files"] == ["gone.py"]
    assert extra["commits_since"] == 0


def test_omits_the_count_when_git_cannot_answer(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("a = 0\n", encoding="utf-8")
    assert _stale_row_currency(tmp_path, _decision(["a.py"]), ["a.py"]) == {}
