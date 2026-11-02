"""Tests for logging setup at workspace command boundaries."""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from repowise.cli.commands.workspace_cmd import group as workspace_cmd_group
from repowise.cli.main import cli


@pytest.mark.parametrize(
    ("extra_args", "expected_verbose"),
    [([], False), (["--verbose"], True)],
)
def test_workspace_add_configures_logging_before_workspace_resolution(
    monkeypatch: pytest.MonkeyPatch,
    extra_args: list[str],
    expected_verbose: bool,
) -> None:
    events: list[tuple[str, bool | None]] = []

    def fake_configure_cli_logging(*, verbose: bool = False) -> None:
        events.append(("logging", verbose))

    def fake_require_workspace(*_args: object, **_kwargs: object) -> None:
        events.append(("workspace", None))
        raise click.ClickException("stop after workspace resolution")

    monkeypatch.setattr(workspace_cmd_group, "configure_cli_logging", fake_configure_cli_logging)
    monkeypatch.setattr(workspace_cmd_group, "_require_workspace", fake_require_workspace)

    result = CliRunner().invoke(cli, ["workspace", "add", ".", *extra_args])

    assert result.exit_code == 1
    assert "stop after workspace resolution" in result.output
    assert events == [("logging", expected_verbose), ("workspace", None)]


@pytest.mark.parametrize(
    ("extra_args", "expected_verbose"),
    [([], False), (["--verbose"], True)],
)
def test_workspace_scan_configures_logging_before_path_resolution(
    monkeypatch: pytest.MonkeyPatch,
    extra_args: list[str],
    expected_verbose: bool,
) -> None:
    events: list[tuple[str, bool | None]] = []

    def fake_configure_cli_logging(*, verbose: bool = False) -> None:
        events.append(("logging", verbose))

    def fake_resolve_repo_path(_path: str | None) -> None:
        events.append(("path", None))
        raise click.ClickException("stop after path resolution")

    monkeypatch.setattr(workspace_cmd_group, "configure_cli_logging", fake_configure_cli_logging)
    monkeypatch.setattr(workspace_cmd_group, "resolve_repo_path", fake_resolve_repo_path)

    result = CliRunner().invoke(cli, ["workspace", "scan", *extra_args])

    assert result.exit_code == 1
    assert "stop after path resolution" in result.output
    assert events == [("logging", expected_verbose), ("path", None)]
