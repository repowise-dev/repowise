"""Materialized performance opportunities: one writer, indexed readers.

Grouping runs once per health persistence transaction over the authoritative
stored findings, and everything that serves the queue reads the result. That is
the whole point of this module: a request should cost its page, not the
repository.

The writer is deliberately the only place that decides lifecycle. A cause that
stops being observed is resolved rather than deleted, so an id an agent is
holding keeps answering, and rows minted by an older model version are resolved
for the same reason - they can no longer describe the current tree.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, NamedTuple

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...batches import chunked
from ...models import (
    HealthFileMetric,
    HealthFinding,
    PerformanceOpportunity,
    PerformanceSummary,
    RefactoringSuggestion,
    _new_uuid,
    _now_utc,
)
from ...sql import order_by, rule_predicate
from .refactoring import _refactoring_row_kwargs
from .shown import gated_counts, ungated

if TYPE_CHECKING:
    from ....analysis.execution_roles import ExecutionRoles
    from ....analysis.health.perf.opportunities import (
        PerformanceOpportunity as OpportunityModel,
    )
    from ....analysis.health.refactoring.performance_fix import PerformancePlanPolicy

_PLAN_TYPE = "performance_fix"

_EVIDENCE_LIMIT = 8
"""Evidence rows folded into a persisted plan's affected-location list.

Serving reads evidence from the findings table by opportunity, so this bounds
the plan payload only.
"""

# A finding's queue judgement: its cause's, written by the finalizer.
_QUEUE_COLUMNS = (
    HealthFinding.queue_eligible,
    HealthFinding.queue_reason,
    HealthFinding.queue_value,
    HealthFinding.queue_tier,
)
# Columns the causal model reads off a row. Narrower than the ORM entity on
# purpose: the finalizer walks every open performance finding, and ``severity``
# and ``health_impact`` are not inputs to grouping.
_OBSERVATION_COLUMNS = (
    HealthFinding.id,
    HealthFinding.public_id,
    HealthFinding.file_path,
    HealthFinding.biomarker_type,
    HealthFinding.function_name,
    HealthFinding.line_start,
    HealthFinding.line_end,
    HealthFinding.reason,
    HealthFinding.details_json,
    HealthFinding.dimension,
    HealthFinding.opportunity_id,
)


def opportunity_details(
    opportunity: OpportunityModel, plan: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The explanatory half of one opportunity, the half no query filters on.

    Everything a column already holds is deliberately absent, so the row and
    the payload cannot drift into disagreeing about the same fact. *plan* is
    the stored plan's validation and economics, resolved once at finalize.
    """
    return {
        **({"plan": plan} if plan else {}),
        "biomarker_types": list(opportunity.biomarker_types),
        "intervention_kind": opportunity.intervention_kind,
        "terminal_sinks": list(opportunity.terminal_sinks),
        "shared_path_suffix": list(opportunity.shared_path_suffix),
        "resource_fingerprints": list(opportunity.resource_fingerprints),
        "reliable_entry_reachability": opportunity.reliable_entry_reachability,
        "provenance": opportunity.provenance,
        "facets": dict(opportunity.facets),
        "actionability_reason": opportunity.actionability_reason,
        "prerequisites": list(opportunity.prerequisites),
        "rank_factors": dict(opportunity.rank_factors),
        "why_ranked": [dict(entry) for entry in opportunity.why_ranked],
        "fix_rationale": opportunity.fix.rationale if opportunity.fix else None,
        "may_lead": opportunity.may_lead,
        **({"fix_api": opportunity.fix.api} if opportunity.fix and opportunity.fix.api else {}),
        "siblings": [dict(entry) for entry in opportunity.siblings],
    }


