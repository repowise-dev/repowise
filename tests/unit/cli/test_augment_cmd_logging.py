"""Tests for logging setup at the ``augment`` command boundary."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from repowise.cli.commands.augment_cmd import command as augment_cmd
from repowise.cli.main import cli


@pytest.mark.parametrize(
    ("extra_args", "expected_verbose"),
    [([], False), (["--verbose"], True), (["-v"], True)],
)
def test_augment_configures_logging_before_hook_work(
    monkeypatch: pytest.MonkeyPatch,
    extra_args: list[str],
    expected_verbose: bool,
) -> None:
    events: list[tuple[str, object]] = []

    def fake_configure_cli_logging(*, verbose: bool = False) -> None:
        events.append(("logging", verbose))

    def fake_run_augment(*, client: str | None = None) -> None:
        events.append(("run", client))

    monkeypatch.setattr(augment_cmd, "configure_cli_logging", fake_configure_cli_logging)
    monkeypatch.setattr(augment_cmd, "_run_augment", fake_run_augment)

    result = CliRunner().invoke(cli, ["augment", *extra_args])

    assert result.exit_code == 0
    assert events == [("logging", expected_verbose), ("run", None)]


def test_augment_help_lists_verbose() -> None:
    result = CliRunner().invoke(cli, ["augment", "--help"])

    assert result.exit_code == 0
    assert "--verbose" in result.output
