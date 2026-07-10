"""Claude Code SessionStart context block: freshness states and gating.

The block's whole value is calibrated trust, so the contract under test is
which of the four states gets emitted (current / update-in-flight / behind /
git-unavailable) and that a git failure never produces a false "current"
claim. Outside an indexed repo the handler must stay silent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.cli.commands.augment_cmd import session_start

_INDEXED = "1" * 40
_LIVE = "2" * 40


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "state.json").write_text(
        json.dumps({"last_sync_commit": _INDEXED}), encoding="utf-8"
    )
    return tmp_path


def _handle(cwd: Path) -> str | None:
    return session_start._handle_claude_session_start(str(cwd))


def test_current_index(repo, monkeypatch) -> None:
    monkeypatch.setattr(session_start, "_git_head", lambda p: _INDEXED)
    out = _handle(repo)
    assert out is not None
    assert "current" in out
    assert _INDEXED[:8] in out
    assert "get_answer" in out


def test_behind_head_with_changed_count(repo, monkeypatch) -> None:
    monkeypatch.setattr(session_start, "_git_head", lambda p: _LIVE)
    monkeypatch.setattr(session_start, "_read_in_flight_marker", lambda p: None)
    monkeypatch.setattr(session_start, "_changed_file_count", lambda p, a, b: 12)
    out = _handle(repo)
    assert out is not None
    assert f"indexed {_INDEXED[:8]}, now {_LIVE[:8]}" in out
    assert "(12 files changed since)" in out
    assert "stale_warning" in out
    assert "repowise update" in out


def test_behind_head_without_changed_count(repo, monkeypatch) -> None:
    monkeypatch.setattr(session_start, "_git_head", lambda p: _LIVE)
    monkeypatch.setattr(session_start, "_read_in_flight_marker", lambda p: None)
    monkeypatch.setattr(session_start, "_changed_file_count", lambda p, a, b: None)
    out = _handle(repo)
    assert out is not None
    assert "files changed" not in out
    assert "repowise update" in out


def test_update_in_flight_gets_positive_notice(repo, monkeypatch) -> None:
    monkeypatch.setattr(session_start, "_git_head", lambda p: _LIVE)
    monkeypatch.setattr(
        session_start,
        "_read_in_flight_marker",
        lambda p: {"source": "lock", "target_commit": _LIVE, "elapsed_seconds": 34.2},
    )
    out = _handle(repo)
    assert out is not None
    assert "update in progress" in out
    assert "started 34s ago" in out
    # Positive notice, not a stale scare.
    assert "repowise update" not in out


def test_git_failure_never_claims_current(repo, monkeypatch) -> None:
    monkeypatch.setattr(session_start, "_git_head", lambda p: None)
    out = _handle(repo)
    assert out is not None
    assert "current" not in out
    assert "MCP tools" in out


def test_silent_outside_indexed_repo(tmp_path) -> None:
    assert _handle(tmp_path) is None


def test_silent_without_state_json(tmp_path) -> None:
    (tmp_path / ".repowise").mkdir()
    assert _handle(tmp_path) is None


def test_silent_without_last_sync_commit(tmp_path) -> None:
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "state.json").write_text("{}", encoding="utf-8")
    assert _handle(tmp_path) is None


def test_changed_file_count_real_git(tmp_path) -> None:
    import subprocess

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "t@t.t")
    git("config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "one")
    sha1 = git("rev-parse", "HEAD")
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("z = 3\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "two")
    sha2 = git("rev-parse", "HEAD")

    assert session_start._git_head(tmp_path) == sha2
    assert session_start._changed_file_count(tmp_path, sha1, sha2) == 2
    # An unresolvable SHA degrades to None (count omitted), never raises.
    assert session_start._changed_file_count(tmp_path, "f" * 40, sha2) is None
