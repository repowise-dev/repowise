"""A bulk commit must not be able to stall the commit-health scan.

The scan's wall-clock budget is only checked between commits, so one commit
that touches tens of thousands of files (an initial import) ran for minutes
regardless of it. Such commits are skipped, and a fast index (essential git
tier) does not run the scan on update at all.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import git as gitpython
import pytest

from repowise.core.analysis.change_health.commit_scan import (
    changed_file_count,
    max_files_per_commit,
    scan_commits,
)
from repowise.core.analysis.change_health.service import MAX_CHANGED_FILES
from repowise.core.pipeline.commit_health import (
    _is_fast_index,
    refresh_commit_health_after_update,
)


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, list[str]]:
    """Three commits: a 5-file root, a 1-file change, and a merge."""
    r = gitpython.Repo.init(tmp_path, initial_branch="main")
    with r.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    names = [f"f{i}.py" for i in range(5)]
    for n in names:
        (tmp_path / n).write_text("x = 1\n", encoding="utf-8")
    r.index.add(names)
    root = r.index.commit("bulk root")
    (tmp_path / "f0.py").write_text("x = 2\n", encoding="utf-8")
    r.index.add(["f0.py"])
    small = r.index.commit("small")
    r.close()
    return tmp_path, [root.hexsha, small.hexsha]


def test_changed_file_count_covers_root_and_ordinary_commits(repo) -> None:
    path, (root, small) = repo

    assert changed_file_count(str(path), root) == 5
    assert changed_file_count(str(path), small) == 1


def test_changed_file_count_is_none_when_git_cannot_answer(tmp_path: Path) -> None:
    assert changed_file_count(str(tmp_path / "missing"), "a" * 40) is None


class _Service:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def compare(self, request):
        self.seen.append(request.revspec)
        raise RuntimeError("not under test")


def test_a_commit_over_the_cap_is_skipped_without_analysis(repo, monkeypatch) -> None:
    path, (root, small) = repo
    monkeypatch.setenv("REPOWISE_COMMIT_HEALTH_MAX_FILES", "3")
    service = _Service()

    scan = scan_commits(str(path), [root, small], service=service)

    assert service.seen == [small]
    assert scan.skipped == 2  # the bulk root by the cap, the small one by the stub's error


def test_the_cap_can_be_disabled(repo, monkeypatch) -> None:
    path, (root, small) = repo
    monkeypatch.setenv("REPOWISE_COMMIT_HEALTH_MAX_FILES", "0")
    service = _Service()

    scan_commits(str(path), [root, small], service=service)

    assert service.seen == [root, small]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("nope", MAX_CHANGED_FILES), ("-4", MAX_CHANGED_FILES), ("12", 12)],
)
def test_cap_override_ignores_junk(monkeypatch, raw: str, expected: int) -> None:
    monkeypatch.setenv("REPOWISE_COMMIT_HEALTH_MAX_FILES", raw)

    assert max_files_per_commit() == expected


@pytest.mark.parametrize(
    ("state", "fast"),
    [
        ({"git_tier": "essential"}, True),
        ({"git_tier": "full"}, False),
        ({}, False),
        ("not-an-object", False),
    ],
)
def test_fast_index_is_read_from_state(tmp_path: Path, state, fast: bool) -> None:
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "state.json").write_text(json.dumps(state), encoding="utf-8")

    assert _is_fast_index(str(tmp_path)) is fast


def test_a_missing_or_corrupt_state_file_is_not_a_fast_index(tmp_path: Path) -> None:
    assert _is_fast_index(str(tmp_path)) is False
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "state.json").write_text("{broken", encoding="utf-8")
    assert _is_fast_index(str(tmp_path)) is False


def test_an_update_on_a_fast_index_never_opens_a_session(tmp_path: Path) -> None:
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "state.json").write_text(
        json.dumps({"git_tier": "essential"}), encoding="utf-8"
    )
    opened: list[bool] = []

    def factory():
        opened.append(True)
        raise AssertionError("a fast index must not be scanned")

    stats = asyncio.run(refresh_commit_health_after_update(factory, "repo", str(tmp_path)))

    assert opened == []
    assert stats["scanned"] == 0
