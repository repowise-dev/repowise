"""One read model for refactoring opportunities, shared by REST and MCP.

Both surfaces used to load every open plan, hydrate it, rank it and page it in
Python, and they disagreed about almost everything they did with the result:
different filter vocabularies, two incompatible pagination contracts, and two
ways of resolving one id - REST an indexed point lookup, MCP a full hydration
followed by a linear scan. Query, filter, order, page, facets, detail and the
directive live here now, so the agent surface and the product surface cannot
drift apart.

Nothing in this module composes or ranks. Composition is
``analysis/health/refactoring/opportunity.py`` and it runs at index time; this
module reads what the finalizer wrote.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.crud.analysis.refactoring_opportunities import (
    get_refactoring_opportunity,
    get_refactoring_summary,
    list_refactoring_opportunities,
    refactoring_facet_counts,
)
from repowise.core.persistence.models import RefactoringOpportunity, RefactoringSuggestion

# ``refactoring_view`` predates the opportunity. Both old values keep working
# and both are documented in docs/layers/REFACTORING.md:
#
# - ``canonical``  the published rank order, ties and all. What the old default
#   produced, kept for a caller that wants the score order verbatim.
# - ``file_spread``  asked for one row per file. An opportunity *is* one file's
#   work, so the spread is now satisfied by construction; the value maps onto
#   the diversified order, which is what it was reaching for.
# - ``diversified``  the new default. Rank order round-robined over cause and
#   directory, because the ranked head is a genuine run of ties.
_VIEW_ORDERS: dict[str, str] = {
    "canonical": "rank",
    "file_spread": "queue",
    "diversified": "queue",
}
CANONICAL_VIEWS = tuple(_VIEW_ORDERS)
DEFAULT_VIEW = "diversified"

# The legacy plan list has no notion of the diversified order, so the new
# default resolves to the value that list has always defaulted to.
_PLAN_VIEWS = {"canonical": "canonical", "file_spread": "file_spread", "diversified": "canonical"}

# The triage vocabulary, shared with health findings.
_STATUSES = ("open", "acknowledged", "resolved", "false_positive")

CANONICAL_ORDERS = ("queue", "rank", "health", "effort", "file")
_CONFIDENCES = ("low", "medium", "high")
_EFFORTS = ("S", "M", "L", "XL")
_TYPES = (
    "break_cycle",
    "extract_class",
    "extract_helper",
    "extract_method",
    "move_method",
    "split_file",
)

_UNAVAILABLE = {
    "status": "unavailable",
    "reason": "no_refactoring_analysis",
    "detail": "No refactoring analysis is stored for this repository. Run `repowise update`.",
}


@dataclass(frozen=True, slots=True)
class RefactoringQuery:
    """A normalized queue request. The only shape either adapter passes down."""

    lead_types: tuple[str, ...] | None = None
    status: str = "open"
    confidence: str | None = None
    effort: str | None = None
    mechanical_only: bool = False
    addresses_primary: bool | None = None
    file_paths: tuple[str, ...] | None = None
    path_contains: str | None = None
    view: str = DEFAULT_VIEW
    order: str | None = None
    limit: int = 20
    offset: int = 0

    @property
    def resolved_order(self) -> str:
        """An explicit ``order`` wins; otherwise the view picks one."""
        if self.order in CANONICAL_ORDERS:
            return self.order
        return _VIEW_ORDERS.get(self.view, _VIEW_ORDERS[DEFAULT_VIEW])


@dataclass(frozen=True, slots=True)
class RefactoringPage:
    items: list[dict[str, Any]]
    total: int
    offset: int
    next_offset: int | None
    facets: dict[str, dict[str, int]] = field(default_factory=dict)
    summary: dict[str, Any] | None = None
    ignored_arguments: dict[str, str] = field(default_factory=dict)


def parse_query(
    *,
    lead_type: str | Sequence[str] | None = None,
    status: str | None = None,
    confidence: str | None = None,
    effort: str | None = None,
    mechanical: bool | None = None,
    addresses_primary: bool | None = None,
    file_paths: list[str] | tuple[str, ...] | None = None,
    search: str | None = None,
    view: str | None = None,
    order: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[RefactoringQuery, dict[str, str]]:
    """Normalize a caller's arguments, naming anything it had to discard.

    An unrecognized value is reported back rather than silently treated as "no
    filter": a caller who misspells a type should not be told the repository is
    clean.
    """
    ignored: dict[str, str] = {}

    def admit(name: str, value: str | None, allowed: tuple[str, ...]) -> str | None:
        if value is None:
            return None
        if value in allowed:
            return value
        ignored[name] = value
        return None

    def admit_many(
        name: str, value: str | Sequence[str] | None, allowed: tuple[str, ...]
    ) -> tuple[str, ...] | None:
        """One value or several. A comma-separated string is how a query
        parameter carries a set; every member is admitted on its own, so a
        misspelling in a list is reported rather than narrowing the result to
        the members that happened to be spelled right."""
        if value is None:
            return None
        raw = value.split(",") if isinstance(value, str) else list(value)
        kept = [item.strip() for item in raw if item.strip()]
        good = tuple(item for item in kept if item in allowed)
        bad = [item for item in kept if item not in allowed]
        if bad:
            ignored[name] = ",".join(bad)
        return good or None

    resolved_view = view or DEFAULT_VIEW
    if resolved_view not in _VIEW_ORDERS:
        ignored["refactoring_view"] = resolved_view
        resolved_view = DEFAULT_VIEW
    return (
        RefactoringQuery(
            lead_types=admit_many("refactoring_type", lead_type, _TYPES),
            status=admit("status", status, _STATUSES) or "open",
            confidence=admit("confidence", confidence, _CONFIDENCES),
            effort=admit("effort", effort, _EFFORTS),
            mechanical_only=bool(mechanical),
            addresses_primary=addresses_primary,
            file_paths=tuple(file_paths) if file_paths else None,
            path_contains=(search or "").strip() or None,
            view=resolved_view,
            order=admit("order", order, CANONICAL_ORDERS),
            limit=max(int(limit), 0),
            offset=max(int(offset), 0),
        ),
        ignored,
    )


def plan_view(view: str | None) -> Literal["canonical", "file_spread"]:
    """The legacy plan list's view for a caller's ``refactoring_view``."""
    return _PLAN_VIEWS.get(view or DEFAULT_VIEW, "canonical")  # type: ignore[return-value]


def evidence_block(
    evidence: list[dict[str, Any]], total: int, offset: int
) -> dict[str, Any]:
    """One evidence page plus the exact call that reads the rest."""
    emitted = len(evidence)
    block: dict[str, Any] = {
        "evidence": evidence,
        "evidence_total": total,
        "evidence_emitted": emitted,
        "evidence_truncated": offset + emitted < total,
    }
    if block["evidence_truncated"]:
        block["evidence_reduced_reason"] = "evidence_page"
        block["evidence_next_cursor"] = offset + emitted
    return block


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
        """
        rows, total = await list_refactoring_opportunities(
            self._session,
            self._repository_id,
            status=query.status,
            lead_types=list(query.lead_types) if query.lead_types else None,
            confidence=query.confidence,
            effort=query.effort,
            file_paths=list(query.file_paths) if query.file_paths else None,
            path_contains=query.path_contains,
            mechanical_only=query.mechanical_only,
            addresses_primary=query.addresses_primary,
            order=query.resolved_order,
            limit=query.limit,
            offset=query.offset,
        )
        items = [
            self._serialize(row, steps_limit=steps_per_item, evidence_limit=evidence_per_item)
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
                    self._session, self._repository_id, status=query.status
                )
                if with_facets
                else {}
            ),
            summary=await self.summary() if with_summary else None,
        )

    # -- headline ---------------------------------------------------------

    async def summary(self) -> dict[str, Any]:
        """The Level-1 rollup, read by primary key."""
        row = await get_refactoring_summary(self._session, self._repository_id)
        if row is None:
            return dict(_UNAVAILABLE)
        payload = _loads(row.summary_json)
        payload["status"] = "available"
        payload["refactoring_model_version"] = row.refactoring_model_version
        payload["analyzed_commit"] = row.analyzed_commit
        return payload

    async def directive(self) -> dict[str, Any]:
        """The Level-0 lead: one opportunity, and the exact call that opens it.

        The same primary-key read the summary uses, so a bare dashboard pays one
        statement for it and never touches the queue.
        """
        row = await get_refactoring_summary(self._session, self._repository_id)
        if row is None:
            return dict(_UNAVAILABLE)
        payload = _loads(row.summary_json)
        lead = payload.get("lead")
        if not lead:
            return {
                "status": "clear",
                "reason": "no_open_opportunities",
                "detail": "No refactoring opportunity is open for this repository.",
                "opportunities_total": int(payload.get("opportunities_total") or 0),
            }
        addresses = lead.get("addresses_primary_problem")
        directive: dict[str, Any] = {
            "status": "available",
            "opportunity_id": lead.get("opportunity_id"),
            "fix_first": lead.get("file_path"),
            "reason": lead.get("lead_biomarker"),
            "lead_refactoring_type": lead.get("lead_refactoring_type"),
            "steps": lead.get("step_count"),
            "mechanical_steps": lead.get("mechanical_steps"),
            "judgment_steps": lead.get("judgment_steps"),
            "effort_bucket": lead.get("effort_bucket"),
            "confidence": lead.get("confidence"),
            "recovers_health_points": lead.get("recoverable_health"),
            "addresses_primary_problem": addresses,
            "opportunities_total": int(payload.get("opportunities_total") or 0),
            "next_action": {
                "tool": "get_health",
                "arguments": {"opportunity_id": lead.get("opportunity_id")},
            },
        }
        # The honest half. A file's plans very often answer a different question
        # from the one that made it the worst file, and saying so beats routing
        # an agent to cleanup it will read as the fix.
        if addresses is False:
            directive["note"] = (
                f"These steps do not address {lead.get('lead_biomarker')!r}, this file's "
                "dominant finding. Treat them as related cleanup, not the fix for it."
            )
        elif addresses is None:
            directive["note"] = (
                "No dominant finding was recorded for this file, so whether these steps "
                "address it is unknown rather than no."
            )
        return directive

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
        """One opportunity by id: an indexed seek, then its member plans."""
        row = await get_refactoring_opportunity(
            self._session, self._repository_id, opportunity_id
        )
        if row is None:
            return {
                "resolved": False,
                "opportunity_id": opportunity_id,
                "reason": "unknown_opportunity_id",
            }
        details = _loads(row.details_json)
        steps = list(details.get("steps") or [])
        page = steps[step_offset : step_offset + max(step_limit, 0)]
        payload = self._serialize(row, steps_limit=None, evidence_limit=0)
        payload["resolved"] = True
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
        payload["next_actions"] = self._next_actions(row, page)
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

        Every read is a seek. The row comes from the storage-or-public id index,
        its rank inputs from the one metric row and the one file's centrality,
        and its validation from the profile the finalizer already resolved. The
        payload is built by the same ``build_recommendations`` every surface
        uses, so it is field-identical to the hydrated form; what is gone is the
        two full-table reads and the test-reachability walk that made resolving
        one id cost the repository.
        """
        from repowise.core.analysis.health.refactoring.recommendations import (
            build_recommendations,
            rehydrate_suggestion,
        )
        from repowise.core.persistence.crud import get_refactoring_suggestion

        row = await get_refactoring_suggestion(self._session, self._repository_id, plan_id)
        if row is None:
            return {"resolved": False, "plan_id": plan_id, "reason": "unknown_plan_id"}
        owner = await self._owning_opportunity(row.public_id, row.file_path)
        metric_by_path, centrality = await self._rank_inputs(row.file_path)
        built = build_recommendations(
            [rehydrate_suggestion(row)],
            metric_by_path=metric_by_path,
            centrality=centrality,
            validations={0: _stored_validation(owner, row.public_id)},
        )
        payload = built[0].as_dict() if built else _plan_payload(row)
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
                        RefactoringOpportunity.file_path == file_path,
                    )
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            steps = _loads(row.details_json).get("steps") or []
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
        return [_plan_payload(by_id[pid]) for pid in plan_ids if pid in by_id]

    def _next_actions(self, row: Any, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Structured follow-ups, so a drill-down ends somewhere rather than stops."""
        actions: list[dict[str, Any]] = []
        first = steps[0] if steps else None
        if first and first.get("line_start") is not None and first.get("line_end") is not None:
            actions.append(
                {
                    "why": "read the span the first step names",
                    "tool": "get_symbol",
                    "arguments": {
                        "symbol_id": f"{first['file_path']}:{first['line_start']}-{first['line_end']}"
                    },
                }
            )
        actions.append(
            {
                "why": "what history says about touching this file",
                "tool": "get_risk",
                "arguments": {"targets": [row.file_path]},
            }
        )
        if row.evidence_total:
            actions.append(
                {
                    "why": "page the supporting observations",
                    "tool": "get_health",
                    "arguments": {
                        "opportunity_id": row.opportunity_id,
                        "only": ["refactoring_evidence"],
                    },
                }
            )
        return actions

    def _serialize(
        self, row: Any, *, steps_limit: int | None = None, evidence_limit: int = 0
    ) -> dict[str, Any]:
        details = _loads(row.details_json)
        steps = list(details.get("steps") or [])
        evidence = list(details.get("evidence") or [])
        payload: dict[str, Any] = {
            "opportunity_id": row.opportunity_id,
            "refactoring_model_version": row.refactoring_model_version,
            "status": row.status,
            "file_path": row.file_path,
            "lead_biomarker": row.lead_biomarker,
            "lead_refactoring_type": row.lead_refactoring_type,
            "addresses_primary_problem": row.addresses_primary_problem,
            "effort_bucket": row.effort_bucket,
            "confidence": row.confidence,
            "step_count": row.step_count,
            "mechanical_steps": row.mechanical_steps,
            "judgment_steps": row.judgment_steps,
            "evidence_total": row.evidence_total,
            "affected_files_total": row.affected_files_total,
            "recoverable_health": round(float(row.recoverable_health), 3),
            "rank_score": round(float(row.rank_score), 4),
            "rank_position": row.rank_position,
            "queue_position": row.queue_position,
            "rank_factors": details.get("rank_factors") or {},
            "why_ranked": details.get("why_ranked") or [],
        }
        # The file's own size and reach, recorded by the finalizer. Omitted when
        # the store predates them, so a surface can tell "not measured" from a
        # genuine zero rather than plotting an unmeasured file at the origin.
        for key in ("file_nloc", "dependents"):
            if isinstance(details.get(key), int):
                payload[key] = details[key]
        if steps_limit is not None:
            kept = steps[: max(steps_limit, 0)]
            payload["steps"] = kept
            payload["steps_total"] = len(steps)
            payload["steps_emitted"] = len(kept)
            if len(kept) < len(steps):
                payload["steps_reduced_reason"] = "limit"
        if evidence_limit:
            payload.update(evidence_block(evidence[:evidence_limit], len(evidence), 0))
        return payload


