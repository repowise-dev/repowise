"""One-line summaries of chat tool results, used as artifact titles."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _overview_summary(result: dict[str, Any]) -> str:
    title = result.get("title", "")
    modules = len(result.get("key_modules", []))
    return f"Overview: {title} ({modules} key modules)"


def _context_summary(result: dict[str, Any]) -> str:
    targets = result.get("targets", {})
    return f"Context for {len(targets)} target(s)"


def _risk_summary(result: dict[str, Any]) -> str:
    targets = result.get("targets", {})
    increasing = sum(1 for t in targets.values() if t.get("trend") == "increasing")
    bug_prone = sum(1 for t in targets.values() if t.get("risk_type") == "bug-prone")
    parts = [f"Risk assessment for {len(targets)} file(s)"]
    if increasing:
        parts.append(f"{increasing} increasing")
    if bug_prone:
        parts.append(f"{bug_prone} bug-prone")
    return ", ".join(parts)


def _change_risk_summary(result: dict[str, Any]) -> str:
    ref = result.get("ref", "change")
    priority = result.get("review_priority") or result.get("classification") or "unknown"
    pct = result.get("risk_percentile")
    if pct is not None:
        return f"Change risk for {ref}: {priority} (p{pct})"
    score = result.get("score")
    if score is not None:
        return f"Change risk for {ref}: {priority} (score {score})"
    return f"Change risk for {ref}: {priority}"


def _why_path_summary(result: dict[str, Any]) -> str:
    decisions = result.get("decisions", [])
    alignment = result.get("alignment", {})
    score = alignment.get("score", "unknown")
    origin = result.get("origin_story", {})
    author = origin.get("primary_author", "unknown") if origin.get("available") else "unknown"
    # ``decisions`` is capped by the path-mode projection; report what
    # governs the file, not how many survived the cap.
    total = result.get("decisions_total", len(decisions))
    return f"{total} decision(s), alignment: {score}, author: {author}"


def _why_summary(result: dict[str, Any]) -> str:
    mode = result.get("mode", "")
    if mode == "health":
        counts = result.get("counts", {})
        return f"Decision health: {counts.get('active', 0)} active, {counts.get('stale', 0)} stale"
    if mode == "path":
        return _why_path_summary(result)
    decisions = result.get("decisions", [])
    return f"Found {len(decisions)} decision(s)"


def _search_summary(result: dict[str, Any]) -> str:
    results = result.get("results", [])
    return f"Found {len(results)} result(s)"


def _dead_code_summary(result: dict[str, Any]) -> str:
    summary = result.get("summary", {})
    tiers = result.get("tiers", {})
    high_count = tiers.get("high", {}).get("count", 0)
    total = summary.get("total_findings", 0)
    lines = summary.get("deletable_lines", 0)
    return f"{total} findings ({high_count} high-confidence), {lines} deletable lines"


_SUMMARIZERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "get_overview": _overview_summary,
    "get_context": _context_summary,
    "get_risk": _risk_summary,
    "get_change_risk": _change_risk_summary,
    "get_why": _why_summary,
    "search_codebase": _search_summary,
    "get_dead_code": _dead_code_summary,
}


def _build_tool_summary(tool_name: str, result: dict[str, Any]) -> str:
    """Build a short summary string from a tool result."""
    if "error" in result:
        return f"Error: {result['error']}"
    summarize = _SUMMARIZERS.get(tool_name)
    return summarize(result) if summarize else "Completed"
