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


def test_list_reachable_shas_excludes_abandoned_branch_commits(tmp_path) -> None:
    """Rows for a squash-merged branch have to be prunable.

    An update run while a feature branch is checked out persists that branch's
    shas. Once it is squash-merged onto main those commits are unreachable and
    no full index would ever produce them again, so they have to come back as
    not-reachable or they inflate every commit-level count forever.
    """
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    _commit(repo, tmp_path / "a.py", "x = 1\n", "feat: add a")
    main = repo.head.commit.hexsha

    repo.git.checkout("-b", "feature")
    _commit(repo, tmp_path / "b.py", "y = 2\n", "feat: add b")
    abandoned = repo.head.commit.hexsha
    repo.git.checkout(main)

    reachable = GitIndexer(tmp_path).list_reachable_shas()

    assert reachable is not None
    assert main in reachable
    assert abandoned not in reachable


def test_list_reachable_shas_returns_none_on_a_shallow_clone(tmp_path) -> None:
    """Unknown history must never be read as "these rows are orphans"."""
    import git as gitpython

    origin_path = tmp_path / "origin"
    origin_path.mkdir()
    origin = gitpython.Repo.init(origin_path)
    _commit(origin, origin_path / "a.py", "x = 1\n", "feat: add a")
    _commit(origin, origin_path / "b.py", "y = 2\n", "feat: add b")

    clone_path = tmp_path / "shallow"
    gitpython.Repo.clone_from(origin_path.as_uri(), clone_path, depth=1)

    assert GitIndexer(clone_path).list_reachable_shas() is None


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


def _configure_author(repo, name: str, email: str) -> None:
    with repo.config_writer() as cw:
        cw.set_value("user", "name", name)
        cw.set_value("user", "email", email)


@pytest.mark.asyncio
async def test_index_repo_captures_whole_history_totals(tmp_path) -> None:
    """``index_repo`` stamps true whole-history totals on the summary, and a
    small ``commit_limit`` must NOT bound them (issue #730)."""
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    _configure_author(repo, "Ada Lovelace", "ada@example.com")
    _commit(repo, tmp_path / "a.py", "x = 1\n", "feat: add a")
    _commit(repo, tmp_path / "b.py", "y = 2\n", "feat: add b")
    _commit(repo, tmp_path / "c.py", "w = 4\n", "feat: add c")

    # commit_limit far below the true count: the sample bounds, the totals don't.
    idx = GitIndexer(tmp_path, tier=GitIndexTier.FULL, commit_limit=1)
    summary, _results = await idx.index_repo("repo1")

    totals = summary.repo_totals
    assert totals is not None
    assert totals.total_commit_count == 3
    assert totals.total_contributor_count == 1
    assert totals.first_commit_author == "Ada Lovelace"
    assert totals.first_commit_at is not None


def test_capture_repo_totals_method(tmp_path) -> None:
    """The public capture (used by the incremental update path) opens its own
    repo and returns the same whole-history record."""
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    _configure_author(repo, "Grace Hopper", "grace@example.com")
    _commit(repo, tmp_path / "a.py", "x = 1\n", "feat: add a")
    _commit(repo, tmp_path / "b.py", "y = 2\n", "feat: add b")

    totals = GitIndexer(tmp_path, tier=GitIndexTier.FULL).capture_repo_totals()
    assert totals.total_commit_count == 2
    assert totals.total_contributor_count == 1
    assert totals.first_commit_author == "Grace Hopper"


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
