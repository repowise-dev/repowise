"""One read model for refactoring opportunities, shared by REST and MCP.

Both surfaces used to load every open plan, hydrate it, rank it and page it in
Python, and they disagreed about almost everything they did with the result:
different filter vocabularies, two incompatible pagination contracts, and two
ways of resolving one id - REST an indexed point lookup, MCP a full hydration
followed by a linear scan. Query, filter, order, page, facets, detail and the
directive live here now, so the agent surface and the product surface cannot
drift apart.

The session-free half (query parsing, the sort/filter/facet rules and every
response shape) is ``analysis/health/refactoring/serving.py``; this module
runs the reads and hands rows to it. Nothing here composes, and nothing ranks
except the plan-list fallback for a store the finalizer has not ranked.
Composition is ``analysis/health/refactoring/opportunity.py`` and it runs at
index time; this module reads what the finalizer wrote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.queue_rules import keep, sort_key
from repowise.core.analysis.health.refactoring.identity import REFACTORING_MODEL_VERSION
from repowise.core.analysis.health.refactoring.recommendations import (
    EFFORT_RANK,
    PLAN_FILTERS,
    PLAN_SORTS,
    UNKNOWN_EFFORT,
    apply_view,
    blast_size,
    detail_recommendations,
    hydrate_recommendations,
    matches_search,
    plan_types,
    stored_recommendation,
)
from repowise.core.analysis.health.refactoring.serving import (
    RefactoringQuery,
    directive_from_summary,
    evidence_block,
    next_actions,
    plan_payload,
    serialize,
    stored_validation,
    summary_payload,
    validation_from_profile,
)
from repowise.core.analysis.health.refactoring_summary import summarize_plans
from repowise.core.analysis.health.rows import detail_map, json_field
from repowise.core.persistence.crud.analysis.queue_counts import unit_counts
from repowise.core.persistence.crud.analysis.refactoring import (
    get_refactoring_suggestions,
    ranked_refactoring_suggestions,
    summarize_open_plans,
)
from repowise.core.persistence.crud.analysis.refactoring_opportunities import (
    get_refactoring_opportunity,
    get_refactoring_summary,
    list_refactoring_opportunities,
    refactoring_facet_counts,
    refactoring_opportunities_by_id,
    refactoring_reason_counts,
    refactoring_step_counts,
)
from repowise.core.persistence.models import RefactoringOpportunity, RefactoringSuggestion


@dataclass(frozen=True, slots=True)
class RefactoringPage:
    items: list[dict[str, Any]]
    total: int
    offset: int
    next_offset: int | None
    facets: dict[str, dict[str, int]] = field(default_factory=dict)
    summary: dict[str, Any] | None = None
    ignored_arguments: dict[str, str] = field(default_factory=dict)
    scope: str = "all"
    #: Under ``fix_first``: the opportunities the same filters match that Fix
    #: first leaves out, ``{"total": n, "by_reason": {reason: n}}``.
    hidden: dict[str, Any] | None = None
    #: The plans' count vocabulary (``queue.counts``) in the query's files.
    counts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RefactoringPlanPage:
    """One page of the queue's plans: stored steps, each with its opportunity id."""

    items: list[dict[str, Any]]
    #: Plans in scope, and the opportunities they are steps of.
    total: int
    opportunities_total: int
    #: Where the next page starts: the page's offset plus the plans it covers.
    next_offset: int
    scope: str = "all"
    hidden: dict[str, Any] | None = None
    counts: dict[str, Any] = field(default_factory=dict)


def _filters(query: RefactoringQuery) -> dict[str, Any]:
    """The store's filter arguments for *query*."""
    return {
        "status": query.status,
        "lead_types": list(query.lead_types) if query.lead_types else None,
        "confidence": query.confidence,
        "effort": query.effort,
        "file_paths": list(query.file_paths) if query.file_paths is not None else None,
        "path_contains": query.path_contains,
        "path_prefix": query.path_prefix,
        "mechanical_only": query.mechanical_only,
        "addresses_primary": query.addresses_primary,
    }


