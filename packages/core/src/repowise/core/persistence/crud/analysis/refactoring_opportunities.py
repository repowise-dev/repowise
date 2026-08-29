"""Materialized refactoring opportunities: one writer, indexed readers.

Composition and validation run once per health persistence transaction over the
authoritative stored plans, and everything that serves the queue reads the
result. Measured on the dogfood index before this existed: folding 2,283 plans
cost 91 ms per request (787 ms at ten times the rows) and hydrating their
validation profiles another 1,118 ms, so a page of twenty cost the repository.

The writer is the only place that decides lifecycle, and it decides it by
rolling up the member plans' triage rather than holding an opinion of its own -
an opportunity is exactly as resolved as the work it names. An opportunity
nobody composes this run is resolved rather than deleted, so a held id keeps
answering and stops reading as current.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import (
    HealthFinding,
    RefactoringOpportunity,
    RefactoringSuggestion,
    RefactoringSummary,
    _new_uuid,
    _now_utc,
)
from ...sql import LIKE_ESCAPE, escape_like

if TYPE_CHECKING:
    from ....analysis.health.refactoring.opportunity import (
        RefactoringOpportunity as OpportunityModel,
    )

_PERF_TYPE = "performance_fix"

# Plan states an opportunity is still composed from. ``resolved`` and
# ``false_positive`` are the two that stop describing work.
_LIVE_PLAN_STATUSES = frozenset({"open", "acknowledged"})

# Terminal states a person chose. An opportunity in one of these is not composed
# again, so the reconciler must not read its absence as "the work disappeared"
# and restate the decision as its own.
_DECIDED_STATUSES = frozenset({"resolved", "false_positive"})

# Orders the queue can be read in. Every one ends in a unique column so the
# total order is deterministic and a deep offset cannot repeat or skip a row.
_ORDERS: dict[str, tuple[Any, ...]] = {
    "queue": (RefactoringOpportunity.queue_position.asc(),),
    "rank": (RefactoringOpportunity.rank_position.asc(),),
    "health": (
        RefactoringOpportunity.recoverable_health.desc(),
        RefactoringOpportunity.rank_position.asc(),
    ),
    "effort": (
        RefactoringOpportunity.step_count.asc(),
        RefactoringOpportunity.rank_position.asc(),
    ),
    "file": (
        RefactoringOpportunity.file_path.asc(),
        RefactoringOpportunity.rank_position.asc(),
    ),
}

DEFAULT_ORDER = "queue"


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def _diversified_order(opportunities: list[OpportunityModel]) -> list[int]:
    """Rank positions reordered so the head is not one cause in one directory.

    The ranked head is honestly flat: on the dogfood index eight of the top ten
    score identically, single high-confidence extractions recovering the same
    quantised deduction, separated only by file path. They are equal under the
    published factors, so the fix is not a tiebreaker invented to hide the tie -
    it is showing a queue that spends its first rows on distinct problems.

    Round-robins over (lead biomarker, lead refactoring type, area), groups
    ordered by their best member, members within a group in rank order.

    The area is the containing directory capped at two segments, never the file
    itself: the flat head is eight *different* files, so any key that varies per
    file gives each row its own group and reproduces rank order exactly. What
    actually repeats is the cause, the kind of work and the part of the tree.

    Deterministic, and a repository with one cause in one area degrades to
    plain rank order rather than inventing a difference.
    """
    groups: dict[tuple[str, str, str], list[int]] = {}
    for position, item in enumerate(opportunities):
        parent = item.file_path.rsplit("/", 1)[0] if "/" in item.file_path else ""
        area = "/".join(parent.split("/")[:2])
        groups.setdefault(
            (item.lead_biomarker or "", item.lead_refactoring_type, area), []
        ).append(position)
    ordered_groups = sorted(groups.values(), key=lambda members: members[0])
    order: list[int] = []
    for round_index in range(max((len(m) for m in ordered_groups), default=0)):
        for members in ordered_groups:
            if round_index < len(members):
                order.append(members[round_index])
    return order


def _finding_ids_by_file(findings: list[HealthFinding]) -> dict[str, dict[str, list[str]]]:
    """Public finding ids per file, grouped by biomarker, for evidence round-trips."""
    out: dict[str, dict[str, list[str]]] = {}
    for finding in findings:
        public_id = finding.public_id
        if not public_id:
            continue
        out.setdefault(finding.file_path, {}).setdefault(finding.biomarker_type, []).append(
            public_id
        )
    return out


def _details_payload(
    opportunity: OpportunityModel,
    *,
    validations: dict[str, dict[str, Any]],
    finding_ids: dict[str, list[str]],
    figures: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Everything explanatory, kept out of the indexed columns.

    Steps carry a validation profile id rather than the profile, and the
    profiles are stored once per opportunity: a file's steps usually share one
    test set, and repeating it per step is how a response stops fitting.
    """
    profiles: dict[str, dict[str, Any]] = {}
    steps: list[dict[str, Any]] = []
    for step in opportunity.steps:
        payload = step.as_dict()
        validation = validations.get(step.plan_id)
        if validation:
            profile_id = _validation_profile_id(validation)
            profiles.setdefault(profile_id, {"id": profile_id, **validation})
            payload["validation_profile_id"] = profile_id
        # The findings this step's cause names, so an agent can round-trip from
        # a step back to the diagnosis that produced it in one call. Emitted
        # only when this file has addressable findings at all: on a store
        # written before findings carried public ids there are none, and an
        # empty list there would claim "this cause produced no finding" when
        # the truth is "no finding here is addressable by id".
        if finding_ids:
            payload["finding_ids"] = finding_ids.get(step.source_biomarker, [])
        steps.append(payload)
    return {
        "steps": steps,
        "evidence": [item.as_dict() for item in opportunity.evidence],
        "affected_files": list(opportunity.affected_files),
        "rank_factors": dict(opportunity.rank_factors),
        "why_ranked": [dict(item) for item in opportunity.why_ranked],
        "validation_profiles": list(profiles.values()),
        # Absent rather than zero on a store written before this existed: a
        # zero here would plot a 1,400-line file at the origin.
        **(figures or {}),
        **(
            {"lead_finding_ids": finding_ids.get(opportunity.lead_biomarker or "", [])}
            if finding_ids
            else {}
        ),
    }


