"""Stored git rows written under older history rules are refreshed once.

Only a full git walk rewrites the per-file rows of files no commit touched, so
``git_history_version`` (missing counts as stale) forces exactly one on the next
update, and nothing else: no page is regenerated for it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from repowise.cli.commands.init_cmd.persistence import apply_git_history_coverage_state
from repowise.cli.helpers import load_state, save_state
from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION
from repowise.core.ingestion.git_indexer import (
    GIT_HISTORY_VERSION,
    GIT_HISTORY_VERSION_KEY,
    GitIndexer,
    git_history_stale,
)

from .test_update_up_to_date_lock import _indexed_repo, _invoke_update

UP_TO_DATE = "Already up to date"


@pytest.fixture
def walks(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count full git walks (``GitIndexer.index_repo``)."""
    calls: list[int] = []
    real = GitIndexer.index_repo

    async def _spy(self, *a, **k):
        calls.append(1)
        return await real(self, *a, **k)

    monkeypatch.setattr(GitIndexer, "index_repo", _spy)
    return calls


@pytest.fixture
def persist_kwargs(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """The index-only persist call's scope flags."""
    from repowise.cli.commands.update_cmd import command as upd_cmd

    seen: list[dict] = []
    real = upd_cmd._persist_index_only_update

    def _spy(*a, **k):
        seen.append(k)
        return real(*a, **k)

    monkeypatch.setattr(upd_cmd, "_persist_index_only_update", _spy)
    return seen


def _current(repo, **extra) -> None:
    save_state(
        repo,
        {**load_state(repo), "health_analyzer_version": HEALTH_ANALYZER_VERSION, **extra},
    )


@pytest.mark.parametrize("stored", [None, GIT_HISTORY_VERSION - 1])
def test_stale_stamp_walks_once_then_updates_incrementally(
    tmp_path, walks, persist_kwargs, stored
) -> None:
    repo, c2 = _indexed_repo(tmp_path)
    walks.clear()  # the fixture's own index walked once
    state = {**load_state(repo), "health_analyzer_version": HEALTH_ANALYZER_VERSION}
    state.pop(GIT_HISTORY_VERSION_KEY, None)  # an index written before the stamp
    if stored is not None:
        state[GIT_HISTORY_VERSION_KEY] = stored
    save_state(repo, state)

    out = _invoke_update(repo)

    assert UP_TO_DATE not in out
    assert "Git history rules changed" in out
    assert len(walks) == 1
    state = load_state(repo)
    assert state[GIT_HISTORY_VERSION_KEY] == GIT_HISTORY_VERSION
    assert state["last_sync_commit"] == c2
    # The forced walk regenerates nothing: no full-scope reconcile, no page
    # set, no config-driven re-score.
    (kwargs,) = persist_kwargs
    assert kwargs["full_git_summary"] is not None
    assert kwargs["full_generation_page_ids"] is None
    assert kwargs["reconcile_full_scope"] is False
    assert kwargs["force_full_rescore"] is False
    assert kwargs["require_config_rebuild_success"] is False
    # Mined git-archaeology decisions survive: nothing re-mines them here.
    assert kwargs["history_window_changed"] is False

    # Stamped, so the next run is quiet and walks nothing.
    assert UP_TO_DATE in _invoke_update(repo)
    assert len(walks) == 1


def test_current_stamp_forces_no_walk(tmp_path, walks) -> None:
    repo, _ = _indexed_repo(tmp_path)
    walks.clear()
    _current(repo, **{GIT_HISTORY_VERSION_KEY: GIT_HISTORY_VERSION})

    assert UP_TO_DATE in _invoke_update(repo)
    assert not walks


def test_missing_stamp_is_stale() -> None:
    assert git_history_stale({})
    assert git_history_stale({GIT_HISTORY_VERSION_KEY: GIT_HISTORY_VERSION - 1})
    assert not git_history_stale({GIT_HISTORY_VERSION_KEY: GIT_HISTORY_VERSION})


def test_init_stamps_only_when_git_ran() -> None:
    walked: dict = {}
    apply_git_history_coverage_state(walked, SimpleNamespace(git_summary=SimpleNamespace()))
    assert walked[GIT_HISTORY_VERSION_KEY] == GIT_HISTORY_VERSION

    skipped: dict = {}
    apply_git_history_coverage_state(skipped, SimpleNamespace(git_summary=None))
    assert GIT_HISTORY_VERSION_KEY not in skipped


def test_workspace_update_walks_a_stale_repo_once(tmp_path, walks) -> None:
    from repowise.core.workspace.update import update_single_repo_index

    repo, _ = _indexed_repo(tmp_path)
    walks.clear()
    state = load_state(repo)
    state.pop(GIT_HISTORY_VERSION_KEY)
    save_state(repo, state)

    result = asyncio.run(update_single_repo_index(repo))
    assert result.updated and result.git_history_refreshed
    assert len(walks) == 1

    _current(repo, **{GIT_HISTORY_VERSION_KEY: GIT_HISTORY_VERSION})
    result = asyncio.run(update_single_repo_index(repo))
    assert result.updated and not result.git_history_refreshed
    assert len(walks) == 1


def _health_shas(repo) -> dict[str, int]:
    from sqlalchemy import select

    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.database import resolve_db_url
    from repowise.core.persistence.models import GitCommitHealthDelta

    async def _read() -> dict[str, int]:
        engine = create_engine(resolve_db_url(repo))
        try:
            async with get_session(create_session_factory(engine)) as session:
                rows = (await session.execute(select(GitCommitHealthDelta))).scalars()
                return {r.sha: r.introduced_count for r in rows}
        finally:
            await engine.dispose()

    return asyncio.run(_read())


def test_stamp_forced_walk_keeps_commit_health(tmp_path) -> None:
    """The stamp re-reads the same window under new rules, so per-commit health
    (capped when rebuilt) survives; only a history-window change wipes it."""
    from sqlalchemy import select

    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.database import resolve_db_url
    from repowise.core.persistence.models import GitCommitHealthDelta, Repository

    repo, _ = _indexed_repo(tmp_path)
    old_sha = "a" * 40  # a commit past what a rebuild would rescan

    async def _seed() -> None:
        engine = create_engine(resolve_db_url(repo))
        try:
            async with get_session(create_session_factory(engine)) as session:
                repo_id = (await session.execute(select(Repository.id))).scalar_one()
                session.add(
                    GitCommitHealthDelta(repository_id=repo_id, sha=old_sha, introduced_count=7)
                )
        finally:
            await engine.dispose()

    asyncio.run(_seed())
    state = {**load_state(repo), "health_analyzer_version": HEALTH_ANALYZER_VERSION}
    state.pop(GIT_HISTORY_VERSION_KEY)
    save_state(repo, state)

    assert "Git history rules changed" in _invoke_update(repo)
    assert _health_shas(repo).get(old_sha) == 7
