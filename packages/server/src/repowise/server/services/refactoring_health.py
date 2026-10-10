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
runs the reads and hands rows to it. Nothing here composes or ranks. Composition is
``analysis/health/refactoring/opportunity.py`` and it runs at index time; this
module reads what the finalizer wrote.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.refactoring.identity import REFACTORING_MODEL_VERSION
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
from repowise.core.analysis.health.rows import detail_map, json_field
from repowise.core.persistence.crud.analysis.fix_first import load_fix_first
from repowise.core.persistence.crud.analysis.refactoring_opportunities import (
    get_refactoring_opportunity,
    get_refactoring_summary,
    list_refactoring_opportunities,
    refactoring_facet_counts,
    refactoring_opportunities_by_id,
    refactoring_opportunity_ids,
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
        filters = _filters(query)
        shown_ids, hidden = await self._scope(query, filters)
        rows, total = await list_refactoring_opportunities(
            self._session,
            self._repository_id,
            **filters,
            opportunity_ids=shown_ids,
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
                # Scoped to the status being listed. Facets counting the open
                # set while the list shows the resolved one would put a badge on
                # a tab that returns nothing.
                await refactoring_facet_counts(
                    self._session,
                    self._repository_id,
                    status=query.status,
                    opportunity_ids=shown_ids,
                )
                if with_facets
                else {}
            ),
            summary=await self.summary() if with_summary else None,
            scope=query.scope,
            hidden=hidden,
        )

    async def plan_page(self, query: RefactoringQuery) -> RefactoringPlanPage:
        """The queue's plans in queue order: each opportunity's steps, in step order.

        A plan is a step of one opportunity, so the plan list is a view of the
        queue: the same scope, filters and order, paged by plan. One narrow read
        of step counts places the page; only the opportunities it touches are
        decoded.
        """
        filters = _filters(query)
        shown_ids, hidden = await self._scope(query, filters)
        counts = await refactoring_step_counts(
            self._session,
            self._repository_id,
            **filters,
            opportunity_ids=shown_ids,
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
        )

    async def _scope(
        self, query: RefactoringQuery, filters: dict[str, Any]
    ) -> tuple[list[str] | None, dict[str, Any] | None]:
        """The ids a ``fix_first`` scope keeps and what it hides; ``all`` keeps every row."""
        if query.scope != "fix_first":
            return None, None
        return await self._fix_first_scope(filters)

    async def _fix_first_scope(
        self, filters: dict[str, Any]
    ) -> tuple[list[str], dict[str, Any]]:
        """The filtered ids Fix first takes, and what it leaves out by reason.

        Ceiling: the shown ids go back to the page read as an ``IN`` list, one
        entry per open opportunity Fix first takes (94 on this repository).
        Upgrade path: store the eligibility on the opportunity row at index
        time and filter on the column.
        """
        queue = await load_fix_first(self._session, self._repository_id, limit=0, verify=False)
        reasons = queue.refactoring_reasons
        matched = await refactoring_opportunity_ids(
            self._session, self._repository_id, **filters
        )
        # The queue is keyed on the stores' newest write, so it has read every
        # open id; one written between the two reads is in neither count.
        shown = [i for i in matched if i in reasons and reasons[i] is None]
        left_out = Counter(r for i in matched if (r := reasons.get(i)))
        return shown, {"total": sum(left_out.values()), "by_reason": dict(left_out.most_common())}

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
        payload = (await self.plan_recommendation(row, owner=owner)).as_dict()
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
