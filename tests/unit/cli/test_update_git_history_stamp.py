"""Stored git rows written under older history rules are refreshed once.

Only a full git walk rewrites the per-file rows of files no commit touched, so
``git_history_version`` (missing counts as stale) forces exactly one on the next
update, and nothing else: no page is regenerated for it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.cli.commands.init_cmd.persistence import apply_git_history_coverage_state
from repowise.cli.helpers import load_state, save_state
from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION
from repowise.core.ingestion.git_indexer import (
    GIT_HISTORY_BACKFILL_REWALK,
    GIT_HISTORY_VERSION,
    GIT_HISTORY_VERSION_KEY,
    GitIndexer,
    git_history_stale,
)

from .test_update_up_to_date_lock import _git, _indexed_repo, _invoke_update

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


def test_backfill_marker_walks_once_and_names_the_backfill(tmp_path, walks) -> None:
    repo, _ = _indexed_repo(tmp_path)
    walks.clear()
    _current(repo, **{GIT_HISTORY_VERSION_KEY: GIT_HISTORY_BACKFILL_REWALK})

    out = _invoke_update(repo)

    assert "attribution backfill" in out
    assert "Git history rules changed" not in out
    assert len(walks) == 1
    assert load_state(repo)[GIT_HISTORY_VERSION_KEY] == GIT_HISTORY_VERSION


def test_backfill_leaves_a_stale_stamp_alone(tmp_path) -> None:
    from repowise.cli.commands.attribution_cmd import _request_history_rewalk

    repo, _ = _indexed_repo(tmp_path)
    _current(repo, **{GIT_HISTORY_VERSION_KEY: GIT_HISTORY_VERSION - 1})

    assert _request_history_rewalk(repo)
    # The rules really did change, so the update still says that.
    assert load_state(repo)[GIT_HISTORY_VERSION_KEY] == GIT_HISTORY_VERSION - 1


def test_current_stamp_forces_no_walk(tmp_path, walks) -> None:
    repo, _ = _indexed_repo(tmp_path)
    walks.clear()
    _current(repo, **{GIT_HISTORY_VERSION_KEY: GIT_HISTORY_VERSION})

    assert UP_TO_DATE in _invoke_update(repo)
    assert not walks


def test_missing_stamp_is_stale() -> None:
    assert git_history_stale({})
    assert git_history_stale({GIT_HISTORY_VERSION_KEY: GIT_HISTORY_VERSION - 1})
    # v1 co-change records have no focused_frequency, so v1 must rewalk.
    assert git_history_stale({GIT_HISTORY_VERSION_KEY: 1})
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


def _repo_with_functions(tmp_path: Path) -> Path:
    """Index two files with enough history for FULL-tier function blame."""
    from repowise.core.pipeline.full_index import index_repo_full

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    for version in range(6):
        (repo / "a.py").write_text(
            f"def alpha():\n    return {version}\n\n\ndef beta():\n    return 2\n"
        )
        (repo / "b.py").write_text(f"def gamma():\n    return {version}\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", f"c{version}")
    head = _git(repo, "rev-parse", "HEAD")
    (repo / ".repowise").mkdir()
    asyncio.run(index_repo_full(repo))
    save_state(
        repo,
        {
            "last_sync_commit": head,
            "last_docs_commit": head,
            "docs_enabled": False,
            GIT_HISTORY_VERSION_KEY: GIT_HISTORY_VERSION,
            "health_analyzer_version": HEALTH_ANALYZER_VERSION,
        },
    )
    return repo


async def _with_session(repo: Path, work):
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.database import resolve_db_url

    engine = create_engine(resolve_db_url(repo))
    try:
        async with get_session(create_session_factory(engine)) as session:
            return await work(session)
    finally:
        await engine.dispose()


def _function_blame(repo: Path) -> dict[str, int]:
    """The persisted function modification counts."""
    from sqlalchemy import select

    from repowise.core.persistence.models import GitFunctionBlame

    async def read(session) -> dict[str, int]:
        rows = (await session.execute(select(GitFunctionBlame))).scalars()
        return {row.symbol_id: row.mod_count for row in rows}

    return asyncio.run(_with_session(repo, read))


def _empty_function_blame(repo: Path) -> None:
    from sqlalchemy import delete

    from repowise.core.persistence.models import GitFunctionBlame

    async def empty(session) -> None:
        await session.execute(delete(GitFunctionBlame))

    asyncio.run(_with_session(repo, empty))


@pytest.mark.parametrize("docs_changed", [False, True])
@pytest.mark.parametrize(
    ("analyzer_version", "git_tier", "refilled"),
    [
        pytest.param(HEALTH_ANALYZER_VERSION, None, True, id="full"),
        pytest.param(HEALTH_ANALYZER_VERSION - 1, None, True, id="full-with-rescore"),
        pytest.param(HEALTH_ANALYZER_VERSION, "essential", False, id="essential"),
    ],
)
def test_stale_stamp_refills_the_whole_function_blame(
    tmp_path, analyzer_version, git_tier, refilled, docs_changed
) -> None:
    """A full history refresh restores init's rows, including unchanged files."""
    repo = _repo_with_functions(tmp_path)
    indexed = _function_blame(repo)
    assert {sid.split("::")[0] for sid in indexed} == {"a.py", "b.py"}
    _empty_function_blame(repo)
    if docs_changed:
        (repo / "README.md").write_text("Documentation changed.\n")
        _git(repo, "add", "README.md")
        _git(repo, "commit", "-m", "update documentation")
    state = {**load_state(repo), "health_analyzer_version": analyzer_version}
    state.pop(GIT_HISTORY_VERSION_KEY)
    if git_tier is not None:
        state["git_tier"] = git_tier
    save_state(repo, state)

    assert "Git history rules changed" in _invoke_update(repo)
    assert _function_blame(repo) == (indexed if refilled else {})


