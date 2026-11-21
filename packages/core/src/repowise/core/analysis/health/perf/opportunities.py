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

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ....evidence.query_counts import strongest
from ....ingestion.symbol_identity import file_of_symbol
from ...execution_roles import COLD_ROLES, ENTRY_ROLES, hottest_role
from ..worth import dormant
from .actionability import (
    MEASURED_HOTSPOT,
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
    settled_context,
    shared_path_suffix,
    stable_id,
)
from .facts import evidence_row
from .opportunity_rank import (
    amplification,
    change_risk,
    dominant_marker,
    exposure,
    growth_source,
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
    growth: str
    # The leading workload whose newest run ranks a live member's loop owner.
    measured: str | None
    # The unit-test workload whose newest run ranks one, when no leading one does.
    in_tests: str | None
    # What query counts at a live member's loop owner or call path say.
    queries: Any


def _read_live(
    members: list[Any],
    measured: Mapping[str, str],
    queries: Mapping[str, Any],
    in_tests: Mapping[str, str],
) -> _Reading:
    """Read a group's facts off its live members.

    Inert members (:func:`_inert`) decide nothing while a live member exists.
    The loop size is read only off the members the group's role comes from:
    a request loop nobody measured never borrows a startup loop's growth, and
    a group is ``bounded`` only when every loop of its hottest role is.

    *measured* maps a function a leading workload ranks to that workload. One
    live member whose loop owner is in it makes the group measured, which
    overrides the static guesses that it runs rarely or never. *in_tests* is
    the same for unit-test runs: it marks the group measured in tests and
    overrides no guess about where the code runs.
    """
    live = [facts for facts in members if not _inert(facts)] or members
    markers = tuple(sorted({facts.marker for facts in live}))
    marker = dominant_marker(markers)
    sites = {facts.site for facts in live}
    role = hottest_role(facts.execution_role for facts in live)
    owners = [f for f in live if hottest_role((f.execution_role,)) == role]
    lead = _measured_workload(live, measured)
    return _Reading(
        live=live,
        markers=markers,
        marker=marker,
        sites=sites,
        files={site[0] for site in sites},
        provenance=weakest_provenance({facts.provenance for facts in live}),
        # A hot role is an entry reaching the group, a handler a framework
        # registers included, which the entry-file walk does not see.
        reachable=True
        if role in ENTRY_ROLES
        else _reachability({facts.reliable_entry_reachability for facts in live}),
        role=role,
        sinks=tuple(sorted({facts.terminal_sink for facts in live if facts.terminal_sink})),
        magnitude=loop_magnitude(marker, [f.details for f in owners]),
        growth=growth_source(marker, owners),
        measured=lead,
        in_tests=None if lead else _measured_workload(live, in_tests),
        queries=_query_reading(live, queries),
    )


def _query_reading(live: list[Any], queries: Mapping[str, Any]) -> Any:
    """The strongest query-count reading at any live member's loop owner or on
    its call path to the sink: the statements issue there, not always in the
    function that loops."""
    sites = set()
    for f in live:
        sites.add(f.details.get("role_owner"))
        sites.update(f.details.get("path") or ())
    return strongest(queries[s] for s in sites if s in queries)


def _measured_workload(live: list[Any], measured: Mapping[str, str]) -> str | None:
    """The first by name of the workloads that rank a live member's loop owner."""
    names = {name for f in live if (name := measured.get(f.details.get("role_owner") or ""))}
    return min(names) if names else None


def _facets(
    r: _Reading, confidence: str, cold_name: bool, confirmed: str | None
) -> dict[str, str]:
    return {
        "actionability_confidence": confidence,
        "exposure": exposure(r.reachable),
        "amplification": amplification(r.marker),
        "leverage": leverage(len(r.sites)),
        "change_risk": change_risk(len(r.files)),
        "loop_magnitude": r.magnitude,
        "growth_source": r.growth,
        "execution_role": r.role,
        # Only with runtime evidence, so a repo without any stores what it did.
        **({"measured": r.measured} if r.measured else {}),
        **({"measured_in_tests": r.in_tests} if r.in_tests else {}),
        # The name said it runs once and a run said otherwise: kept, never
        # led, unless the run measured it above the lead bar (``confirmed``).
        **({"measured_overrides": "cold_path"} if r.measured and cold_name else {}),
        **_query_facets(r.queries),
        **({"confirmed": confirmed} if confirmed else {}),
    }


def _confirmed(
    r: _Reading, hot: frozenset[str], hot_in_tests: frozenset[str], cold: bool = False
) -> str | None:
    """What confirms the cause, or ``None``: exact evidence at its call path
    (``query_count``: statements grow with the items), else a live member's
    loop owner a leading workload measured above the lead bar
    (``lead_candidate``, ``measured``), else one a unit-test run did
    (``measured_in_tests``). For a *cold* cause (its role or name says it runs
    once per process) the measurement comes first: it is what answers that
    guess, and query counts say nothing about how often the code runs."""
    owners = {f.details.get("role_owner") for f in r.live}
    if cold and owners & hot:
        return "measured"
    if r.queries is not None and r.queries.verdict == "grows":
        return "query_count"
    if owners & hot:
        return "measured"
    if owners & hot_in_tests:
        return "measured_in_tests"
    return None


def _query_facets(reading: Any) -> dict[str, str]:
    """``queries`` (``grows``, ``flat`` or ``ran``) and the largest count seen,
    only when query counts reached the group."""
    if reading is None:
        return {}
    out = {"queries": reading.verdict, "query_statements": str(reading.statements)}
    if reading.items is not None:
        out["query_items"] = str(reading.items)
    return out


def _assemble(
    key: Any,
    members: list[Any],
    cap: int,
    origins: Mapping[str, str | None],
    measured: Mapping[str, str],
    queries: Mapping[str, Any],
    hot: frozenset[str],
    in_tests: Mapping[str, str],
    hot_in_tests: frozenset[str],
) -> PerformanceOpportunity:
    """Read one group's answers off its owners. Decides nothing itself.

    Context, boundary, and intervention are kernel inputs, so the group
    already agrees on them by construction. Taking them off the key keeps one
    owner for each instead of reclassifying a representative row. A stored
    origin may settle an ``unknown`` context; the id keeps the key's. Sinks
    are members' facts, so they are listed rather than assumed shared.
    Everything else comes from :func:`_read_live`; inert members are listed
    last.
    """
    context = settled_context(key_context(key), {facts.file_path for facts in members}, origins)
    boundary = key_boundary(key)
    # Statement counts speak only to a cause that crosses a database boundary.
    r = _read_live(members, measured, queries if boundary == "db" else {}, in_tests)
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
        expected_reason=expected_reason(members, r.magnitude, measured=r.measured is not None),
    )
    cold_name = r.measured is not None and expected_reason(members, r.magnitude) == "cold_path"
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
    cold = cold_name or r.role in COLD_ROLES
    facets = _facets(r, acted.confidence, cold_name, _confirmed(r, hot, hot_in_tests, cold))
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


