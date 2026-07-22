"""Shared LLM-cost confirmation gate.

Both ``repowise init`` and ``repowise generate`` put a spend estimate in front
of the user and ask before running a model. The gate constant, the declined
signal, and the confirm/format helpers used to live in ``init_cmd/generation``;
they now live here so the two commands share one gate rather than one importing
the other's internals.
"""

from __future__ import annotations

import sys
from typing import Any

import click

from repowise.cli.helpers import console

# The LLM-cost confirmation threshold. A run whose estimate exceeds this asks
# for confirmation (unless ``--yes``); below it generation proceeds silently.
COST_GATE_USD = 2.00

# The point where Enter-through stops meaning yes. Between COST_GATE_USD and
# this, the gate is an FYI on a run the user just configured and the default
# continues it. Past it, a mis-keyed Enter is an expensive mistake, so the
# default flips and approval has to be typed.
COST_GATE_HARD_USD = 25.00


class CostGateDeclined(Exception):  # noqa: N818 — a control-flow signal, not an error
    """Raised when the user answers No at the LLM-cost confirmation prompt.

    Carries no payload — the caller just needs to know that generation was
    declined so it can persist state in index-only shape (no docs) and
    return cleanly. Using an exception (vs. a sentinel return value) lets
    us bail out of nested generation flows without rethreading return
    types through every helper.
    """


def confirm_cost_gate(message: str, *, estimated_usd: float | None = None) -> bool:
    """Render the cost-gate prompt with visual padding. True means "spend it".

    Click's plain ``confirm`` interleaves with the trailing line of any
    prior Rich output (progress-bar frames, status spinners), making the
    confirm glyphs hard to spot — users have walked past it and approved
    a $14 bill thinking they were still in cost-estimate territory. A
    blank line + horizontal rule cleanly separates the prompt from
    whatever was printed above it.

    Default is Yes: the user just picked a coverage tier with the cost
    range printed next to it, so Enter-through should continue the run
    they configured — the gate exists to make the spend visible, not to
    interrupt it. Above :data:`COST_GATE_HARD_USD` that reverses, because
    Enter-through approving a bill that size is the footgun the padding
    above was added to prevent, and a default cannot be the safe answer
    and the expensive one at the same time.

    Declining is not a dead end and the prompt now says so: the caller
    renders the wiki from templates instead, so No costs the user their
    model pages, not their wiki.

    Never prompts when stdin is not a terminal. There is nothing to read
    there, and ``click.confirm`` would raise ``Abort`` and throw away an
    index that is already built — which is the whole run, minutes of it.
    """
    console.line()
    console.rule(style="yellow")
    console.print(
        "  [dim]No builds the same wiki from structure instead: no model, "
        "no spend. You can upgrade it later with [bold]repowise update "
        "--full[/bold].[/dim]"
    )
    if not sys.stdin.isatty():
        console.print("  [dim]Not a terminal, so nothing to ask: building from structure.[/dim]")
        return False
    default = True
    if estimated_usd is not None and estimated_usd > COST_GATE_HARD_USD:
        console.print(
            f"  [yellow]This is over ${COST_GATE_HARD_USD:.0f}.[/yellow] "
            "[dim]Answer yes explicitly to approve it.[/dim]"
        )
        default = False
    return click.confirm(message, default=default)


def cost_gate_declined(est: Any, *, yes: bool, message: str) -> bool:
    """Return ``True`` when the run should skip generation on cost grounds.

    Only prompts when the estimate clears :data:`COST_GATE_USD` and ``--yes``
    was not passed; a declined prompt yields ``True``.
    """
    if est.estimated_cost_usd <= COST_GATE_USD or yes:
        return False
    return not confirm_cost_gate(message, estimated_usd=est.estimated_cost_usd)


def format_cost(est: Any) -> str:
    """Render an estimate as a human-readable USD string (range when known)."""
    if est.cost_range is not None:
        cost_str = (
            f"${est.cost_range.low:.2f} - ${est.cost_range.high:.2f} USD "
            f"(median ${est.estimated_cost_usd:.2f})"
        )
        if est.is_calibrated:
            cost_str += " [calibrated]"
        return cost_str
    return f"${est.estimated_cost_usd:.2f} USD"
