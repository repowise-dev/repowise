""" "Commits" means non-merge commits reachable from HEAD on every surface.

A merge-inclusive whole-history total read against the ``--no-merges`` commit
sample made a complete sample look truncated ("5000 of 5498"). These tests
build a repo with merge commits and check every surface that reports a commit
total agrees, with merges counted apart.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repowise.core.ingestion.git_commit_index import _count_non_merge_commits
from repowise.core.ingestion.git_indexer import GitIndexer, GitIndexTier
from repowise.core.ingestion.git_indexer.file_history import _per_file_log_args
from repowise.core.stats_highlights import build_commit_pass


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def _commit(cwd: Path, path: str, content: str, message: str) -> None:
    (cwd / path).write_text(content)
    _git(cwd, "add", path)
    _git(cwd, "commit", "-q", "-m", message)


@pytest.fixture
def merge_repo(tmp_path: Path) -> Path:
    """Six non-merge commits and two ``--no-ff`` merges, one of them an evil merge."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.name", "Ada")
    _git(tmp_path, "config", "user.email", "ada@example.com")
    _commit(tmp_path, "a.py", "a = 1\n", "feat: add a")
    _git(tmp_path, "checkout", "-q", "-b", "topic")
    _commit(tmp_path, "b.py", "b = 1\n", "feat: add b")
    _commit(tmp_path, "a.py", "a = 2\n", "feat: bump a on topic")
    _git(tmp_path, "checkout", "-q", "main")
    _commit(tmp_path, "c.py", "c = 1\n", "feat: add c")
    _git(tmp_path, "merge", "-q", "--no-ff", "--no-commit", "topic")
    # An evil merge: the merge itself changes a.py beyond either parent.
    (tmp_path / "a.py").write_text("a = 3\n")
    _git(tmp_path, "add", "a.py")
    _git(tmp_path, "commit", "-q", "-m", "Merge branch 'topic'")
    _git(tmp_path, "checkout", "-q", "-b", "second")
    _commit(tmp_path, "d.py", "d = 1\n", "feat: add d")
    _git(tmp_path, "checkout", "-q", "main")
    _commit(tmp_path, "e.py", "e = 1\n", "feat: add e")
    _git(tmp_path, "merge", "-q", "--no-ff", "-m", "Merge branch 'second'", "second")
    _commit(tmp_path, "a.py", "a = 4\n", "fix: a")
    return tmp_path


def test_capture_counts_non_merge_and_merges_apart(merge_repo: Path) -> None:
    totals = GitIndexer(merge_repo, tier=GitIndexTier.FULL).capture_repo_totals()
    assert totals.total_commit_count == 7
    assert totals.total_merge_commit_count == 2
    assert int(_git(merge_repo, "rev-list", "--count", "HEAD")) == 9


@pytest.mark.asyncio
async def test_every_surface_reports_the_same_total(merge_repo: Path) -> None:
    import git as gitpython

    summary, _results = await GitIndexer(merge_repo, tier=GitIndexTier.FULL).index_repo("r")
    totals = summary.repo_totals
    assert totals is not None
    rows = summary.commit_rows
    stored = {
        "total_commit_count": totals.total_commit_count,
        "total_merge_commit_count": totals.total_merge_commit_count,
        "first_commit_at": totals.first_commit_at,
    }
    stats = build_commit_pass(rows, stored)

    surfaces = {
        "repo_totals": totals.total_commit_count,
        "commit_rows": len(rows),
        "history_walk_total": _count_non_merge_commits(gitpython.Repo(merge_repo)),
        "stats_origin": stats["origin"]["total_commits"],
        "stats_window": stats["rhythm"]["window"]["commits"],
    }
    assert set(surfaces.values()) == {7}, surfaces
    assert stats["origin"]["total_merge_commits"] == 2
    # The sample holds every non-merge commit, so it is not truncated.
    assert stats["rhythm"]["window"]["complete"] is True


@pytest.mark.asyncio
async def test_truncated_only_when_non_merge_total_exceeds_depth(merge_repo: Path) -> None:
    summary, _results = await GitIndexer(
        merge_repo, tier=GitIndexTier.FULL, commit_limit=3
    ).index_repo("r")
    totals = summary.repo_totals
    stats = build_commit_pass(
        summary.commit_rows, {"total_commit_count": totals.total_commit_count}
    )
    assert len(summary.commit_rows) == 3
    assert stats["origin"]["total_commits"] == 7
    assert stats["rhythm"]["window"]["complete"] is False


def test_every_per_file_lane_skips_merges() -> None:
    for follow in (True, False):
        assert "--no-merges" in _per_file_log_args("a.py", 10, follow)


@pytest.mark.asyncio
async def test_follow_lane_counts_match_non_merge_log(merge_repo: Path) -> None:
    _summary, results = await GitIndexer(
        merge_repo, tier=GitIndexTier.FULL, follow_renames=True
    ).index_repo("r")
    by_path = {r["file_path"]: r for r in results}
    expected = int(_git(merge_repo, "rev-list", "--count", "--no-merges", "HEAD", "--", "a.py"))
    assert by_path["a.py"]["commit_count_total"] == expected == 3
    assert by_path["a.py"]["merge_commit_count_90d"] == 0