#: Rank points for a measured hotspot's place in its run: 3 for the top
#: function, then one fewer per place. The other rank terms are the same for
#: every hotspot, so this orders them by what the run measured. Ceiling: from
#: the fourth place on, hotspots tie and fall to their id.
_TOP_PLACE_POINTS = 3


def _measured_hotspot(symbol_id: str, workload: str, rank: int | None) -> PerformanceOpportunity:
    """A work item for a function a leading workload measured above the lead
    bar where no static finding names a cause.

    The run is the proof of cost (``confirmed`` ``measured``) and of reach
    (``measured``), so it is plan-ready work in production, never a guess:
    ``evidence.hotness`` lists production functions only (``code_origin``),
    charges library and built-in time to the repo frame that called it, never
    counts a wait as self time, and ranks no stale row, so a framework,
    standard-library or blocked frame cannot be one. What to change is the
    reader's call, so its one step is a judgment and its fix is advisory.
    Ceiling: a function whose own work is inherent (a parser, a writer) clears
    the same bar; the share bar is what keeps those few.
    """
    file_path = file_of_symbol(symbol_id)
    function = symbol_id[len(file_path) + 2 :] or None
    key = (MEASURED_HOTSPOT, "production", MEASURED_HOTSPOT, None, file_path, function)
    marker, magnitude, role = MEASURED_HOTSPOT, "unknown", "unknown"
    factors = rank_factors(
        marker=marker,
        boundary=None,
        context="production",
        role=role,
        site_count=1,
        provenance="direct",
        magnitude=magnitude,
    )
    factors["measured_rank"] = max(0, _TOP_PLACE_POINTS + 1 - (rank or _TOP_PLACE_POINTS + 1))
    inputs = {
        "measured_rank": rank,
        "multiplier_shape": marker,
        "boundary_kind": None,
        "execution_context": "production",
        "execution_role": role,
        "affected_call_sites": 1,
        "provenance": "direct",
        "loop_magnitude": magnitude,
    }
    facets = {
        "actionability_confidence": "high",
        "exposure": exposure(None),
        "amplification": amplification(marker),
        "leverage": leverage(1),
        "change_risk": change_risk(1),
        "loop_magnitude": magnitude,
        "growth_source": "n/a",
        "execution_role": role,
        "measured": workload,
        "confirmed": "measured",
    }
    fix = PerformanceFix(
        "reduce_measured_self_time",
        "advisory",
        f"The {workload} run measured this function above the lead bar and no "
        "static check names the cause: profile it to find the repeated work.",
    )
    return PerformanceOpportunity(
        opportunity_id=stable_id(key),
        performance_model_version=PERFORMANCE_MODEL_VERSION,
        biomarker_type=marker,
        biomarker_types=(marker,),
        boundary_kind=None,
        execution_context="production",
        terminal_sink=None,
        shared_path_suffix=(),
        intervention_symbol=key_intervention_symbol(key),
        intervention_kind=key_intervention_kind(key),
        terminal_sinks=(),
        resource_fingerprints=(),
        affected_call_sites_total=1,
        affected_files_total=1,
        observations_total=0,
        evidence=(
            {
                "finding_id": None,
                "file_path": file_path,
                "biomarker_type": marker,
                "function_name": function,
                "line_start": None,
                "line_end": None,
                "reason": fix.rationale,
                "path": [],
                "provenance": "direct",
            },
        ),
        evidence_truncated=False,
        reliable_entry_reachability=None,
        provenance="direct",
        confidence="high",
        facets=facets,
        actionability_state="plan_ready",
        actionability_reason="measured_cost",
        prerequisites=(),
        rank_score=sum(factors.values()),
        rank_factors=factors,
        why_ranked=why_ranked(factors, inputs),
        fix=fix,
        may_lead=may_lead(marker, set(), facets),
    )