def _plan_window(
    counts: list[tuple[str, int]], offset: int, limit: int
) -> dict[str, tuple[int, int]]:
    """Which steps of which opportunities fall in plans ``[offset, offset + limit)``.

    *counts* is ``(opportunity_id, step_count)`` in queue order; the result
    maps each touched opportunity to its ``(start, stop)`` step slice.
    """
    window: dict[str, tuple[int, int]] = {}
    position, end = 0, offset + max(limit, 0)
    for opportunity_id, steps in counts:
        if position >= end:
            break
        start, stop = max(offset - position, 0), min(end - position, steps)
        if start < stop:
            window[opportunity_id] = (start, stop)
        position += steps
    return window


@dataclass(frozen=True, slots=True)
class PlanListQuery:
    """One plan-list request: the filters, search, order and page."""

    refactoring_type: str | None = None
    min_confidence: str | None = None
    confidences: frozenset[str] = frozenset()
    efforts: frozenset[str] = frozenset()
    file_path: str | None = None
    search: str = ""
    sort: str = "canonical"
    view: Literal["canonical", "file_spread"] = "canonical"
    limit: int | None = None
    offset: int = 0

    def filters(self) -> dict[str, Any]:
        """The params ``PLAN_FILTERS`` reads, on either path."""
        return {
            "refactoring_types": plan_types(self.refactoring_type),
            "file_path": self.file_path,
            "confidences": self.confidences,
            "efforts": self.efforts,
        }


#: Structural plans a plan page leads with.
_STRUCTURAL_LEADS = 12


def _live_sorted(items: list[Any], sort: str) -> list[Any]:
    """*items*, in view order, under a named sort from the shared table."""
    position = {id(item): index for index, item in enumerate(items)}

    def key(item: Any) -> tuple[Any, ...]:
        suggestion = item.suggestion
        if sort == "effort":
            bucket = EFFORT_RANK.get(suggestion.effort_bucket, UNKNOWN_EFFORT)
            return (bucket, position[id(item)])
        row = {
            "impact_delta": suggestion.impact_delta,
            "blast_size": blast_size(suggestion),
            "file_path": suggestion.file_path,
            "target_symbol": suggestion.target_symbol,
            "id": item.id,
        }
        return (*sort_key(PLAN_SORTS[sort], row), position[id(item)])

    if sort != "effort" and sort not in PLAN_SORTS:
        return items
    return sorted(items, key=key)


def _plan_page_payload(
    items: list[Any], total: int, offset: int, summary: dict[str, Any], leads: list[Any]
) -> dict[str, Any]:
    next_offset = offset + len(items) if offset + len(items) < total else None
    return {
        "items": [item.as_dict() for item in items],
        "total": total,
        "has_more": next_offset is not None,
        "next_offset": next_offset,
        "summary": summary,
        "structural_leads": [item.as_dict() for item in leads],
    }


