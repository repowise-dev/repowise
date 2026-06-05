"""``repowise expand`` — restore content behind an omission marker."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from repowise.cli.helpers import find_repowise_repo_root


@click.command(
    "expand",
    short_help="Print the full content behind a [repowise#<ref>] omission marker.",
)
@click.argument("ref")
@click.option(
    "--query",
    "-q",
    default=None,
    help="Return only the lines matching this regex (or substring).",
)
def expand_command(ref: str, query: str | None) -> None:
    """Restore the original output referenced by an omission marker.

    REF is the 12-hex id from a ``[repowise#<ref>: ...]`` marker emitted by
    ``repowise distill``. Looks in the current repo's store first, then the
    user-level fallback store.
    """
    from repowise.core.distill.markers import MARKER_RE, is_valid_ref
    from repowise.core.distill.store import OmissionStore

    # Accept a pasted whole marker, not just the bare ref.
    if not is_valid_ref(ref):
        match = MARKER_RE.search(ref)
        if match:
            ref = match.group("ref")
    if not is_valid_ref(ref):
        click.echo(f"Not a valid omission ref: {ref!r} (expected 12 hex chars)", err=True)
        sys.exit(2)

    for db_path in _candidate_stores():
        if not db_path.exists():
            continue
        store = OmissionStore(db_path)
        try:
            content = store.get(ref, query=query)
        finally:
            store.close()
        if content is not None:
            _echo_safely(content)
            return

    click.echo(f"No stored content for {ref} (expired, pruned, or never stored here).", err=True)
    sys.exit(1)


def _candidate_stores() -> list[Path]:
    """Repo-local store first, then the home fallback, deduplicated."""
    paths = []
    repo_root = find_repowise_repo_root()
    if repo_root is not None:
        paths.append(default_store_path_for(repo_root))
    home_store = default_store_path_for(Path.home())
    if home_store not in paths:
        paths.append(home_store)
    return paths


def default_store_path_for(root: Path) -> Path:
    from repowise.core.distill.store import OMISSIONS_DB_FILENAME, OMISSIONS_DIRNAME

    return root / ".repowise" / OMISSIONS_DIRNAME / OMISSIONS_DB_FILENAME


def _echo_safely(text: str) -> None:
    try:
        click.echo(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace") + b"\n")
