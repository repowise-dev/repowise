"""A workspace update whose Git persist fails re-covers the range on the next run.

The sync pointer advances after a degraded run, so without a repair marker the
commits whose git rows never landed are skipped for good. The single-repo update
records ``pending_repair`` and widens to it; the workspace update writes the same
``state.json`` and must do both too.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout.strip()


def _state(repo: Path) -> dict:
    return json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))


def test_failed_git_persist_is_retried_by_the_next_workspace_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.cli.helpers import save_state
    from repowise.core.ingestion.git_indexer import GIT_HISTORY_VERSION, GIT_HISTORY_VERSION_KEY
    from repowise.core.persistence import crud
    from repowise.core.pipeline.full_index import index_repo_full
    from repowise.core.workspace.update import update_single_repo_index
    from repowise.core.workspace.update.workspace_pass import _repo_is_stale, _write_repo_state

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "c0")
    c0 = _git(repo, "rev-parse", "HEAD")
    asyncio.run(index_repo_full(repo))
    save_state(
        repo,
        {"last_sync_commit": c0, "docs_mode": "none", GIT_HISTORY_VERSION_KEY: GIT_HISTORY_VERSION},
    )

    (repo / "a.py").write_text("def a():\n    return 2\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "c1")
    c1 = _git(repo, "rev-parse", "HEAD")

    real_upsert = crud.upsert_git_metadata_bulk
    persisted: list[set[str]] = []

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("disk full")

    async def _spy(session, repo_id, rows, *args, **kwargs):
        persisted.append({row["file_path"] for row in rows})
        return await real_upsert(session, repo_id, rows, *args, **kwargs)

    monkeypatch.setattr(crud, "upsert_git_metadata_bulk", _boom)
    result = asyncio.run(update_single_repo_index(repo))
    assert result.updated
    assert result.pending_repair == {"from_commit": c0, "steps": ["Git persist"]}
    _write_repo_state(repo, "repo", c1, False, result)
    state = _state(repo)
    assert state["last_sync_commit"] == c1
    assert state["pending_repair"] == {"from_commit": c0, "steps": ["Git persist"]}

    # HEAD has not moved, but the repo still owes the failed range.
    assert asyncio.run(_repo_is_stale(repo, include_working_tree=False))[0]

    monkeypatch.setattr(crud, "upsert_git_metadata_bulk", _spy)
    result = asyncio.run(update_single_repo_index(repo))
    assert result.updated
    assert result.pending_repair is None
    assert any("a.py" in paths for paths in persisted)
    _write_repo_state(repo, "repo", c1, False, result)
    assert "pending_repair" not in _state(repo)


def test_marker_survives_a_run_that_could_not_check_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git failing inside the widening leaves the base unwidened: the marker stays."""
    from repowise.cli.helpers import save_state
    from repowise.core.ingestion.git_indexer import GIT_HISTORY_VERSION, GIT_HISTORY_VERSION_KEY
    from repowise.core.pipeline.full_index import index_repo_full
    from repowise.core.pipeline.incremental import repair
    from repowise.core.workspace.update import update_single_repo_index

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "c0")
    c0 = _git(repo, "rev-parse", "HEAD")
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "c1")
    c1 = _git(repo, "rev-parse", "HEAD")
    asyncio.run(index_repo_full(repo))
    marker = {"from_commit": c0, "steps": ["Git persist"]}
    save_state(
        repo,
        {
            "last_sync_commit": c1,
            "docs_mode": "none",
            "pending_repair": marker,
            GIT_HISTORY_VERSION_KEY: GIT_HISTORY_VERSION,
        },
    )
    # What resolve_repair_base returns when its git calls raise.
    monkeypatch.setattr(repair, "resolve_repair_base", lambda _p, _s, base, _h: (base, None))

    result = asyncio.run(update_single_repo_index(repo))
    assert result.updated
    assert result.pending_repair == marker
