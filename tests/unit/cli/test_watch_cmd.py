"""Tests for the ``watch`` command boundary."""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from repowise.cli.commands import watch_cmd
from repowise.cli.helpers import CommandTarget
from repowise.cli.main import cli


@pytest.mark.parametrize(
    ("extra_args", "expected_verbose"),
    [([], False), (["--verbose"], True)],
)
def test_watch_configures_logging_before_target_resolution(
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

    monkeypatch.setattr(watch_cmd, "configure_cli_logging", fake_configure_cli_logging)
    monkeypatch.setattr(watch_cmd, "resolve_command_target", fake_resolve_command_target)

    result = CliRunner().invoke(cli, ["watch", *extra_args])

    assert result.exit_code == 1
    assert "stop after target resolution" in result.output
    assert events == [("logging", expected_verbose), ("target", None)]


@pytest.mark.parametrize(
    ("extra_args", "expected_verbose"),
    [([], False), (["--verbose"], True)],
)
def test_watch_forwards_logging_mode_to_single_repo_updates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    extra_args: list[str],
    expected_verbose: bool,
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(watch_cmd, "configure_cli_logging", lambda **_kwargs: None)
    monkeypatch.setattr(
        watch_cmd,
        "resolve_command_target",
        lambda **_kwargs: CommandTarget(mode="single", repo_path=tmp_path),
    )
    monkeypatch.setattr(
        watch_cmd,
        "_watch_single_repo",
        lambda *args: calls.append(args),
    )

    result = CliRunner().invoke(
        cli,
        [
            "watch",
            "--provider",
            "demo-provider",
            "--model",
            "demo-model",
            "--debounce",
            "750",
            *extra_args,
        ],
    )

    assert result.exit_code == 0
    assert calls == [(tmp_path, "demo-provider", "demo-model", 750, expected_verbose)]
