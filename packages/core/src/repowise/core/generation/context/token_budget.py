"""Token-budget estimation and selection helpers for context assembly.

Extracted from ContextAssembler as free functions (the assembler keeps thin
delegating wrappers). Token estimate uses the 4-chars-per-token heuristic, so
there is no tiktoken dependency.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, TypeVar

T = TypeVar("T")


def estimate_tokens(text: str) -> int:
    """Estimate token count using the 4-chars-per-token heuristic."""
    return len(text) // 4


def trim_to_budget(text: str, remaining: int) -> str:
    """Truncate *text* so it fits within *remaining* token budget.

    Appends ``"...[truncated]"`` when truncated.  The suffix is included
    in the budget calculation so the result never exceeds *remaining* tokens.
    """
    if estimate_tokens(text) <= remaining:
        return text
    suffix = "...[truncated]"
    suffix_tokens = estimate_tokens(suffix)
    max_chars = (remaining - suffix_tokens) * 4
    if max_chars <= 0:
        return suffix if remaining > 0 else ""
    return text[:max_chars] + suffix


def estimate_kg_tokens(kg_context: Any) -> int:
    """Estimate token cost for KG context sections in the template."""
    parts = []
    if kg_context.layer_name:
        parts.append(kg_context.layer_name)
        parts.append(kg_context.layer_description[:300])
    if kg_context.role:
        parts.append(kg_context.role)
    for n in kg_context.neighbors[:10]:
        parts.append(n.get("path", ""))
    if kg_context.tour_step:
        parts.append(kg_context.tour_step.get("title", ""))
        parts.append(kg_context.tour_step.get("description", "")[:300])
    if kg_context.node_summary:
        parts.append(kg_context.node_summary)
    return estimate_tokens(" ".join(parts))


def items_within_budget(
    items: Iterable[T],
    used: int,
    budget: int,
    cost_fn: Callable[[T], int],
) -> tuple[list[T], int]:
    """Greedily keep items while their cumulative cost stays under *budget*.

    Stops at the first item that would not fit (the predicate is
    ``used + cost < budget``, strict, matching the symbol-selection loops this
    replaces). Returns ``(selected, used)`` with *used* advanced by the cost of
    every selected item.
    """
    selected: list[T] = []
    for item in items:
        cost = cost_fn(item)
        if used + cost < budget:
            selected.append(item)
            used += cost
        else:
            break
    return selected, used
