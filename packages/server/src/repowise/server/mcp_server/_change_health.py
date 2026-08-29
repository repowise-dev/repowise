"""Render a change-health comparison as the action-first half of the response.

The tool owns orchestration; this module owns what an agent is told first and
how little of it there is. Everything here is derived from the core delta, so
the MCP surface holds no comparison policy of its own.
"""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.change_health.models import ChangeFinding, ChangeHealthDelta

#: Actionable findings carried in the default response. The rest are counted
#: and recoverable by an exact call, never silently dropped.
TOP_FINDINGS_LIMIT = 3

#: Reasons carried on the directive.
REASON_LIMIT = 3

_BLOCKING_SEVERITIES = {"high", "critical"}

#: Dimensions whose findings are advice, not a gate. A performance advisory
#: never blocks on its own; it ranks and it explains.
_ADVISORY_DIMENSIONS = {"performance"}


def directive(delta: ChangeHealthDelta, tests: dict[str, Any] | None) -> dict[str, Any]:
    """The first thing an agent reads: a verdict and what to do next."""
    status, headline = _verdict(delta)
    return {
        "status": status,
        "headline": headline,
        "reasons": _reasons(delta)[:REASON_LIMIT],
        "next_actions": _next_actions(delta, tests),
    }


def health_delta_block(delta: ChangeHealthDelta, *, revspec: str | None) -> dict[str, Any]:
    """The compact delta: what got worse, how much was compared, and how sure."""
    emitted = delta.findings[:TOP_FINDINGS_LIMIT]
    block: dict[str, Any] = {
        "status": delta.status,
        "explanation": delta.explanation,
        "basis": delta.comparison_basis,
        "introduced": delta.introduced_total,
        "worsened": delta.worsened_total,
        "resolved": delta.resolved_total,
        "by_dimension": _counts(delta.findings, "dimension"),
        "by_severity": _counts(delta.findings, "severity"),
        "scope": delta.scope.as_dict(),
        "top_findings": [finding_row(f, revspec) for f in emitted],
        "findings_total": len(delta.findings),
        "findings_emitted": len(emitted),
    }
    if delta.base is not None:
        block["base"] = delta.base.as_dict()
    if delta.head is not None:
        block["head"] = delta.head.as_dict()
    if delta.fingerprint is not None:
        block["analyzer"] = delta.fingerprint.as_dict()
    if len(delta.findings) > len(emitted):
        block["findings_reduced_reason"] = "top_findings_cap"
        block["all_findings_via"] = _call(revspec, extra="include=['findings']")
    if delta.skipped:
        block["skipped"] = _skipped(delta.skipped)
    if delta.limits:
        block["limits"] = delta.limits
    return block


def finding_row(finding: ChangeFinding, revspec: str | None) -> dict[str, Any]:
    """One surfaced finding, with the exact call that expands it."""
    row: dict[str, Any] = {
        "id": finding.change_finding_id,
        "change": finding.change_kind,
        "dimension": finding.dimension,
        "biomarker": finding.biomarker_type,
        "severity": finding.severity,
        "path": finding.path,
        "reason": finding.reason,
        "attribution": {
            "basis": finding.attribution_basis,
            "confidence": finding.attribution_confidence,
            "why": finding.attribution_detail,
        },
        "inspect": _call(revspec, extra=f"finding_id={finding.change_finding_id!r}"),
    }
    if finding.suggestion and finding.suggestion != finding.reason:
        row["suggestion"] = finding.suggestion
    if finding.symbol:
        row["symbol"] = finding.symbol
    if finding.line_start is not None:
        row["lines"] = [finding.line_start, finding.line_end or finding.line_start]
    if finding.severity_before:
        row["severity_before"] = finding.severity_before
    if finding.opportunity_id:
        row["opportunity_id"] = finding.opportunity_id
        row["opportunity_rank"] = finding.opportunity_rank
    if finding.health_reference:
        row["health_reference"] = finding.health_reference
    return row


def finding_detail(finding: ChangeFinding, revspec: str | None) -> dict[str, Any]:
    """The drill-down view: the row plus the evidence behind it."""
    row = finding_row(finding, revspec)
    row.pop("inspect", None)
    row["evidence"] = finding.evidence
    row["health_impact"] = finding.health_impact
    return row


# -- internals --------------------------------------------------------------


def _verdict(delta: ChangeHealthDelta) -> tuple[str, str]:
    if delta.status in {"unavailable", "unsupported_range", "too_large", "timeout"}:
        return "unknown", f"Change health could not be compared: {delta.explanation}"
    if delta.status in {"analyzer_mismatch", "rules_mismatch", "stale_baseline"}:
        return "unknown", f"No trustworthy baseline: {delta.explanation}"
    blocking = [
        f
        for f in delta.findings
        if f.severity in _BLOCKING_SEVERITIES and f.dimension not in _ADVISORY_DIMENSIONS
    ]
    if blocking:
        lead = blocking[0]
        return "review_required", (
            f"{len(blocking)} new {_plural('finding', len(blocking))} "
            f"{'needs' if len(blocking) == 1 else 'need'} review, "
            f"starting with {lead.biomarker_type} in {lead.path}."
        )
    if delta.findings:
        return "review_recommended", (
            f"{len(delta.findings)} new {_plural('finding', len(delta.findings))} "
            "of low or advisory severity."
        )
    if delta.status == "partial":
        return "unknown", (
            "Nothing new in what was compared, but part of the change was not analysed."
        )
    return "clear_in_analyzed_scope", "No supported new findings surfaced in the analyzed scope."


def _reasons(delta: ChangeHealthDelta) -> list[str]:
    reasons = [
        f"{f.severity} {f.dimension}: {f.biomarker_type} in "
        f"{f.symbol or f.path} ({f.attribution_basis})"
        for f in delta.findings
    ]
    if delta.skipped:
        reasons.append(
            f"{len(delta.skipped)} changed {_plural('file', len(delta.skipped))} "
            "were not analysed, so this is not a clean bill."
        )
    return reasons


def _next_actions(delta: ChangeHealthDelta, tests: dict[str, Any] | None) -> list[str]:
    actions: list[str] = []
    for finding in delta.findings[:2]:
        where = f"{finding.path}:{finding.line_start}" if finding.line_start else finding.path
        actions.append(f"Inspect {where} ({finding.change_finding_id})")
    to_run = (tests or {}).get("tests_to_run") or []
    if to_run:
        actions.append(f"Run: {' '.join(to_run[:3])}")
    elif tests and tests.get("status") in {"no_map", "no_index"}:
        actions.append("No measured test map; run the suite covering the changed files.")
    if delta.skipped:
        actions.append("Review the skipped files by hand; they were not compared.")
    return actions


def _counts(findings: list[ChangeFinding], attribute: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        key = str(getattr(finding, attribute))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _skipped(skipped: dict[str, str]) -> dict[str, Any]:
    by_reason: dict[str, int] = {}
    for reason in skipped.values():
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return {"total": len(skipped), "by_reason": by_reason}


def _call(revspec: str | None, *, extra: str) -> str:
    ref = f"revspec={revspec!r}, " if revspec else ""
    return f"get_change_risk({ref}{extra})"


def _plural(word: str, count: int) -> str:
    return word if count == 1 else f"{word}s"