def _validation_profile_id(validation: dict[str, Any]) -> str:
    import hashlib

    encoded = json.dumps(validation, sort_keys=True, separators=(",", ":"), default=str)
    return "validation_" + hashlib.sha256(encoded.encode()).hexdigest()[:16]


def _row_kwargs(
    opportunity: OpportunityModel,
    *,
    rank_position: int,
    queue_position: int,
    status: str,
    details: dict[str, Any],
    analyzed_commit: str | None,
) -> dict[str, Any]:
    return {
        "opportunity_id": opportunity.opportunity_id,
        "refactoring_model_version": opportunity.refactoring_model_version,
        "status": status,
        "rank_position": rank_position,
        "queue_position": queue_position,
        "rank_score": float(opportunity.rank_score),
        "file_path": opportunity.file_path,
        "lead_biomarker": opportunity.lead_biomarker,
        "lead_refactoring_type": opportunity.lead_refactoring_type,
        "addresses_primary_problem": opportunity.addresses_primary_problem,
        "effort_bucket": opportunity.effort_bucket,
        "confidence": opportunity.confidence,
        "step_count": opportunity.step_count,
        "mechanical_steps": opportunity.mechanical_steps,
        "judgment_steps": opportunity.judgment_steps,
        "evidence_total": len(opportunity.evidence),
        "affected_files_total": len(opportunity.affected_files),
        "recoverable_health": float(opportunity.recoverable_health),
        "details_json": json.dumps(details, separators=(",", ":"), default=str),
        "analyzed_commit": analyzed_commit,
    }


