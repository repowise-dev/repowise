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
:mod:`.opportunity_rank` decides how much it costs and in what order it lands.
The orchestration below holds no policy of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .actionability import (
    ActionabilityState,
    FixSafety,
    FixStrategy,
    OpportunityConfidence,
    PerformanceFix,
    actionability,
    assess_fix,
    provenance_confidence,
)
from .causal import (
    PERFORMANCE_MODEL_VERSION,
    ExecutionContext,
    execution_context,
    group_observations,
    key_boundary,
    key_context,
    key_intervention_symbol,
    key_is_cross_function,
    key_terminal_sink,
    link_performance_findings,
    model_state,
    opportunity_id_for_finding,
    opportunity_id_model_version,
    shared_path_suffix,
    stable_id,
)
from .facts import evidence_row
from .opportunity_rank import (
    amplification,
    change_risk,
    dominant_marker,
    exposure,
    leverage,
    rank_factors,
    rank_sort_key,
    weakest_provenance,
    why_ranked,
)


@dataclass(frozen=True, slots=True)
class PerformanceOpportunity:
    """One cause, its evidence, and what can be done about it.

    Seven facets are reported separately and must not be read as one another.
    Two of them keep the names callers already join on: ``confidence`` is
    evidence confidence, and ``fix.safety`` is fix safety. The remaining five
    live in ``facets``, so no number or label is published twice.
    """

    opportunity_id: str
    performance_model_version: int
    biomarker_type: str
    biomarker_types: tuple[str, ...]
    boundary_kind: str | None
    execution_context: ExecutionContext
    terminal_sink: str | None
    shared_path_suffix: tuple[str, ...]
    intervention_symbol: str | None
    resource_fingerprints: tuple[str, ...]
    affected_call_sites_total: int
    affected_files_total: int
    observations_total: int
    evidence: tuple[dict[str, Any], ...]
    evidence_truncated: bool
    reliable_entry_reachability: bool | None
    provenance: str
    confidence: OpportunityConfidence
    facets: dict[str, str]
    actionability_state: ActionabilityState
    actionability_reason: str
    prerequisites: tuple[str, ...]
    rank_score: int
    rank_factors: dict[str, int]
    why_ranked: tuple[dict[str, Any], ...]
    fix: PerformanceFix | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "performance_model_version": self.performance_model_version,
            "biomarker_type": self.biomarker_type,
            "biomarker_types": list(self.biomarker_types),
            "boundary_kind": self.boundary_kind,
            "execution_context": self.execution_context,
            "terminal_sink": self.terminal_sink,
            "shared_path_suffix": list(self.shared_path_suffix),
            "intervention_symbol": self.intervention_symbol,
            "resource_fingerprints": list(self.resource_fingerprints),
            "affected_call_sites_total": self.affected_call_sites_total,
            "affected_files_total": self.affected_files_total,
            "observations_total": self.observations_total,
            "evidence": list(self.evidence),
            "evidence_truncated": self.evidence_truncated,
            "reliable_entry_reachability": self.reliable_entry_reachability,
            "provenance": self.provenance,
            "confidence": self.confidence,
            "facets": dict(self.facets),
            "actionability_state": self.actionability_state,
            "actionability_reason": self.actionability_reason,
            "prerequisites": list(self.prerequisites),
            "rank_score": self.rank_score,
            "rank_factors": dict(self.rank_factors),
            "why_ranked": [dict(entry) for entry in self.why_ranked],
            "fix": self.fix.as_dict() if self.fix else None,
        }


def _reachability(values: set[Any]) -> bool | None:
    """One reachable caller makes the group reachable; unknown outranks False."""
    if True in values:
        return True
    return False if values == {False} else None


def _assemble(key: Any, members: list[Any], cap: int) -> PerformanceOpportunity:
    """Read one group's answers off its owners. Decides nothing itself.

    Context, boundary, intervention symbol, and terminal sink are kernel
    inputs, so the group already agrees on them by construction. Taking them
    off the key keeps one owner for each instead of reclassifying a
    representative row.
    """
    context = key_context(key)
    boundary = key_boundary(key)
    markers = tuple(sorted({facts.marker for facts in members}))
    marker = dominant_marker(markers)
    sites = {facts.site for facts in members}
    files = {site[0] for site in sites}
    provenance = weakest_provenance({facts.provenance for facts in members})
    evidence_confidence = provenance_confidence(provenance)
    reachable = _reachability({facts.reliable_entry_reachability for facts in members})
    assessment = assess_fix(
        marker,
        markers,
        boundary,
        [facts.details for facts in members],
        cross_function=key_is_cross_function(key),
    )
    acted = actionability(assessment, evidence_confidence)
    factors = rank_factors(
        marker=marker,
        boundary=boundary,
        context=context,
        reachable=reachable,
        site_count=len(sites),
        provenance=provenance,
    )
    return PerformanceOpportunity(
        opportunity_id=stable_id(key),
        performance_model_version=PERFORMANCE_MODEL_VERSION,
        biomarker_type=marker,
        biomarker_types=markers,
        boundary_kind=boundary,
        execution_context=context,
        terminal_sink=key_terminal_sink(key),
        shared_path_suffix=shared_path_suffix([facts.path for facts in members if facts.path]),
        intervention_symbol=key_intervention_symbol(key),
        resource_fingerprints=tuple(
            sorted({facts.resource_fingerprint for facts in members if facts.resource_fingerprint})
        ),
        affected_call_sites_total=len(sites),
        affected_files_total=len(files),
        observations_total=len(members),
        evidence=tuple(evidence_row(facts) for facts in members[:cap]),
        evidence_truncated=len(members) > cap,
        reliable_entry_reachability=reachable,
        provenance=provenance,
        confidence=evidence_confidence,
        facets={
            "actionability_confidence": acted.confidence,
            "exposure": exposure(reachable),
            "amplification": amplification(marker),
            "leverage": leverage(len(sites)),
            "change_risk": change_risk(len(files)),
        },
        actionability_state=acted.state,
        actionability_reason=acted.reason,
        prerequisites=acted.prerequisites,
        rank_score=sum(factors.values()),
        rank_factors=factors,
        why_ranked=why_ranked(
            factors,
            {
                "multiplier_shape": marker,
                "boundary_kind": boundary,
                "execution_context": context,
                "entry_reachability": reachable,
                "affected_call_sites": len(sites),
                "provenance": provenance,
            },
        ),
        fix=acted.fix,
    )


def build_performance_opportunities(
    findings: list[Any], *, evidence_limit: int = 8
) -> list[PerformanceOpportunity]:
    """Group and rank performance rows in one deterministic pass."""
    cap = max(0, evidence_limit)
    opportunities = [
        _assemble(key, members, cap) for key, members in group_observations(findings).items()
    ]
    opportunities.sort(key=rank_sort_key)
    return opportunities


__all__ = [
    "PERFORMANCE_MODEL_VERSION",
    "ActionabilityState",
    "FixSafety",
    "FixStrategy",
    "PerformanceFix",
    "PerformanceOpportunity",
    "build_performance_opportunities",
    "execution_context",
    "link_performance_findings",
    "model_state",
    "opportunity_id_for_finding",
    "opportunity_id_model_version",
    "provenance_confidence",
]
