"""`repowise update` exit codes: 0 ready, 2 finished with gaps, 130 interrupted."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from repowise.cli.commands.update_cmd.command import UpdateOutcome
from repowise.cli.exit_codes import EXIT_DEGRADED, EXIT_INTERRUPTED
from repowise.cli.main import cli


def test_update_help_names_exit_codes():
    result = CliRunner().invoke(cli, ["update", "--help"])
    assert result.exit_code == 0
    assert "Exit codes: 0 ready, 2 finished with gaps" in " ".join(result.output.split())


def test_update_clean_run_exits_0(tmp_path):
    with patch(
        "repowise.cli.commands.update_cmd.command.run_update",
        return_value=UpdateOutcome.NOOP,
    ):
        result = CliRunner().invoke(cli, ["update", str(tmp_path)])
    assert result.exit_code == 0, result.output


def test_update_degraded_step_exits_2(tmp_path):
    def _fake_run_update(*args, degraded_out=None, **kwargs):
        if degraded_out is not None:
            degraded_out.append("Knowledge-graph export: [Errno 13] Permission denied")
        return UpdateOutcome.REGENERATED

    with patch("repowise.cli.commands.update_cmd.command.run_update", side_effect=_fake_run_update):
        result = CliRunner().invoke(cli, ["update", str(tmp_path)])

    assert result.exit_code == EXIT_DEGRADED, result.output
    assert "Finished with gaps: Knowledge-graph export: [Errno 13] Permission denied." in result.output


def test_update_keyboard_interrupt_exits_130(tmp_path):
    def _interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    with patch("repowise.cli.commands.update_cmd.command.run_update", side_effect=_interrupt):
        result = CliRunner().invoke(cli, ["update", str(tmp_path)])

    assert result.exit_code == EXIT_INTERRUPTED, result.output
    assert "Interrupted." in result.output
    assert "repowise update" in result.output
