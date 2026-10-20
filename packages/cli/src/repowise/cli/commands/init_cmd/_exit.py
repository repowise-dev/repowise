"""How ``repowise init`` ends: ready, finished with gaps, or interrupted."""

from __future__ import annotations

from typing import NoReturn

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
    console.print(f"[{WARN}]Finished with gaps:[/] {'; '.join(gaps)}.", highlight=False)
    raise SystemExit(EXIT_DEGRADED)


def run_gaps(
    *,
    failed_pages: int,
    embed_error: str | None,
) -> list[str]:
    """What a finished run was asked for and did not get, one phrase each."""
    gaps = []
    if failed_pages:
        gaps.append(f"{failed_pages:,} page(s) failed to generate")
    if embed_error:
        gaps.append(embed_error.rstrip("."))
    return gaps
