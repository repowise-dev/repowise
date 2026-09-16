"""Render a change-health comparison as the action-first half of the response.

The tool owns orchestration; this module owns what an agent is told first and
how little of it there is. Everything here is derived from the core delta, so
the MCP surface holds no comparison policy of its own.
"""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.change_health.models import ChangeFinding, ChangeHealthDelta
from repowise.core.analysis.review_directive import (
    CoveringTestEvidence,
    ReviewAction,
    ReviewDirective,
    review_directive,
)

#: Actionable findings carried in the default response. The rest are counted
#: and recoverable by an exact call, never silently dropped.
TOP_FINDINGS_LIMIT = 3

#: Reasons carried on the directive.
REASON_LIMIT = 3

#: Findings whose next-action is carried in the default response.
_ACTIONABLE_FINDINGS = 2

#: Test ids rendered into a single run command.
_TESTS_SHOWN = 3


def directive(delta: ChangeHealthDelta, tests: dict[str, Any] | None) -> dict[str, Any]:
    """The first thing an agent reads: a verdict and what to do next.

    The verdict is decided by core. This caps it and renders it.
    """
    decided = review_directive(delta, _test_evidence(tests))
    return {
        "status": decided.status,
        "headline": decided.headline,
        "reasons": list(decided.reasons[:REASON_LIMIT]),
        "next_actions": _render_actions(decided),
    }


def _test_evidence(tests: dict[str, Any] | None) -> CoveringTestEvidence:
    """Translate the tool's test block into the lane core reasons over."""
    if not tests:
        # No block at all: the lane was never consulted, so there is nothing to
        # report and nothing to ask for.
        return CoveringTestEvidence()
    to_run = tuple(tests.get("tests_to_run") or ())
    if to_run:
        state = "available"
    elif tests.get("status") in {"no_map", "no_index"}:
        state = "unavailable"
    else:
        state = "available"
    return CoveringTestEvidence(state=state, tests_to_run=to_run, basis=tests.get("basis"))


def _render_actions(decided: ReviewDirective) -> list[str]:
    """Strings for the wire. Every cap and every join belongs here, not in core."""
    out: list[str] = []
    inspected = 0
    for action in decided.actions:
        rendered = _render_action(action, inspected)
        if rendered is None:
            continue
        if action.kind == "inspect_finding":
            inspected += 1
        out.append(rendered)
    return out


def _render_action(action: ReviewAction, inspected: int) -> str | None:
    if action.kind == "inspect_finding":
        if inspected >= _ACTIONABLE_FINDINGS:
            return None
        return f"{action.explanation} ({action.targets[0]})"
    if action.kind == "run_tests":
        return f"Run: {' '.join(action.targets[:_TESTS_SHOWN])}"
    return action.explanation


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
