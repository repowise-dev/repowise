"""Shared LLM-cost confirmation gate.

Both ``repowise init`` and ``repowise generate`` put a spend estimate in front
of the user and ask before running a model. The gate constant, the declined
signal, and the confirm/format helpers used to live in ``init_cmd/generation``;
they now live here so the two commands share one gate rather than one importing
the other's internals.
"""

from __future__ import annotations

from typing import Any

import click

from repowise.cli.helpers import console

# The LLM-cost confirmation threshold. A run whose estimate exceeds this asks
# for confirmation (unless ``--yes``); below it generation proceeds silently.
COST_GATE_USD = 2.00


class CostGateDeclined(Exception):  # noqa: N818 — a control-flow signal, not an error
    """Raised when the user answers No at the LLM-cost confirmation prompt.

    Carries no payload — the caller just needs to know that generation was
    declined so it can persist state in index-only shape (no docs) and
    return cleanly. Using an exception (vs. a sentinel return value) lets
    us bail out of nested generation flows without rethreading return
    types through every helper.
    """


def confirm_cost_gate(message: str) -> bool:
    """Render the cost-gate ``[Y/n]`` prompt with visual padding.

    Click's plain ``confirm`` interleaves with the trailing line of any
    prior Rich output (progress-bar frames, status spinners), making the
    confirm glyphs hard to spot — users have walked past it and approved
    a $14 bill thinking they were still in cost-estimate territory. A
    blank line + horizontal rule cleanly separates the prompt from
    whatever was printed above it.

    Default is Yes: the user just picked a coverage tier with the cost
    range printed next to it, so Enter-through should continue the run
    they configured — the gate exists to make the spend visible, not to
    interrupt it.
    """
    console.line()
    console.rule(style="yellow")
    return click.confirm(message, default=True)


def cost_gate_declined(est: Any, *, yes: bool, message: str) -> bool:
    """Return ``True`` when the run should skip generation on cost grounds.

    Only prompts when the estimate clears :data:`COST_GATE_USD` and ``--yes``
    was not passed; a declined prompt yields ``True``.
    """
    return est.estimated_cost_usd > COST_GATE_USD and not yes and not confirm_cost_gate(message)


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
