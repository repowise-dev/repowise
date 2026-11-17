"""``repowise grammars``: stage the grammars that download on first use.

COBOL and Apex grammars come from the language pack at runtime rather than in a
wheel. ``fetch`` fills a directory on a machine with network access, to copy to
an offline one and point ``REPOWISE_GRAMMAR_DIR`` at.
"""

from __future__ import annotations

from pathlib import Path

import click

from repowise.cli.helpers import console


@click.group("grammars")
def grammars_group() -> None:
    """Manage grammars that download on first use (COBOL, Apex)."""


@grammars_group.command("fetch")
@click.argument("directory", type=click.Path(file_okay=False, path_type=Path))
def fetch_command(directory: Path) -> None:
    """Download every runtime grammar into DIRECTORY for offline use."""
    from repowise.core.ingestion.languages.registry import REGISTRY
    from repowise.core.ingestion.parser.grammars import GRAMMAR_DIR_ENV, fetch_runtime_grammars

    directory = directory.resolve()
    results = fetch_runtime_grammars(str(directory))
    for tag, cached, error in results:
        spec = REGISTRY.get(tag)
        name = spec.display_name if spec else tag
        if cached:
            console.print(f"  [green]OK[/green]   {name}: cached in {directory}")
        else:
            console.print(f"  [red]FAIL[/red] {name}: {error or 'not downloaded'}")
    if not all(cached for _tag, cached, _error in results):
        raise SystemExit(1)
    console.print(f"\nOn the offline machine, set {GRAMMAR_DIR_ENV}={directory}")
