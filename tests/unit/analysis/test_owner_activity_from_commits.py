"""An owner's "commits in the last 90 days" counts distinct commits, anchored to HEAD.

Summing per-file counts credited a commit once per file it touched, and used
the author's all-time count on each file, so the directory showed several
times the repo's real commit volume and nearly everyone as active. The count
now comes from the per-commit rows.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from repowise.core.analysis.owners import aggregate_owners
from repowise.core.ingestion.git_indexer import GitIndexer, GitIndexTier

# Fixed in the past so HEAD time, not the wall clock, must anchor the window.
_HEAD = datetime(2025, 3, 1, 12, 0, tzinfo=UTC)
_FILES = ("a.py", "b.py", "c.py")


def _commit(cwd: Path, who: str, when: datetime, n: int) -> None:
    for name in _FILES:
        (cwd / name).write_text(f"# {who} {n}\n")
    subprocess.run(["git", "add", *_FILES], cwd=cwd, check=True)
    stamp = when.isoformat()
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_DATE": stamp,
        "GIT_AUTHOR_NAME": who,
        "GIT_AUTHOR_EMAIL": f"{who.lower()}@example.com",
        "GIT_COMMITTER_NAME": who,
        "GIT_COMMITTER_EMAIL": f"{who.lower()}@example.com",
    }
    subprocess.run(["git", "commit", "-q", "-m", f"change {n}"], cwd=cwd, check=True, env=env)


@pytest.fixture
def three_author_repo(tmp_path: Path) -> Path:
    """Carol stopped 150 days before HEAD; Ada and Bob are active.

    Every commit touches all three files, so per-file sums triple-count.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    plan = [
        ("Carol", 400),
        ("Carol", 300),
        ("Carol", 150),
        ("Ada", 200),
        ("Bob", 120),
        ("Ada", 60),
        ("Bob", 40),
        ("Ada", 20),
        ("Ada", 0),
    ]
    for n, (who, days_before_head) in enumerate(plan):
        _commit(tmp_path, who, _HEAD - timedelta(days=days_before_head), n)
    return tmp_path


async def _index(repo: Path, commit_limit: int | None = None):
    summary, results = await GitIndexer(
        repo, tier=GitIndexTier.FULL, commit_limit=commit_limit
    ).index_repo("r")
    return summary, results


def _window_commits(repo: Path) -> int:
    since = (_HEAD - timedelta(days=90)).isoformat()
    out = subprocess.run(
        ["git", "rev-list", "--count", "--no-merges", f"--since={since}", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return int(out.stdout)


@pytest.mark.asyncio
async def test_counts_distinct_commits_in_the_window_before_head(three_author_repo: Path) -> None:
    summary, results = await _index(three_author_repo)
    accs, _ = aggregate_owners(
        results, [], summary.commit_rows, summary.repo_totals.total_commit_count
    )
    ada, bob, carol = (accs[f"{n}@example.com"] for n in ("ada", "bob", "carol"))

    assert (ada.commit_count_90d, bob.commit_count_90d, carol.commit_count_90d) == (3, 1, 0)
    # The inactive author is not active, and their last commit is their own.
    assert carol.last_commit_at == _HEAD - timedelta(days=150)
    assert ada.last_commit_at == _HEAD
    # Invariant: the owners never add up to more commits than the repo made.
    total = sum(a.commit_count_90d for a in accs.values() if a.key)
    assert total <= _window_commits(three_author_repo) == 4


@pytest.mark.asyncio
async def test_no_commit_rows_means_unknown_not_zero(three_author_repo: Path) -> None:
    _summary, results = await _index(three_author_repo)
    accs, _ = aggregate_owners(results, [])
    assert {a.commit_count_90d for a in accs.values() if a.key} == {None}


@pytest.mark.asyncio
async def test_a_sample_that_stops_inside_the_window_is_unknown(three_author_repo: Path) -> None:
    # Two rows reach back only 20 days; the window is 90 and history is deeper.
    summary, results = await _index(three_author_repo, commit_limit=2)
    accs, _ = aggregate_owners(
        results, [], summary.commit_rows, summary.repo_totals.total_commit_count
    )
    assert {a.commit_count_90d for a in accs.values() if a.key} == {None}


@pytest.mark.asyncio
async def test_a_short_history_inside_the_window_is_complete(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    _commit(tmp_path, "Ada", _HEAD - timedelta(days=10), 0)
    _commit(tmp_path, "Ada", _HEAD, 1)
    summary, results = await _index(tmp_path)
    accs, _ = aggregate_owners(
        results, [], summary.commit_rows, summary.repo_totals.total_commit_count
    )
    assert accs["ada@example.com"].commit_count_90d == 2
