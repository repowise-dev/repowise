"""``repowise report`` - evidence reports a team hands to someone else.

Each subcommand lives in its own module and is listed in :data:`_SUBCOMMANDS`;
adding one is adding one line there.
"""

from __future__ import annotations

import click

from repowise.core.registry import LazyCommand

#: ``"module:attr"`` of each ``repowise report`` subcommand.
_SUBCOMMANDS: tuple[str, ...] = (
    "repowise.cli.commands.report_assessment_cmd:assessment_command",
    "repowise.cli.commands.report_inventory_cmd:inventory_command",
    "repowise.cli.commands.report_changes_cmd:changes_command",
)


@click.group("report")
def report_group() -> None:
    """Evidence reports built from the index: assessment, inventory, changes."""


for _target in _SUBCOMMANDS:
    report_group.add_command(LazyCommand("", _target).load())
