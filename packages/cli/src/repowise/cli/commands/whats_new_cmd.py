"""``repowise whats-new`` — show release notes since the version you last saw."""

from __future__ import annotations

import click

from repowise.cli import __version__
from repowise.cli.helpers import console
from repowise.cli.output import emit_json, format_option, notice_console
from repowise.cli.whats_new import (
    load_changelog_entries,
    read_last_seen_version,
    render_whats_new,
    write_last_seen_version,
)


def _entry_dict(entry) -> dict:
    """One changelog release as plain data.

    The table path renders through ``render_whats_new``, which caps at 5
    releases and 8 bullets each to keep a panel readable. json applies no such
    cap: the selection is already the caller's (``--version`` / ``--all`` /
    the last-seen watermark), and silently dropping the rest of what was asked
    for is exactly the truncation this phase exists to remove.
    """
    return {
        "version": entry.version,
        "label": entry.label,
        "sections": [{"name": s.name, "items": list(s.items)} for s in entry.sections],
    }


@click.command("whats-new")
@click.option(
    "--version", "version", default=None, help="Show notes for a single version (e.g. 0.21.0)."
)
@click.option("--all", "show_all", is_flag=True, help="Show the full changelog history.")
@format_option()
def whats_new_command(version: str | None, show_all: bool, fmt: str) -> None:
    """Show what changed in recent repowise releases.

    By default shows releases newer than the last one you viewed, then records
    the current version as seen. ``--all`` shows everything; ``--version`` shows
    one specific release.
    """
    notices = notice_console(fmt)
    entries = load_changelog_entries()
    if not entries:
        from repowise.cli.whats_new import RELEASES_URL

        notices.print(f"[yellow]No changelog found.[/yellow] Release notes: {RELEASES_URL}")
        if fmt == "json":
            emit_json({"current_version": __version__, "releases": []})
        return

    if version:
        match = [e for e in entries if e.version == version]
        if not match:
            notices.print(f"[yellow]No changelog entry for v{version}.[/yellow]")
            if fmt == "json":
                emit_json({"current_version": __version__, "releases": []})
            return
        if fmt == "json":
            emit_json(
                {
                    "current_version": __version__,
                    "releases": [_entry_dict(e) for e in match],
                }
            )
            return
        render_whats_new(console, match, since_version=None, title=f"repowise v{version}")
        return

    if show_all:
        if fmt == "json":
            emit_json(
                {
                    "current_version": __version__,
                    "releases": [_entry_dict(e) for e in entries],
                }
            )
            return
        render_whats_new(
            console, entries, since_version=None, max_versions=len(entries), title="Changelog"
        )
        return

    since = read_last_seen_version()
    if fmt == "json":
        from repowise.core.upgrade.changelog import entries_between

        selected = entries_between(entries, newer_than=since, up_to=__version__)
        emit_json(
            {
                "current_version": __version__,
                "last_seen_version": since,
                "releases": [_entry_dict(e) for e in selected],
            }
        )
        # Same watermark write as the table path: json mode is still "I have
        # seen these", and skipping it would re-report the same releases every
        # run to the one consumer least able to notice the repetition.
        write_last_seen_version(__version__)
        return

    rendered = render_whats_new(
        console, entries, since_version=since, up_to_version=__version__, title="What's new"
    )
    if not rendered:
        console.print(f"[green]You're up to date on release notes (v{__version__}).[/green]")
    write_last_seen_version(__version__)


__all__ = ["whats_new_command"]