def _row_kwargs(
    opportunity: OpportunityModel,
    *,
    position: int,
    plan_state: str,
    analyzed_commit: str | None,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from ....analysis.health.perf.serving import intervention_file
    from ....analysis.health.queue.counts import perf_judgement
    from ....analysis.health.queue.eligibility import queue_proof

    fix = opportunity.fix
    row = {
        "opportunity_id": opportunity.opportunity_id,
        "performance_model_version": opportunity.performance_model_version,
        "status": "open",
        "rank_position": position,
        "rank_score": opportunity.rank_score,
        "execution_context": opportunity.execution_context,
        "boundary_kind": opportunity.boundary_kind,
        "biomarker_type": opportunity.biomarker_type,
        "actionability_state": opportunity.actionability_state,
        "evidence_confidence": opportunity.confidence,
        "plan_state": plan_state,
        "cost_proof": queue_proof(opportunity),
        "execution_role": opportunity.facets.get("execution_role", "unknown"),
        "fix_strategy": fix.strategy if fix else None,
        "fix_safety": fix.safety if fix else None,
        "file_path": intervention_file(opportunity),
        "intervention_symbol": opportunity.intervention_symbol,
        "terminal_sink": opportunity.terminal_sink,
        "observations_total": opportunity.observations_total,
        "affected_call_sites_total": opportunity.affected_call_sites_total,
        "affected_files_total": opportunity.affected_files_total,
        "details_json": json.dumps(opportunity_details(opportunity, plan), separators=(",", ":")),
        "analyzed_commit": analyzed_commit,
    }
    # Judged on the row as stored, which Fix first reads the same way.
    judged = {
        **row,
        "facets": opportunity.facets,
        "actionability_reason": opportunity.actionability_reason,
        "prerequisites": opportunity.prerequisites,
    }
    return {**row, **perf_judgement(judged, opportunity.facets, plan or {}).columns()}


def _summary_payload(
    opportunities: list[OpportunityModel],
    plan_states: dict[str, str],
    plans: dict[str, dict[str, Any]] | None = None,
) -> dict:
    """The compact current headline, written once and read by primary key."""
    from ....analysis.health.perf.serving import intervention_file
    from ....analysis.health.queue.eligibility import (
        perf_confident_verdict,
        perf_queue_counts,
        queue_proof,
    )

    counts: dict[str, int] = {}
    contexts: dict[str, int] = {}
    boundaries: dict[str, int] = {}
    proofs: dict[str, int] = {}
    for item in opportunities:
        counts[item.actionability_state] = counts.get(item.actionability_state, 0) + 1
        contexts[item.execution_context] = contexts.get(item.execution_context, 0) + 1
        key = item.boundary_kind or "none"
        boundaries[key] = boundaries.get(key, 0) + 1
        proof = queue_proof(item)
        proofs[proof] = proofs.get(proof, 0) + 1
    # The lead is the first confident cause of the default queue; never a marker
    # whose measured precision is below the bar for leading.
    lead = next(
        (o for o in opportunities if perf_confident_verdict(o).eligible and o.may_lead),
        None,
    )
    return {
        "default_queue": perf_queue_counts(opportunities),
        "actionability": counts,
        "context": contexts,
        "boundary": boundaries,
        "proof": proofs,
        "with_plan_total": sum(1 for state in plan_states.values() if state == "available"),
        "lead": None
        if lead is None
        else {
            "opportunity_id": lead.opportunity_id,
            "actionability_state": lead.actionability_state,
            "biomarker_type": lead.biomarker_type,
            "boundary_kind": lead.boundary_kind,
            "execution_context": lead.execution_context,
            "intervention_symbol": lead.intervention_symbol,
            "terminal_sink": lead.terminal_sink,
            "file_path": intervention_file(lead),
            "observations_total": lead.observations_total,
            "affected_call_sites_total": lead.affected_call_sites_total,
            "affected_files_total": lead.affected_files_total,
            "plan_state": plan_states.get(lead.opportunity_id, "no_safe_plan"),
            "why_ranked": [dict(entry) for entry in lead.why_ranked],
            "prerequisites": list(lead.prerequisites),
            "actionability_reason": lead.actionability_reason,
            **({"plan": plan} if (plan := (plans or {}).get(lead.opportunity_id)) else {}),
        },
    }


async def finalize_performance_opportunities(
    session: AsyncSession,
    repository_id: str,
    *,
    analyzed_commit: str | None = None,
    plan_policy: PerformancePlanPolicy | None = None,
    execution_roles: ExecutionRoles | None = None,
) -> int:
    """Rebuild this repository's performance read model from stored findings.

    Called by both the repo-wide and the per-path health writers, after their
    own rows land and inside their transaction, so the queue can never describe
    a set of findings that was never committed. Returns the number of open
    opportunities.

    *execution_roles* is this run's role map over the whole graph. A role can
    change in a file the run did not rescan (a seed or call moved elsewhere),
    so every stored finding is restamped from it before grouping.
    """
    # Deferred throughout this module: the analysis package imports
    # persistence, so a module-level import here would close the cycle.
    from ....analysis.health.perf.opportunities import build_performance_opportunities
    from ....analysis.health.refactoring.performance_fix import PerformancePlanPolicy

    policy = plan_policy or PerformancePlanPolicy()
    rows = list(
        (
            await session.execute(
                select(*_OBSERVATION_COLUMNS, *_QUEUE_COLUMNS).where(
                    HealthFinding.repository_id == repository_id,
                    HealthFinding.status == "open",
                    HealthFinding.dimension == "performance",
                )
            )
        ).all()
    )
    if execution_roles is not None:
        rows = await _restamp_roles(session, rows, execution_roles)
    measured = await _measured_functions(session, repository_id)
    opportunities = build_performance_opportunities(
        rows,
        evidence_limit=_EVIDENCE_LIMIT,
        origins=await _unknown_context_origins(session, repository_id, rows),
        measured=measured.measured,
        hot=measured.hot,
        in_tests=measured.in_tests,
        hot_in_tests=measured.hot_in_tests,
        ranks=measured.ranks,
        queries=await _query_readings(session, repository_id),
    )

    plan_states, plans = await _replace_plans(session, repository_id, opportunities, policy)
    verdicts = await _reconcile_opportunities(
        session, repository_id, opportunities, plan_states, analyzed_commit, plans
    )
    await _restamp_findings(session, rows, verdicts)
    await _write_summary(
        session,
        repository_id,
        opportunities,
        plan_states,
        analyzed_commit,
        plans,
        workloads=measured.workloads,
    )
    return len(opportunities)


async def _restamp_roles(
    session: AsyncSession, rows: list[Any], roles: ExecutionRoles
) -> list[Any]:
    """Each stored finding with its loop owner's current role; changed rows are written.

    A row stored before ``role_owner`` existed gets its owner found the way
    the analysis finds it, and keeps it from then on.
    """
    out: list[Any] = []
    changed = []
    for row in rows:
        details = json.loads(row.details_json or "{}")
        if "role_owner" in details:
            owner = details["role_owner"]
        else:
            owner = roles.owner_of(row.file_path, row.line_start, details)
        role = roles.role_of(owner, row.file_path)
        if details.get("execution_role") == role and "role_owner" in details:
            out.append(row)
            continue
        stamped = {**details, "execution_role": role, "role_owner": owner}
        details_json = json.dumps(stamped, separators=(",", ":"))
        changed.append({"id": row.id, "details_json": details_json})
        out.append(SimpleNamespace(**{**row._mapping, "details_json": details_json}))
    if changed:
        await session.execute(update(HealthFinding), changed)
    return out


class _Measured(NamedTuple):
    #: Function -> the leading workload that ranks it best.
    measured: dict[str, str]
    #: Functions a leading workload measured in the ``lead_candidate`` tier.
    hot: frozenset[str]
    #: The leading workloads whose runs measured any function: what a cause
    #: no facet marks was not exercised in.
    workloads: tuple[str, ...]
    #: The same two for ``unit_test`` runs, kept apart as measured in tests.
    in_tests: dict[str, str]
    hot_in_tests: frozenset[str]
    #: Function -> its best place in a leading workload's run (1 is the top).
    ranks: dict[str, int]


async def _measured_functions(session: AsyncSession, repository_id: str) -> _Measured:
    """Each function a leading workload's newest run ranks, with that workload's
    name, and the ones any such run puts in ``evidence.hotness``'s
    ``lead_candidate`` tier (the share and interval rule, or ranked in
    several runs).

    Read off ``workload_hotness``, which the health writers rebuild before
    this runs, so it describes the current code. Workload classes that may
    lead (``evidence.hotness.LEAD_CLASSES``) fill ``measured`` and ``hot``;
    ``unit_test`` runs fill ``in_tests`` and ``hot_in_tests`` apart, since a
    test run shows what the code costs, not what production runs, and never
    marks a cause not exercised. A deterministic tracer's order is not a
    measured cost (``ordering_only``), so it never counts, as it never leads
    in ``evidence.hotness``. A function two workloads rank takes the one that
    ranks it best, ties by name. One indexed lookup without evidence.
    """
    from ....evidence.hotness import LEAD_CLASSES
    from .evidence import ranked_by_workload

    best: dict[bool, dict[str, tuple[int, str]]] = {True: {}, False: {}}
    hot: dict[bool, set[str]] = {True: set(), False: set()}
    workloads: set[str] = set()
    for run, rows in await ranked_by_workload(session, repository_id):
        leads = run.workload_class in LEAD_CLASSES
        if not leads and run.workload_class != "unit_test":
            continue
        for row in rows:
            if row.ordering_only:
                continue
            if leads:
                workloads.add(run.workload_name)
            if row.tier == "lead_candidate":
                hot[leads].add(row.symbol_id)
            key = (row.rank, run.workload_name)
            if row.symbol_id not in best[leads] or key < best[leads][row.symbol_id]:
                best[leads][row.symbol_id] = key

    def names(by: dict[str, tuple[int, str]]) -> dict[str, str]:
        return {sid: name for sid, (_rank, name) in by.items()}

    return _Measured(
        names(best[True]),
        frozenset(hot[True]),
        tuple(sorted(workloads)),
        names(best[False]),
        frozenset(hot[False]),
        {sid: rank for sid, (rank, _name) in best[True].items()},
    )


async def _query_readings(session: AsyncSession, repository_id: str) -> dict[str, Any]:
    """Each call site's query-count reading (``evidence.query_counts``), by symbol id.

    Read from each workload's newest complete ``query_count`` run, so a
    superseded run never speaks for the current code and a partly mapped one,
    which is never a basis for ranking either, never decides. Sizes are
    compared within one run and one scope kind only: a test and a request, or
    two workloads, are not the same measurement. Ceiling: a newest run
    measured before the site's body changed still counts; query rows carry
    no body digest to tell. Two statements, the second only when such a run
    exists.
    """
    from ....evidence.hotness import newest_runs
    from ....evidence.query_counts import read_statement, strongest
    from ...models import EvidenceQueryCount, EvidenceRun

    runs = (
        await session.execute(
            select(EvidenceRun).where(
                EvidenceRun.repository_id == repository_id, EvidenceRun.kind == "query_count"
            )
        )
    ).scalars()
    keep = sorted(newest_runs(r for r in runs if not r.mapping_partial))
    if not keep:
        return {}
    q = EvidenceQueryCount
    points: dict[tuple[str, str, str, str], list[tuple[int | None, int]]] = {}
    columns = (q.run_id, q.scope_kind, q.site_symbol_id, q.statement_fp)
    for batch in chunked(keep):
        result = await session.execute(
            select(*columns, q.scope_items, q.query_count).where(
                q.repository_id == repository_id, q.run_id.in_(batch)
            )
        )
        for run_id, kind, site, fp, items, count in result:
            points.setdefault((run_id, kind, site, fp), []).append((items, count))
    by_site: dict[str, list[Any]] = {}
    for (_run, _kind, site, _fp), seen in points.items():
        by_site.setdefault(site, []).append(read_statement(seen))
    return {site: strongest(readings) for site, readings in by_site.items()}


async def _unknown_context_origins(
    session: AsyncSession, repository_id: str, rows: list[Any]
) -> dict[str, str | None]:
    """The stored ``code_origin`` of each file whose code context is ``unknown``."""
    from ....analysis.health.perf.causal import execution_context

    paths = sorted(p for p in {r.file_path for r in rows} if execution_context(p) == "unknown")
    origins: dict[str, str | None] = {}
    m = HealthFileMetric
    for batch in chunked(paths):
        result = await session.execute(
            select(m.file_path, m.code_origin).where(
                m.repository_id == repository_id, m.file_path.in_(batch)
            )
        )
        origins.update(result.tuples().all())
    return origins


async def _restamp_findings(
    session: AsyncSession, rows: list[Any], verdicts: dict[str, dict[str, Any]]
) -> None:
    """Point every stored observation at its cause, give it a public id, and
    write its cause's queue judgement on it.

    The causal key reads one observation's own facts, so the link only ever
    changes for rows written before the column existed or under an older model.
    """

    from ....analysis.health.finding_identity import finding_public_id
    from ....analysis.health.perf.opportunities import opportunity_id_for_finding

    changed = []
    for row in rows:
        opportunity_id = opportunity_id_for_finding(row)
        values = {
            name: value
            for name, value in verdicts.get(opportunity_id, {}).items()
            if getattr(row, name) != value
        }
        if opportunity_id != row.opportunity_id:
            values["opportunity_id"] = opportunity_id
        if not row.public_id:
            # A store written before the column existed. Backfilling here is
            # what lets its evidence carry a quotable reference without a
            # separate migration pass over the table.
            values["public_id"] = finding_public_id(row)
        if values:
            changed.append({"id": row.id, **values})
    if changed:
        await session.execute(update(HealthFinding), changed)


async def _replace_plans(
    session: AsyncSession,
    repository_id: str,
    opportunities: list[OpportunityModel],
    policy: PerformancePlanPolicy,
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Write the authoritative plans; report each opportunity's plan state and facts.

    Plans are generated here rather than from an analysis report because the
    report sees one run's findings while this sees the merged stored set, and a
    partial run must not be allowed to publish a plan built from a subset.
    """

    from ....analysis.health.refactoring.performance_fix import performance_fix_suggestions

    await session.execute(
        delete(RefactoringSuggestion).where(
            RefactoringSuggestion.repository_id == repository_id,
            RefactoringSuggestion.refactoring_type == _PLAN_TYPE,
        )
    )
    states = {
        item.opportunity_id: ("no_safe_plan" if item.fix is None else "not_persisted")
        for item in opportunities
    }
    if not policy.enabled:
        return states, {}

    suggestions = performance_fix_suggestions(
        opportunities, min_confidence=policy.min_confidence
    )
    rows = []
    for suggestion in suggestions:
        row = RefactoringSuggestion(**_refactoring_row_kwargs(suggestion, repository_id))
        session.add(row)
        rows.append(row)
        states[suggestion.plan["opportunity_id"]] = "available"
    await session.flush()
    return states, await _plan_facts(session, repository_id, rows)


async def _plan_facts(
    session: AsyncSession, repository_id: str, rows: list[Any]
) -> dict[str, dict[str, Any]]:
    """Validation and economics per plan, resolved once here rather than per read.

    Serving one plan cannot afford the test-reachability walk, and without this
    it fell back to an empty profile that read as "no tests" for every plan.
    """
    from ....analysis.health.refactoring.recommendations import steps_with_verify
    from .refactoring_recommendations import hydrate_recommendations, repository_checkout

    if not rows:
        return {}
    facts: dict[str, dict[str, Any]] = {}
    checkout = await repository_checkout(session, repository_id)
    for recommendation in await hydrate_recommendations(
        session, repository_id, rows, step_verify=True, checkout=checkout
    ):
        suggestion = recommendation.suggestion
        facts[suggestion.plan["opportunity_id"]] = {
            "steps": steps_with_verify(
                (suggestion.plan or {}).get("steps") or [], recommendation.validation
            ),
            "effort_bucket": suggestion.effort_bucket,
            "benefit": recommendation.benefit,
            "cost": recommendation.cost,
            "risk": recommendation.risk,
            "validation": recommendation.validation.detail_dict(),
        }
    return facts


async def _reconcile_opportunities(
    session: AsyncSession,
    repository_id: str,
    opportunities: list[OpportunityModel],
    plan_states: dict[str, str],
    analyzed_commit: str | None,
    plans: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Update, insert, and resolve, in one pass over the stored rows. Returns
    each open cause's queue judgement columns, by opportunity id."""
    stored = {
        (row.performance_model_version, row.opportunity_id): row
        for row in (
            await session.execute(
                select(PerformanceOpportunity).where(
                    PerformanceOpportunity.repository_id == repository_id
                )
            )
        )
        .scalars()
        .all()
    }
    seen: set[tuple[int, str]] = set()
    verdicts: dict[str, dict[str, Any]] = {}
    now = _now_utc()
    for position, item in enumerate(opportunities):
        key = (item.performance_model_version, item.opportunity_id)
        seen.add(key)
        values = _row_kwargs(
            item,
            position=position,
            plan_state=plan_states.get(item.opportunity_id, "no_safe_plan"),
            analyzed_commit=analyzed_commit,
            plan=(plans or {}).get(item.opportunity_id),
        )
        verdicts[item.opportunity_id] = {c.key: values[c.key] for c in _QUEUE_COLUMNS}
        row = stored.get(key)
        if row is None:
            session.add(
                PerformanceOpportunity(
                    id=_new_uuid(), repository_id=repository_id, **values
                )
            )
            continue
        for name, value in values.items():
            setattr(row, name, value)
        row.updated_at = now
    for key, row in stored.items():
        # A cause nobody observed this run, or one an older model minted, is
        # resolved rather than removed: a held id must keep answering, and it
        # must not keep reading as current.
        if key not in seen and row.status != "resolved":
            row.status = "resolved"
            row.updated_at = now
    await session.flush()
    return verdicts


async def _write_summary(
    session: AsyncSession,
    repository_id: str,
    opportunities: list[OpportunityModel],
    plan_states: dict[str, str],
    analyzed_commit: str | None,
    plans: dict[str, dict[str, Any]] | None = None,
    *,
    workloads: tuple[str, ...] = (),
) -> None:

    from ....analysis.health.perf.opportunities import PERFORMANCE_MODEL_VERSION

    summary = _summary_payload(opportunities, plan_states, plans)
    if workloads:
        # Only with runtime evidence, so a repo without any stores what it did.
        # The causes were judged against these runs: a run added since is not
        # in their facets until the next index, so it is not named either.
        summary["evidence_workloads"] = list(workloads)
    payload = json.dumps(summary, separators=(",", ":"))
    row = await session.get(PerformanceSummary, repository_id)
    if row is None:
        row = PerformanceSummary(repository_id=repository_id)
        session.add(row)
    row.performance_model_version = PERFORMANCE_MODEL_VERSION
    row.opportunities_total = len(opportunities)
    row.summary_json = payload
    row.analyzed_commit = analyzed_commit
    row.updated_at = _now_utc()
    await session.flush()


# ---------------------------------------------------------------------------
# Indexed readers
# ---------------------------------------------------------------------------


async def get_performance_summary(
    session: AsyncSession, repository_id: str
) -> PerformanceSummary | None:
    """The current headline, by primary key. One statement, no scan."""
    return await session.get(PerformanceSummary, repository_id)


async def get_performance_opportunity(
    session: AsyncSession, repository_id: str, opportunity_id: str
) -> PerformanceOpportunity | None:
    """One materialized opportunity, current model or resolved older one."""
    result = await session.execute(
        select(PerformanceOpportunity)
        .where(
            PerformanceOpportunity.repository_id == repository_id,
            PerformanceOpportunity.opportunity_id == opportunity_id,
        )
        .order_by(PerformanceOpportunity.performance_model_version.desc())
        .limit(1)
    )
    return result.scalars().first()


def _predicates(
    repository_id: str,
    *,
    include_unverified: bool = False,
    include_measured_hotspots: bool = True,
    **params: Any,
) -> list[Any]:
    """The ``WHERE`` for *params* over open rows, built from the serving layer's filter table.

    Opportunities on a file of a language the registry gates for performance
    are left out unless ``include_unverified``; causes minted from runtime
    evidence alone (:func:`measured_hotspot_ids`) unless
    ``include_measured_hotspots``.
    """
    from ....analysis.health.perf.actionability import MEASURED_HOTSPOT
    from ....analysis.health.perf.serving import FILTERS
    from ....analysis.health.queue_rules import active_filters

    return [
        PerformanceOpportunity.repository_id == repository_id,
        *(
            ()
            if include_measured_hotspots
            else (PerformanceOpportunity.biomarker_type != MEASURED_HOTSPOT,)
        ),
        ungated(
            "performance",
            PerformanceOpportunity.file_path,
            PerformanceOpportunity.biomarker_type,
            include_unverified=include_unverified,
        ),
        *(
            rule_predicate(PerformanceOpportunity, rule, value)
            for rule, value in active_filters(FILTERS, {"status": "open", **params})
        ),
    ]


async def list_performance_opportunities(
    session: AsyncSession,
    repository_id: str,
    *,
    contexts: frozenset[str] | None = None,
    boundary: str | None = None,
    confidence: str | None = None,
    actionabilities: frozenset[str] | None = None,
    proofs: frozenset[str] | None = None,
    roles: frozenset[str] | None = None,
    queue_eligible: bool | None = None,
    file_paths: tuple[str, ...] | None = None,
    area_prefix: str | None = None,
    sort: str = "rank",
    limit: int = 20,
    offset: int = 0,
    include_unverified: bool = False,
    include_measured_hotspots: bool = True,
) -> tuple[list[PerformanceOpportunity], int]:
    """One page in the requested order, plus the exact total behind it.

    Both statements are index-driven and the fetched rows are the page, so cost
    tracks the page rather than the repository.
    """
    from ....analysis.health.perf.serving import sort_keys

    where = _predicates(
        repository_id,
        contexts=contexts,
        boundary=boundary,
        confidence=confidence,
        actionabilities=actionabilities,
        proofs=proofs,
        roles=roles,
        queue_eligible=queue_eligible,
        file_paths=file_paths,
        area_prefix=area_prefix,
        include_unverified=include_unverified,
        include_measured_hotspots=include_measured_hotspots,
    )
    total = int(
        (
            await session.execute(
                select(func.count()).select_from(PerformanceOpportunity).where(*where)
            )
        ).scalar_one()
    )
    rows = (
        (
            await session.execute(
                select(PerformanceOpportunity)
                .where(*where)
                .order_by(*order_by(PerformanceOpportunity, sort_keys(sort)))
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(rows), total


async def performance_facet_counts(
    session: AsyncSession,
    repository_id: str,
    *,
    file_paths: tuple[str, ...] | None = None,
    include_unverified: bool = False,
    scoped: bool = False,
    area_prefix: str | None = None,
    include_measured_hotspots: bool = True,
) -> list[tuple[Any, ...]]:
    """Grouped counts over every open opportunity, in one aggregate statement.

    Returned pre-aggregation, one ``(*FACET_FIELDS, count)`` tuple per group,
    so ``perf.serving.fold_facets`` can cross-filter: a facet must be counted
    with every filter applied *except* its own, or choosing one value would
    erase the alternatives.
    """
    from ....analysis.health.perf.serving import FACET_FIELDS, SCOPED_GROUP_FIELDS

    # ``scoped`` also groups by the stored queue verdict, for scope-aware facets.
    fields = SCOPED_GROUP_FIELDS if scoped else FACET_FIELDS
    columns = tuple(getattr(PerformanceOpportunity, name) for name in fields)
    result = await session.execute(
        select(*columns, func.count())
        .where(
            *_predicates(
                repository_id,
                file_paths=file_paths,
                area_prefix=area_prefix,
                include_unverified=include_unverified,
                include_measured_hotspots=include_measured_hotspots,
            )
        )
        .group_by(*columns)
    )
    return [tuple(row) for row in result.all()]


class PerformanceHeadline(NamedTuple):
    """The performance headline every surface prints, in the Performance
    view's three scopes: the default queue (worth fixing now) leads, and every
    production cause and every cause give it context."""

    now: int
    production: int
    all: int
    per_10k_loc: float | None
    """Every cause per 10K lines a performance detector ran on; ``None`` with none."""

    def as_dict(self) -> dict[str, Any]:
        return self._asdict()


async def measured_hotspot_ids(session: AsyncSession, repository_id: str) -> frozenset[str]:
    """The open causes minted from runtime evidence alone
    (``perf.opportunities.measured_hotspots``), for a surface that shows no
    evidence. One indexed lookup."""
    from ....analysis.health.perf.actionability import MEASURED_HOTSPOT

    result = await session.execute(
        select(PerformanceOpportunity.opportunity_id).where(
            PerformanceOpportunity.repository_id == repository_id,
            PerformanceOpportunity.status == "open",
            PerformanceOpportunity.biomarker_type == MEASURED_HOTSPOT,
        )
    )
    return frozenset(result.scalars().all())


async def measured_hotspot_counts(
    session: AsyncSession, repository_id: str, *, include_unverified: bool = False
) -> tuple[int, int]:
    """``(open, in the default queue)`` over the causes runtime evidence alone
    minted that a language gate does not hold back, for a surface that shows
    no evidence to take out of a stored rollup. One aggregate."""
    from ....analysis.health.perf.actionability import MEASURED_HOTSPOT

    row = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(PerformanceOpportunity.queue_eligible.is_(True)),
            ).where(
                *_predicates(repository_id, include_unverified=include_unverified),
                PerformanceOpportunity.biomarker_type == MEASURED_HOTSPOT,
            )
        )
    ).one()
    return int(row[0]), int(row[1])


async def performance_headline(
    session: AsyncSession,
    repository_id: str,
    *,
    covered_nloc: int,
    include_unverified: bool = False,
    include_measured_hotspots: bool = True,
) -> PerformanceHeadline:
    """The one count of open performance causes, read by every surface.

    The scope sizes the Performance view's scope control shows, from the same
    grouped aggregate, so a headline never states a number that view
    contradicts. *covered_nloc* is the caller's ``PerfCoverage.covered_nloc``.
    ``include_measured_hotspots=False`` leaves out the causes runtime evidence alone
    minted, for a surface that shows no evidence.
    """
    from ....analysis.health.perf.serving import scope_counts
    from .queue_counts import any_unjudged

    groups = await performance_facet_counts(
        session,
        repository_id,
        include_unverified=include_unverified,
        scoped=True,
        include_measured_hotspots=include_measured_hotspots,
    )
    judged = not await any_unjudged(session, repository_id, "causes")
    scopes = scope_counts(groups, judged=judged)
    every = scopes["all"]
    return PerformanceHeadline(
        now=scopes["now"],
        production=scopes["production"],
        all=every,
        per_10k_loc=round(10000.0 * every / covered_nloc, 2) if covered_nloc > 0 else None,
    )


async def performance_area_counts(
    session: AsyncSession,
    repository_id: str,
    *,
    include_unverified: bool = False,
    **filters: Any,
) -> list[tuple[str, str, int]]:
    """``(file_path, actionability_state, count)`` over the rows *filters*
    (the serving layer's ``store_filters``) list, for the area facet."""
    result = await session.execute(
        select(
            PerformanceOpportunity.file_path,
            PerformanceOpportunity.actionability_state,
            func.count(),
        )
        .where(*_predicates(repository_id, include_unverified=include_unverified, **filters))
        .group_by(PerformanceOpportunity.file_path, PerformanceOpportunity.actionability_state)
    )
    return [(row[0], row[1], int(row[2])) for row in result.all()]


_ACTIONABILITY_STATES = ("plan_ready", "advisory", "investigate", "expected")
"""The states a rollup breaks down. Named, so a fifth cannot vanish silently."""


class PerformanceFileRollup(NamedTuple):
    """One file's open performance burden, folded from the grouped counts."""

    file_path: str
    opportunities: int
    observations: int
    plan_ready: int
    advisory: int
    investigate: int
    expected: int
    best_rank: int
    """Lowest ``rank_position`` on the file, so a map can rank files by cause."""
    queued: int = 0
    """Causes in the default queue: what a map colours, as the headline leads with them."""
    queued_states: frozenset[str] = frozenset()
    """The actionability states among those queued causes."""


async def gated_performance_counts(
    session: AsyncSession, repository_id: str, *, include_unverified: bool = False
) -> dict[str, dict]:
    """The open opportunities a language gate holds back, by language."""
    return await gated_counts(
        session,
        "performance",
        PerformanceOpportunity,
        PerformanceOpportunity.repository_id == repository_id,
        PerformanceOpportunity.status == "open",
        kind_col=PerformanceOpportunity.biomarker_type,
        include_unverified=include_unverified,
    )


def _fold_rollups(groups: Any) -> list[PerformanceFileRollup]:
    """One rollup per file from the grouped ``performance_file_rollups`` rows."""
    folded: dict[str, dict[str, int]] = {}
    queued_states: dict[str, set[str]] = {}
    for path, state, count, observations, best_rank, eligible in groups:
        if not path:
            continue
        # The stored verdict: a row stored before causes were judged counts as
        # not queued until the next index judges it.
        if eligible:
            queued_states.setdefault(path, set()).add(state)
        row = folded.setdefault(
            path,
            {
                "opportunities": 0,
                "observations": 0,
                "plan_ready": 0,
                "advisory": 0,
                "investigate": 0,
                "expected": 0,
                "best_rank": best_rank or 0,
                "queued": 0,
            },
        )
        row["queued"] += int(count or 0) if eligible else 0
        row["opportunities"] += int(count or 0)
        row["observations"] += int(observations or 0)
        if state in _ACTIONABILITY_STATES:
            row[state] += int(count or 0)
        row["best_rank"] = min(row["best_rank"], int(best_rank or 0))
    rollups = [
        PerformanceFileRollup(
            file_path=path,
            opportunities=row["opportunities"],
            observations=row["observations"],
            plan_ready=row["plan_ready"],
            advisory=row["advisory"],
            investigate=row["investigate"],
            expected=row["expected"],
            best_rank=row["best_rank"],
            queued=row["queued"],
            queued_states=frozenset(queued_states.get(path, ())),
        )
        for path, row in folded.items()
    ]
    return rollups


async def performance_file_rollups(
    session: AsyncSession,
    repository_id: str,
    *,
    file_paths: tuple[str, ...] | None = None,
) -> list[PerformanceFileRollup]:
    """Per-file burden for every file carrying an open opportunity.

    One indexed aggregate over ``(repository_id, status, file_path)`` returning
    a few rows per file rather than one per observation, so a caller
    that needs the whole repository still pays a grouped scan and not a
    per-file query. Ordered best-rank first, which is the order a bounded map
    feed admits files in.
    """
    where = _predicates(repository_id, file_paths=file_paths)
    result = await session.execute(
        select(
            PerformanceOpportunity.file_path,
            PerformanceOpportunity.actionability_state,
            func.count(),
            func.sum(PerformanceOpportunity.observations_total),
            func.min(PerformanceOpportunity.rank_position),
            PerformanceOpportunity.queue_eligible,
        )
        .where(*where)
        .group_by(
            PerformanceOpportunity.file_path,
            PerformanceOpportunity.actionability_state,
            PerformanceOpportunity.queue_eligible,
        )
    )
    rollups = _fold_rollups(result.all())
    # Best rank first, then path, so the order a cap is applied against is
    # total and does not move between two reads of the same store.
    rollups.sort(key=lambda r: (r.best_rank, r.file_path))
    return rollups


async def get_performance_plan_rows(
    session: AsyncSession, repository_id: str, opportunity_ids: list[str]
) -> dict[str, RefactoringSuggestion]:
    """The plans for one page of opportunities, in one indexed batch."""
    if not opportunity_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(RefactoringSuggestion).where(
                    RefactoringSuggestion.repository_id == repository_id,
                    RefactoringSuggestion.refactoring_type == _PLAN_TYPE,
                    RefactoringSuggestion.opportunity_id.in_(opportunity_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    matches: dict[str, RefactoringSuggestion] = {}
    for row in rows:
        # The writer replaces every plan for the repository, so one per cause is
        # what a store this writer produced holds. Choosing deterministically
        # keeps a store an older writer left with two from returning a different
        # plan on every read.
        key = row.opportunity_id
        if key and (key not in matches or row.id < matches[key].id):
            matches[key] = row
    return matches


async def list_opportunity_evidence(
    session: AsyncSession,
    repository_id: str,
    opportunity_id: str,
    *,
    limit: int = 8,
    offset: int = 0,
    total: int | None = None,
) -> tuple[list[Any], int]:
    """One page of the observations behind a cause, and their exact total.

    A caller holding the opportunity row already knows the total, and it is the
    same number over the same rows in the same transaction, so passing it in
    saves a count.
    """
    where = [
        HealthFinding.repository_id == repository_id,
        HealthFinding.status == "open",
        HealthFinding.dimension == "performance",
        HealthFinding.opportunity_id == opportunity_id,
    ]
    if total is None:
        total = int(
            (
                await session.execute(
                    select(func.count()).select_from(HealthFinding).where(*where)
                )
            ).scalar_one()
        )
    rows = (
        await session.execute(
            select(*_OBSERVATION_COLUMNS)
            .where(*where)
            .order_by(
                HealthFinding.file_path.asc(),
                HealthFinding.line_start.asc(),
                HealthFinding.id.asc(),
            )
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return list(rows), total


async def list_evidence_for_opportunities(
    session: AsyncSession,
    repository_id: str,
    opportunity_ids: list[str],
    *,
    per_opportunity: int = 3,
) -> dict[str, list[Any]]:
    """A few evidence rows for each opportunity on a page, in one statement.

    Ranked inside the database rather than fetched and trimmed afterwards, so
    a page containing one very large cause still reads a bounded number of
    rows. Both supported backends implement the window function this uses.
    """
    if not opportunity_ids or per_opportunity <= 0:
        return {}
    ranked = (
        select(
            *_OBSERVATION_COLUMNS,
            func.row_number()
            .over(
                partition_by=HealthFinding.opportunity_id,
                order_by=(
                    HealthFinding.file_path.asc(),
                    HealthFinding.line_start.asc(),
                    HealthFinding.id.asc(),
                ),
            )
            .label("position"),
        )
        .where(
            HealthFinding.repository_id == repository_id,
            HealthFinding.status == "open",
            HealthFinding.dimension == "performance",
            HealthFinding.opportunity_id.in_(opportunity_ids),
        )
        .subquery()
    )
    rows = (
        await session.execute(select(ranked).where(ranked.c.position <= per_opportunity))
    ).all()
    grouped: dict[str, list[Any]] = {}
    for row in sorted(rows, key=lambda item: item.position):
        grouped.setdefault(row.opportunity_id, []).append(row)
    return grouped


async def get_health_finding_by_public_id(
    session: AsyncSession, repository_id: str, public_id: str
) -> HealthFinding | None:
    """One finding by its quoted public id, through the index."""
    result = await session.execute(
        select(HealthFinding)
        .where(
            HealthFinding.repository_id == repository_id,
            HealthFinding.public_id == public_id,
        )
        .limit(1)
    )
    return result.scalars().first()


# ---------------------------------------------------------------------------
# Measured costs: what stored runs measured, beside the static causes
# ---------------------------------------------------------------------------

_MEASURED_CALLERS = 3
_MEASURED_TESTS = 3


async def measured_costs(
    session: AsyncSession,
    repository_id: str,
    *,
    limit: int = 10,
    now: datetime | None = None,
    run_id: str | None = None,
) -> dict[str, Any] | None:
    """What each workload's newest stored run measured, ranked by what it costs.

    *run_id* keeps that one run (a workload's newest); any other id ranks
    nothing here and gives ``None``.

    Read at request time from ``workload_hotness``, which every ingest and
    update rebuilds, so nothing new is stored and an index without evidence
    pays one indexed lookup. ``None`` when no function is ranked: callers add
    nothing then, and every output stays as it was.

    - ``measured``: one block per run in a workload class that may lead
      (``evidence.hotness.LEAD_CLASSES``), newest run first, each with at most
      *limit* of its ranked functions, best rank first. A function two
      workloads rank is listed under each.
    - ``measured_in_tests``: the same for ``unit_test`` runs, listed apart and
      never leading.
    - Each group carries ``total``: its ranked rows before the *limit* cut.

    Each run names its basis (workload class and name, commit, age, samples)
    and where its time went (``repo_self_share``, ``wait_share``,
    ``library_share``). Each row's ``share`` is the function's self time (its
    cost: built-ins and libraries it called included, waits excluded) with its
    interval; ``inclusive`` is context. A row carries the function's
    lines, callers outside test code, owner, nearest tests and, when an open
    static performance cause sits on it and that cause's evidence runs in
    production code, ``on_static_findings`` with the cause ids. Stale evidence
    and code outside production are never rows.
    """
    from ....evidence.hotness import LEAD_CLASSES
    from ....ingestion.symbol_identity import file_of_symbol
    from .evidence import ranked_by_workload

    ranked = await ranked_by_workload(session, repository_id)
    if run_id is not None:
        ranked = [(run, rows) for run, rows in ranked if run.id == run_id]
    if not ranked:
        return None
    groups: dict[str, list[tuple[Any, list[Any]]]] = {"measured": [], "measured_in_tests": []}
    for run, rows in ranked:
        if run.workload_class in LEAD_CLASSES:
            groups["measured"].append((run, rows))
        elif run.workload_class == "unit_test":
            groups["measured_in_tests"].append((run, rows))
    if not any(groups.values()):
        return None
    # ``limit=0`` keeps each group's runs and total with no rows.
    shown = {key: [(run, rows[: max(limit, 0)]) for run, rows in runs] for key, runs in groups.items()}
    ids = sorted({r.symbol_id for runs in shown.values() for _, rows in runs for r in rows})
    spans = await _symbol_spans(session, repository_id, {file_of_symbol(i) for i in ids})
    covered = await _static_causes_on(session, repository_id, set(ids), spans)
    context = await _function_context(session, repository_id, ids)
    at = now or _now_utc()
    view: dict[str, Any] = {}
    for key, runs in shown.items():
        if runs:
            view[key] = {
                "runs": [_measured_run(run, rows, at) for run, rows in groups[key]],
                "rows": [
                    _measured_row(r, spans, context, covered) for _, rows in runs for r in rows
                ],
                "total": sum(len(rows) for _, rows in groups[key]),
            }
    return view or None


async def _symbol_spans(
    session: AsyncSession, repository_id: str, paths: set[str]
) -> dict[str, list[Any]]:
    """Every indexed symbol's span in *paths*, per file."""
    from ...models import WikiSymbol

    out: dict[str, list[Any]] = {}
    for batch in chunked(sorted(paths)):
        result = await session.execute(
            select(
                WikiSymbol.file_path,
                WikiSymbol.symbol_id,
                WikiSymbol.start_line,
                WikiSymbol.end_line,
            ).where(WikiSymbol.repository_id == repository_id, WikiSymbol.file_path.in_(batch))
        )
        for row in result:
            out.setdefault(row.file_path, []).append(row)
    return out


async def _static_causes_on(
    session: AsyncSession,
    repository_id: str,
    symbol_ids: set[str],
    spans: dict[str, list[Any]],
) -> dict[str, set[str]]:
    """The open static performance causes on each of *symbol_ids* that has one.

    On a function means the cause's edit site is the function, or an open
    performance finding's line falls inside it and in no narrower symbol.
    A finding with no line names no function, and one with no cause id has
    no listed cause to carry the measurement, so neither covers a function.
    Only a cause whose evidence runs in production code links: a cause seen
    only from tests says nothing about a production measurement.
    """
    from ....analysis.health.perf.actionability import MEASURED_HOTSPOT
    from ....symbol_spans import symbol_at

    out: dict[str, set[str]] = {}
    for batch in chunked(sorted(symbol_ids)):
        result = await session.execute(
            select(
                PerformanceOpportunity.intervention_symbol, PerformanceOpportunity.opportunity_id
            ).where(
                PerformanceOpportunity.repository_id == repository_id,
                PerformanceOpportunity.status == "open",
                PerformanceOpportunity.intervention_symbol.in_(batch),
                # A cause the measurement itself minted is no static cause.
                PerformanceOpportunity.biomarker_type != MEASURED_HOTSPOT,
            )
        )
        for symbol_id, opportunity_id in result:
            out.setdefault(symbol_id, set()).add(opportunity_id)
    for batch in chunked(sorted(spans)):
        result = await session.execute(
            select(
                HealthFinding.file_path, HealthFinding.line_start, HealthFinding.opportunity_id
            ).where(
                HealthFinding.repository_id == repository_id,
                HealthFinding.dimension == "performance",
                HealthFinding.status == "open",
                HealthFinding.file_path.in_(batch),
            )
        )
        for path, line, opportunity_id in result:
            held = symbol_at(spans[path], line) if line else None
            if opportunity_id and held is not None and held.symbol_id in symbol_ids:
                out.setdefault(held.symbol_id, set()).add(opportunity_id)
    production: set[str] = set()
    for batch in chunked(sorted({i for ids in out.values() for i in ids})):
        result = await session.execute(
            select(PerformanceOpportunity.opportunity_id).where(
                PerformanceOpportunity.repository_id == repository_id,
                PerformanceOpportunity.status == "open",
                PerformanceOpportunity.execution_context == "production",
                PerformanceOpportunity.opportunity_id.in_(batch),
            )
        )
        production.update(result.scalars())
    linked = {sid: ids & production for sid, ids in out.items()}
    return {sid: ids for sid, ids in linked.items() if ids}


class _FunctionContext(NamedTuple):
    callers_total: dict[str, int]
    callers: dict[str, list[str]]
    owners: dict[str, Any]
    tests: dict[str, Any]


async def _function_context(
    session: AsyncSession, repository_id: str, symbol_ids: list[str]
) -> _FunctionContext:
    """Callers, file owner and nearest tests of *symbol_ids*, from the stores
    ``get_context`` reads: graph edges, git metadata and the function test map.

    Callers in test code (``code_origin``) are neither listed nor counted: a
    measured production function's callers say who pays for it in production.
    """
    from ....evidence.ingest import load_origins
    from ....ingestion.models import SYMBOL_USE_EDGE_TYPES
    from ....ingestion.symbol_identity import file_of_symbol
    from ..git import get_git_metadata_bulk
    from ..graph import get_node_degree_counts_bulk
    from .function_facts import tests_for_functions

    if not symbol_ids:
        return _FunctionContext({}, {}, {}, {})
    # Which functions the graph knows: an unknown one has no caller count at all.
    degrees = await get_node_degree_counts_bulk(
        session, repository_id, symbol_ids, edge_types=sorted(SYMBOL_USE_EDGE_TYPES)
    )
    sources = await _callers_of(session, repository_id, symbol_ids)
    origins = await load_origins(
        session, repository_id, {file_of_symbol(s) for found in sources.values() for s in found}
    )
    outside_tests = {
        sid: [s for s in found if origins.get(file_of_symbol(s)) != "test"]
        for sid, found in sources.items()
    }
    return _FunctionContext(
        callers_total={i: len(outside_tests.get(i, ())) for i in symbol_ids if i in degrees},
        callers={sid: found[:_MEASURED_CALLERS] for sid, found in outside_tests.items() if found},
        owners=await get_git_metadata_bulk(
            session, repository_id, sorted({file_of_symbol(i) for i in symbol_ids})
        ),
        tests=await tests_for_functions(session, repository_id, symbol_ids, k=_MEASURED_TESTS),
    )


async def _callers_of(
    session: AsyncSession, repository_id: str, symbol_ids: list[str]
) -> dict[str, list[str]]:
    """Every caller of each of *symbol_ids*, once each, most confident first.

    One statement per chunk of ids for all of them, reading two columns, so
    the cost is the callers the shown functions have, not one query each.
    """
    from ....ingestion.models import SYMBOL_USE_EDGE_TYPES
    from ...models import GraphEdge

    out: dict[str, list[str]] = {}
    for batch in chunked(sorted(symbol_ids)):
        result = await session.execute(
            select(GraphEdge.target_node_id, GraphEdge.source_node_id)
            .where(
                GraphEdge.repository_id == repository_id,
                GraphEdge.target_node_id.in_(batch),
                GraphEdge.edge_type.in_(sorted(SYMBOL_USE_EDGE_TYPES)),
            )
            .order_by(
                GraphEdge.target_node_id,
                GraphEdge.confidence.desc(),
                GraphEdge.source_node_id,
                GraphEdge.edge_type,
            )
        )
        for target, source in result:
            out.setdefault(target, []).append(source)
    # One caller per function, whichever edge types link them.
    return {target: list(dict.fromkeys(found)) for target, found in out.items()}


def _measured_run(run: Any, rows: list[Any], at: datetime) -> dict[str, Any]:
    """A run's basis: workload, commit, age, samples and where its time went.

    *rows* are all the run's ranked functions, not the ones a limit kept.
    """
    from ....evidence.hotness import run_samples
    from ....evidence.status import time_split

    measured_at = run.measured_at or run.ingested_at
    if measured_at is not None and measured_at.tzinfo is None:
        measured_at = measured_at.replace(tzinfo=UTC)
    return {
        "run_id": run.id,
        "workload_class": run.workload_class,
        "workload_name": run.workload_name,
        "commit": run.commit_sha,
        "measured_at": measured_at.isoformat() if measured_at else None,
        "age_days": max((at - measured_at).days, 0) if measured_at else None,
        "samples": run_samples(run),
        **time_split(run),
        "outside_production": run.unlisted_symbols,
        "value_kind": run.value_kind,
        "ordering_only": any(r.ordering_only for r in rows),
    }


def _measured_row(
    row: Any,
    spans: dict[str, list[Any]],
    context: _FunctionContext,
    covered: dict[str, set[str]],
) -> dict[str, Any]:
    from ....ingestion.symbol_identity import file_of_symbol

    symbol_id = row.symbol_id
    path = file_of_symbol(symbol_id)
    span = next((s for s in spans.get(path, ()) if s.symbol_id == symbol_id), None)
    owner = context.owners.get(path)
    tests = context.tests.get(symbol_id)
    out = {
        "symbol_id": symbol_id,
        "file_path": path,
        "lines": [span.start_line, span.end_line] if span is not None else None,
        "run_id": row.run_id,
        "rank": row.rank,
        # The cost the rank is by: self time, waits excluded.
        "share": row.self_share,
        "share_low": row.share_low,
        "share_high": row.share_high,
        "inclusive": row.total_share,
        "samples": row.samples,
        "freshness": row.freshness,
        "callers_total": context.callers_total.get(symbol_id),
        "callers": context.callers.get(symbol_id, []),
        "owner": (
            {"name": owner.primary_owner_name, "commit_pct": owner.primary_owner_commit_pct}
            if owner is not None and owner.primary_owner_name
            else None
        ),
        # ``None``: the function was never mapped to tests; ``[]``: no test reaches it.
        "tests": [t.selector for t in tests] if tests is not None else None,
    }
    if symbol_id in covered:
        out["on_static_findings"] = sorted(covered[symbol_id])
    return out


__all__ = [
    "PerformanceHeadline",
    "finalize_performance_opportunities",
    "gated_performance_counts",
    "get_health_finding_by_public_id",
    "get_performance_opportunity",
    "get_performance_plan_rows",
    "get_performance_summary",
    "list_evidence_for_opportunities",
    "list_opportunity_evidence",
    "list_performance_opportunities",
    "measured_costs",
    "opportunity_details",
    "performance_facet_counts",
    "performance_headline",
]
