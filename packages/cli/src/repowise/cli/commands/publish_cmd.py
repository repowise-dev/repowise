"""``repowise publish``: put this repo on repowise.dev with one command.

repowise.dev indexes the repo's GitHub remote itself; nothing from this
machine is uploaded, so only pushed code is published. Signed out, the
command runs the normal browser sign-in first. The decision and every
message live in :mod:`repowise.cli.platform.publish`, which the local web
UI's publish button shares.
"""

from __future__ import annotations

import webbrowser
from pathlib import Path

import click
from rich.markup import escape

from repowise.cli.helpers import console


@click.command(name="publish")
@click.argument("path", required=False, type=click.Path(exists=True, file_okay=False))
@click.option(
    "--ref",
    default=None,
    help="Branch or tag on GitHub to publish (default: the branch you are on, "
    "if it is pushed, else the repo's default branch).",
)
@click.option("--no-open", is_flag=True, help="Don't open the indexing page in the browser.")
def publish_command(path: str | None, ref: str | None, no_open: bool) -> None:
    """Publish this repo on repowise.dev (indexed from GitHub, free for public repos)."""
    from repowise.cli.platform import publish as pub
    from repowise.cli.platform import telemetry

    repo_path = Path(path or ".").resolve()

    if pub.read_remote(repo_path) is not None and not pub.is_signed_in():
        from repowise.cli.commands.login_cmd import _default_device_name, browser_sign_in

        console.print("Publishing needs a free repowise.dev account. Signing you in first.\n")
        browser_sign_in(_default_device_name(), src=pub.SRC)
        console.print()

    result = pub.publish(repo_path, ref=ref)
    telemetry.add_command_outcome(outcome=result.outcome)

    ok = result.outcome in {"published", "already_published"}
    mark = "[green]✓[/green] " if ok else ""
    console.print(f"{mark}{escape(result.message)}")
    if result.url:
        console.print(f"  [cyan][link={result.url}]{escape(result.url)}[/link][/cyan]")
    for line in result.details:
        console.print(f"  [dim]{escape(line)}[/dim]")
    if result.outcome != "not_github":
        console.print(f"\n[dim]{escape(pub.PUSHED_ONLY)}[/dim]")

    if result.open_url and not no_open:
        webbrowser.open(result.open_url)

    if not ok:
        raise SystemExit(1)