def _stored_validation(owner: Any, public_id: str | None) -> Any:
    """The validation profile the finalizer resolved for this step.

    Test reachability is a graph walk over the whole unanswered set; it belongs
    at index time, and this reads its result rather than repeating it.
    """
    from repowise.core.analysis.health.refactoring.recommendations import (
        ValidationPlan,
        ValidationTarget,
    )

    if owner is None or not public_id:
        return None
    details = _loads(owner.details_json)
    wanted = next(
        (
            step.get("validation_profile_id")
            for step in (details.get("steps") or [])
            if step.get("plan_id") == public_id
        ),
        None,
    )
    if not wanted:
        return None
    profile = next(
        (p for p in (details.get("validation_profiles") or []) if p.get("id") == wanted),
        None,
    )
    if profile is None:
        return None
    target_fields = set(ValidationTarget.__dataclass_fields__)
    values = {
        key: value
        for key, value in profile.items()
        if key in ValidationPlan.__dataclass_fields__
    }
    values["targets"] = [
        ValidationTarget(**{k: v for k, v in target.items() if k in target_fields})
        for target in (profile.get("targets") or [])
        if isinstance(target, dict)
    ]
    return ValidationPlan(**values)


def _plan_payload(row: Any) -> dict[str, Any]:
    """The stored plan, without re-hydrating rank or validation.

    Detail reads the payload the detector wrote and the finalizer already
    validated; recomputing benefit and coverage here is what made a one-row
    lookup cost the repository.
    """
    return {
        "id": row.public_id or row.id,
        "refactoring_type": row.refactoring_type,
        "file_path": row.file_path,
        "target_symbol": row.target_symbol,
        "line_start": row.line_start,
        "line_end": row.line_end,
        "plan": _loads(row.plan_json),
        "evidence": _loads(row.evidence_json),
        "blast_radius": _loads(row.blast_radius_json),
        "impact_delta": row.impact_delta,
        "effort_bucket": row.effort_bucket,
        "confidence": row.confidence,
        "source_biomarker": row.source_biomarker,
        "status": row.status,
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
    "CANONICAL_ORDERS",
    "CANONICAL_VIEWS",
    "DEFAULT_VIEW",
    "RefactoringHealthService",
    "RefactoringPage",
    "RefactoringQuery",
    "evidence_block",
    "parse_query",
    "plan_view",
]
