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

from ...execution_roles import hottest_role
from ..worth import dormant
from .actionability import (
    ActionabilityState,
    FixSafety,
    FixStrategy,
    OpportunityConfidence,
    PerformanceFix,
    actionability,
    assess_fix,
    expected_reason,
    provenance_confidence,
)
from .causal import (
    PERFORMANCE_MODEL_VERSION,
    ExecutionContext,
    InterventionKind,
    execution_context,
    group_observations,
    key_boundary,
    key_context,
    key_intervention_kind,
    key_intervention_symbol,
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
    loop_magnitude,
    may_lead,
    rank_factors,
    rank_sort_key,
    weakest_provenance,
    why_ranked,
)
from .siblings import link_siblings


@dataclass(frozen=True, slots=True)
class PerformanceOpportunity:
    """One cause, its evidence, and what can be done about it.

    Its facets are reported separately and must not be read as one another.
    Two of them keep the names callers already join on: ``confidence`` is
    evidence confidence, and ``fix.safety`` is fix safety. The rest live in
    ``facets``, so no number or label is published twice.
    """

    opportunity_id: str
    performance_model_version: int
    biomarker_type: str
    biomarker_types: tuple[str, ...]
    boundary_kind: str | None
    execution_context: ExecutionContext
    # The one sink every member reaches, else ``None``; all of them are in
    # ``terminal_sinks``, because one loop can reach several.
    terminal_sink: str | None
    shared_path_suffix: tuple[str, ...]
    intervention_symbol: str
    intervention_kind: InterventionKind
    terminal_sinks: tuple[str, ...]
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
    # Whether this group may lead the directive; see ``opportunity_rank.may_lead``.
    may_lead: bool
    # Other causes observed on the same lines; see :mod:`.siblings`.
    siblings: tuple[dict[str, Any], ...] = ()

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
            "intervention_kind": self.intervention_kind,
            "terminal_sinks": list(self.terminal_sinks),
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
            "may_lead": self.may_lead,
            "siblings": [dict(entry) for entry in self.siblings],
        }


def _reachability(values: set[Any]) -> bool | None:
    """One reachable caller makes the group reachable; unknown outranks False."""
    if True in values:
        return True
    return False if values == {False} else None


def _inert(facts: Any) -> bool:
    """A member that is no cost to fix: switched off by a constant-false flag,
    or a loop that already walks its keys in chunks."""
    return dormant(facts.details) or bool(facts.details.get("chunked_iteration"))


@dataclass(frozen=True, slots=True)
class _Reading:
    """What a group's live members say, read once."""

    live: list[Any]
    markers: tuple[str, ...]
    marker: str
    sites: set[Any]
    files: set[str]
    provenance: str
    reachable: bool | None
    role: str
    sinks: tuple[str, ...]
    magnitude: str


def _read_live(members: list[Any]) -> _Reading:
    """Read a group's facts off its live members.

    Inert members (:func:`_inert`) decide nothing while a live member exists.
    The loop size is read only off the members the group's role comes from:
    a request loop nobody measured never borrows a startup loop's growth, and
    a group is ``bounded`` only when every loop of its hottest role is.
    """
    live = [facts for facts in members if not _inert(facts)] or members
    markers = tuple(sorted({facts.marker for facts in live}))
    marker = dominant_marker(markers)
    sites = {facts.site for facts in live}
    role = hottest_role(facts.execution_role for facts in live)
    return _Reading(
        live=live,
        markers=markers,
        marker=marker,
        sites=sites,
        files={site[0] for site in sites},
        provenance=weakest_provenance({facts.provenance for facts in live}),
        reachable=_reachability({facts.reliable_entry_reachability for facts in live}),
        role=role,
        sinks=tuple(sorted({facts.terminal_sink for facts in live if facts.terminal_sink})),
        magnitude=loop_magnitude(
            marker, [f.details for f in live if hottest_role((f.execution_role,)) == role]
        ),
    )


