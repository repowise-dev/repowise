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
from typing import TYPE_CHECKING, Any, NamedTuple

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import (
    HealthFileMetric,
    HealthFinding,
    PerformanceOpportunity,
    PerformanceSummary,
    RefactoringSuggestion,
    _new_uuid,
    _now_utc,
)
from .refactoring import _refactoring_row_kwargs

if TYPE_CHECKING:
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


def opportunity_details(opportunity: OpportunityModel) -> dict[str, Any]:
    """The explanatory half of one opportunity, the half no query filters on.

    Everything a column already holds is deliberately absent, so the row and
    the payload cannot drift into disagreeing about the same fact.
    """
    return {
        "biomarker_types": list(opportunity.biomarker_types),
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
    }


def _row_kwargs(
    opportunity: OpportunityModel,
    *,
    position: int,
    plan_state: str,
    analyzed_commit: str | None,
) -> dict[str, Any]:
    fix = opportunity.fix
    return {
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
        "fix_strategy": fix.strategy if fix else None,
        "fix_safety": fix.safety if fix else None,
        "file_path": _intervention_file(opportunity),
        "intervention_symbol": opportunity.intervention_symbol,
        "terminal_sink": opportunity.terminal_sink,
        "observations_total": opportunity.observations_total,
        "affected_call_sites_total": opportunity.affected_call_sites_total,
        "affected_files_total": opportunity.affected_files_total,
        "details_json": json.dumps(opportunity_details(opportunity), separators=(",", ":")),
        "analyzed_commit": analyzed_commit,
    }


def _intervention_file(opportunity: OpportunityModel) -> str:
    """The file a reader would open first: the intervention, else the evidence."""
    symbol = opportunity.intervention_symbol
    if symbol:
        return symbol.split("::", 1)[0]
    return opportunity.evidence[0]["file_path"] if opportunity.evidence else ""


def _summary_payload(opportunities: list[OpportunityModel], plan_states: dict[str, str]) -> dict:
    """The compact current headline, written once and read by primary key."""
    counts: dict[str, int] = {}
    contexts: dict[str, int] = {}
    boundaries: dict[str, int] = {}
    for item in opportunities:
        counts[item.actionability_state] = counts.get(item.actionability_state, 0) + 1
        contexts[item.execution_context] = contexts.get(item.execution_context, 0) + 1
        key = item.boundary_kind or "none"
        boundaries[key] = boundaries.get(key, 0) + 1
    lead = opportunities[0] if opportunities else None
    return {
        "actionability": counts,
        "context": contexts,
        "boundary": boundaries,
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
            "file_path": _intervention_file(lead),
            "observations_total": lead.observations_total,
            "affected_call_sites_total": lead.affected_call_sites_total,
            "affected_files_total": lead.affected_files_total,
            "plan_state": plan_states.get(lead.opportunity_id, "no_safe_plan"),
            "why_ranked": [dict(entry) for entry in lead.why_ranked],
            "prerequisites": list(lead.prerequisites),
            "actionability_reason": lead.actionability_reason,
        },
    }


async def finalize_performance_opportunities(
    session: AsyncSession,
    repository_id: str,
    *,
    analyzed_commit: str | None = None,
    plan_policy: PerformancePlanPolicy | None = None,
) -> int:
    """Rebuild this repository's performance read model from stored findings.

    Called by both the repo-wide and the per-path health writers, after their
    own rows land and inside their transaction, so the queue can never describe
    a set of findings that was never committed. Returns the number of open
    opportunities.
    """
    # Deferred throughout this module: the analysis package imports
    # persistence, so a module-level import here would close the cycle.
    from ....analysis.health.perf.opportunities import build_performance_opportunities
    from ....analysis.health.refactoring.performance_fix import PerformancePlanPolicy

    policy = plan_policy or PerformancePlanPolicy()
    rows = list(
        (
            await session.execute(
                select(*_OBSERVATION_COLUMNS).where(
                    HealthFinding.repository_id == repository_id,
                    HealthFinding.status == "open",
                    HealthFinding.dimension == "performance",
                )
            )
        ).all()
    )
    opportunities = build_performance_opportunities(rows, evidence_limit=_EVIDENCE_LIMIT)

    await _restamp_findings(session, rows)
    plan_states = await _replace_plans(session, repository_id, opportunities, policy)
    await _reconcile_opportunities(session, repository_id, opportunities, plan_states, analyzed_commit)
    await _write_summary(session, repository_id, opportunities, plan_states, analyzed_commit)
    return len(opportunities)


