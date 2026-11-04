"""``repowise update`` exit-code contract (issue #4277).

Matches ``repowise init``: 0 ready, 2 finished with gaps, 130
interrupted. The mapping lives in the Click command only — init, watch
and the workspace loop call :func:`run_update` directly and keep its
:class:`UpdateOutcome` return untouched.
"""

from __future__ import annotations

from click.testing import CliRunner

from repowise.cli.commands.update_cmd import command as update_command_module
from repowise.cli.commands.update_cmd.command import UpdateOutcome
from repowise.cli.main import cli


def _invoke(monkeypatch, run_update_stub):
    monkeypatch.setattr(update_command_module, "run_update", run_update_stub)
    return CliRunner().invoke(cli, ["update", "--no-workspace"])


def test_clean_run_exits_zero(monkeypatch) -> None:
    def _clean(**_kwargs):
        return UpdateOutcome.REGENERATED

    result = _invoke(monkeypatch, _clean)
    assert result.exit_code == 0, result.output


def test_degraded_run_exits_two_with_one_line_summary(monkeypatch) -> None:
    def _degraded(*, degraded_out=None, **_kwargs):
        if degraded_out is not None:
            degraded_out.append("Knowledge-graph export: boom")
        return UpdateOutcome.REGENERATED

    result = _invoke(monkeypatch, _degraded)
    assert result.exit_code == 2, result.output
    assert "Finished with gaps" in result.output
    assert "Knowledge-graph export: boom" in result.output


def test_keyboard_interrupt_exits_130_not_click_aborted(monkeypatch) -> None:
    def _interrupted(**_kwargs):
        raise KeyboardInterrupt

    result = _invoke(monkeypatch, _interrupted)
    assert result.exit_code == 130, result.output
    assert "Aborted!" not in result.output
    assert "Interrupted" in result.output


def test_help_documents_exit_codes() -> None:
    result = CliRunner().invoke(cli, ["update", "--help"])
    assert result.exit_code == 0, result.output
    # Click wraps the epilog, so compare against whitespace-normalised text.
    text = " ".join(result.output.split())
    assert "0 ready" in text
    assert "130 interrupted" in text
