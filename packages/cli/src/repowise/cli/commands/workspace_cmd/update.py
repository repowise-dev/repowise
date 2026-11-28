"""``repowise workspace update``: the same command as ``repowise update --workspace``."""

from __future__ import annotations

import click

from .group import workspace_group


@workspace_group.command(
    "update",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
    add_help_option=False,
)
@click.pass_context
def workspace_update(ctx: click.Context) -> None:
    """Update stale repos and the cross-repo analysis (same as `repowise update --workspace`)."""
    # Imported on use: the update command's module is heavy, and every other
    # workspace subcommand would pay for it at startup. Its options, --help
    # included, pass through unchanged.
    from repowise.cli.commands.update_cmd.command import update_command

    code = update_command.main(
        args=["--workspace", *ctx.args],
        prog_name="repowise workspace update",
        standalone_mode=False,
    )
    ctx.exit(code if isinstance(code, int) else 0)
