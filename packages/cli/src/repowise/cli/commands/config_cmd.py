"""``repowise config``: local CLI preferences kept in ``~/.repowise``."""

from __future__ import annotations

import click

from repowise.cli.helpers import console


@click.group(name="config")
def config_group() -> None:
    """Change local CLI preferences."""


@config_group.command(name="hints")
@click.argument("state", type=click.Choice(["on", "off"]))
def config_hints(state: str) -> None:
    """Turn the occasional repowise.dev tip on or off."""
    from repowise.cli import hints

    hints.set_enabled(state == "on")
    if state == "on":
        console.print("[green]✓[/green] repowise.dev tips are on (at most one a week each).")
    else:
        console.print("[green]✓[/green] repowise.dev tips are off.")


config_command = config_group
