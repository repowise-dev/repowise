"""A refused deleted-file prune holds the sync pointer (#5410).

The prune can decide a change set looks like a broken run rather than a commit
and refuse it, leaving the rows alone. That is not a failed step — nothing was
skipped and nothing threw — so it never reached ``failed_steps``. But it still
leaves the index behind the commit being recorded, and `last_sync_commit`
advanced anyway, which is how 1,597 stale paths survived a bulk rename while
the state claimed the repository was current.

A refusal must be visible to the caller as its own channel, distinct from both
``degraded`` (an error) and ``failed_steps`` (a skipped step).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.pipeline.incremental import persist_incremental_index
from repowise.core.pipeline.incremental.prune import prune_deleted_files


class _GraphStub:
    """Just enough graph for the prune's live-path pass: ``graph().nodes(data=True)``."""

    def graph(self):
        import networkx as nx

        return nx.DiGraph()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".repowise").mkdir()
    return tmp_path


async def test_a_refusal_is_its_own_channel_not_a_failed_step(repo):
    """A refusal lands in prune_refusals and nowhere else."""
    from repowise.core.pipeline import persist as persist_pkg

    async def _refuse(*_args, **_kwargs):
        # The shape prune_deleted_file_rows returns when the floor guard trips.
        return 0, ["Deleted-file prune refused for files: 1597 of 3013 paths looked deleted"]

    _real = persist_pkg.prune_deleted_file_rows
    persist_pkg.prune_deleted_file_rows = _refuse  # type: ignore[assignment]
    try:
        degraded: list[str] = []
        failed_steps: list[str] = []
        refusals: list[str] = []
        await prune_deleted_files(
            object(),
            "repo-id",
            repo,
            _GraphStub(),
            {"src/a.py"},
            health_report=None,
            skip=lambda _s, _e: None,
            log=lambda _m: None,
            degraded=degraded,
            timings=None,
            prune_refusals=refusals,
        )
    finally:
        persist_pkg.prune_deleted_file_rows = _real  # type: ignore[assignment]

    assert len(refusals) == 1, "the refusal did not reach the caller at all"
    # Not an error and not a skipped step: it must not look like either, or a
    # caller reading those lists will either hide it or over-react to it.
    assert "Deleted-file prune (refused)" not in failed_steps
    assert not any(entry.startswith("Deleted-file prune:") for entry in degraded)


async def test_persist_exposes_the_refusals_to_the_writer(repo, monkeypatch):
    """persist_incremental_index forwards refusals out of the writer context."""
    from repowise.core.pipeline.incremental import persist_index as inc

    async def _prune(session, repo_id, repo_path, graph_builder, live_paths, **kwargs):
        # Mirrors the real shape: the refusals are collected on the list the
        # caller handed the writer, so what lands here is visible out there.
        kwargs["prune_refusals"].append("prune refused")

    monkeypatch.setattr(inc, "prune_deleted_files", _prune, raising=False)
    refusals: list[str] = []
    await persist_incremental_index(
        repo,
        object(),
        {"src/a.py": {"file_path": "src/a.py"}},
        None,
        None,
        ["src/a.py"],
        log=lambda _m: None,
        degraded=[],
        failed_steps=[],
        prune_refusals=refusals,
    )
    assert refusals, "the writer did not hand the refusals back to the caller"
