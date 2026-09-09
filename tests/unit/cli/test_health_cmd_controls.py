"""``repowise health`` offers the same two controls the API and MCP do.

An agent or a script that can ask "is this repo's code getting better" through
one surface and not another gets a different answer depending on where it
asked, which is the drift these two options exist to remove.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner
from rich.console import Console

from repowise.cli.commands.health_cmd import command as health_module
from repowise.cli.commands.health_cmd.command import health_command
from repowise.cli.main import cli
from repowise.core.analysis.health.counts import COUNTS, DEFAULT_COUNTS
from repowise.core.analysis.health.scope import DEFAULT_SCOPE, SCOPES


def _option(name: str):
    return next(p for p in health_command.params if p.name == name)


def test_both_controls_offer_the_shared_vocabulary() -> None:
    scope, counts = _option("scope"), _option("counts")
    assert tuple(scope.type.choices) == SCOPES
    assert scope.default == DEFAULT_SCOPE
    assert tuple(counts.type.choices) == COUNTS
    assert counts.default == DEFAULT_COUNTS


def test_an_unknown_value_is_refused_rather_than_silently_defaulted() -> None:
    # Falling back to the default would report the calibrated number under a
    # flag that asked for the other one. Assert on click's rejection and not
    # just a non-zero exit: a missing index exits non-zero too.
    result = CliRunner().invoke(cli, ["health", "--counts", "code-shape"])
    assert result.exit_code != 0
    assert "Invalid value for '--counts'" in result.output
    assert "code-shape" in result.output


def test_the_distribution_line_names_every_band() -> None:
    """Three segments on a five-band scale reads as a scale that has three."""
    import re

    from repowise.cli.commands.health_cmd.summary import _render_distribution_line
    from repowise.core.analysis.health.grading import BAND_LABEL, BAND_ORDER

    console = Console(file=io.StringIO(), width=400, force_terminal=False)
    with patch("repowise.cli.commands.health_cmd.summary.console", console):
        _render_distribution_line(
            {
                "total_files": 5,
                "total_nloc": 500,
                "bands": {b: {"files": 1, "nloc": 100, "pct": 20.0} for b in BAND_ORDER},
            }
        )
    out = re.sub(r"\s+", " ", console.file.getvalue())
    for band in BAND_ORDER:
        assert f"{BAND_LABEL[band].lower()} (1 files)" in out


def test_the_trend_says_the_controls_do_not_reach_it() -> None:
    """The trend reads calibrated snapshots, so a flag on it would be a lie."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path(".repowise").mkdir()
        with (
            patch.object(health_module, "_render_trend") as render,
            patch.object(health_module, "resolve_command_target") as resolve,
        ):
            resolve.return_value = SimpleNamespace(
                is_workspace=False,
                repo_path=Path("."),
                notice=lambda *a, **k: None,
            )
            result = runner.invoke(cli, ["health", "--trend", "--counts", "code_shape"])
        render.assert_called_once()
    assert "do not apply to it" in result.output


def test_the_trend_stays_quiet_when_no_control_was_passed() -> None:
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path(".repowise").mkdir()
        with (
            patch.object(health_module, "_render_trend"),
            patch.object(health_module, "resolve_command_target") as resolve,
        ):
            resolve.return_value = SimpleNamespace(
                is_workspace=False,
                repo_path=Path("."),
                notice=lambda *a, **k: None,
            )
            result = runner.invoke(cli, ["health", "--trend"])
    assert "do not apply to it" not in result.output