def _facets(r: _Reading, confidence: str) -> dict[str, str]:
    return {
        "actionability_confidence": confidence,
        "exposure": exposure(r.reachable),
        "amplification": amplification(r.marker),
        "leverage": leverage(len(r.sites)),
        "change_risk": change_risk(len(r.files)),
        "loop_magnitude": r.magnitude,
        "execution_role": r.role,
    }


def _assemble(key: Any, members: list[Any], cap: int) -> PerformanceOpportunity:
    """Read one group's answers off its owners. Decides nothing itself.

    Context, boundary, and intervention are kernel inputs, so the group
    already agrees on them by construction. Taking them off the key keeps one
    owner for each instead of reclassifying a representative row. Sinks are
    members' facts, so they are listed rather than assumed shared. Everything
    else comes from :func:`_read_live`; inert members are listed last.
    """
    context = key_context(key)
    boundary = key_boundary(key)
    r = _read_live(members)
    evidence_confidence = provenance_confidence(r.provenance)
    assessment = assess_fix(
        r.marker,
        r.markers,
        boundary,
        [facts.details for facts in r.live],
        cross_function=any(facts.cross_function for facts in r.live),
    )
    acted = actionability(
        assessment,
        evidence_confidence,
        expected_reason=expected_reason(members, r.magnitude),
    )
    inputs = {
        "multiplier_shape": r.marker,
        "boundary_kind": boundary,
        "execution_context": context,
        "execution_role": r.role,
        "affected_call_sites": len(r.sites),
        "provenance": r.provenance,
        "loop_magnitude": r.magnitude,
    }
    factors = rank_factors(
        marker=r.marker,
        boundary=boundary,
        context=context,
        role=r.role,
        site_count=len(r.sites),
        provenance=r.provenance,
        magnitude=r.magnitude,
    )
    facets = _facets(r, acted.confidence)
    return PerformanceOpportunity(
        opportunity_id=stable_id(key),
        performance_model_version=PERFORMANCE_MODEL_VERSION,
        biomarker_type=r.marker,
        biomarker_types=r.markers,
        boundary_kind=boundary,
        execution_context=context,
        terminal_sink=r.sinks[0] if len(r.sinks) == 1 else None,
        shared_path_suffix=shared_path_suffix([facts.path for facts in r.live if facts.path]),
        intervention_symbol=key_intervention_symbol(key),
        intervention_kind=key_intervention_kind(key),
        terminal_sinks=r.sinks,
        resource_fingerprints=tuple(
            sorted({facts.resource_fingerprint for facts in members if facts.resource_fingerprint})
        ),
        affected_call_sites_total=len(r.sites),
        affected_files_total=len(r.files),
        observations_total=len(members),
        evidence=tuple(evidence_row(facts) for facts in sorted(members, key=_inert)[:cap]),
        evidence_truncated=len(members) > cap,
        reliable_entry_reachability=r.reachable,
        provenance=r.provenance,
        confidence=evidence_confidence,
        facets=facets,
        actionability_state=acted.state,
        actionability_reason=acted.reason,
        prerequisites=acted.prerequisites,
        rank_score=sum(factors.values()),
        rank_factors=factors,
        why_ranked=why_ranked(factors, inputs),
        fix=acted.fix,
        may_lead=may_lead(r.marker, {facts.details.get("orm") for facts in r.live}, facets),
    )


def build_performance_opportunities(
    findings: list[Any], *, evidence_limit: int = 8
) -> list[PerformanceOpportunity]:
    """Group, link, and rank performance rows in one deterministic pass."""
    cap = max(0, evidence_limit)
    opportunities: list[PerformanceOpportunity] = []
    sites: dict[str, set[Any]] = {}
    for key, members in group_observations(findings).items():
        opportunity = _assemble(key, members, cap)
        opportunities.append(opportunity)
        sites[opportunity.opportunity_id] = {facts.site for facts in members}
    return link_siblings(opportunities, sites, rank_sort_key)


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
