"""Steps every report-ingest command shares: open the index, report what did not map.

A report ingest (``coverage add``, and the commands built like it) reads a
file made by another tool, maps its paths onto the indexed tree and stores
the result. Finding the index and saying what did not map is the same for
all of them, so it is worded once here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.markup import escape

from repowise.cli.helpers import console


@dataclass(frozen=True)
class IngestTarget:
    """The indexed repository a report is ingested into."""

    repo: Any
    #: Canonical repo-relative keys of the indexed files.
    file_keys: set[str]
    #: The commit the working tree is at (see ``live_head_commit``).
    head_sha: str | None


async def indexed_file_keys(session: Any, repo_id: str) -> set[str]:
    """Canonical repo file keys to resolve report paths against.

    Sourced from the persisted health metrics (one row per indexed file);
    these already carry the repo-relative POSIX key the resolver needs.
    """
    from repowise.core.persistence.crud import get_health_metrics

    return {m.file_path for m in await get_health_metrics(session, repo_id)}


async def open_ingest_target(
    session: Any,
    repo_path: Path,
    *,
    what: str,
    load_keys: Callable[[Any, str], Awaitable[set[str]]] = indexed_file_keys,
    out: Any = None,
) -> IngestTarget | None:
    """The indexed repository at *repo_path*, or ``None`` (and why, printed).

    *what* names what is being added, for the message (``coverage``).
    *load_keys* reads the indexed file keys. Every helper here prints to *out*,
    the CLI console unless given (a ``--format json`` command passes stderr's).
    """
    out = out or console
    from repowise.core.analysis.health.coverage import live_head_commit
    from repowise.core.persistence.crud import get_repository_by_path

    repo_row = await get_repository_by_path(session, str(repo_path))
    if repo_row is None:
        out.print(
            f"[yellow]No index yet. Run `repowise init` once before adding {what}.[/yellow]"
        )
        return None
    file_keys = await load_keys(session, repo_row.id)
    if not file_keys:
        out.print("[yellow]No indexed files found. Run `repowise init` first.[/yellow]")
        return None
    return IngestTarget(repo_row, file_keys, live_head_commit(repo_path, repo_row))


def refuse_partial(part: str, hint: str, out: Any = None) -> None:
    """Say that most of *part* did not map, so nothing was stored, and how to fix it."""
    (out or console).print(
        f"[red]More than half the {part} did not map to the repo tree, so "
        f"nothing was stored.[/red] To fix it, {escape(hint)} and re-run, "
        "or pass [cyan]--allow-partial[/cyan] to store the fragment."
    )


def report_unmapped(unmapped: Sequence[str], what: str, out: Any = None) -> None:
    """Name how many of *what* did not map, with a few of them."""
    if not unmapped:
        return
    sample = ", ".join(unmapped[:5])
    (out or console).print(
        f"[yellow]{len(unmapped)} {what} did not map to the "
        f"repo tree[/yellow] (e.g. {escape(sample)})."
    )


def report_stored_partial(what: str, out: Any = None) -> None:
    """Say that a fragment of *what* was stored on request, flagged partial."""
    (out or console).print(
        "[yellow]Stored a fragment (--allow-partial): more than half the "
        f"report did not map to the repo tree, and the stored {what} is "
        "flagged partial.[/yellow]"
    )