def _summary_payload(
    opportunities: list[OpportunityModel],
    statuses: dict[str, str],
    lead_details: dict[str, Any] | None,
) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    by_effort: dict[str, int] = {}
    by_confidence: dict[str, int] = {}
    by_status: dict[str, int] = {}
    mechanical = judgment = 0
    addresses_primary = not_primary = unknown_primary = 0
    for item in opportunities:
        by_type[item.lead_refactoring_type] = by_type.get(item.lead_refactoring_type, 0) + 1
        by_effort[item.effort_bucket] = by_effort.get(item.effort_bucket, 0) + 1
        by_confidence[item.confidence] = by_confidence.get(item.confidence, 0) + 1
        state = statuses.get(item.opportunity_id, "open")
        by_status[state] = by_status.get(state, 0) + 1
        mechanical += item.mechanical_steps
        judgment += item.judgment_steps
        if item.addresses_primary_problem is True:
            addresses_primary += 1
        elif item.addresses_primary_problem is False:
            not_primary += 1
        else:
            unknown_primary += 1
    return {
        "opportunities_total": len(opportunities),
        "files_total": len({item.file_path for item in opportunities}),
        "steps_total": mechanical + judgment,
        "mechanical_steps_total": mechanical,
        "judgment_steps_total": judgment,
        "by_lead_type": by_type,
        "by_effort": by_effort,
        "by_confidence": by_confidence,
        "by_status": by_status,
        "addresses_primary_problem": {
            "yes": addresses_primary,
            "no": not_primary,
            "unknown": unknown_primary,
        },
        "lead": lead_details,
    }


async def finalize_refactoring_opportunities(
    session: AsyncSession,
    repository_id: str,
    *,
    analyzed_commit: str | None = None,
) -> int:
    """Compose the stored plans into opportunities and reconcile the read model.

    Called by both index paths inside the transaction that has just written the
    plans, so composition always sees the authoritative set rather than a
    partially-written one. Repository-wide even on an incremental run: an
    opportunity is a fold over a file's plans, and a file the run did not touch
    can still lose its opportunity when a cross-file clone elsewhere resolves.

    Returns the number of open opportunities.
    """
    from ....analysis.health.models import primary_biomarker_by_file
    from ....analysis.health.refactoring.opportunity import (
        compose_opportunities,
        opportunity_status,
    )
    from ....analysis.health.refactoring.recommendations import hydrate_recommendations

    plan_rows = list(
        (
            await session.execute(
                select(RefactoringSuggestion).where(
                    RefactoringSuggestion.repository_id == repository_id,
                    RefactoringSuggestion.refactoring_type != _PERF_TYPE,
                )
            )
        )
        .scalars()
        .all()
    )
    # Acknowledged is outstanding work a person has picked up, not work that is
    # gone: dropping it here would resolve the opportunity the moment someone
    # said they were doing it.
    live_plans = [row for row in plan_rows if row.status in _LIVE_PLAN_STATUSES]
    findings = list(
        (
            await session.execute(
                select(HealthFinding).where(HealthFinding.repository_id == repository_id)
            )
        )
        .scalars()
        .all()
    )

    opportunities = compose_opportunities(
        live_plans, primary_biomarker_by_file=primary_biomarker_by_file(findings)
    )

    # Validation is resolved once, here, for exactly the plans that became
    # steps. It used to be rebuilt on every request for every open plan, which
    # is the single largest cost the serving path used to carry.
    step_plan_ids = {step.plan_id for item in opportunities for step in item.steps}
    step_rows = [row for row in live_plans if row.public_id in step_plan_ids]
    validations: dict[str, dict[str, Any]] = {}
    file_figures: dict[str, dict[str, int]] = {}
    if step_rows:
        # Keyed by the storage id ``rehydrate_suggestion`` carries through, never
        # by position: ``hydrate_recommendations`` returns its results in rank
        # order, so zipping them against the input pairs most steps with another
        # plan's validation profile.
        public_by_storage = {row.id: row.public_id for row in step_rows}
        for recommendation in await hydrate_recommendations(
            session, repository_id, step_rows
        ):
            public_id = public_by_storage.get(
                getattr(recommendation.suggestion, "id", None)
            )
            # The file's size and reach, which hydration has already computed
            # here and which nothing else on the read path can answer cheaply.
            # Recorded per file so the structural field can plot one mark per
            # opportunity without joining back to the unbounded plan list.
            path = getattr(recommendation.suggestion, "file_path", None)
            if path and path not in file_figures:
                file_figures[path] = {
                    "file_nloc": int(recommendation.file_nloc or 0),
                    "dependents": int(recommendation.dependents or 0),
                }
            # Through ``as_dict`` rather than the attribute: ``validation`` is a
            # dataclass, and the serialized form is the one every surface shows.
            validation = recommendation.as_dict().get("validation")
            if public_id and validation:
                validations[public_id] = validation

    plan_status = {row.public_id: row.status for row in plan_rows if row.public_id}
    statuses = {
        item.opportunity_id: opportunity_status(item, plan_status) for item in opportunities
    }
    finding_ids = _finding_ids_by_file(findings)
    queue_order = _diversified_order(opportunities)

    details_by_id = {
        item.opportunity_id: _details_payload(
            item,
            validations=validations,
            finding_ids=finding_ids.get(item.file_path, {}),
            figures=file_figures.get(item.file_path),
        )
        for item in opportunities
    }
    queue_position_by_rank = {rank: slot for slot, rank in enumerate(queue_order)}

    await _reconcile_opportunities(
        session,
        repository_id,
        opportunities,
        statuses=statuses,
        details_by_id=details_by_id,
        queue_position_by_rank=queue_position_by_rank,
        analyzed_commit=analyzed_commit,
    )

    lead_rank = queue_order[0] if queue_order else None
    lead = opportunities[lead_rank] if lead_rank is not None else None
    lead_details = None
    if lead is not None:
        lead_details = {
            "opportunity_id": lead.opportunity_id,
            "file_path": lead.file_path,
            "lead_biomarker": lead.lead_biomarker,
            "lead_refactoring_type": lead.lead_refactoring_type,
            "addresses_primary_problem": lead.addresses_primary_problem,
            "step_count": lead.step_count,
            "mechanical_steps": lead.mechanical_steps,
            "judgment_steps": lead.judgment_steps,
            "effort_bucket": lead.effort_bucket,
            "confidence": lead.confidence,
            "recoverable_health": round(lead.recoverable_health, 3),
            "status": statuses.get(lead.opportunity_id, "open"),
        }
    await _write_summary(
        session,
        repository_id,
        opportunities,
        statuses=statuses,
        lead_details=lead_details,
        analyzed_commit=analyzed_commit,
    )
    return sum(1 for state in statuses.values() if state != "resolved")


