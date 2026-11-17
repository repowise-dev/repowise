"""`repowise workspace update`, and the up-to-date path re-running edited settings."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from repowise.cli.commands.update_cmd import workspace as ws_update
from repowise.cli.main import cli
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig


def test_workspace_update_runs_update_in_workspace_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from repowise.cli.commands.update_cmd import command

    calls: list[dict] = []
    monkeypatch.setattr(command, "run_update", lambda **kw: calls.append(kw))

    result = CliRunner().invoke(cli, ["workspace", "update", "--index-only", "--repo", "api"])

    assert result.exit_code == 0, result.output
    assert calls[0]["workspace"] is True
    assert calls[0]["index_only"] is True
    assert calls[0]["repo_alias"] == "api"


def test_workspace_update_rejects_an_unknown_option() -> None:
    result = CliRunner().invoke(cli, ["workspace", "update", "--bogus"])
    assert result.exit_code == 2
    assert "--bogus" in result.output


@pytest.mark.parametrize("changed", [True, False])
def test_up_to_date_workspace_reruns_analysis_only_when_settings_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: bool
) -> None:
    from repowise.core.workspace import update

    ws_config = WorkspaceConfig(
        repos=[RepoEntry(path="api", alias="api"), RepoEntry(path="web", alias="web")]
    )
    hooks = AsyncMock()
    monkeypatch.setattr(update, "run_cross_repo_hooks", hooks)
    monkeypatch.setattr(update, "analysis_config_changed", lambda *_: changed)

    ws_update._rerun_analysis_if_config_changed(tmp_path, ws_config, datetime.now(UTC))

    assert hooks.await_count == (1 if changed else 0)
