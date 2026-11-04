"""How ``repowise init`` ends: ready, finished with gaps, or interrupted."""

from __future__ import annotations

import shlex
from typing import NoReturn

from rich.markup import escape

from repowise.cli.exit_codes import EXIT_DEGRADED, EXIT_INTERRUPTED
from repowise.cli.helpers import console
from repowise.cli.ui import WARN

_NO_TERMINAL = (
    "because the estimate needed confirmation and there was no terminal. "
    "Re-run with --yes to accept the cost"
)
#: The cost gate could not ask, so the model did not run.
COST_UNCONFIRMED_BUILT = f"documentation built from code, {_NO_TERMINAL}"
COST_UNCONFIRMED_KEPT = f"documentation kept as it was, {_NO_TERMINAL}"


def exit_interrupted() -> NoReturn:
    """Ctrl-C at any point: one line, then the shell's interrupt code."""
    from repowise.cli.hints import maybe_hint
    from repowise.cli.ui import mascot

    console.print()
    console.print(
        mascot.mini_text(mascot.INTERRUPTED),
        f"[{WARN}]Interrupted.[/] Indexed work so far is saved. Run "
        "[bold]repowise init --resume[/] to continue where it stopped.",
    )
    maybe_hint("interrupt")
    raise SystemExit(EXIT_INTERRUPTED)


def exit_on_gaps(gaps: list[str]) -> None:
    """Exit degraded when any gap is left; everything is already saved."""
    if not gaps:
        return
    # Escaped: a gap can quote a page id, and a path such as ``app/[slug]``
    # would otherwise be read as markup and drop out of the fix command.
    console.print(f"[{WARN}]Finished with gaps:[/] {escape('; '.join(gaps))}.", highlight=False)
    raise SystemExit(EXIT_DEGRADED)


#: Up to this many failed pages are named one by one in the fix command.
_NAMED_FAILED_PAGES = 3


def failed_pages_gap(page_ids: list[str]) -> str:
    """The gap line for pages the model did not write, ending on the command that writes them.

    The documented contract for exit 2 is that the last line names the gap
    and its fix. A few pages are named exactly; past that, ``--unwritten``
    picks up every page still on its placeholder. ``generate`` writes only the
    model-written page types, so any other failure is left to ``--resume``.
    """
    from repowise.core.generation.models import MODEL_WRITTEN_PAGE_TYPES

    count = len(page_ids)
    if any(p.split(":", 1)[0] not in MODEL_WRITTEN_PAGE_TYPES for p in page_ids):
        command = "repowise init --resume"
    elif count <= _NAMED_FAILED_PAGES:
        command = "repowise generate " + " ".join(f"--page {shlex.quote(p)}" for p in page_ids)
    else:
        command = "repowise generate --unwritten"
    noun = "page" if count == 1 else "pages"
    pronoun = "it" if count == 1 else "them"
    return f"{count:,} {noun} failed to generate; write {pronoun} with: {command}"


def run_gaps(
    *,
    failed_page_ids: list[str],
    embed_error: str | None,
) -> list[str]:
    """What a finished run was asked for and did not get, one phrase each."""
    gaps = []
    if failed_page_ids:
        gaps.append(failed_pages_gap(failed_page_ids))
    if embed_error:
        gaps.append(embed_error.rstrip("."))
    return gaps
