"""``repowise report`` - the repo report, and evidence reports a team hands to someone else.

Bare ``repowise report`` is ``repowise report repo`` on the current directory,
with the same options. Each subcommand lives in its own module and is listed in
:data:`_SUBCOMMANDS`; adding one is adding one line there.
"""

from __future__ import annotations

import click

from repowise.core.registry import LazyCommand

_REPO_REPORT = "repowise.cli.commands.report_first_look_cmd:first_look_command"

#: ``"module:attr"`` of each ``repowise report`` subcommand.
_SUBCOMMANDS: tuple[str, ...] = (
    "repowise.cli.commands.report_assessment_cmd:assessment_command",
    "repowise.cli.commands.report_inventory_cmd:inventory_command",
    "repowise.cli.commands.report_changes_cmd:changes_command",
    "repowise.cli.commands.report_estate_cmd:estate_command",
    _REPO_REPORT,
    "repowise.cli.commands.report_first_look_cmd:first_look_alias",
)

_REPO = LazyCommand("", _REPO_REPORT).load()


@click.group("report", invoke_without_command=True)
@click.pass_context
def report_group(ctx: click.Context, **options: object) -> None:
    """The repo report: what Repowise found, most urgent first, with each item's
    status. Bare `repowise report` is `repowise report repo` on this directory.

    \b
    For an agent: read the items, fix one, then record it by its id
      repowise report --format json && repowise mark fl_3a91c0d2e4f1 done --json

    More reports for teams and audits: assessment, inventory, changes, estate.
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(_REPO, **options)
        return
    given = [
        p.opts[-1]
        for p in ctx.command.params
        if ctx.get_parameter_source(p.name) is not click.core.ParameterSource.DEFAULT
    ]
    if given:
        # They would be read and dropped: each subcommand takes its own.
        raise click.UsageError(
            f"Put {', '.join(given)} after the subcommand: "
            f"repowise report {ctx.invoked_subcommand} {given[0]} ..."
        )


# The repo report's options, without its PATH: a group argument would read a
# subcommand's name as a path. `repowise report repo PATH` takes one.
report_group.params.extend(p for p in _REPO.params if not isinstance(p, click.Argument))

for _target in _SUBCOMMANDS:
    report_group.add_command(LazyCommand("", _target).load())
