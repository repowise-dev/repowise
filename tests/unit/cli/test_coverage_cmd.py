"""Tests for the ``coverage add`` command boundary."""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from repowise.cli.commands import coverage_cmd
from repowise.cli.main import cli


@pytest.mark.parametrize(
    ("extra_args", "expected_verbose"),
    [([], False), (["--verbose"], True)],
)
def test_coverage_add_configures_logging_before_repo_resolution(
    monkeypatch: pytest.MonkeyPatch,
    extra_args: list[str],
    expected_verbose: bool,
) -> None:
    events: list[tuple[str, bool | None]] = []

    def fake_configure_cli_logging(*, verbose: bool = False) -> None:
        events.append(("logging", verbose))

    def fake_resolve_coverage_repo(_path: str | None) -> None:
        events.append(("repo", None))
        raise click.ClickException("stop after repo resolution")

    monkeypatch.setattr(coverage_cmd, "configure_cli_logging", fake_configure_cli_logging)
    monkeypatch.setattr(coverage_cmd, "_resolve_coverage_repo", fake_resolve_coverage_repo)

    result = CliRunner().invoke(cli, ["coverage", "add", *extra_args])

    assert result.exit_code == 1
    assert "stop after repo resolution" in result.output
    assert events == [("logging", expected_verbose), ("repo", None)]


def test_coverage_add_help_lists_verbose() -> None:
    result = CliRunner().invoke(cli, ["coverage", "add", "--help"])

    assert result.exit_code == 0
    assert "--verbose" in result.output
