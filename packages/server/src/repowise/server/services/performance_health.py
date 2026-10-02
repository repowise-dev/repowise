"""One read model for performance opportunities, serving REST and MCP.

Both surfaces used to load every open finding, regroup the repository, filter
in Python, and link plans by reading two different JSON fields. They disagreed
about the second of those, so one of them never linked a plan at all. This
module runs the reads for the query, the order, the page, the facets, the plan
link, the detail, and the recovery once; the two adapters map their own
vocabulary onto it and serialize what comes back.

The session-free half (query parsing, the sort/filter/facet rules, the plan
link and every response shape) is ``analysis/health/perf/serving.py``; this
module runs the reads and hands rows to it. Nothing here decides what an
opportunity *means*: grouping, actionability, and rank live in the analysis
package, and the values read here were decided when the opportunities were
materialized.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.perf.opportunities import model_state
from repowise.core.analysis.health.perf.serving import (
    PerformanceQuery,
    PlanLink,
    evidence_payload,
    fold_facets,
    plan_link,
    rescope_summary,
    serialize,
    summary_payload,
    unresolved_detail,
)
from repowise.core.analysis.health.refactoring.serving import evidence_block
from repowise.core.persistence.crud import (
    gated_performance_counts,
    get_performance_opportunity,
    get_performance_plan_rows,
    get_performance_summary,
    list_evidence_for_opportunities,
    list_opportunity_evidence,
    list_performance_opportunities,
    performance_facet_counts,
)

from ..mcp_server._references import refactoring_plan_id


@dataclass(frozen=True, slots=True)
class PerformancePage:
    items: list[dict[str, Any]]
    total: int
    offset: int
    next_offset: int | None
    facets: dict[str, list[dict[str, Any]]]
    summary: dict[str, Any]
    ignored_arguments: dict[str, str] = field(default_factory=dict)
    #: Open opportunities a language gate held back, ``{language: {count, ...}}``.
    gated: dict[str, dict] = field(default_factory=dict)


class PerformanceHealthService:
    """Query, page, detail, facets, plan linkage, and recovery, in one place.

    Lists, counts, facets and the headline leave out what a language gate holds
    back unless ``include_unverified``; a lookup by id never does.
    """

    def __init__(
        self,
        session: AsyncSession,
        repository_id: str,
        repository: str,
        *,
        include_unverified: bool = False,
    ) -> None:
        self._session = session
        self._repository_id = repository_id
        self._repository = repository
        self._include_unverified = include_unverified

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
            actionabilities=query.actionabilities,
            file_paths=query.file_paths,
            sort=query.sort,
            limit=query.limit,
            offset=query.offset,
            include_unverified=self._include_unverified,
        )
        links = await self._plan_links(rows)
        evidence = await self._evidence_for(rows, evidence_per_item)
        items = [
            {
                **serialize(row, links[row.opportunity_id], summary=query.view == "summary"),
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
            gated=await self.gated(),
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
            public_id = None if plan is None else refactoring_plan_id(plan, self._repository)
            links[row.opportunity_id] = plan_link(row, plan, public_id)
        return links

    async def _facets(self, query: PerformanceQuery) -> dict[str, list[dict[str, Any]]]:
        """Counts per filter value, each cross-filtered by the *other* filters."""
        grouped = await performance_facet_counts(
            self._session,
            self._repository_id,
            file_paths=query.file_paths,
            include_unverified=self._include_unverified,
        )
        return fold_facets(grouped, query)

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
        base = summary_payload(await get_performance_summary(self._session, self._repository_id))
        if base["status"] == "unavailable":
            return base
        base["repository_total"] = base["total"]
        gated = await self.gated()
        if contexts is None and not gated:
            return base
        # The stored rollup counts gated rows too, so with any held back the
        # headline is recounted from the same gated facet aggregate.
        grouped = await performance_facet_counts(
            self._session, self._repository_id, include_unverified=self._include_unverified
        )
        if contexts is None:
            contexts = frozenset(group[0] for group in grouped)
        return {**rescope_summary(base, grouped, contexts), "gated": gated}

    async def gated(self) -> dict[str, dict]:
        """Open opportunities a language gate holds back; empty when opted in."""
        return await gated_performance_counts(
            self._session, self._repository_id, include_unverified=self._include_unverified
        )

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
                "found": False,
                "opportunity_id": opportunity_id,
                "model_state": state,
                "detail": unresolved_detail(state),
            }
        links = await self._plan_links([row])
        payload = serialize(row, links[opportunity_id])
        evidence, total = await self.evidence(
            opportunity_id,
            limit=evidence_limit,
            offset=evidence_offset,
            total=row.observations_total,
        )
        payload.update(
            {
                "found": True,
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


__all__ = [
    "PerformanceHealthService",
    "PerformancePage",
]