async def _reconcile_opportunities(
    session: AsyncSession,
    repository_id: str,
    opportunities: list[OpportunityModel],
    *,
    statuses: dict[str, str],
    details_by_id: dict[str, dict[str, Any]],
    queue_position_by_rank: dict[int, int],
    analyzed_commit: str | None,
) -> None:
    """Update, insert, and resolve, in one pass over the stored rows."""
    from ....analysis.health.refactoring.identity import REFACTORING_MODEL_VERSION

    stored = {
        (row.refactoring_model_version, row.opportunity_id): row
        for row in (
            await session.execute(
                select(RefactoringOpportunity).where(
                    RefactoringOpportunity.repository_id == repository_id,
                    # Rows an older model minted are left exactly as they are:
                    # their keys can never be composed again, so reading them
                    # only to re-resolve them would grow this read for the life
                    # of the repository.
                    RefactoringOpportunity.refactoring_model_version
                    == REFACTORING_MODEL_VERSION,
                )
            )
        )
        .scalars()
        .all()
    }
    seen: set[tuple[int, str]] = set()
    now = _now_utc()
    for rank_position, item in enumerate(opportunities):
        key = (item.refactoring_model_version, item.opportunity_id)
        seen.add(key)
        values = _row_kwargs(
            item,
            rank_position=rank_position,
            queue_position=queue_position_by_rank.get(rank_position, rank_position),
            status=statuses.get(item.opportunity_id, "open"),
            details=details_by_id.get(item.opportunity_id, {}),
            analyzed_commit=analyzed_commit,
        )
        row = stored.get(key)
        if row is None:
            session.add(
                RefactoringOpportunity(id=_new_uuid(), repository_id=repository_id, **values)
            )
            continue
        for name, value in values.items():
            setattr(row, name, value)
        row.updated_at = now
    for key, row in stored.items():
        # A person's decision stands. Both terminal states mean "nobody will
        # compose this again", so an uncomposed row is expected in either case -
        # but they are different claims, and auto-resolving a dismissal would
        # quietly restate "this was never real" as "this got done".
        if key not in seen and row.status not in _DECIDED_STATUSES:
            row.status = "resolved"
            row.updated_at = now
    await session.flush()


