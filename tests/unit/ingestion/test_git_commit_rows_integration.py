"""Integration test: GitIndexer produces per-commit rows on a real repo.

Builds a small temporary git repository and runs the full indexer, asserting
that ``GitIndexSummary.commit_rows`` is populated with correctly-shaped rows
carrying change features + a just-in-time change-risk score — the data the
``git_commits`` table persists.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.git_indexer import GitIndexer, GitIndexTier


def _commit(repo, path, content, message):
    f = path
    f.write_text(content)
    repo.index.add([f.name])
    repo.index.commit(message)


@pytest.mark.asyncio
async def test_index_repo_collects_commit_rows(tmp_path) -> None:
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    # Three commits; the middle one is a fix touching two files.
    _commit(repo, tmp_path / "a.py", "x = 1\n", "feat: add a")
    (tmp_path / "b.py").write_text("y = 2\n")
    (tmp_path / "a.py").write_text("x = 1\nz = 3\n")
    repo.index.add(["a.py", "b.py"])
    repo.index.commit("fix: patch a and add b")
    _commit(repo, tmp_path / "c.py", "w = 4\n", "feat: add c")

    idx = GitIndexer(tmp_path, tier=GitIndexTier.FULL)
    summary, _results = await idx.index_repo("repo1")

    rows = summary.commit_rows
    assert len(rows) == 3

    # Every row has the persisted shape.
    for r in rows:
        assert r["sha"]
        assert set(
            [
                "lines_added",
                "files_changed",
                "dirs_changed",
                "subsystems_changed",
                "entropy",
                "is_fix",
                "change_risk_score",
                "change_risk_level",
            ]
        ).issubset(r.keys())
        assert 0.0 <= r["change_risk_score"] <= 10.0
        assert r["change_risk_level"] in {"low", "moderate", "high"}

    # The fix commit is flagged and touches two files.
    fix = next(r for r in rows if r["subject"].startswith("fix:"))
    assert fix["is_fix"] is True
    assert fix["files_changed"] == 2


@pytest.mark.asyncio
async def test_capture_new_commit_rows_bounds_by_since_ts(tmp_path) -> None:
    """The incremental capture walks the same commit index but drops commits at
    or older than *since_ts* — only genuinely new commits are returned."""
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    _commit(repo, tmp_path / "a.py", "x = 1\n", "feat: add a")
    _commit(repo, tmp_path / "b.py", "y = 2\n", "feat: add b")
    _commit(repo, tmp_path / "c.py", "w = 4\n", "feat: add c")

    idx = GitIndexer(tmp_path, tier=GitIndexTier.FULL)

    # No bound → all three commits captured.
    all_rows = idx.capture_new_commit_rows()
    assert len(all_rows) == 3

    # Bound at/after the newest → nothing new. (Commits made in the same second
    # share a timestamp at 1s git resolution, so a far-future bound is the
    # robust assertion; the precise boundary is covered by the unit test.)
    newest_ts = max(r["committed_at"].timestamp() for r in all_rows)
    assert idx.capture_new_commit_rows(since_ts=int(newest_ts)) == []


@pytest.mark.asyncio
async def test_capture_new_commit_rows_empty_in_rename_mode(tmp_path) -> None:
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    _commit(repo, tmp_path / "a.py", "x = 1\n", "feat: add a")
    idx = GitIndexer(tmp_path, tier=GitIndexTier.FULL, follow_renames=True)
    assert idx.capture_new_commit_rows() == []


@pytest.mark.asyncio
async def test_index_repo_commit_rows_empty_in_rename_mode(tmp_path) -> None:
    """Rename-tracking mode uses the per-file walk (no batched commit index),
    so commit rows are not collected — documented fallback, not an error."""
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    _commit(repo, tmp_path / "a.py", "x = 1\n", "feat: add a")

    idx = GitIndexer(tmp_path, tier=GitIndexTier.FULL, follow_renames=True)
    summary, _results = await idx.index_repo("repo1")
    assert summary.commit_rows == []
