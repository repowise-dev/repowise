"""Workspace updates route already-indexed repos through the incremental path.

``update_single_repo_index`` previously re-ran the full init pipeline for
every stale repo. Already-indexed repos (persisted ``last_sync_commit`` that
still resolves + existing ``wiki.db``) now take the incremental update path —
changed-files diff, partial analysis, upsert persistence — and inherit the
persisted state flags (``git_tier``, ``include_submodules``,
``include_nested_repos``). Never-indexed repos and incremental failures fall
back to the full pipeline.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from repowise.core.workspace.update import (
    commit_exists,
    get_head_commit,
    read_repo_state,
    update_single_repo_index,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)
    return result.stdout.strip()


def _make_git_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.py").write_text("def alpha():\n    return 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _mark_indexed(repo: Path, commit: str, **extra_state) -> None:
    """Simulate a prior index: state.json with last_sync_commit + wiki.db."""
    state_dir = repo / ".repowise"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "state.json").write_text(
        json.dumps({"last_sync_commit": commit, **extra_state}), encoding="utf-8"
    )
    (state_dir / "wiki.db").touch()


def _write_repo_settings(repo: Path, settings: dict) -> None:
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.database import resolve_db_url

    async def _seed() -> None:
        engine = create_engine(resolve_db_url(repo))
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            await upsert_repository(
                session,
                name=repo.name,
                local_path=str(repo),
                settings=settings,
            )
        await engine.dispose()

    asyncio.run(_seed())


def _add_commit(repo: Path, filename: str = "b.py") -> str:
    (repo / filename).write_text("def beta():\n    return 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", f"add {filename}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def forbid_full_pipeline(monkeypatch):
    """Make the full pipeline unreachable so tests prove the incremental
    path was taken."""
    import repowise.core.pipeline as pipeline_pkg

    async def _boom(*args, **kwargs):  # pragma: no cover - failure path
        raise AssertionError("full pipeline must not run for indexed repos")

    monkeypatch.setattr(pipeline_pkg, "run_pipeline", _boom)


@pytest.fixture
def stub_full_pipeline(monkeypatch):
    """Replace the full pipeline + its persistence with recording stubs."""
    import repowise.core.pipeline as pipeline_pkg
    import repowise.core.pipeline.persist as persist_mod

    calls: list[dict] = []

    class _FakeResult:
        repo_name = "stub"
        file_count = 7
        symbol_count = 9

    async def _fake_pipeline(repo_path, **kwargs):
        calls.append({"repo_path": repo_path, **kwargs})
        return _FakeResult()

    async def _fake_persist(result, session, repo_id):
        return None

    monkeypatch.setattr(pipeline_pkg, "run_pipeline", _fake_pipeline)
    monkeypatch.setattr(persist_mod, "persist_pipeline_result", _fake_persist)
    return calls


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_indexed_repo_takes_incremental_path(tmp_path, forbid_full_pipeline):
    """An already-indexed repo with new commits updates incrementally —
    the full pipeline is never invoked."""
    repo = _make_git_repo(tmp_path)
    base = get_head_commit(repo)
    _mark_indexed(repo, base)
    _add_commit(repo, "b.py")

    result = asyncio.run(update_single_repo_index(repo))

    assert result.error is None
    assert result.updated is True
    assert result.file_count >= 2  # a.py + b.py (+ any traversed metadata files)
    assert result.symbol_count == 2  # alpha + beta
    # The upsert path initialized the schema in the pre-existing wiki.db.
    assert (repo / ".repowise" / "wiki.db").stat().st_size > 0


def test_no_relevant_changes_still_reports_updated(tmp_path, forbid_full_pipeline):
    """An empty commit produces no file diffs; the repo still reports
    updated=True so the caller bumps last_sync_commit instead of
    re-diffing forever."""
    repo = _make_git_repo(tmp_path)
    base = get_head_commit(repo)
    _mark_indexed(repo, base)
    _git(repo, "commit", "--allow-empty", "-m", "empty")

    result = asyncio.run(update_single_repo_index(repo))

    assert result.updated is True
    assert result.file_count == 0


def test_deleted_file_falls_back_to_full_pipeline(tmp_path, stub_full_pipeline):
    """Incremental persistence is upsert-only — it can't prune rows for
    removed paths. A diff containing deletions must run the full pipeline
    (delete-then-insert) so stale graph/health rows are cleaned up."""
    repo = _make_git_repo(tmp_path)
    _add_commit(repo, "b.py")
    base = get_head_commit(repo)
    _mark_indexed(repo, base)
    (repo / "b.py").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "remove b.py")

    result = asyncio.run(update_single_repo_index(repo))

    assert len(stub_full_pipeline) == 1
    assert result.updated is True


def test_renamed_file_falls_back_to_full_pipeline(tmp_path, stub_full_pipeline):
    """Renames leave the old path behind in upsert-only persistence —
    same prune requirement as deletions."""
    repo = _make_git_repo(tmp_path)
    base = get_head_commit(repo)
    _mark_indexed(repo, base)
    _git(repo, "mv", "a.py", "renamed.py")
    _git(repo, "commit", "-m", "rename a.py")

    result = asyncio.run(update_single_repo_index(repo))

    assert len(stub_full_pipeline) == 1
    assert result.updated is True


def test_never_indexed_repo_runs_full_pipeline(tmp_path, stub_full_pipeline):
    repo = _make_git_repo(tmp_path)

    result = asyncio.run(update_single_repo_index(repo))

    assert len(stub_full_pipeline) == 1
    assert result.updated is True
    assert result.file_count == 7


def test_unresolvable_base_commit_falls_back_to_full_pipeline(tmp_path, stub_full_pipeline):
    """A last_sync_commit that no longer resolves (rebase, gc) must not be
    treated as 'no changes' — it falls back to the full pipeline."""
    repo = _make_git_repo(tmp_path)
    _mark_indexed(repo, "deadbeef" * 5)
    _add_commit(repo, "b.py")

    result = asyncio.run(update_single_repo_index(repo))

    assert len(stub_full_pipeline) == 1
    assert result.updated is True


def test_incremental_failure_falls_back_to_full_pipeline(tmp_path, stub_full_pipeline, monkeypatch):
    import repowise.core.pipeline.incremental as incremental_mod

    repo = _make_git_repo(tmp_path)
    base = get_head_commit(repo)
    _mark_indexed(repo, base)
    _add_commit(repo, "b.py")

    async def _boom(*args, **kwargs):
        raise RuntimeError("incremental exploded")

    monkeypatch.setattr(incremental_mod, "rebuild_graph_and_git", _boom)

    result = asyncio.run(update_single_repo_index(repo))

    assert len(stub_full_pipeline) == 1
    assert result.updated is True
    assert result.error is None


def test_incremental_threads_persisted_state_flags(tmp_path, forbid_full_pipeline, monkeypatch):
    """git_tier / include_submodules / include_nested_repos from state.json
    reach the incremental rebuild."""
    import repowise.core.pipeline.incremental as incremental_mod

    repo = _make_git_repo(tmp_path)
    base = get_head_commit(repo)
    _mark_indexed(
        repo,
        base,
        git_tier="essential",
        include_submodules=True,
        include_nested_repos=True,
    )
    _add_commit(repo, "b.py")

    captured: dict = {}

    async def _fake_rebuild(repo_path, file_diffs, cfg, exclude_patterns, **kwargs):
        captured.update(kwargs)
        return [], {}, None, None, 0, {}

    def _fake_analysis(*args, **kwargs):
        return None, None

    async def _fake_persist(*args, **kwargs):
        return None

    monkeypatch.setattr(incremental_mod, "rebuild_graph_and_git", _fake_rebuild)
    monkeypatch.setattr(incremental_mod, "run_partial_analysis", _fake_analysis)
    monkeypatch.setattr(incremental_mod, "persist_incremental_index", _fake_persist)

    result = asyncio.run(update_single_repo_index(repo))

    assert result.updated is True
    assert captured["git_tier"] == "essential"
    assert captured["include_submodules"] is True
    assert captured["include_nested_repos"] is True


def test_incremental_merges_repo_settings_excludes(tmp_path, forbid_full_pipeline, monkeypatch):
    import repowise.core.pipeline.incremental as incremental_mod

    repo = _make_git_repo(tmp_path)
    base = get_head_commit(repo)
    _mark_indexed(repo, base)
    _write_repo_settings(repo, {"exclude_patterns": ["tools/"]})
    _add_commit(repo, "b.py")

    captured: dict = {}

    async def _fake_rebuild(repo_path, file_diffs, cfg, exclude_patterns, **kwargs):
        captured["exclude_patterns"] = exclude_patterns
        return [], {}, None, None, 0, {}

    def _fake_analysis(*args, **kwargs):
        return None, None

    async def _fake_persist(*args, **kwargs):
        return None

    monkeypatch.setattr(incremental_mod, "rebuild_graph_and_git", _fake_rebuild)
    monkeypatch.setattr(incremental_mod, "run_partial_analysis", _fake_analysis)
    monkeypatch.setattr(incremental_mod, "persist_incremental_index", _fake_persist)

    result = asyncio.run(update_single_repo_index(repo))

    assert result.updated is True
    assert captured["exclude_patterns"] == ["tools/"]


def test_full_pipeline_merges_repo_settings_excludes(tmp_path, stub_full_pipeline):
    repo = _make_git_repo(tmp_path)
    _mark_indexed(repo, "deadbeef" * 5)
    _write_repo_settings(repo, {"exclude_patterns": ["tools/"]})

    result = asyncio.run(update_single_repo_index(repo))

    assert len(stub_full_pipeline) == 1
    assert result.updated is True
    assert stub_full_pipeline[0]["exclude_patterns"] == ["tools/"]


# ---------------------------------------------------------------------------
# Helpers under test
# ---------------------------------------------------------------------------


def test_commit_exists(tmp_path):
    repo = _make_git_repo(tmp_path)
    head = get_head_commit(repo)
    assert commit_exists(repo, head) is True
    assert commit_exists(repo, "deadbeef" * 5) is False


def test_commit_exists_non_git_dir(tmp_path):
    assert commit_exists(tmp_path, "deadbeef" * 5) is False


def test_read_repo_state(tmp_path):
    repo = tmp_path / "r"
    (repo / ".repowise").mkdir(parents=True)
    (repo / ".repowise" / "state.json").write_text(
        json.dumps({"last_sync_commit": "abc", "include_submodules": True})
    )
    state = read_repo_state(repo)
    assert state["last_sync_commit"] == "abc"
    assert state["include_submodules"] is True


def test_read_repo_state_missing_or_malformed(tmp_path):
    assert read_repo_state(tmp_path) == {}
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "state.json").write_text("not json{")
    assert read_repo_state(tmp_path) == {}