def measured_hotspots(
    groups: Mapping[Any, list[Any]],
    measured: Mapping[str, str],
    hot: frozenset[str],
    ranks: Mapping[str, int] | None = None,
) -> list[PerformanceOpportunity]:
    """One :func:`_measured_hotspot` per function in *hot* that no static
    cause owns.

    A static group whose live member's loop owner is the function already
    carries the measurement (:func:`_confirmed`, read off the same live
    members as :func:`_read_live`), so the function gets no second item:
    never both. Ceiling: a static cause with no fix strategy (an observation)
    still owns it, so its measured cost shows as that observation's mark,
    not as work. *hot* is a leading workload's ``lead_candidate`` tier only,
    so a unit-test run never mints one.
    """
    owned = {
        f.details.get("role_owner")
        for members in groups.values()
        for f in ([m for m in members if not _inert(m)] or members)
    }
    return [
        _measured_hotspot(symbol_id, measured[symbol_id], (ranks or {}).get(symbol_id))
        for symbol_id in sorted(hot - owned)
        if symbol_id in measured and "::" in symbol_id
    ]


def build_performance_opportunities(
    findings: list[Any],
    *,
    evidence_limit: int = 8,
    origins: Mapping[str, str | None] | None = None,
    measured: Mapping[str, str] | None = None,
    queries: Mapping[str, Any] | None = None,
    hot: frozenset[str] = frozenset(),
    in_tests: Mapping[str, str] | None = None,
    hot_in_tests: frozenset[str] = frozenset(),
    ranks: Mapping[str, int] | None = None,
) -> list[PerformanceOpportunity]:
    """Group, link, and rank performance rows in one deterministic pass.

    *origins* is each file's stored ``code_origin``, which settles an
    ``unknown`` context (:func:`.causal.settled_context`). *measured* maps
    each function a leading workload's newest run ranks to that workload's
    name; a group whose loop owner is one carries the ``measured`` facet.
    *queries* maps a call site's symbol id to its query-count reading
    (``evidence.query_counts``); a database cause reached by one carries the
    ``queries`` facet. *hot* holds the functions a leading workload
    measured in the ``lead_candidate`` tier; a cause inside one, or whose query counts grow, carries
    the ``confirmed`` facet. *in_tests* and *hot_in_tests* are the same two
    for unit-test runs: the ``measured_in_tests`` facet, and ``confirmed`` as
    ``measured_in_tests`` when nothing stronger confirms the cause. A
    function in *hot* that no static cause owns becomes a measured hotspot
    of its own (:func:`measured_hotspots`), ordered by *ranks*, its best
    place in a leading workload's run.
    """
    cap = max(0, evidence_limit)
    opportunities: list[PerformanceOpportunity] = []
    sites: dict[str, set[Any]] = {}
    groups = group_observations(findings)
    for opportunity in measured_hotspots(groups, measured or {}, hot, ranks):
        opportunities.append(opportunity)
        sites[opportunity.opportunity_id] = set()
    for key, members in groups.items():
        opportunity = _assemble(
            key,
            members,
            cap,
            origins or {},
            measured or {},
            queries or {},
            hot,
            in_tests or {},
            hot_in_tests,
        )
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
    "measured_hotspots",
    "model_state",
    "opportunity_id_for_finding",
    "opportunity_id_model_version",
    "provenance_confidence",
]