async def _restamp_findings(session: AsyncSession, rows: list[Any]) -> None:
    """Point every stored observation at its cause, and give it a public id.

    The causal key reads one observation's own facts, so the link only ever
    changes for rows written before the column existed or under an older model.
    """

    from ....analysis.health.finding_identity import finding_public_id
    from ....analysis.health.perf.opportunities import opportunity_id_for_finding

    changed = []
    for row in rows:
        values = {}
        if (opportunity_id := opportunity_id_for_finding(row)) != row.opportunity_id:
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
) -> dict[str, str]:
    """Write the authoritative plans and report each opportunity's plan state.

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
        return states

    nloc_by_file = dict(
        (
            await session.execute(
                select(HealthFileMetric.file_path, HealthFileMetric.nloc).where(
                    HealthFileMetric.repository_id == repository_id
                )
            )
        ).all()
    )
    suggestions = performance_fix_suggestions(
        opportunities,
        nloc_by_file=nloc_by_file,
        min_confidence=policy.min_confidence,
    )
    for suggestion in suggestions:
        session.add(RefactoringSuggestion(**_refactoring_row_kwargs(suggestion, repository_id)))
        states[suggestion.plan["opportunity_id"]] = "available"
    await session.flush()
    return states


async def _reconcile_opportunities(
    session: AsyncSession,
    repository_id: str,
    opportunities: list[OpportunityModel],
    plan_states: dict[str, str],
    analyzed_commit: str | None,
) -> None:
    """Update, insert, and resolve, in one pass over the stored rows."""
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
    now = _now_utc()
    for position, item in enumerate(opportunities):
        key = (item.performance_model_version, item.opportunity_id)
        seen.add(key)
        values = _row_kwargs(
            item,
            position=position,
            plan_state=plan_states.get(item.opportunity_id, "no_safe_plan"),
            analyzed_commit=analyzed_commit,
        )
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


async def _write_summary(
    session: AsyncSession,
    repository_id: str,
    opportunities: list[OpportunityModel],
    plan_states: dict[str, str],
    analyzed_commit: str | None,
) -> None:

    from ....analysis.health.perf.opportunities import PERFORMANCE_MODEL_VERSION

    payload = json.dumps(_summary_payload(opportunities, plan_states), separators=(",", ":"))
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
    contexts: frozenset[str] | None,
    boundary: str | None,
    confidence: str | None,
    actionability: str | None,
    file_paths: tuple[str, ...] | None,
) -> list[Any]:
    where: list[Any] = [
        PerformanceOpportunity.repository_id == repository_id,
        PerformanceOpportunity.status == "open",
    ]
    if contexts is not None:
        where.append(PerformanceOpportunity.execution_context.in_(sorted(contexts)))
    if boundary is not None:
        where.append(
            PerformanceOpportunity.boundary_kind.is_(None)
            if boundary == "none"
            else PerformanceOpportunity.boundary_kind == boundary
        )
    if confidence is not None:
        where.append(PerformanceOpportunity.evidence_confidence == confidence)
    if actionability is not None:
        where.append(PerformanceOpportunity.actionability_state == actionability)
    if file_paths is not None:
        where.append(PerformanceOpportunity.file_path.in_(list(file_paths)))
    return where


_ORDERS = {
    "rank": (PerformanceOpportunity.rank_position.asc(),),
    "leverage": (
        PerformanceOpportunity.affected_call_sites_total.desc(),
        PerformanceOpportunity.rank_position.asc(),
    ),
    "observations": (
        PerformanceOpportunity.observations_total.desc(),
        PerformanceOpportunity.rank_position.asc(),
    ),
}
"""Orderings the queue offers, each ending in rank so every one is total.

