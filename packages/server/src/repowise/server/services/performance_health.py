"""One read model for performance opportunities, serving REST and MCP.

Both surfaces used to load every open finding, regroup the repository, filter
in Python, and link plans by reading two different JSON fields. They disagreed
about the second of those, so one of them never linked a plan at all. This
module owns the query, the order, the page, the facets, the plan link, the
detail, and the recovery once; the two adapters map their own vocabulary onto
it and serialize what comes back.

Nothing here decides what an opportunity *means*. Grouping, actionability, and
rank live in the analysis package, and the values this module reads were
already decided when the opportunities were materialized.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.finding_identity import finding_public_id
from repowise.core.analysis.health.perf.opportunities import (
    PERFORMANCE_MODEL_VERSION,
    model_state,
)
from repowise.core.analysis.health.rows import detail_map
from repowise.core.persistence.crud import (
    get_performance_opportunity,
    get_performance_plan_rows,
    get_performance_summary,
    list_evidence_for_opportunities,
    list_opportunity_evidence,
    list_performance_opportunities,
    performance_facet_counts,
)

from ..mcp_server._references import refactoring_plan_id

PerformanceContext = Literal["production", "tooling", "test", "unknown", "all"]

CANONICAL_CONTEXTS = ("production", "tooling", "test", "unknown")
"""The whole vocabulary. ``all`` is every one of them, never a subset."""

DEFAULT_CONTEXT: PerformanceContext = "production"
"""What a caller that names no context is asking about.

Measured on a 17-repository corpus: 56.5% of opportunities are outside
production code, and the markers concentrated there are the ones that fail
hand-labelling. Test fixtures, benchmark harnesses, CI scripts and mocks are
real code, but "this benchmark repeats work" is a fact about a benchmark, not
performance work someone should schedule. Defaulting to production makes the
first answer the one a reader can act on; every other context stays one
selection away and is counted in ``repository_total`` either way.
"""

_DEPRECATED_CONTEXTS = {"production_tooling": frozenset({"production", "tooling"})}
"""Accepted for one compatibility window and never emitted as canonical.