async def _write_summary(
    session: AsyncSession,
    repository_id: str,
    opportunities: list[OpportunityModel],
    *,
    statuses: dict[str, str],
    lead_details: dict[str, Any] | None,
    analyzed_commit: str | None,
) -> None:
    from ....analysis.health.refactoring.identity import REFACTORING_MODEL_VERSION

    payload = json.dumps(
        _summary_payload(opportunities, statuses, lead_details), separators=(",", ":")
    )
    row = await session.get(RefactoringSummary, repository_id)
    if row is None:
        row = RefactoringSummary(repository_id=repository_id)
        session.add(row)
    row.refactoring_model_version = REFACTORING_MODEL_VERSION
    row.opportunities_total = len(opportunities)
    row.summary_json = payload
    row.analyzed_commit = analyzed_commit
    row.updated_at = _now_utc()
    await session.flush()


# ---------------------------------------------------------------------------
# Indexed readers
# ---------------------------------------------------------------------------


def _opportunity_filters(
    repository_id: str,
    *,
    status: str,
    lead_types: list[str] | None,
    confidence: str | None,
    effort: str | None,
    file_paths: list[str] | None,
    path_contains: str | None,
    mechanical_only: bool,
    addresses_primary: bool | None,
) -> list[Any]:
    predicates: list[Any] = [
        RefactoringOpportunity.repository_id == repository_id,
        RefactoringOpportunity.status == status,
    ]
    if lead_types:
        # One value is still one equality; a list is how the board's
        # "Structural" tab asks for its four types without four round trips.
        predicates.append(
            RefactoringOpportunity.lead_refactoring_type == lead_types[0]
            if len(lead_types) == 1
            else RefactoringOpportunity.lead_refactoring_type.in_(lead_types)
        )
    if confidence is not None:
        predicates.append(RefactoringOpportunity.confidence == confidence)
    if effort is not None:
        predicates.append(RefactoringOpportunity.effort_bucket == effort)
    if file_paths is not None:
        predicates.append(RefactoringOpportunity.file_path.in_(file_paths))
    if path_contains:
        # A residual filter over the open set, not an index seek: the board's
        # search box is the one caller, and it is bounded by open row count
        # rather than by page. Escaping goes through the shared helper so a
        # path fragment is read as a path fragment here the same way it is
        # everywhere else that builds a LIKE.
        predicates.append(
            RefactoringOpportunity.file_path.ilike(
                f"%{escape_like(path_contains)}%", escape=LIKE_ESCAPE
            )
        )
    if mechanical_only:
        predicates.append(RefactoringOpportunity.mechanical_steps > 0)
    if addresses_primary is not None:
        predicates.append(
            RefactoringOpportunity.addresses_primary_problem.is_(addresses_primary)
        )
    return predicates


async def list_refactoring_opportunities(
    session: AsyncSession,
    repository_id: str,
    *,
    status: str = "open",
    lead_types: list[str] | None = None,
    confidence: str | None = None,
    effort: str | None = None,
    file_paths: list[str] | None = None,
    path_contains: str | None = None,
    mechanical_only: bool = False,
    addresses_primary: bool | None = None,
    order: str = DEFAULT_ORDER,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[RefactoringOpportunity], int]:
    """One page and its total: two statements, whatever the row count."""
    predicates = _opportunity_filters(
        repository_id,
        status=status,
        lead_types=lead_types,
        confidence=confidence,
        effort=effort,
        file_paths=file_paths,
        path_contains=path_contains,
        mechanical_only=mechanical_only,
        addresses_primary=addresses_primary,
    )
    total = int(
        (
            await session.execute(
                select(func.count()).select_from(RefactoringOpportunity).where(*predicates)
            )
        ).scalar_one()
    )
    query: Select[Any] = (
        select(RefactoringOpportunity)
        .where(*predicates)
        .order_by(*_ORDERS.get(order, _ORDERS[DEFAULT_ORDER]))
        .offset(max(offset, 0))
        .limit(max(limit, 0))
    )
    rows = list((await session.execute(query)).scalars().all())
    return rows, total


