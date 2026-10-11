"""``repowise publish``: put this repo on repowise.dev with one command.

repowise.dev indexes the repo's GitHub remote itself; nothing from this
machine is uploaded, so only pushed code is published. Signed out, the
command runs the normal browser sign-in first. The decision and every
message live in :mod:`repowise.cli.platform.publish`, which the local web
UI's publish button shares.
"""

from __future__ import annotations

import re
import webbrowser
from pathlib import Path

import click
from rich.markup import escape

from repowise.cli.helpers import console
from repowise.cli.output import emit_json, format_option


@click.command(name="publish")
@click.argument("path", required=False, type=click.Path(exists=True, file_okay=False))
@click.option(
    "--ref",
    default=None,
    help="Branch or tag on GitHub to publish (default: the branch you are on, "
    "if it is pushed, else the repo's default branch).",
)
@click.option("--no-open", is_flag=True, help="Don't open the indexing page in the browser.")
@click.option(
    "--src",
    default=None,
    hidden=True,
    help="Surface that asked for the publish, for the links' attribution.",
)
@format_option(
    help="table prints for a person; json prints the result as one object and never "
    "starts a browser sign-in (signed out reads as outcome signed_out)."
)
def publish_command(
    path: str | None, ref: str | None, no_open: bool, src: str | None, fmt: str
) -> None:
    """Publish this repo on repowise.dev (indexed from GitHub, free for public repos)."""
    from repowise.cli.platform import publish as pub
    from repowise.cli.platform import telemetry

    repo_path = Path(path or ".").resolve()
    if src is not None and not re.fullmatch(r"[a-z0-9_]{1,64}", src):
        raise click.BadParameter("must be 1-64 of a-z, 0-9 and _", param_hint="--src")

    interactive = fmt != "json"
    if interactive and pub.read_remote(repo_path) is not None and not pub.is_signed_in():
        from repowise.cli.commands.login_cmd import _default_device_name, browser_sign_in

        console.print("Publishing needs a free repowise.dev account. Signing you in first.\n")
        browser_sign_in(_default_device_name(), src=pub.SRC)
        console.print()

    result = pub.publish(repo_path, ref=ref, src=src or pub.SRC)
    telemetry.add_command_outcome(outcome=result.outcome)

    ok = result.outcome in {"published", "curated"}
    if not interactive:
        emit_json(result.to_dict())
        if not ok:
            raise SystemExit(1)
        return

    mark = "[green]✓[/green] " if ok else ""
    console.print(f"{mark}{escape(result.message)}")
    if result.url:
        # Unhighlighted, so the URL stays one hyperlink instead of one per token.
        console.print(
            f"  [cyan][link={result.url}]{escape(result.url)}[/link][/cyan]", highlight=False
        )
    for line in result.details:
        console.print(f"  [dim]{escape(line)}[/dim]", highlight=False)
    if result.outcome != "not_github":
        # No highlighting: it would colour "<branch>" inside the dim line.
        console.print(f"\n[dim]{escape(pub.PUSHED_ONLY)}[/dim]", highlight=False)

    if result.open_url and not no_open:
        webbrowser.open(result.open_url)

    if not ok:
        raise SystemExit(1)
