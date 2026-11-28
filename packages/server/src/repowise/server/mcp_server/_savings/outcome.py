"""What a check tool's reply returned, in a few counts for the savings ledger.

Read off the reply already in hand when its event is written: no index, file or
git read, and nothing the tool did not already put in the reply. The counts say
what Repowise returned, never what the agent did with it. The vocabulary is the
ledger's (:func:`repowise.core.savings.contracts.normalize_outcome`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from repowise.core.analysis.risk_semantics import (
    REVIEW_PRIORITY_HIGH_PERCENTILE,
    REVIEW_PRIORITY_MODERATE_PERCENTILE,
)


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _tercile(percentile: Any) -> str | None:
    """The review-priority tercile of a 0-100 percentile, as high/medium/low."""
    if isinstance(percentile, bool) or not isinstance(percentile, (int, float)):
        return None
    if percentile >= REVIEW_PRIORITY_HIGH_PERCENTILE:
        return "high"
    if percentile >= REVIEW_PRIORITY_MODERATE_PERCENTILE:
        return "medium"
    return "low"


def _change_risk(result: dict[str, Any]) -> dict[str, Any]:
    """The change's repo-relative level, from the same terciles ``review_priority`` uses.

    ``{}`` when the reply ranks nothing (no baseline, or a per-commit range).
    """
    level = _tercile(result.get("risk_percentile"))
    return {"level": level} if level else {}


#: A non-hotspot file's churn-rank tercile, capped at medium.
_NOT_HOTSPOT_LEVEL = {"high": "medium", "medium": "medium", "low": "low"}


def _risk(result: dict[str, Any]) -> dict[str, Any]:
    """Assessed files per level: a stored hotspot is high, else its churn rank's tercile."""
    targets = result.get("targets")
    counts = {"high": 0, "medium": 0, "low": 0}
    for card in targets.values() if isinstance(targets, dict) else ():
        if not isinstance(card, dict) or card.get("error") or "hotspot_score" not in card:
            continue
        if card.get("is_hotspot"):
            counts["high"] += 1
            continue
        score = card.get("hotspot_score")
        # Only the stored hotspot flag (activity floors included) makes a file
        # high, so a top-third churn rank without it reads medium.
        level = _tercile(score * 100 if isinstance(score, (int, float)) else None)
        counts[_NOT_HOTSPOT_LEVEL.get(level, "low")] += 1
    return counts


#: Where a get_why reply lists decision rows: top-level lanes, and per file card.
_WHY_LANES = ("decisions", "candidates")
_WHY_CARD_LANES = ("governing_decisions", "candidate_decisions")


def _why(result: dict[str, Any]) -> dict[str, Any]:
    """Whether the reply served an accepted decision, only candidates, or none.

    Reads the decision lanes directly rather than walking the reply.
    """
    if result.get("answer_basis") == "decision":
        return {"decision": "accepted"}
    rows = [row for lane in _WHY_LANES for row in result.get(lane) or ()]
    cards = result.get("target_context")
    for card in cards.values() if isinstance(cards, dict) else ():
        if isinstance(card, dict):
            rows.extend(row for lane in _WHY_CARD_LANES for row in card.get(lane) or ())
    if any(isinstance(row, dict) and row.get("authority") == "accepted" for row in rows):
        return {"decision": "accepted"}
    return {"decision": "candidate" if rows else "none"}


def _listed(result: dict[str, Any], *keys: str) -> int:
    for key in keys:
        if isinstance(result.get(key), list):
            return len(result[key])
    return 0


def _health(result: dict[str, Any]) -> dict[str, Any]:
    """Findings the reply reported (its total, else its list) and files it judged."""
    findings = _count(result.get("findings_total")) or _count(result.get("top_findings_total"))
    unresolved = result.get("unresolved")
    missed = len(unresolved) if isinstance(unresolved, list) else 0
    files = max(_listed(result, "targets") - missed, _listed(result, "metrics"))
    return {
        "findings": findings or _listed(result, "findings", "top_findings"),
        "files": files,
    }


def _dead_code(result: dict[str, Any]) -> dict[str, Any]:
    """Items matching across the tiers, and how many of them are high confidence."""
    tiers = result.get("tiers")
    if not isinstance(tiers, dict):
        return {}
    count = {name: _count(tier.get("count")) for name, tier in tiers.items() if isinstance(tier, dict)}
    return {"items": sum(count.values()), "high_confidence": count.get("high", 0)}


_RULES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "get_change_risk": _change_risk,
    "get_risk": _risk,
    "get_why": _why,
    "get_health": _health,
    "get_dead_code": _dead_code,
}


def check_outcome(tool: str, result: Any) -> dict[str, Any] | None:
    """The outcome of a check *tool*'s *result*, or ``None`` for any other call.

    ``None`` too for an error reply, which checked nothing. Raises only on a
    reply shape no tool produces; the caller drops the field then.
    """
    rule = _RULES.get(tool)
    if rule is None or not isinstance(result, dict) or result.get("error"):
        return None
    return rule(result)
