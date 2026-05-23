"""Unit tests for the tiered git indexer (ESSENTIAL vs FULL) and backfill.

These exercise the tier gating directly on the module-level functions and the
``GitIndexer`` shims, plus the resumable backfill worker against a fake
JobStore — no real git repo required.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from repowise.core.ingestion.git_indexer import (
    BACKFILL_PHASE,
    GitIndexer,
    GitIndexTier,
    backfill_full_tier,
    index_file,
)
from repowise.core.ingestion.git_indexer.records import _CommitRec

# ---------------------------------------------------------------------------
# Tier flags
# ---------------------------------------------------------------------------


def test_full_tier_includes_expensive_signals() -> None:
    assert GitIndexTier.FULL.includes_blame is True
    assert GitIndexTier.FULL.includes_co_change is True


def test_essential_tier_skips_expensive_signals() -> None:
    assert GitIndexTier.ESSENTIAL.includes_blame is False
    assert GitIndexTier.ESSENTIAL.includes_co_change is False


def test_indexer_defaults_to_full_tier() -> None:
    assert GitIndexer("/tmp/repo").tier is GitIndexTier.FULL


# ---------------------------------------------------------------------------
# index_file: include_blame gating
# ---------------------------------------------------------------------------


def _commits() -> list[_CommitRec]:
    now = int(time.time())
    return [
        _CommitRec("a" * 40, "Alice", "a@x.io", now, False, "feat: add widget", 10, 2),
        _CommitRec("b" * 40, "Bob", "b@x.io", now - 86400, False, "fix bug", 4, 1),
    ]


def test_index_file_essential_skips_blame(tmp_path) -> None:
    """With include_blame=False, get_blame_ownership must never be consulted —
    ownership falls back to the top commit author.
    """
    repo = MagicMock()
    # If blame were called it would raise, proving it must not be reached.
    repo.blame.side_effect = AssertionError("blame must not run in ESSENTIAL tier")
    (tmp_path / "a.py").write_text("x = 1\n")

    meta = index_file(
        repo,
        "a.py",
        repo_path=tmp_path,
        commit_limit=500,
        follow_renames=False,
        include_blame=False,
        precomputed_commits=_commits(),
    )
    assert meta["commit_count_total"] == 2
    assert meta["primary_owner_name"] == "Alice"  # commit-author fallback
    repo.blame.assert_not_called()


def test_index_file_full_consults_blame(tmp_path) -> None:
    """With include_blame=True, blame ownership overrides the commit author."""
    repo = MagicMock()
    blame_commit = MagicMock()
    blame_commit.author.name = "Carol"
    blame_commit.author.email = "c@x.io"
    repo.blame.return_value = [(blame_commit, ["line1", "line2", "line3"])]
    (tmp_path / "a.py").write_text("x = 1\n")

    meta = index_file(
        repo,
        "a.py",
        repo_path=tmp_path,
        commit_limit=500,
        follow_renames=False,
        include_blame=True,
        precomputed_commits=_commits(),
    )
    assert meta["primary_owner_name"] == "Carol"  # blame wins
    repo.blame.assert_called_once()


# ---------------------------------------------------------------------------
# index_repo: ESSENTIAL skips the co-change walk
# ---------------------------------------------------------------------------


async def test_index_repo_essential_skips_co_change(tmp_path) -> None:
    """ESSENTIAL tier must not invoke compute_co_changes and must leave
    co_change_partners empty, while still producing per-file metadata.
    """
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    repo.index.add(["a.py"])
    repo.index.commit("feat: add a")
    g = tmp_path / "b.py"
    g.write_text("y = 2\n")
    repo.index.add(["b.py"])
    repo.index.commit("feat: add b")

    co_change_seen = {"called": False}
    import repowise.core.ingestion.git_indexer.indexer as indexer_mod

    orig = indexer_mod.compute_co_changes

    def _spy(*args, **kwargs):  # pragma: no cover - should not run
        co_change_seen["called"] = True
        return orig(*args, **kwargs)

    indexer_mod.compute_co_changes = _spy
    try:
        idx = GitIndexer(tmp_path, tier=GitIndexTier.ESSENTIAL)
        summary, results = await idx.index_repo("repo1")
    finally:
        indexer_mod.compute_co_changes = orig

    assert co_change_seen["called"] is False
    assert summary.files_indexed >= 1
    for meta in results:
        assert meta.get("co_change_partners_json", "[]") == "[]"


# ---------------------------------------------------------------------------
# Backfill worker (resumable via JobStore)
# ---------------------------------------------------------------------------


class _FakeJobStore:
    """Minimal in-memory JobStore capturing the lifecycle for assertions."""

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.states: list[tuple[str, str]] = []
        self._seq = 0

    async def create_job(self, *, repository_id, phase, metadata=None):
        from repowise.core.persistence._interfaces.job_store import JobRecord, JobState

        self._seq += 1
        jid = f"job{self._seq}"
        self.created.append({"id": jid, "phase": phase, "metadata": metadata})
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        return JobRecord(jid, repository_id, phase, JobState.PENDING, None, now, now, None, metadata or {})

    async def update_state(self, job_id, state, *, cursor=None, error=None):
        self.states.append((job_id, state.value))
        return None


async def test_backfill_runs_full_and_records_job(tmp_path) -> None:
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n")
    repo.index.add(["a.py"])
    repo.index.commit("feat: add a")

    idx = GitIndexer(tmp_path, tier=GitIndexTier.ESSENTIAL)
    store = _FakeJobStore()

    summary, _results = await backfill_full_tier(idx, "repo1", job_store=store)

    # The job was recorded under the backfill phase and reached COMPLETED.
    assert store.created and store.created[0]["phase"] == BACKFILL_PHASE
    assert ("job1", "running") in store.states
    assert ("job1", "completed") in store.states
    # The indexer's tier is restored after the backfill.
    assert idx.tier is GitIndexTier.ESSENTIAL
    assert summary.files_indexed >= 1


async def test_backfill_marks_failed_on_error(tmp_path, monkeypatch) -> None:
    idx = GitIndexer(tmp_path, tier=GitIndexTier.ESSENTIAL)
    store = _FakeJobStore()

    async def _boom(_repo_id):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(idx, "index_repo", _boom)
    with pytest.raises(RuntimeError):
        await backfill_full_tier(idx, "repo1", job_store=store)
    assert ("job1", "failed") in store.states
    assert idx.tier is GitIndexTier.ESSENTIAL  # restored even on failure