An older client asks for Production+Tooling under one name. Answering it keeps
that client working; echoing the name back would make a retired spelling look
like the current product concept.
"""

CANONICAL_SORTS = ("rank", "leverage", "observations")
CANONICAL_VIEWS = ("detail", "summary")
_CONFIDENCES = ("high", "medium", "low")
_ACTIONABILITIES = ("plan_ready", "advisory", "investigate")
_BOUNDARIES = ("db", "network", "filesystem", "subprocess", "lock", "none")

_PLAN_REASONS = {
    "available": "A stored performance plan addresses this exact opportunity.",
    "no_safe_plan": (
        "The analysis found the shared cause but could not prove one coherent "
        "intervention without guessing."
    ),
    "not_persisted": (
        "A supported strategy exists, but this index does not contain its matching "
        "stored plan. Reindex to refresh recommendations."
    ),
}

_SUMMARY_UNAVAILABLE = {
    "status": "unavailable",
    "reason": "no_materialized_analysis",
    "detail": (
        "This index has no materialized performance analysis. Run repowise update "
        "to build it."
    ),
}


@dataclass(frozen=True, slots=True)
class PerformanceQuery:
    """One request for a page of the queue, in canonical terms.

    Built by :func:`parse_query` so both adapters normalize identically and an
    unrecognized value is reported rather than silently read as "no results".
    """

    context: PerformanceContext = DEFAULT_CONTEXT
    boundary: str | None = None
    confidence: str | None = None
    actionability: str | None = None
    view: str = "detail"
    sort: str = "rank"
    file_paths: tuple[str, ...] | None = None
    limit: int = 20
    offset: int = 0

    @property
    def contexts(self) -> frozenset[str] | None:
        if self.context == "all":
            return None
        alias = _DEPRECATED_CONTEXTS.get(self.context)
        return alias if alias is not None else frozenset({self.context})


@dataclass(frozen=True, slots=True)
class PlanLink:
    """Where the plan for one opportunity lives, in both address spaces.

    ``row_id`` resolves through the repository-scoped REST route and
    ``public_id`` through the ``plan_id`` selector on the agent surface. The
    rule that decides *whether* there is a plan is above them and is written
    once.
    """

    state: str
    row_id: str | None = None
    public_id: str | None = None

    @property
    def reason(self) -> str:
        return _PLAN_REASONS[self.state]


@dataclass(frozen=True, slots=True)
class PerformancePage:
    items: list[dict[str, Any]]
    total: int
    offset: int
    next_offset: int | None
    facets: dict[str, list[dict[str, Any]]]
    summary: dict[str, Any]
    ignored_arguments: dict[str, str] = field(default_factory=dict)


def parse_query(
    *,
    context: str | None = None,
    boundary: str | None = None,
    confidence: str | None = None,
    actionability: str | None = None,
    view: str | None = None,
    sort: str | None = None,
    file_paths: tuple[str, ...] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[PerformanceQuery, dict[str, str]]:
    """Normalize caller input, reporting anything unrecognized by name.

    An unknown filter value must not read as an empty repository, so it is
    dropped from the query and named in the returned map instead.
    """
    ignored: dict[str, str] = {}

    def pick(name: str, value: str | None, allowed: tuple[str, ...]) -> str | None:
        if value is None or value == "":
            return None
        if value in allowed:
            return value
        ignored[name] = value
        return None

    resolved_context: PerformanceContext = DEFAULT_CONTEXT
    if context:
        if context in CANONICAL_CONTEXTS or context == "all" or context in _DEPRECATED_CONTEXTS:
            resolved_context = context  # type: ignore[assignment]
        else:
            # Named, so the caller learns the value was not understood, then
            # treated as absent like every other unrecognized filter.
            ignored["performance_context"] = context
    return (
        PerformanceQuery(
            context=resolved_context,
            boundary=pick("performance_boundary", boundary, _BOUNDARIES),
            confidence=pick("performance_confidence", confidence, _CONFIDENCES),
            actionability=pick("performance_actionability", actionability, _ACTIONABILITIES),
            view=pick("performance_view", view, CANONICAL_VIEWS) or "detail",
            sort=pick("performance_sort", sort, CANONICAL_SORTS) or "rank",
            file_paths=file_paths,
            limit=max(0, limit),
            offset=max(0, offset),
        ),
        ignored,
    )


class PerformanceHealthService:
    """Query, page, detail, facets, plan linkage, and recovery, in one place."""

    def __init__(self, session: AsyncSession, repository_id: str, repository: str) -> None:
        self._session = session
        self._repository_id = repository_id
        self._repository = repository

    # -- collection --------------------------------------------------------

    async def page(
        self,
        query: PerformanceQuery,
        *,
        evidence_per_item: int = 0,
        with_facets: bool = False,
        with_summary: bool = False,
    ) -> PerformancePage:
        """One page and its plan links, plus whatever else the caller renders.

        Three statements for the page itself: the rows, their count, and their
        plans, and each fetches the page rather than the repository. Evidence,
        facets, and the headline each cost one more and are opt-in, because a
        caller that quotes one row should not pay for a rollup it discards.

        The facet aggregate is the one that reads more than the page: it groups
        every open opportunity, which is the only way to report the counts a
        filter control needs. It stays a single indexed aggregate over a table
        that already holds one row per cause rather than one per observation.
        """
        rows, total = await list_performance_opportunities(
            self._session,
            self._repository_id,
            contexts=query.contexts,
            boundary=query.boundary,
            confidence=query.confidence,
            actionability=query.actionability,
            file_paths=query.file_paths,
            sort=query.sort,
            limit=query.limit,
            offset=query.offset,
        )
        links = await self._plan_links(rows)
        evidence = await self._evidence_for(rows, evidence_per_item)
        items = [
            {
                **self._serialize(
                    row, links[row.opportunity_id], summary=query.view == "summary"
                ),
                **(
                    evidence_block(
                        evidence.get(row.opportunity_id, []), row.observations_total, 0
                    )
                    if evidence_per_item
                    else {}
                ),
            }
            for row in rows
        ]
        emitted = query.offset + len(items)
        return PerformancePage(
            items=items,
            total=total,
            offset=query.offset,
            next_offset=emitted if emitted < total else None,
            facets=await self._facets(query) if with_facets else {},
            summary=await self.summary(query.contexts) if with_summary else {},
        )

    async def _evidence_for(
        self, rows: list[Any], per_item: int
    ) -> dict[str, list[dict[str, Any]]]:
        """A few observations for every row on the page, in one statement.

        Ranked inside the database, so a page holding one very large cause
        still reads a bounded number of rows.
        """
        if not per_item or not rows:
            return {}
        grouped = await list_evidence_for_opportunities(
            self._session,
            self._repository_id,
            [row.opportunity_id for row in rows],
            per_opportunity=per_item,
        )
        return {key: [evidence_payload(row) for row in group] for key, group in grouped.items()}

    async def _plan_links(self, rows: list[Any]) -> dict[str, PlanLink]:
        """One indexed batch for the whole page, never one query per row."""
        plans = await get_performance_plan_rows(
            self._session, self._repository_id, [row.opportunity_id for row in rows]
        )
        links: dict[str, PlanLink] = {}
        for row in rows:
            plan = plans.get(row.opportunity_id)
            if plan is None:
                # The materialized state already distinguishes "no safe plan"
                # from "a strategy exists but this index has no plan row".
                state = row.plan_state if row.plan_state != "available" else "not_persisted"
                links[row.opportunity_id] = PlanLink(state)
                continue
            links[row.opportunity_id] = PlanLink(
                "available",
                row_id=str(plan.id),
                public_id=refactoring_plan_id(plan, self._repository),
            )
        return links

    async def _facets(self, query: PerformanceQuery) -> dict[str, list[dict[str, Any]]]:
        """Counts per filter value, each cross-filtered by the *other* filters.

        Counting a facet under its own filter would leave every alternative at
        zero, so choosing one value would erase the others from the control.
        """
        grouped = await performance_facet_counts(
            self._session, self._repository_id, file_paths=query.file_paths
        )
        dimensions = {
            "context": 0,
            "boundary": 1,
            "confidence": 2,
            "actionability": 3,
            "plan_state": 4,
        }
        selected = {
            "context": query.contexts,
            "boundary": None if query.boundary is None else frozenset({query.boundary}),
            "confidence": None if query.confidence is None else frozenset({query.confidence}),
            "actionability": (
                None if query.actionability is None else frozenset({query.actionability})
            ),
            "plan_state": None,
        }
        facets: dict[str, list[dict[str, Any]]] = {}
        for name, index in dimensions.items():
            counts: dict[str, int] = {}
            for row in grouped:
                if any(
                    values is not None
                    and other != name
                    and (row[dimensions[other]] or "none") not in values
                    for other, values in selected.items()
                ):
                    continue
                counts[row[index] or "none"] = counts.get(row[index] or "none", 0) + row[5]
            facets[name] = [
                {"value": value, "total": total}
                for value, total in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            ]
        return facets

    # -- headline ----------------------------------------------------------

    async def summary(self, contexts: frozenset[str] | None = None) -> dict[str, Any]:
        """The compact rollup, over *contexts* when one is selected.

        Reads by primary key, so a bare dashboard pays one statement for its
        performance headline however large the repository is. Selecting a
        context costs one more aggregate and rewrites the counts to describe
        that context, because a headline that counted the whole repository
        beside a queue that showed one slice of it would state a number the
        list below it contradicts.

        ``repository_total`` survives every scoping, so the census of what was
        analyzed is never the thing a filter hides.
        """
        base = _summary_of(await get_performance_summary(self._session, self._repository_id))
        if base["status"] == "unavailable":
            return base
        base["repository_total"] = base["total"]
        if contexts is None:
            return base
        grouped = await performance_facet_counts(self._session, self._repository_id)
        return _rescope(base, grouped, contexts)

    async def directive(self) -> dict[str, Any]:
        """One bounded next action for a bare dashboard call.

        Selection is the best concrete next action rather than the highest raw
        rank: the materialized order already sorts actionable work above
        high-volume evidence, so the lead is the first row.

        The lead and the counts come off the same row read, because the lead is
        the reason the row exists.
        """
        row = await get_performance_summary(self._session, self._repository_id)
        summary = _summary_of(row)
        if summary["status"] == "unavailable":
            return {
                "status": "unavailable",
                "reason": summary["reason"],
                "detail": summary["detail"],
            }
        lead = _loads(row.summary_json).get("lead")
        counts = summary["actionability"]
        base = {
            "performance_model_version": PERFORMANCE_MODEL_VERSION,
            "opportunities_total": summary["total"],
            "plan_ready_total": counts.get("plan_ready", 0),
            "advisory_total": counts.get("advisory", 0),
            "investigate_total": counts.get("investigate", 0),
        }
        if summary["status"] == "stale_model":
            return {
                **base,
                "status": "unavailable",
                "reason": "stale_model",
                "detail": (
                    "The stored performance analysis predates the current model. "
                    "Run repowise update to rescore."
                ),
            }
        if lead is None:
            return {
                **base,
                "status": "clear",
                # Never "fast": the analysis found no supported pattern, which
                # is not a measurement of how this code runs.
                "detail": "No supported open pattern surfaced.",
            }
        return {
            **base,
            "status": lead["actionability_state"],
            "opportunity_id": lead["opportunity_id"],
            "title": _title(lead),
            "file_path": lead["file_path"],
            "execution_context": lead["execution_context"],
            "boundary_kind": lead["boundary_kind"],
            "affected_call_sites_total": lead["affected_call_sites_total"],
            "observations_total": lead["observations_total"],
            "why_ranked": lead["why_ranked"][:3],
            "plan_state": lead["plan_state"],
            "plan_reason": _PLAN_REASONS[lead["plan_state"]],
            "prerequisites": lead["prerequisites"],
            "next_action": {
                "tool": "get_health",
                "arguments": {"opportunity_id": lead["opportunity_id"]},
            },
        }

    # -- detail ------------------------------------------------------------

    async def detail(
        self, opportunity_id: str, *, evidence_limit: int = 3, evidence_offset: int = 0
    ) -> dict[str, Any]:
        """One opportunity by id, with bounded evidence and exact plan state."""
        state = model_state(opportunity_id)
        row = await get_performance_opportunity(
            self._session, self._repository_id, opportunity_id
        )
        if row is None:
            return {
                "resolved": False,
                "opportunity_id": opportunity_id,
                "model_state": state,
                "detail": _unresolved_detail(state),
            }
        links = await self._plan_links([row])
        payload = self._serialize(row, links[opportunity_id])
        evidence, total = await self.evidence(
            opportunity_id,
            limit=evidence_limit,
            offset=evidence_offset,
            total=row.observations_total,
        )
        payload.update(
            {
                "resolved": True,
                "lifecycle_status": row.status,
                "analyzed_commit": row.analyzed_commit,
                "model_state": state,
                **evidence_block(evidence, total, evidence_offset),
            }
        )
        return payload

    async def evidence(
        self,
        opportunity_id: str,
        *,
        limit: int = 3,
        offset: int = 0,
        total: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """One page of the observations behind a cause, and their exact total."""
        rows, total = await list_opportunity_evidence(
            self._session,
            self._repository_id,
            opportunity_id,
            limit=max(0, limit),
            offset=max(0, offset),
            total=total,
        )
        return [evidence_payload(row) for row in rows], total

    # -- serialization -----------------------------------------------------

    def _serialize(self, row: Any, link: PlanLink, *, summary: bool = False) -> dict[str, Any]:
        """Rebuild the canonical opportunity payload from row plus details.

        Columns carry what a query filters or orders on and ``details_json``
        carries the rest, so no fact is stored twice and nothing here computes
        a value the writer already decided.
        """
        details = _loads(row.details_json)
        payload = {
            "opportunity_id": row.opportunity_id,
            "performance_model_version": row.performance_model_version,
            "biomarker_type": row.biomarker_type,
            "biomarker_types": details.get("biomarker_types", []),
            "boundary_kind": row.boundary_kind,
            "execution_context": row.execution_context,
            "terminal_sink": row.terminal_sink,
            "intervention_symbol": row.intervention_symbol,
            "file_path": row.file_path,
            "affected_call_sites_total": row.affected_call_sites_total,
            "affected_files_total": row.affected_files_total,
            "observations_total": row.observations_total,
            "confidence": row.evidence_confidence,
            "actionability_state": row.actionability_state,
            "rank_score": row.rank_score,
            "rank_position": row.rank_position,
            "why_ranked": details.get("why_ranked", []),
            "plan_id": link.row_id,
            "plan_reference": link.public_id,
            "plan_status": link.state,
            "plan_reason": link.reason,
        }
        if summary:
            return payload
        fix_strategy = row.fix_strategy
        return {
            **payload,
            "shared_path_suffix": details.get("shared_path_suffix", []),
            "resource_fingerprints": details.get("resource_fingerprints", []),
            "reliable_entry_reachability": details.get("reliable_entry_reachability"),
            "provenance": details.get("provenance"),
            "facets": details.get("facets", {}),
            "actionability_reason": details.get("actionability_reason"),
            "prerequisites": details.get("prerequisites", []),
            "rank_factors": details.get("rank_factors", {}),
            "fix": None
            if fix_strategy is None
            else {
                "strategy": fix_strategy,
                "safety": row.fix_safety,
                "rationale": details.get("fix_rationale") or "",
            },
        }


def _rescope(
    base: dict[str, Any],
    grouped: list[tuple[str, str | None, str, str, str, int]],
    contexts: frozenset[str],
) -> dict[str, Any]:
    """Recount one stored rollup over *contexts*, from the facet aggregate.

    The same grouped counts the filter control is drawn from, summed a second
    way. Deriving the scoped headline here keeps one materialized row as the
    only stored rollup: a per-context rollup would be four more rows to write,
    version and keep honest for a number two sums recover exactly.
    """
    actionability: dict[str, int] = {}
    context: dict[str, int] = {}
    boundary: dict[str, int] = {}
    total = 0
    with_plan = 0
    for execution_context, boundary_kind, _confidence, state, plan_state, count in grouped:
        if execution_context not in contexts:
            continue
        total += count
        actionability[state] = actionability.get(state, 0) + count
        context[execution_context] = context.get(execution_context, 0) + count
        key = boundary_kind or "none"
        boundary[key] = boundary.get(key, 0) + count
        if plan_state == "available":
            with_plan += count
    return {
        **base,
        "total": total,
        "actionability": actionability,
        "context": context,
        "boundary": boundary,
        "with_plan_total": with_plan,
    }


def _summary_of(row: Any) -> dict[str, Any]:
    """The canonical rollup for one stored summary row, or its absence.

    A missing row means this index has never materialized the analysis, which
    is a different answer from an empty one and must not read as a clean
    repository.
    """
    if row is None:
        return dict(_SUMMARY_UNAVAILABLE)
    payload = _loads(row.summary_json)
    stale = row.performance_model_version != PERFORMANCE_MODEL_VERSION
    return {
        "status": "stale_model" if stale else "current",
        "performance_model_version": PERFORMANCE_MODEL_VERSION,
        "materialized_model_version": row.performance_model_version,
        "analyzed_commit": row.analyzed_commit,
        "total": row.opportunities_total,
        "actionability": payload.get("actionability", {}),
        "context": payload.get("context", {}),
        "boundary": payload.get("boundary", {}),
        "with_plan_total": payload.get("with_plan_total", 0),
        **(
            {"refresh_required": True, "detail": "Run repowise update to rescore."}
            if stale
            else {}
        ),
    }


def evidence_block(
    evidence: list[dict[str, Any]], total: int, offset: int
) -> dict[str, Any]:
    """Evidence plus the exact recovery for what it left out."""
    emitted = offset + len(evidence)
    block: dict[str, Any] = {
        "evidence": evidence,
        "evidence_total": total,
        "evidence_emitted": len(evidence),
        "evidence_truncated": emitted < total,
    }
    if emitted < total:
        block["evidence_reduced_reason"] = "evidence_page"
        block["evidence_next_cursor"] = emitted
    return block


def _title(lead: dict[str, Any]) -> str:
    """A human cause, not a marker name."""
    symbol = lead.get("intervention_symbol") or lead.get("terminal_sink") or lead["file_path"]
    return f"{lead['biomarker_type']} reaching {symbol}"


def _unresolved_detail(state: dict[str, Any]) -> str:
    if state["state"] == "stale_model":
        return (
            "That id was minted by performance model "
            f"{state['requested_model_version']}; this index is on model "
            f"{state['performance_model_version']}. Membership differs between "
            "models, so the id is not translated. Run repowise update to rescore."
        )
    if state["state"] == "unrecognized":
        return "That is not a performance opportunity id."
    return "No open or resolved opportunity in this repository carries that id."


def evidence_payload(row: Any) -> dict[str, Any]:
    """One public evidence entry, addressed by the finding's public id.

    The stored id when there is one, the same kernel recomputed when a store
    predates the column, and never the storage row id.
    """
    details = detail_map(row)
    return {
        "finding_id": row.public_id or finding_public_id(row),
        "file_path": row.file_path,
        "biomarker_type": row.biomarker_type or "",
        "function_name": row.function_name,
        "line_start": row.line_start,
        "line_end": row.line_end,
        "reason": row.reason,
        "path": list(details.get("path", ())),
        "provenance": details.get("resolution_basis", "direct"),
    }


def _loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


__all__ = [
    "CANONICAL_CONTEXTS",
    "CANONICAL_SORTS",
    "CANONICAL_VIEWS",
    "PerformanceHealthService",
    "PerformancePage",
    "PerformanceQuery",
    "PlanLink",
    "evidence_block",
    "evidence_payload",
    "parse_query",
]
