"""One resolver names the commit an index describes from state.json and the DB row."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from repowise.core.workspace.update import resolve_indexed_commit


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


def _state(repo: Path, sha: str | None) -> None:
    (repo / ".repowise").mkdir(exist_ok=True)
    payload = {} if sha is None else {"last_sync_commit": sha}
    (repo / ".repowise" / "state.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    """``old`` and its child ``new`` on main, and ``side`` on a branch cut from root."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.co")
    _git(tmp_path, "config", "user.name", "t")
    root = _commit(tmp_path, "root")
    old = _commit(tmp_path, "old")
    new = _commit(tmp_path, "new")
    _git(tmp_path, "checkout", "-q", "-b", "side", root)
    side = _commit(tmp_path, "side")
    _git(tmp_path, "checkout", "-q", "main")
    return tmp_path, {"old": old, "new": new, "side": side}


def test_equal_stamps_name_their_commit(repo) -> None:
    path, c = repo
    _state(path, c["new"])
    assert resolve_indexed_commit(path, c["new"]) == (c["new"], None)


@pytest.mark.parametrize(("state", "row"), [("old", "new"), ("new", "old")])
def test_when_one_contains_the_other_the_older_wins(repo, state, row) -> None:
    path, c = repo
    _state(path, c[state])
    assert resolve_indexed_commit(path, c[row]) == (c["old"], None)


def test_unrelated_stamps_leave_the_commit_unknown(repo) -> None:
    path, c = repo
    _state(path, c["new"])
    resolved = resolve_indexed_commit(path, c["side"])
    assert resolved.sha is None
    assert "neither contains the other" in resolved.problem


def test_an_unreadable_sha_leaves_the_commit_unknown(repo) -> None:
    path, c = repo
    _state(path, c["new"])
    resolved = resolve_indexed_commit(path, "f" * 40)
    assert resolved.sha is None
    assert resolved.problem


def test_a_missing_stamp_defers_to_the_other(repo) -> None:
    path, c = repo
    assert resolve_indexed_commit(path, c["new"]) == (c["new"], None)
    _state(path, None)
    assert resolve_indexed_commit(path, c["new"]) == (c["new"], None)
    _state(path, c["old"])
    assert resolve_indexed_commit(path, None) == (c["old"], None)
    assert resolve_indexed_commit(None, c["new"]) == (c["new"], None)
    assert resolve_indexed_commit(path / "absent", None) == (None, None)


def test_the_server_freshness_reads_the_same_commit(repo) -> None:
    from repowise.server.mcp_server._meta import resolve_indexed_commit as meta_resolve

    path, c = repo
    _state(path, c["new"])
    assert meta_resolve(c["old"], str(path)) == c["old"]
    assert meta_resolve(c["side"], str(path)) is None
    # Hosted: no checkout, so the row is all there is.
    assert meta_resolve(c["side"], None) == c["side"]


def test_only_a_definite_ancestry_answer_is_cached(monkeypatch) -> None:
    from repowise.core import git_refs
    from repowise.core.workspace.update import state as update

    monkeypatch.setattr(update, "_ancestry_cache", {})
    answers = iter([None, None, True])  # a timeout both ways, then git answers
    calls: list[tuple[str, str]] = []

    def _is_ancestor(_repo, older, newer):
        calls.append((older, newer))
        return next(answers)

    monkeypatch.setattr(git_refs, "is_ancestor", _is_ancestor)
    assert update._older_commit("r", "a", "b") is None
    assert update._older_commit("r", "a", "b") == "a"
    assert update._older_commit("r", "a", "b") == "a"
    assert len(calls) == 3
