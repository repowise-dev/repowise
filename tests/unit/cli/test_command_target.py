"""Unit tests for ``resolve_command_target`` — the workspace/single-repo router.

Covers every decision branch listed in the docstring of
``repowise.cli.helpers.resolve_command_target`` so the workspace
auto-detection contract stays stable across refactors.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import click
import pytest

from repowise.cli.helpers import (
    CommandTarget,
    WorkspaceNotFound,
    resolve_command_target,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_workspace(tmp_path: Path) -> Path:
    """Build a workspace root with two child repos.

    Layout:
        tmp/
          .repowise-workspace.yaml
          backend/.git
          backend/.repowise/state.json
          frontend/.git
    """
    ws_root = tmp_path
    backend = ws_root / "backend"
    frontend = ws_root / "frontend"
    (backend / ".git").mkdir(parents=True)
    (frontend / ".git").mkdir(parents=True)

    # Backend has been indexed (has its own state.json) — frontend has not.
    (backend / ".repowise").mkdir()
    (backend / ".repowise" / "state.json").write_text(
        json.dumps({"last_sync_commit": "abc123", "docs_enabled": True}),
        encoding="utf-8",
    )

    (ws_root / ".repowise-workspace.yaml").write_text(
        "version: 1\n"
        "default_repo: backend\n"
        "repos:\n"
        "  - path: backend\n"
        "    alias: backend\n"
        "    is_primary: true\n"
        "  - path: frontend\n"
        "    alias: frontend\n",
        encoding="utf-8",
    )
    return ws_root


@pytest.fixture
def chdir(monkeypatch):
    """Convenience: chdir helper that uses monkeypatch.chdir."""

    def _chdir(path: Path) -> None:
        monkeypatch.chdir(path)

    return _chdir


# ---------------------------------------------------------------------------
# Rule 1: --no-workspace forces single-repo mode
# ---------------------------------------------------------------------------


def test_no_workspace_flag_forces_single(fake_workspace, chdir):
    chdir(fake_workspace)
    target = resolve_command_target(no_workspace_flag=True)
    assert target.mode == "single"
    assert target.repo_path == fake_workspace
    assert target.auto_detected is False


def test_workspace_and_no_workspace_conflict(fake_workspace, chdir):
    chdir(fake_workspace)
    with pytest.raises(click.UsageError):
        resolve_command_target(workspace_flag=True, no_workspace_flag=True)


def test_repo_with_no_workspace_conflict(fake_workspace, chdir):
    chdir(fake_workspace)
    with pytest.raises(click.UsageError):
        resolve_command_target(repo_alias="backend", no_workspace_flag=True)


# ---------------------------------------------------------------------------
# Rule 2: --workspace / --repo
# ---------------------------------------------------------------------------


def test_workspace_flag_loads_config(fake_workspace, chdir):
    chdir(fake_workspace)
    target = resolve_command_target(workspace_flag=True)
    assert target.mode == "workspace"
    assert target.ws_root == fake_workspace
    assert target.ws_config is not None
    assert target.auto_detected is False
    assert target.repo_filter is None


def test_repo_alias_implies_workspace(fake_workspace, chdir):
    chdir(fake_workspace)
    target = resolve_command_target(repo_alias="backend")
    assert target.mode == "workspace"
    assert target.repo_filter == "backend"


def test_workspace_flag_raises_when_no_workspace(tmp_path, chdir):
    chdir(tmp_path)
    with pytest.raises(WorkspaceNotFound):
        resolve_command_target(workspace_flag=True)


def test_unknown_repo_alias_raises(fake_workspace, chdir):
    chdir(fake_workspace)
    with pytest.raises(click.UsageError):
        resolve_command_target(repo_alias="bogus")


# ---------------------------------------------------------------------------
# Rule 3: explicit path argument
# ---------------------------------------------------------------------------


def test_explicit_path_to_workspace_root_auto_promotes(fake_workspace, tmp_path):
    # Run from somewhere unrelated, but point path at the workspace root.
    os.chdir(tmp_path)
    target = resolve_command_target(path=str(fake_workspace))
    assert target.mode == "workspace"
    assert target.ws_root == fake_workspace
    assert target.auto_detected is True


def test_explicit_path_to_child_repo_stays_single(fake_workspace):
    target = resolve_command_target(path=str(fake_workspace / "backend"))
    assert target.mode == "single"
    assert target.repo_path == fake_workspace / "backend"
    # Workspace context surfaced for downstream commands' notice.
    assert target.ws_root == fake_workspace


# ---------------------------------------------------------------------------
# Rule 4: no path, no flags — auto-detect
# ---------------------------------------------------------------------------


def test_cwd_is_workspace_root_auto_routes(fake_workspace, chdir):
    chdir(fake_workspace)
    target = resolve_command_target()
    assert target.mode == "workspace"
    assert target.auto_detected is True
    assert "cwd is the workspace root" in target.reason


def test_cwd_indexed_child_repo_stays_single(fake_workspace, chdir):
    """If cwd has its own state.json, cd-into-repo wins over workspace."""
    chdir(fake_workspace / "backend")
    target = resolve_command_target()
    assert target.mode == "single"
    assert target.repo_path == fake_workspace / "backend"
    # Workspace context retained for transparency notice.
    assert target.ws_root == fake_workspace


def test_cwd_unindexed_child_routes_to_workspace(fake_workspace, chdir):
    """If cwd is under a workspace but has no .repowise/state.json, the
    workspace is the more useful target."""
    chdir(fake_workspace / "frontend")
    target = resolve_command_target()
    assert target.mode == "workspace"
    assert target.auto_detected is True


def test_plain_dir_stays_single(tmp_path, chdir):
    plain = tmp_path / "plain"
    plain.mkdir()
    chdir(plain)
    target = resolve_command_target()
    assert target.mode == "single"
    assert target.repo_path == plain
    assert target.ws_root is None
    assert target.auto_detected is False


# ---------------------------------------------------------------------------
# CommandTarget helpers
# ---------------------------------------------------------------------------


def test_primary_path_resolution(fake_workspace, chdir):
    chdir(fake_workspace)
    target = resolve_command_target()
    assert target.primary_path() == fake_workspace / "backend"


def test_resolve_repo_alias(fake_workspace, chdir):
    chdir(fake_workspace)
    target = resolve_command_target()
    assert target.resolve_repo_alias("frontend") == fake_workspace / "frontend"
    assert target.resolve_repo_alias("unknown") is None


def test_is_workspace_property(fake_workspace, chdir):
    chdir(fake_workspace)
    assert resolve_command_target().is_workspace is True
    chdir(fake_workspace / "backend")
    assert resolve_command_target().is_workspace is False
