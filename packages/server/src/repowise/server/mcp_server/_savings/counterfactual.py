"""Per-tool counterfactual estimators — "what raw exploration did this replace?"

Each MCP tool answer stands in for some amount of raw file reading the agent
would otherwise have done. ``replaced_tokens_for`` returns a **conservative
undersell** of that raw cost, computed purely from fields already on the tool's
result dict — no disk reads, no DB queries. When a tool can't be estimated from
its dict alone it returns 0, and the recorder writes no row (undersell by
omission). High-value tools that hold an exact artifact (``get_symbol`` has the
whole source file in hand) instead *declare* their counterfactual via
:func:`repowise.server.mcp_server._savings.wrapper.declare_replaced`; the
wrapper prefers a declared value over anything computed here.

Floors mirror :data:`repowise.core.distill.missed.RATIO_FLOOR` philosophy:
estimates undersell rather than oversell, so the savings number on the Costs
page is always defensible.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: Conservative per-cited-file floor for ``search_codebase``. A semantic hit
#: stands in for the agent grepping then opening that file; we have no file
#: size on the result dict (search never loads bodies), so we undersell each
#: distinct cited path to a small fraction of a typical source file rather than
#: re-reading disk. Deliberately low — the goal is never to overstate.
SEARCH_FLOOR_PER_HIT = 400


def _estimate_get_context(result: dict[str, Any]) -> int:
    """Σ full-file tokens the skeleton/doc replaced, over file targets.

    ``get_context`` auto-upgrades file targets to a body-elided *skeleton*; each
    such target carries ``skeleton.full_tokens`` — the exact size of the file
    the agent would have Read instead. Summing those is a precise, dict-only
    counterfactual. Targets without a skeleton (small-file cards, module/symbol
    targets) contribute nothing.
    """
    targets = result.get("targets")
    if not isinstance(targets, dict):
        return 0
    total = 0
    for target in targets.values():
        if not isinstance(target, dict):
            continue
        skeleton = target.get("skeleton")
        if isinstance(skeleton, dict):
            full = skeleton.get("full_tokens")
            if isinstance(full, int) and full > 0:
                total += full
    return total


def _estimate_search_codebase(result: dict[str, Any]) -> int:
    """``len(distinct cited files) x SEARCH_FLOOR_PER_HIT`` — a conservative floor.

    Each result points the agent at a file it would otherwise have had to find
    and open by hand. We undersell to a per-hit floor because the result dict
    carries only the path (and a snippet), never the file's size.
    """
    results = result.get("results")
    if not isinstance(results, list):
        return 0
    paths = {
        r["target_path"]
        for r in results
        if isinstance(r, dict) and r.get("target_path")
    }
    return len(paths) * SEARCH_FLOOR_PER_HIT


#: Tool name → estimator. Tools absent here emit no counterfactual (skip).
_ESTIMATORS: dict[str, Callable[[dict[str, Any]], int]] = {
    "get_context": _estimate_get_context,
    "search_codebase": _estimate_search_codebase,
}


def replaced_tokens_for(tool: str, result: Any) -> int:
    """Conservative raw-exploration tokens *tool*'s answer replaced.

    Returns 0 for unknown tools, non-dict results, or anything the per-tool
    estimator can't ground in the result dict. Never raises.
    """
    if not isinstance(result, dict):
        return 0
    estimator = _ESTIMATORS.get(tool)
    if estimator is None:
        return 0
    try:
        value = estimator(result)
    except Exception:
        return 0
    return value if isinstance(value, int) and value > 0 else 0
