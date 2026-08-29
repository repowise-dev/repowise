"""Deterministic causal read model over raw performance findings.

Raw health findings remain the line-level source of truth.  This module folds
them once into bounded, ranked opportunities so repeated caller paths to one
sink lead an agent to one intervention.  It is deliberately persistence-
agnostic: analyzer dataclasses, ORM rows, and lightweight SQL rows are accepted
through the same attribute adapter.

This file is the public face of that model. The rules behind it live in four
modules with one owner each: :mod:`.facts` reads a row, :mod:`.causal` decides
what shares a cause and what that cause is called, :mod:`.actionability`
decides whether the evidence supports naming a change, and
:mod:`.opportunity_rank` decides the order. The orchestration below holds no
policy of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .actionability import (
    FixSafety,
    FixStrategy,
    OpportunityConfidence,
    PerformanceFix,
    fix_for,
    provenance_confidence,
)
from .causal import (
    PERFORMANCE_MODEL_VERSION,
    ExecutionContext,
    execution_context,
    group_observations,
    key_boundary,
    key_context,
    key_is_cross_function,
    link_performance_findings,
    opportunity_id_for_finding,
    shared_path_suffix,
    stable_id,
)
from .facts import evidence_row
from .opportunity_rank import dominant_marker, rank_factors, rank_sort_key, weakest_provenance


@dataclass(frozen=True, slots=True)
class PerformanceOpportunity:
    opportunity_id: str
    biomarker_type: str
    biomarker_types: tuple[str, ...]
    boundary_kind: str | None
    execution_context: ExecutionContext
    terminal_sink: str | None
    shared_path_suffix: tuple[str, ...]
    intervention_symbol: str | None
    affected_call_sites_total: int
    affected_files_total: int
    observations_total: int
    evidence: tuple[dict[str, Any], ...]
    evidence_truncated: bool
    reliable_entry_reachability: bool | None
    provenance: str
    confidence: OpportunityConfidence
    rank_score: int
    rank_factors: dict[str, int]
    fix: PerformanceFix | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "biomarker_type": self.biomarker_type,
            "biomarker_types": list(self.biomarker_types),
            "boundary_kind": self.boundary_kind,
            "execution_context": self.execution_context,
            "terminal_sink": self.terminal_sink,
            "shared_path_suffix": list(self.shared_path_suffix),
            "intervention_symbol": self.intervention_symbol,
            "affected_call_sites_total": self.affected_call_sites_total,
            "affected_files_total": self.affected_files_total,
            "observations_total": self.observations_total,
            "evidence": list(self.evidence),
            "evidence_truncated": self.evidence_truncated,
            "reliable_entry_reachability": self.reliable_entry_reachability,
            "provenance": self.provenance,
            "confidence": self.confidence,
            "rank_score": self.rank_score,
            "rank_factors": dict(self.rank_factors),
            "fix": self.fix.as_dict() if self.fix else None,
        }


def _reachability(values: set[Any]) -> bool | None:
    """One reachable caller makes the group reachable; unknown outranks False."""
    if True in values:
        return True
    return False if values == {False} else None


def build_performance_opportunities(
    findings: list[Any], *, evidence_limit: int = 8
) -> list[PerformanceOpportunity]:
    """Group and rank performance rows in one deterministic pass."""
    cap = max(0, evidence_limit)
    opportunities: list[PerformanceOpportunity] = []
    for key, members in group_observations(findings).items():
        # Context and boundary are kernel inputs, so the group already agrees on
        # them by construction. Reading them off the key keeps one owner for the
        # classification instead of reclassifying a representative row.
        context = key_context(key)
        boundary = key_boundary(key)
        paths = [facts.path for facts in members if facts.has_path]
        suffix = shared_path_suffix(paths)
        markers = tuple(sorted({facts.marker for facts in members}))
        marker = dominant_marker(markers)
        sites = {facts.site for facts in members}
        provenance = weakest_provenance({facts.provenance for facts in members})
        reachable = _reachability({facts.reliable_entry_reachability for facts in members})
        factors = rank_factors(
            marker=marker,
            boundary=boundary,
            context=context,
            reachable=reachable,
            site_count=len(sites),
            provenance=provenance,
        )
        opportunities.append(
            PerformanceOpportunity(
                opportunity_id=stable_id(key),
                biomarker_type=marker,
                biomarker_types=markers,
                boundary_kind=boundary,
                execution_context=context,
                terminal_sink=paths[0][-1] if paths else None,
                shared_path_suffix=suffix,
                intervention_symbol=suffix[0] if suffix else None,
                affected_call_sites_total=len(sites),
                affected_files_total=len({site[0] for site in sites}),
                observations_total=len(members),
                evidence=tuple(evidence_row(facts) for facts in members[:cap]),
                evidence_truncated=len(members) > cap,
                reliable_entry_reachability=reachable,
                provenance=provenance,
                confidence=provenance_confidence(provenance),
                rank_score=sum(factors.values()),
                rank_factors=factors,
                fix=fix_for(
                    marker,
                    markers,
                    boundary,
                    [facts.details for facts in members],
                    cross_function=key_is_cross_function(key),
                    shared_suffix=suffix,
                ),
            )
        )
    opportunities.sort(key=rank_sort_key)
    return opportunities


__all__ = [
    "PERFORMANCE_MODEL_VERSION",
    "FixSafety",
    "FixStrategy",
    "PerformanceFix",
    "PerformanceOpportunity",
    "build_performance_opportunities",
    "execution_context",
    "link_performance_findings",
    "opportunity_id_for_finding",
    "provenance_confidence",
]
