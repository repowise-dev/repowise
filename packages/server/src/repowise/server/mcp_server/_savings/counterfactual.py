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
    paths = {r["target_path"] for r in results if isinstance(r, dict) and r.get("target_path")}
    return len(paths) * SEARCH_FLOOR_PER_HIT


#: get_answer subsumes a search_codebase call plus opening the cited files.
#: Same undersell philosophy as SEARCH_FLOOR_PER_HIT — the dict carries paths,
#: never sizes.
ANSWER_SEARCH_FLOOR = 400
ANSWER_READ_FLOOR_PER_FILE = 400

#: Modest fixed counterfactuals for the analysis tools. Each replaces an
#: exploration the agent cannot cheaply reproduce (git log/blame spelunking,
#: ADR archaeology, README + entry-point skim) but whose size the result dict
#: cannot ground — so a flat, deliberately-low floor per successful call.
RISK_FLOOR = 500
WHY_FLOOR = 400
HEALTH_FLOOR = 400
OVERVIEW_FLOOR = 1200


def _estimate_get_answer(result: dict[str, Any]) -> int:
    """Search call + cited-file reads the answer obviated.

    Counts only answered calls (non-empty ``answer``): a gated/low response
    explicitly tells the agent to go read, so the reads still happen and
    claiming them as saved would repeat the E11 miscalibration.
    """
    if not str(result.get("answer") or "").strip():
        return 0
    paths = {
        p
        for p in [*(result.get("citations") or []), *(result.get("fallback_targets") or [])[:3]]
        if isinstance(p, str) and p
    }
    if not paths:
        return 0
    return ANSWER_SEARCH_FLOOR + len(paths) * ANSWER_READ_FLOOR_PER_FILE


def _fixed_floor(tokens: int) -> Callable[[dict[str, Any]], int]:
    """Flat counterfactual for successful calls; errors contribute nothing."""

    def _estimate(result: dict[str, Any]) -> int:
        return 0 if result.get("error") else tokens

    return _estimate


#: Tool name → estimator. Tools absent here emit no counterfactual (skip).
_ESTIMATORS: dict[str, Callable[[dict[str, Any]], int]] = {
    "get_context": _estimate_get_context,
    "search_codebase": _estimate_search_codebase,
    "get_answer": _estimate_get_answer,
    "get_risk": _fixed_floor(RISK_FLOOR),
    "get_why": _fixed_floor(WHY_FLOOR),
    "get_health": _fixed_floor(HEALTH_FLOOR),
    "get_overview": _fixed_floor(OVERVIEW_FLOOR),
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
