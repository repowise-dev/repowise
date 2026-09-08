"""``repowise health`` offers the same two controls the API and MCP do.

An agent or a script that can ask "is this repo's code getting better" through
one surface and not another gets a different answer depending on where it
asked, which is the drift these two options exist to remove.
"""

from __future__ import annotations

from click.testing import CliRunner

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
    # flag that asked for the other one.
    result = CliRunner().invoke(cli, ["health", "--counts", "code-shape"])
    assert result.exit_code != 0
    assert "code-shape" in result.output
