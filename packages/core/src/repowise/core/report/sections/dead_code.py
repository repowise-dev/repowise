"""Dead code by confidence tier, with the findings gated languages hold back.

Counts come from the stored summary every dead-code surface serves; the
stored ``safe_to_delete`` flag is never read.
"""

from __future__ import annotations

from typing import Any

from repowise.core.report.model import ReportContext, Section, check, report_sections

_METHOD = "static reachability findings, open status, tiered by confidence"


async def collect(ctx: ReportContext) -> list[dict[str, Any]]:
    from repowise.core.analysis.dead_code.serving import TIER_DESCRIPTIONS
    from repowise.core.components.store import dead_code_ran
    from repowise.core.persistence.crud.analysis.dead_code import get_dead_code_summary

    if not await dead_code_ran(ctx.session, ctx.repository_id):
        return [
            check(
                "dead-code",
                reason="dead-code analysis has not run for this repository",
                method=_METHOD,
                basis="unresolved",
            )
        ]
    summary = await get_dead_code_summary(ctx.session, ctx.repository_id)
    tiers = summary["confidence_summary"]
    out = [
        check(
            "dead-code",
            reason=(
                f"{summary['total_findings']} open finding(s), {summary['total_lines']} line(s) "
                "where counted"
            ),
            details=[f"{kind}: {n}" for kind, n in sorted(summary["by_kind"].items())],
            method=_METHOD,
            basis="inferred",
            data={"by_kind": summary["by_kind"], "lines": summary["total_lines"]},
        )
    ]
    for tier in ("high", "medium", "low"):
        out.append(
            check(
                f"dead-code:{tier}",
                reason=f"{tiers[tier]} finding(s) at {tier} confidence",
                details=[TIER_DESCRIPTIONS[tier]],
                method=_METHOD,
                basis="inferred",
                data={"count": tiers[tier]},
            )
        )
    for language, held in sorted(summary["gated"].items()):
        out.append(
            check(
                f"dead-code:gated:{language}",
                reason=f"{held['count']} finding(s) held back: {held['reason']}",
                method=_METHOD,
                basis="gated",
                data={"count": held["count"]},
            )
        )
    return out


report_sections.register(
    "dead-code",
    Section(
        title="Dead code",
        summary="Unreachable files and unused exports by confidence tier.",
        collect=collect,
        order=40,
    ),
)
