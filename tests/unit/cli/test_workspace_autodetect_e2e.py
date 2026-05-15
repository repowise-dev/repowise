"""End-to-end-ish tests for the workspace auto-detect rewiring.

These run real Click commands through :class:`CliRunner` so we catch
regressions where the auto-detect helper was wired up incorrectly (e.g.
stray ``.repowise/`` directories left behind, wrong workspace path
resolved, error messages that hide the real fix).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands.status_cmd import status_command
from repowise.cli.commands.update_cmd import update_command


def _build_workspace(root: Path) -> None:
    backend = root / "backend"
    frontend = root / "frontend"
    (backend / ".git").mkdir(parents=True)
    (frontend / ".git").mkdir(parents=True)
    (backend / ".repowise").mkdir()
    (backend / ".repowise" / "state.json").write_text(
        json.dumps({"last_sync_commit": "abc123", "docs_enabled": True}),
    )
    (root / ".repowise-workspace.yaml").write_text(
        "version: 1\n"
        "default_repo: backend\n"
        "repos:\n"
        "  - path: backend\n"
        "    alias: backend\n"
        "    is_primary: true\n"
        "  - path: frontend\n"
        "    alias: frontend\n"
    )


def test_update_from_workspace_root_does_not_create_stray_repowise(
    tmp_path: Path, monkeypatch
):
    """Regression: running ``repowise update`` from a workspace root used
    to leave a stray ``<workspace>/.repowise/`` behind before erroring."""
    _build_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    # Workspace mode auto-routes — the actual update call would try to
    # spawn a real pipeline, so we intercept _workspace_update.
    called = {}

    def _fake_workspace_update(target, *, dry_run, agents_md=None):
        called["target"] = target
        called["dry_run"] = dry_run
        called["agents_md"] = agents_md

    monkeypatch.setattr(
        "repowise.cli.commands.update_cmd._workspace_update",
        _fake_workspace_update,
    )

    result = runner.invoke(update_command, [], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert called["target"].mode == "workspace"
    assert called["target"].ws_root.resolve() == tmp_path.resolve()
    # Critical: no stray .repowise/ directory created at the workspace root.
    assert not (tmp_path / ".repowise").exists()


def test_update_from_child_repo_stays_single(tmp_path: Path, monkeypatch):
    _build_workspace(tmp_path)
    monkeypatch.chdir(tmp_path / "backend")

    runner = CliRunner()
    called = {}

    def _fake_workspace_update(*args, **kwargs):
        called["workspace"] = True

    monkeypatch.setattr(
        "repowise.cli.commands.update_cmd._workspace_update",
        _fake_workspace_update,
    )

    # The full single-repo path tries to do real work; we monkeypatch the
    # downstream pipeline so the command can short-circuit. Easiest: stub
    # the heavy imports by making get_head_commit return the same SHA so
    # the "Already up to date" branch fires.
    monkeypatch.setattr(
        "repowise.cli.commands.update_cmd.get_head_commit",
        lambda _p: "abc123",
    )

    result = runner.invoke(update_command, [], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert "workspace" not in called
    assert "Already up to date" in result.output or "single-repo" in result.output


def test_update_with_no_workspace_flag_overrides_autodetect(
    tmp_path: Path, monkeypatch
):
    """``--no-workspace`` from a workspace root should run the single-repo
    code path on the workspace root and emit the helpful workspace hint."""
    _build_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        update_command,
        ["--no-workspace"],
        catch_exceptions=False,
    )
    # Workspace root has no .repowise/state.json so single-repo update
    # rightly errors — but the message must guide the user to --workspace.
    assert result.exit_code != 0
    assert "--workspace" in result.output


def test_status_autodetects_workspace_with_docs_summary(tmp_path: Path, monkeypatch):
    _build_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(status_command, [], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    # The honest docs summary should mention the un-doc'd repo.
    assert "backend" in result.output
    assert "frontend" in result.output