@pytest.mark.parametrize("rescore_health", [False, True])
def test_workspace_stale_stamp_refills_the_whole_function_blame(tmp_path, rescore_health) -> None:
    from repowise.core.workspace.update import update_single_repo_index

    repo = _repo_with_functions(tmp_path)
    indexed = _function_blame(repo)
    _empty_function_blame(repo)
    state = load_state(repo)
    state.pop(GIT_HISTORY_VERSION_KEY)
    save_state(repo, state)

    result = asyncio.run(update_single_repo_index(repo, rescore_health=rescore_health))
    assert result.git_history_refreshed
    assert _function_blame(repo) == indexed


def test_ordinary_update_reblames_only_the_changed_file(tmp_path) -> None:
    """An ordinary update does not refill an unchanged file's missing rows."""
    repo = _repo_with_functions(tmp_path)
    _empty_function_blame(repo)
    (repo / "a.py").write_text("def alpha():\n    return 100\n\n\ndef beta():\n    return 2\n")
    _git(repo, "commit", "-am", "edit alpha")

    assert "Git history rules changed" not in _invoke_update(repo)
    assert {sid.split("::")[0] for sid in _function_blame(repo)} == {"a.py"}


def test_full_history_replaces_stale_function_rows_in_unchanged_files(tmp_path) -> None:
    """Refilling through an upsert would keep an obsolete symbol beside the new rows."""
    from sqlalchemy import select

    from repowise.core.persistence.models import GitFunctionBlame, Repository

    repo = _repo_with_functions(tmp_path)
    indexed = _function_blame(repo)

    async def seed(session):
        repo_id = (await session.execute(select(Repository.id))).scalar_one()
        session.add(GitFunctionBlame(
            repository_id=repo_id, symbol_id="b.py::obsolete", file_path="b.py",
            function_name="obsolete", start_line=1, end_line=2, line_count=2, mod_count=99,
        ))

    asyncio.run(_with_session(repo, seed))
    assert "b.py::obsolete" in _function_blame(repo)
    state = load_state(repo)
    state.pop(GIT_HISTORY_VERSION_KEY)
    save_state(repo, state)

    assert "Git history rules changed" in _invoke_update(repo)
    assert _function_blame(repo) == indexed