class RefactoringHealthService:
    """Query, page, detail and headline over the materialized opportunities."""

    def __init__(self, session: AsyncSession, repository_id: str, repository: str) -> None:
        self._session = session
        self._repository_id = repository_id
        self._repository = repository

    # -- queue ------------------------------------------------------------

    async def page(
        self,
        query: RefactoringQuery,
        *,
        steps_per_item: int | None = None,
        evidence_per_item: int = 0,
        with_facets: bool = False,
        with_summary: bool = False,
    ) -> RefactoringPage:
        """One page: two statements, plus one each for facets and the headline.

        The statement count is constant in page size and in row count. The
        indexes cover repository, status and the orderings, plus lead type and
        file path; ``mechanical_only`` and ``addresses_primary`` are residual
        filters over the open set, so those two are bounded by the open row
        count rather than by the page. Neither has a consumer yet - index them
        when one exists, not before.

        Under the ``fix_first`` scope the Fix-first queue (cached per store
        write) says which opportunities it takes, and one id-only read of the
        filtered set counts what it leaves out, by reason.
        """
        filters, hidden = await self._scope(query, _filters(query))
        rows, total = await list_refactoring_opportunities(
            self._session,
            self._repository_id,
            **filters,
            order=query.resolved_order,
            limit=query.limit,
            offset=query.offset,
        )
        items = [
            serialize(row, steps_limit=steps_per_item, evidence_limit=evidence_per_item)
            for row in rows
        ]
        next_offset = query.offset + len(items)
        return RefactoringPage(
            items=items,
            total=total,
            offset=query.offset,
            next_offset=next_offset if next_offset < total else None,
            facets=(
                # Under the list's status, scope and filters, so a badge never
                # counts rows its tab would not return.
                await refactoring_facet_counts(self._session, self._repository_id, **filters)
                if with_facets
                else {}
            ),
            summary=await self.summary() if with_summary else None,
            scope=query.scope,
            hidden=hidden,
            counts=await self._counts(query, len(items)),
        )

    async def plan_page(self, query: RefactoringQuery) -> RefactoringPlanPage:
        """The queue's plans in queue order: each opportunity's steps, in step order.

        A plan is a step of one opportunity, so the plan list is a view of the
        queue: the same scope, filters and order, paged by plan. One narrow read
        of step counts places the page; only the opportunities it touches are
        decoded.
        """
        filters, hidden = await self._scope(query, _filters(query))
        counts = await refactoring_step_counts(
            self._session,
            self._repository_id,
            **filters,
            order=query.resolved_order,
        )
        window = _plan_window(counts, query.offset, query.limit)
        rows = await refactoring_opportunities_by_id(
            self._session, self._repository_id, list(window), status=query.status
        )
        # The window is in queue order; the read is not.
        items = [
            {**step, "opportunity_id": opportunity_id}
            for opportunity_id, (start, stop) in window.items()
            if opportunity_id in rows
            for step in (detail_map(rows[opportunity_id]).get("steps") or [])[start:stop]
        ]
        return RefactoringPlanPage(
            items=items,
            total=sum(steps for _id, steps in counts),
            opportunities_total=len(counts),
            next_offset=query.offset + sum(stop - start for start, stop in window.values()),
            scope=query.scope,
            hidden=hidden,
            counts=await self._counts(query, len(window)),
        )

    async def _scope(
        self, query: RefactoringQuery, filters: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """The filters a scope reads, and under ``fix_first`` what the same
        filters match that Fix first leaves out, by the reason stored on each
        row at index time. ``all`` keeps every row."""
        if query.scope != "fix_first":
            return filters, None
        by_reason = await refactoring_reason_counts(self._session, self._repository_id, **filters)
        left_out = {reason: n for reason, n in by_reason.items() if reason is not None}
        return {**filters, "queue_eligible": True}, {
            "total": sum(left_out.values()),
            "by_reason": dict(sorted(left_out.items(), key=lambda i: (-i[1], i[0]))),
        }

    async def _counts(self, query: RefactoringQuery, shown: int) -> dict[str, Any]:
        """The plans' counts in the query's files: where the page sits in the queue."""
        counts = await unit_counts(
            self._session,
            self._repository_id,
            "plans",
            shown=shown,
            file_paths=query.file_paths,
        )
        return counts.as_dict()

    # -- headline ---------------------------------------------------------

    async def summary(self) -> dict[str, Any]:
        """The Level-1 rollup, read by primary key."""
        return summary_payload(await get_refactoring_summary(self._session, self._repository_id))

    async def directive(self) -> dict[str, Any]:
        """The Level-0 lead: one opportunity, and the exact call that opens it.

        The same primary-key read the summary uses, so a bare dashboard pays one
        statement for it and never touches the queue.
        """
        return directive_from_summary(
            await get_refactoring_summary(self._session, self._repository_id)
        )

    # -- detail -----------------------------------------------------------

    async def detail(
        self,
        opportunity_id: str,
        *,
        step_limit: int = 20,
        step_offset: int = 0,
        evidence_limit: int = 3,
        evidence_offset: int = 0,
        with_plans: bool = True,
    ) -> dict[str, Any]:
        """One opportunity by id: an indexed seek, then its member plans.

        ``found`` says whether the id named a stored row; ``status`` is the
        opportunity's triage lifecycle. The lookup flag is not called
        ``resolved`` because ``status`` can itself be ``resolved``, and a
        payload reading ``resolved: true`` beside ``status: "open"`` would
        contradict itself.
        """
        row = await get_refactoring_opportunity(
            self._session, self._repository_id, opportunity_id
        )
        if row is None:
            return {
                "found": False,
                "opportunity_id": opportunity_id,
                "reason": "unknown_opportunity_id",
            }
        details = detail_map(row)
        steps = list(details.get("steps") or [])
        page = steps[step_offset : step_offset + max(step_limit, 0)]
        payload = serialize(row, steps_limit=None, evidence_limit=0)
        payload["found"] = True
        payload["steps"] = page
        payload["steps_total"] = len(steps)
        payload["steps_emitted"] = len(page)
        if step_offset + len(page) < len(steps):
            payload["steps_reduced_reason"] = "limit"
            payload["steps_next_cursor"] = step_offset + len(page)
        evidence = list(details.get("evidence") or [])
        payload.update(
            evidence_block(
                evidence[evidence_offset : evidence_offset + max(evidence_limit, 0)],
                len(evidence),
                evidence_offset,
            )
        )
        payload["validation_profiles"] = list(details.get("validation_profiles") or [])
        payload["affected_files"] = list(details.get("affected_files") or [])
        payload["lead_finding_ids"] = list(details.get("lead_finding_ids") or [])
        payload["next_actions"] = next_actions(row, page)
        if with_plans and page:
            payload["plans"] = await self._plans_for([s["plan_id"] for s in page])
        # Ordered steps carry ``relocated_by``; a surface that renders them must
        # say the symbol has to be located again before the step is applied.
        if any(step.get("relocated_by") for step in page):
            payload["ordering_note"] = (
                "A step carrying `relocated_by` names a symbol an earlier step moves. "
                "Locate it again before applying the step; its file and span describe "
                "where the symbol was."
            )
        return payload

    async def plan_detail(self, plan_id: str) -> dict[str, Any]:
        """Resolve one plan id without hydrating the repository.

        Every read is a seek: the row from the storage-or-public id index, and
        its rank and validation as :meth:`plan_recommendation` finds them.
        """
        from repowise.core.persistence.crud import get_refactoring_suggestion

        row = await get_refactoring_suggestion(self._session, self._repository_id, plan_id)
        if row is None:
            return {"resolved": False, "plan_id": plan_id, "reason": "unknown_plan_id"}
        owner = await self._owning_opportunity(row.public_id, row.file_path)
        payload = (await self.plan_recommendation(row, owner=owner)).detail_dict()
        payload["id"] = row.public_id or row.id
        payload["status"] = row.status
        result: dict[str, Any] = {"resolved": True, "plan_id": plan_id, "plan": payload}
        if owner is not None:
            # On the envelope as well as the plan: a plan is a step of one
            # opportunity, and that is the unit the caller should move to.
            payload["opportunity_id"] = owner.opportunity_id
            result["opportunity_id"] = owner.opportunity_id
            result["next_action"] = {
                "tool": "get_health",
                "arguments": {"opportunity_id": owner.opportunity_id},
            }
        return result

    # -- plan lists ---------------------------------------------------------
    #
    # Each list reads the rank, factors and validation the finalizer stored, so
    # its cost follows the page. A store with an open plan the finalizer did
    # not rank is ranked per request instead. Remove that live path once every
    # served store has been indexed by a version that writes ranks.

    async def ranked_plans(self, query: PlanListQuery) -> dict[str, Any]:
        """Every open plan the query matches, ranked, with the per-type chips.

        The chips ignore the type and file filters, so each type keeps its
        total while one is selected, but honour ``min_confidence``.
        """
        stored = await ranked_refactoring_suggestions(
            self._session,
            self._repository_id,
            min_confidence=query.min_confidence,
            filters=query.filters(),
            view=query.view,
        )
        if stored is not None:
            chips = await summarize_open_plans(
                self._session, self._repository_id, min_confidence=query.min_confidence
            )
            plans = [stored_recommendation(row).as_dict() for row in stored[0]]
        else:
            rows = await get_refactoring_suggestions(
                self._session, self._repository_id, min_confidence=query.min_confidence
            )
            chips = summarize_plans(rows)
            kept = [row for row in rows if keep(PLAN_FILTERS, row, query.filters())]
            ranked = await hydrate_recommendations(
                self._session, self._repository_id, kept, view=query.view
            )
            plans = [item.as_dict() for item in ranked]
        return {"summary": {"total": chips["total"], "by_type": chips["by_type"]}, "plans": plans}

    async def ranked_plan_page(self, query: PlanListQuery) -> dict[str, Any]:
        """One page of open plans, its total, the chips and the structural leads."""
        stored = await ranked_refactoring_suggestions(
            self._session,
            self._repository_id,
            min_confidence=query.min_confidence,
            filters=query.filters(),
            search=query.search,
            sort=query.sort,
            view=query.view,
            limit=query.limit,
            offset=query.offset,
        )
        if stored is None:
            return await self._live_plan_page(query)
        rows, total = stored
        leads = await ranked_refactoring_suggestions(
            self._session,
            self._repository_id,
            min_confidence=query.min_confidence,
            filters={"refactoring_types": plan_types("structural")},
            limit=_STRUCTURAL_LEADS,
        )
        return _plan_page_payload(
            [stored_recommendation(row) for row in rows],
            total,
            query.offset,
            await summarize_open_plans(
                self._session, self._repository_id, min_confidence=query.min_confidence
            ),
            [stored_recommendation(row) for row in (leads[0] if leads else [])],
        )

    async def _live_plan_page(self, query: PlanListQuery) -> dict[str, Any]:
        """The page ranked per request. One batched ranking pass over the open
        plans; the symbol-level evidence that orders each plan's tests is read
        only for the rows this response returns."""
        rows = await get_refactoring_suggestions(
            self._session, self._repository_id, min_confidence=query.min_confidence
        )
        canonical = await hydrate_recommendations(
            self._session, self._repository_id, rows, view="canonical", rank_only=True
        )
        structural = plan_types("structural") or ()
        leads = [item for item in canonical if item.suggestion.refactoring_type in structural]
        leads = leads[:_STRUCTURAL_LEADS]
        filters = query.filters()
        ordered = _live_sorted(
            [
                item
                for item in apply_view(canonical, query.view)
                if keep(PLAN_FILTERS, item.suggestion, filters)
                and (not query.search or matches_search(item.suggestion, query.search))
            ],
            query.sort,
        )
        end = None if query.limit is None else query.offset + query.limit
        page = ordered[query.offset : end]
        shown = list({id(item): item for item in [*page, *leads]}.values())
        detailed = {
            id(item.suggestion): item
            for item in await detail_recommendations(self._session, self._repository_id, shown)
        }
        return _plan_page_payload(
            [detailed[id(item.suggestion)] for item in page],
            len(ordered),
            query.offset,
            summarize_plans(item.suggestion for item in canonical),
            [detailed[id(item.suggestion)] for item in leads],
        )

    async def plan_recommendation(self, row: Any, *, owner: Any = None) -> Any:
        """One stored plan as a ranked recommendation, without the repository.

        The finalizer persists each live plan's rank and validation, so this is
        normally a read of the row itself. A row it did not rank (an index
        written before that, or a plan reopened by hand since) is rebuilt from
        seeks: the one metric row, the one file's centrality and the validation
        profile its opportunity stored, through the same
        ``build_recommendations`` every surface uses.
        """
        from repowise.core.analysis.health.refactoring.recommendations import (
            build_recommendations,
            rehydrate_suggestion,
            stored_recommendation,
        )

        stored = stored_recommendation(row)
        if stored is not None:
            return stored
        if owner is None:
            owner = await self._owning_opportunity(row.public_id, row.file_path)
        metric_by_path, centrality = await self._rank_inputs(row.file_path)
        return build_recommendations(
            [rehydrate_suggestion(row)],
            metric_by_path=metric_by_path,
            centrality=centrality,
            validations={
                0: stored_validation(owner, row.public_id)
                or await self._performance_validation(row)
            },
        )[0]

    async def _performance_validation(self, row: Any) -> Any:
        """A performance plan's profile, stored on its opportunity at finalize.

        Performance plans are never refactoring steps, so the step lookup above
        cannot find them, and an empty fallback reports every one as untested.
        """
        from repowise.core.persistence.crud.analysis.performance import (
            get_performance_opportunity,
        )

        if row.refactoring_type != "performance_fix":
            return None
        plan = json_field(row, "plan_json", {})
        opportunity_id = plan.get("opportunity_id") if isinstance(plan, dict) else None
        if not opportunity_id:
            return None
        owner = await get_performance_opportunity(
            self._session, self._repository_id, opportunity_id
        )
        profile = (detail_map(owner).get("plan") or {}).get("validation") if owner else None
        return validation_from_profile(profile) if profile else None

    async def _rank_inputs(self, file_path: str) -> tuple[dict[str, Any], dict[str, float]]:
        """The two rank inputs for one file, as seeks rather than repo reads.

        ``build_recommendations`` wants a metric per path and an in-degree per
        node. Serving one plan used to load every metric row and every graph
        metric in the repository to supply them for a single file.
        """
        from repowise.core.persistence.models import GraphMetric, HealthFileMetric

        metric = (
            await self._session.execute(
                select(HealthFileMetric).where(
                    HealthFileMetric.repository_id == self._repository_id,
                    HealthFileMetric.file_path == file_path,
                )
            )
        ).scalar_one_or_none()
        rows = (
            await self._session.execute(
                select(GraphMetric.node_id, GraphMetric.in_degree).where(
                    GraphMetric.repository_id == self._repository_id,
                    or_(
                        GraphMetric.node_id == file_path,
                        # The separator matters: a bare ``f"{file_path}%"`` also
                        # matches a sibling whose name extends this one, so
                        # ``Component.ts`` would absorb ``Component.tsx``.
                        GraphMetric.node_id.like(f"{file_path}::%"),
                    ),
                )
            )
        ).all()
        return (
            {metric.file_path: metric} if metric is not None else {},
            {node_id: float(in_degree or 0.0) for node_id, in_degree in rows},
        )

    # -- internals --------------------------------------------------------

    async def _owning_opportunity(self, public_id: str | None, file_path: str) -> Any | None:
        """The opportunity holding this plan, found through its file.

        One indexed lookup on ``(repository_id, status, file_path)``: a plan
        belongs to at most one file's opportunity, so the file narrows it to a
        single candidate and the step list confirms it.
        """
        if not public_id:
            return None
        rows = list(
            (
                await self._session.execute(
                    select(RefactoringOpportunity)
                    .where(
                        RefactoringOpportunity.repository_id == self._repository_id,
                        RefactoringOpportunity.status == "open",
                        RefactoringOpportunity.refactoring_model_version
                        == REFACTORING_MODEL_VERSION,
                        RefactoringOpportunity.file_path == file_path,
                    )
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            steps = detail_map(row).get("steps") or []
            if any(step.get("plan_id") == public_id for step in steps):
                return row
        return None

    async def _plans_for(self, plan_ids: list[str]) -> list[dict[str, Any]]:
        """The member plans' payloads, in one indexed query over the page."""
        if not plan_ids:
            return []
        rows = list(
            (
                await self._session.execute(
                    select(RefactoringSuggestion).where(
                        RefactoringSuggestion.repository_id == self._repository_id,
                        RefactoringSuggestion.public_id.in_(plan_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        by_id = {row.public_id: row for row in rows}
        return [plan_payload(by_id[pid]) for pid in plan_ids if pid in by_id]


__all__ = ["RefactoringHealthService", "RefactoringPage", "RefactoringPlanPage"]
