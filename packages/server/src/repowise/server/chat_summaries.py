"""One-line summaries of chat tool results, used as artifact titles."""

from __future__ import annotations

from typing import Any


def _build_tool_summary(tool_name: str, result: dict[str, Any]) -> str:
    """Build a short summary string from a tool result."""
    if "error" in result:
        return f"Error: {result['error']}"

    if tool_name == "get_overview":
        title = result.get("title", "")
        modules = len(result.get("key_modules", []))
        return f"Overview: {title} ({modules} key modules)"

    if tool_name == "get_context":
        targets = result.get("targets", {})
        return f"Context for {len(targets)} target(s)"

    if tool_name == "get_risk":
        targets = result.get("targets", {})
        increasing = sum(1 for t in targets.values() if t.get("trend") == "increasing")
        bug_prone = sum(1 for t in targets.values() if t.get("risk_type") == "bug-prone")
        parts = [f"Risk assessment for {len(targets)} file(s)"]
        if increasing:
            parts.append(f"{increasing} increasing")
        if bug_prone:
            parts.append(f"{bug_prone} bug-prone")
        return ", ".join(parts)

    if tool_name == "get_change_risk":
        ref = result.get("ref", "change")
        priority = result.get("review_priority") or result.get("classification") or "unknown"
        pct = result.get("risk_percentile")
        if pct is not None:
            return f"Change risk for {ref}: {priority} (p{pct})"
        score = result.get("score")
        if score is not None:
            return f"Change risk for {ref}: {priority} (score {score})"
        return f"Change risk for {ref}: {priority}"

    if tool_name == "get_why":
        mode = result.get("mode", "")
        if mode == "health":
            counts = result.get("counts", {})
            return (
                f"Decision health: {counts.get('active', 0)} active, {counts.get('stale', 0)} stale"
            )
        if mode == "path":
            decisions = result.get("decisions", [])
            alignment = result.get("alignment", {})
            score = alignment.get("score", "unknown")
            origin = result.get("origin_story", {})
            author = (
                origin.get("primary_author", "unknown") if origin.get("available") else "unknown"
            )
            # ``decisions`` is capped by the path-mode projection; report what
            # governs the file, not how many survived the cap.
            total = result.get("decisions_total", len(decisions))
            return f"{total} decision(s), alignment: {score}, author: {author}"
        decisions = result.get("decisions", [])
        return f"Found {len(decisions)} decision(s)"

    if tool_name == "search_codebase":
        results = result.get("results", [])
        return f"Found {len(results)} result(s)"

    if tool_name == "get_dead_code":
        summary = result.get("summary", {})
        tiers = result.get("tiers", {})
        high_count = tiers.get("high", {}).get("count", 0)
        total = summary.get("total_findings", 0)
        lines = summary.get("deletable_lines", 0)
        return f"{total} findings ({high_count} high-confidence), {lines} deletable lines"

    return "Completed"
