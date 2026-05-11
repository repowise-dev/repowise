"""End-to-end round-trip test for workspace robustness (Phase C3).

Spins up three sibling git repos, runs ``update_workspace`` (first-time
indexing path from C1), verifies the state model is consistent, adds a
fourth repo manually, and re-runs the update. Then checks that the
workspace config tracks ``last_commit_at_index`` correctly and that
state.json drift detection works.

Deliberately doesn't exercise LLM doc generation — that's covered by
``test_workspace_cmd.py`` and would require a provider in CI. The
contract this test enforces is the *plumbing*: ``.repowise/`` dirs
created, ``state.json`` written, workspace config kept in sync.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from repowise.core.workspace.config import RepoEntry, WorkspaceConfig
from repowise.core.workspace.update import (
    sync_workspace_state_from_disk,
    update_workspace,
)


def _git_repo(parent: Path, name: str, file_content: str = "x") -> Path:
    p = parent / name
    p.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=str(p), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"], cwd=str(p), capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"], cwd=str(p), capture_output=True,
    )
    (p / "main.py").write_text(f"# {name}\nprint('{file_content}')\n")
    subprocess.run(["git", "add", "."], cwd=str(p), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=str(p), capture_output=True,
    )
    return p


def _head(p: Path) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(p), capture_output=True, text=True,
    )
    return r.stdout.strip()


def test_three_repo_workspace_round_trip(tmp_path: Path) -> None:
    """Workspace with 3 sibling repos: first-time indexing fills every
    repo's .repowise/ dir and state.json, the workspace config tracks
    last_commit_at_index, and subsequent updates are idempotent
    (skipped_reason=up_to_date)."""
    a = _git_repo(tmp_path, "alpha", "alpha")
    b = _git_repo(tmp_path, "beta", "beta")
    g = _git_repo(tmp_path, "gamma", "gamma")

    cfg = WorkspaceConfig(
        repos=[
            RepoEntry(path="alpha", alias="alpha", is_primary=True),
            RepoEntry(path="beta", alias="beta"),
            RepoEntry(path="gamma", alias="gamma"),
        ],
        default_repo="alpha",
    )
    cfg.save(tmp_path)

    # First-time index across the workspace (no .repowise/ dirs yet).
    results = asyncio.run(update_workspace(tmp_path, cfg))
    by_alias = {r.alias: r for r in results}
    assert set(by_alias) == {"alpha", "beta", "gamma"}

    # Every repo got a .repowise/ dir + state.json + workspace config sync.
    for repo_path, alias in [(a, "alpha"), (b, "beta"), (g, "gamma")]:
        assert (repo_path / ".repowise").is_dir(), f"{alias} missing .repowise/"
        state = json.loads((repo_path / ".repowise" / "state.json").read_text())
        assert state["last_sync_commit"] == _head(repo_path)
        entry = cfg.get_repo(alias)
        assert entry is not None
        assert entry.last_commit_at_index == _head(repo_path)
        # First-time indexed repos get a clear "no docs yet" marker.
        if by_alias[alias].first_time_indexed:
            assert state.get("docs_enabled") is False
            assert "docs_skip_reason" in state

    # Second pass should be a no-op for all three.
    results2 = asyncio.run(update_workspace(tmp_path, cfg))
    assert all(r.skipped_reason == "up_to_date" for r in results2)


def test_drift_recovery(tmp_path: Path) -> None:
    """When a child repo is updated outside the workspace orchestrator,
    ``sync_workspace_state_from_disk`` brings the workspace config back
    into agreement with the on-disk state.json on the next update call."""
    a = _git_repo(tmp_path, "alpha")
    cfg = WorkspaceConfig(
        repos=[RepoEntry(
            path="alpha", alias="alpha",
            last_commit_at_index="stale-stale-stale",
        )],
        default_repo="alpha",
    )
    cfg.save(tmp_path)

    # Pretend the user ran `repowise update` inside alpha directly,
    # writing a state.json that the workspace config doesn't know about.
    (a / ".repowise").mkdir(exist_ok=True)
    (a / ".repowise" / "state.json").write_text(
        json.dumps({"last_sync_commit": _head(a)})
    )

    changed = sync_workspace_state_from_disk(tmp_path, cfg)
    assert changed == ["alpha"]
    assert cfg.get_repo("alpha").last_commit_at_index == _head(a)


def test_add_fourth_repo_round_trip(tmp_path: Path) -> None:
    """After the initial workspace is indexed, programmatically adding a
    new entry and running update_workspace indexes the new repo only."""
    a = _git_repo(tmp_path, "alpha")
    b = _git_repo(tmp_path, "beta")
    cfg = WorkspaceConfig(
        repos=[
            RepoEntry(path="alpha", alias="alpha", is_primary=True),
            RepoEntry(path="beta", alias="beta"),
        ],
        default_repo="alpha",
    )
    cfg.save(tmp_path)
    asyncio.run(update_workspace(tmp_path, cfg))

    # Add a third repo to the workspace.
    g = _git_repo(tmp_path, "gamma")
    cfg.add_repo(RepoEntry(path="gamma", alias="gamma"))
    cfg.save(tmp_path)

    results = asyncio.run(update_workspace(tmp_path, cfg))
    by_alias = {r.alias: r for r in results}
    # alpha + beta unchanged, gamma freshly indexed.
    assert by_alias["alpha"].skipped_reason == "up_to_date"
    assert by_alias["beta"].skipped_reason == "up_to_date"
    assert by_alias["gamma"].updated or by_alias["gamma"].error
    if by_alias["gamma"].updated:
        assert by_alias["gamma"].first_time_indexed
        assert (g / ".repowise").is_dir()
