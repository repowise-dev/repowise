"""Group changed performance findings into the causal opportunities they belong to.

Performance is never ranked by defect ``health_impact`` — a performance finding
carries none by construction. Ranking reuses the opportunity layer's own order:
actionable work first, then rank score, then reach.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..health import HealthFindingData
from ..health.perf.opportunities import build_performance_opportunities
from ..health.perf.opportunity_rank import rank_sort_key

#: Supporting evidence rows kept per surfaced opportunity.
EVIDENCE_CAP = 3


@dataclass(slots=True)
class PerfOpportunityView:
    """One causal opportunity, reduced to what a change review needs."""

    opportunity_id: str
    rank_score: int
    actionability_state: str
    actionability_reason: str
    intervention_symbol: str | None
    terminal_sink: str | None
    boundary_kind: str | None
    execution_context: str
    amplification: str
    exposure: str
    affected_call_sites_total: int
    observations_total: int
    evidence: list[dict[str, Any]]

    @property
    def is_cross_function(self) -> bool:
        return self.intervention_symbol is not None

    def summary(self) -> str:
        if self.is_cross_function:
            return (
                f"{self.intervention_symbol} repeats {self.terminal_sink or 'a sink'} "
                f"across {self.affected_call_sites_total} call site(s)"
            )
        return f"{self.boundary_kind or 'work'} repeated in {self.execution_context} code"


def opportunities_for(findings: list[HealthFindingData]) -> list[PerfOpportunityView]:
    """Build and rank the opportunities covering *findings*."""
    rows = [f for f in findings if f.dimension == "performance"]
    if not rows:
        return []
    built = build_performance_opportunities(rows, evidence_limit=max(len(rows), EVIDENCE_CAP))
    return [_view(o) for o in sorted(built, key=rank_sort_key)]


def index_by_finding(
    views: list[PerfOpportunityView], findings: list[HealthFindingData]
) -> dict[int, PerfOpportunityView]:
    """Map ``id(finding)`` to the opportunity whose evidence names it.

    Identity by object rather than by value: two findings can share a file,
    symbol, and marker, and only their position tells them apart.
    """
    by_location: dict[tuple[str, int | None], PerfOpportunityView] = {}
    for view in views:
        for row in view.evidence:
            key = (str(row.get("file_path", "")), row.get("line_start"))
            by_location.setdefault(key, view)
    out: dict[int, PerfOpportunityView] = {}
    for finding in findings:
        view = by_location.get((finding.file_path, finding.line_start))
        if view is not None:
            out[id(finding)] = view
    return out


def _view(opportunity: Any) -> PerfOpportunityView:
    facets = dict(getattr(opportunity, "facets", {}) or {})
    evidence = [dict(row) for row in (getattr(opportunity, "evidence", ()) or ())]
    return PerfOpportunityView(
        opportunity_id=opportunity.opportunity_id,
        rank_score=opportunity.rank_score,
        actionability_state=opportunity.actionability_state,
        actionability_reason=opportunity.actionability_reason,
        intervention_symbol=opportunity.intervention_symbol,
        terminal_sink=opportunity.terminal_sink,
        boundary_kind=opportunity.boundary_kind,
        execution_context=str(opportunity.execution_context),
        amplification=facets.get("amplification", "unknown"),
        exposure=facets.get("exposure", "unknown"),
        affected_call_sites_total=opportunity.affected_call_sites_total,
        observations_total=opportunity.observations_total,
        evidence=evidence[:EVIDENCE_CAP],
    )
