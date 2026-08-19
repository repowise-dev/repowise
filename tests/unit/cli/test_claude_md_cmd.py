"""Tests for the ``generate-claude-md`` command boundary."""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from repowise.cli.commands import claude_md_cmd
from repowise.cli.main import cli


@pytest.mark.parametrize(
    ("extra_args", "expected_verbose"),
    [([], False), (["--verbose"], True)],
)
def test_generate_claude_md_configures_logging_before_target_resolution(
    monkeypatch: pytest.MonkeyPatch,
    extra_args: list[str],
    expected_verbose: bool,
) -> None:
    events: list[tuple[str, bool | None]] = []

    def fake_configure_cli_logging(*, verbose: bool = False) -> None:
        events.append(("logging", verbose))

    def fake_resolve_command_target(**_kwargs: object) -> None:
        events.append(("target", None))
        raise click.ClickException("stop after target resolution")

    monkeypatch.setattr(claude_md_cmd, "configure_cli_logging", fake_configure_cli_logging)
    monkeypatch.setattr(claude_md_cmd, "resolve_command_target", fake_resolve_command_target)

    result = CliRunner().invoke(cli, ["generate-claude-md", *extra_args])

    assert result.exit_code == 1
    assert "stop after target resolution" in result.output
    assert events == [("logging", expected_verbose), ("target", None)]