Applied in SQL rather than to the fetched page: sorting a page selected by a
different key would order twenty rows correctly and the repository wrongly.
"""


async def list_performance_opportunities(
    session: AsyncSession,
    repository_id: str,
    *,
    contexts: frozenset[str] | None = None,
    boundary: str | None = None,
    confidence: str | None = None,
    actionability: str | None = None,
    file_paths: tuple[str, ...] | None = None,
    sort: str = "rank",
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[PerformanceOpportunity], int]:
    """One page in the requested order, plus the exact total behind it.

    Both statements are index-driven and the fetched rows are the page, so cost
    tracks the page rather than the repository.
    """
    where = _predicates(
        repository_id,
        contexts=contexts,
        boundary=boundary,
        confidence=confidence,
        actionability=actionability,
        file_paths=file_paths,
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
                .order_by(*_ORDERS.get(sort, _ORDERS["rank"]))
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
) -> list[tuple[str, str | None, str, str, str, int]]:
    """Grouped counts over every open opportunity, in one aggregate statement.

    Returned pre-aggregation rather than as finished facets so the caller can
    cross-filter: a facet must be counted with every filter applied *except*
    its own, or choosing one value would erase the alternatives.
    """
    where = _predicates(
        repository_id,
        contexts=None,
        boundary=None,
        confidence=None,
        actionability=None,
        file_paths=file_paths,
    )
    result = await session.execute(
        select(
            PerformanceOpportunity.execution_context,
            PerformanceOpportunity.boundary_kind,
            PerformanceOpportunity.evidence_confidence,
            PerformanceOpportunity.actionability_state,
            PerformanceOpportunity.plan_state,
            func.count(),
        )
        .where(*where)
        .group_by(
            PerformanceOpportunity.execution_context,
            PerformanceOpportunity.boundary_kind,
            PerformanceOpportunity.evidence_confidence,
            PerformanceOpportunity.actionability_state,
            PerformanceOpportunity.plan_state,
        )
    )
    return [tuple(row) for row in result.all()]


_ACTIONABILITY_STATES = ("plan_ready", "advisory", "investigate")
"""The states a rollup breaks down. Named, so a fourth cannot vanish silently."""


class PerformanceFileRollup(NamedTuple):
    """One file's open performance burden, folded from the grouped counts."""

    file_path: str
    opportunities: int
    observations: int
    plan_ready: int
    advisory: int
    investigate: int
    best_rank: int
    """Lowest ``rank_position`` on the file, so a map can rank files by cause."""


async def performance_file_rollups(
    session: AsyncSession,
    repository_id: str,
    *,
    file_paths: tuple[str, ...] | None = None,
) -> list[PerformanceFileRollup]:
    """Per-file burden for every file carrying an open opportunity.

    One indexed aggregate over ``(repository_id, status, file_path)`` returning
    at most three rows per file rather than one per observation, so a caller
    that needs the whole repository still pays a grouped scan and not a
    per-file query. Ordered best-rank first, which is the order a bounded map
    feed admits files in.
    """
    where = _predicates(
        repository_id,
        contexts=None,
        boundary=None,
        confidence=None,
        actionability=None,
        file_paths=file_paths,
    )
    result = await session.execute(
        select(
            PerformanceOpportunity.file_path,
            PerformanceOpportunity.actionability_state,
            func.count(),
            func.sum(PerformanceOpportunity.observations_total),
            func.min(PerformanceOpportunity.rank_position),
        )
        .where(*where)
        .group_by(
            PerformanceOpportunity.file_path,
            PerformanceOpportunity.actionability_state,
        )
    )
    folded: dict[str, dict[str, int]] = {}
    for path, state, count, observations, best_rank in result.all():
        if not path:
            continue
        row = folded.setdefault(
            path,
            {
                "opportunities": 0,
                "observations": 0,
                "plan_ready": 0,
                "advisory": 0,
                "investigate": 0,
                "best_rank": best_rank or 0,
            },
        )
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
            best_rank=row["best_rank"],
        )
        for path, row in folded.items()
    ]
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


__all__ = [
    "finalize_performance_opportunities",
    "get_health_finding_by_public_id",
    "get_performance_opportunity",
    "get_performance_plan_rows",
    "get_performance_summary",
    "list_evidence_for_opportunities",
    "list_opportunity_evidence",
    "list_performance_opportunities",
    "opportunity_details",
    "performance_facet_counts",
]