async def get_refactoring_opportunity(
    session: AsyncSession, repository_id: str, opportunity_id: str
) -> RefactoringOpportunity | None:
    """Resolve one opportunity id. Indexed seek, newest model version first."""
    result = await session.execute(
        select(RefactoringOpportunity)
        .where(
            RefactoringOpportunity.repository_id == repository_id,
            RefactoringOpportunity.opportunity_id == opportunity_id,
        )
        .order_by(RefactoringOpportunity.refactoring_model_version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def update_refactoring_opportunity_status(
    session: AsyncSession,
    repository_id: str,
    opportunity_id: str,
    status: str,
    *,
    reason: str = "user",
) -> tuple[RefactoringOpportunity, int] | None:
    """Transition one opportunity by transitioning the steps it is made of.

    An opportunity has no lifecycle of its own: its state is the rollup of its
    member plans, and those already have one transition owner. So this applies
    that owner to each member and then re-reads the rollup, rather than writing
    a second, divergent decision onto the summary row.

    The stored ``status`` column is refreshed here too, because it is what every
    indexed read filters on - leaving it for the next index would hide a
    dismissed opportunity from nothing at all. Returns ``None`` for an unknown
    id, and raises ``ValueError`` for a status outside the triage vocabulary.
    """
    from ....analysis.health.refactoring.opportunity import roll_up_status
    from .refactoring import ALLOWED_STATUSES, update_refactoring_suggestion_status

    if status not in ALLOWED_STATUSES:
        raise ValueError(f"unknown refactoring status: {status}")
    row = await get_refactoring_opportunity(session, repository_id, opportunity_id)
    if row is None:
        return None

    # Stored as a JSON string, like every other ``*_json`` column.
    raw = row.details_json
    details: dict[str, Any] = {}
    if isinstance(raw, str) and raw:
        try:
            loaded = json.loads(raw)
        except ValueError:
            loaded = None
        if isinstance(loaded, dict):
            details = loaded
    elif isinstance(raw, dict):
        details = raw
    plan_ids = [
        step.get("plan_id")
        for step in details.get("steps", [])
        if isinstance(step, dict) and step.get("plan_id")
    ]
    states: list[str] = []
    updated = 0
    for plan_id in plan_ids:
        plan = await update_refactoring_suggestion_status(
            session, repository_id, str(plan_id), status, reason=reason
        )
        # A step whose plan no longer resolves is not silently counted as
        # transitioned; it reads as open, which is what an untriaged step is.
        states.append(plan.status if plan is not None else "open")
        if plan is not None:
            updated += 1

    # Nothing was written, so there is no decision to roll up, and the row is
    # left exactly as it was. This branch matters because the rollup of an empty
    # set is ``open``: without it, a row whose steps are missing or whose plan
    # ids no longer resolve would answer a request to dismiss it with a cheerful
    # "open" and an HTTP 200, and the one thing the caller asked for would be
    # the one thing that did not happen. The caller reads ``steps_updated`` to
    # tell this apart from success.
    if not updated:
        return row, 0

    row.status = roll_up_status(states)
    row.updated_at = _now_utc()
    await session.flush()
    return row, updated


async def get_refactoring_summary(
    session: AsyncSession, repository_id: str
) -> RefactoringSummary | None:
    """The repository headline, by primary key."""
    return await session.get(RefactoringSummary, repository_id)


async def refactoring_facet_counts(
    session: AsyncSession, repository_id: str, *, status: str = "open"
) -> dict[str, dict[str, int]]:
    """Counts for every facet dimension, in one statement.

    Grouped by all three dimensions at once and folded here rather than one
    statement each, so adding a facet never adds a round trip.
    """
    columns = (
        RefactoringOpportunity.lead_refactoring_type,
        RefactoringOpportunity.effort_bucket,
        RefactoringOpportunity.confidence,
    )
    rows = await session.execute(
        select(*columns, func.count())
        .where(
            RefactoringOpportunity.repository_id == repository_id,
            RefactoringOpportunity.status == status,
        )
        .group_by(*columns)
    )
    facets: dict[str, dict[str, int]] = {"lead_type": {}, "effort": {}, "confidence": {}}
    for lead_type, effort, confidence, count in rows.all():
        for name, key in (
            ("lead_type", lead_type),
            ("effort", effort),
            ("confidence", confidence),
        ):
            if key is not None:
                facets[name][str(key)] = facets[name].get(str(key), 0) + int(count)
    return facets


__all__ = [
    "DEFAULT_ORDER",
    "finalize_refactoring_opportunities",
    "get_refactoring_opportunity",
    "get_refactoring_summary",
    "list_refactoring_opportunities",
    "refactoring_facet_counts",
    "update_refactoring_opportunity_status",
]
