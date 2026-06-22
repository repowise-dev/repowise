"""``repowise whats-new`` — show release notes since the version you last saw."""

from __future__ import annotations

import click

from repowise.cli import __version__
from repowise.cli.helpers import console
from repowise.cli.whats_new import (
    load_changelog_entries,
    read_last_seen_version,
    render_whats_new,
    write_last_seen_version,
)


@click.command("whats-new")
@click.option(
    "--version", "version", default=None, help="Show notes for a single version (e.g. 0.21.0)."
)
@click.option("--all", "show_all", is_flag=True, help="Show the full changelog history.")
def whats_new_command(version: str | None, show_all: bool) -> None:
    """Show what changed in recent repowise releases.

    By default shows releases newer than the last one you viewed, then records
    the current version as seen. ``--all`` shows everything; ``--version`` shows
    one specific release.
    """
    entries = load_changelog_entries()
    if not entries:
        from repowise.cli.whats_new import RELEASES_URL

        console.print(f"[yellow]No changelog found.[/yellow] Release notes: {RELEASES_URL}")
        return

    if version:
        match = [e for e in entries if e.version == version]
        if not match:
            console.print(f"[yellow]No changelog entry for v{version}.[/yellow]")
            return
        render_whats_new(console, match, since_version=None, title=f"repowise v{version}")
        return

    if show_all:
        render_whats_new(
            console, entries, since_version=None, max_versions=len(entries), title="Changelog"
        )
        return

    since = read_last_seen_version()
    rendered = render_whats_new(
        console, entries, since_version=since, up_to_version=__version__, title="What's new"
    )
    if not rendered:
        console.print(f"[green]You're up to date on release notes (v{__version__}).[/green]")
    write_last_seen_version(__version__)


__all__ = ["whats_new_command"]
