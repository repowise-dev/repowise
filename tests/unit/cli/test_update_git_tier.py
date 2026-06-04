"""`repowise update` must honor the persisted git tier.

A fast-mode (ESSENTIAL-tier) repo records ``git_tier: essential`` in
state.json; its updates must not pay per-file ``git blame`` for signals the
index never had. ``_rebuild_graph_and_git`` previously constructed GitIndexer
without a tier — silently FULL for every repo.
"""

from __future__ import annotations

import pytest

from repowise.cli.commands.update_cmd import _rebuild_graph_and_git
from repowise.core.ingestion.git_indexer.tiers import GitIndexTier


def _init_repo(tmp_path):
    import git as gitpython

    repo = gitpython.Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Alice")
        cw.set_value("user", "email", "alice@example.com")
    (tmp_path / "a.py").write_text("x = 1\n")
    repo.index.add(["a.py"])
    repo.index.commit("feat: add module a")
    repo.close()


class _RecordingIndexer:
    """Stands in for GitIndexer; records the tier the CLI passed."""

    instances: list["_RecordingIndexer"] = []

    def __init__(self, repo_path, **kwargs):
        self.kwargs = kwargs
        _RecordingIndexer.instances.append(self)

    async def index_changed_files(self, changed_paths):
        return []


@pytest.fixture(autouse=True)
def _reset_recorder():
    _RecordingIndexer.instances = []


@pytest.mark.parametrize(
    ("state_tier", "expected"),
    [
        ("essential", GitIndexTier.ESSENTIAL),
        ("full", GitIndexTier.FULL),
        (None, GitIndexTier.FULL),  # legacy state without git_tier
        ("bogus-tier", GitIndexTier.FULL),  # corrupt value falls back
    ],
)
def test_rebuild_threads_state_git_tier(tmp_path, monkeypatch, state_tier, expected):
    _init_repo(tmp_path)
    monkeypatch.setattr(
        "repowise.core.ingestion.git_indexer.GitIndexer", _RecordingIndexer
    )

    _rebuild_graph_and_git(tmp_path, [], {}, [], git_tier=state_tier)

    assert len(_RecordingIndexer.instances) == 1
    assert _RecordingIndexer.instances[0].kwargs["tier"] is expected


async def test_essential_tier_update_never_blames(tmp_path, monkeypatch):
    """End-to-end through the real GitIndexer: ESSENTIAL updates skip blame."""
    from repowise.core.ingestion.git_indexer import GitIndexer

    _init_repo(tmp_path)
    calls: list[str] = []

    def _no_blame(*args, **kwargs):  # pragma: no cover - failure path
        calls.append("blame")
        raise AssertionError("blame must not run on ESSENTIAL updates")

    monkeypatch.setattr(
        "repowise.core.ingestion.git_indexer.file_history.build_blame_index", _no_blame
    )
    monkeypatch.setattr(
        "repowise.core.ingestion.git_indexer.file_history.get_blame_ownership", _no_blame
    )

    indexer = GitIndexer(tmp_path, tier=GitIndexTier.ESSENTIAL)
    meta = await indexer.index_changed_files(["a.py"])

    assert calls == []
    assert meta and meta[0]["file_path"] == "a.py"
    # Ownership still present via the commit-author fallback.
    assert meta[0].get("primary_owner_name") == "Alice"
