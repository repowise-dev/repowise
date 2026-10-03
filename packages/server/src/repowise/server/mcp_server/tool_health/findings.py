"""Which open findings one get_health call ranks, emits, and totals."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from repowise.core.analysis.health.worth import LOW_PRIORITY_LABEL, finding_priorities
from repowise.core.persistence.models import HealthFinding
from repowise.server.mcp_server.tool_health.population import Population
from repowise.server.mcp_server.tool_health.request import HealthRequest
from repowise.server.mcp_server.tool_health.serialize import _rank_emitted


def _in_dimensions(row: Any, dimensions: set[str]) -> bool:
    """True when *row* belongs to one of *dimensions* (empty set -> everything).

    A NULL ``dimension`` means ``defect``: the column was added without a
    backfill, so older rows stay NULL until the next index.
    """
    if not dimensions:
        return True
    return (row.dimension or "defect") in dimensions


@dataclass
class FindingSets:
    """Two row sets, deliberately split.

    ``finding_rows`` are the ones this response will serialize. ``lead_rows``
    is the wider set the per-file leads and exact totals come from, and needs
    only a few columns.

    Targeted mode names a handful of files, so one full read serves both.
    Dashboard mode reads narrow, since hydrating every open finding grows with
    the repo.

    ``test_finding_rows`` is the dashboard-only test bucket. Targeted mode never
    fills it: the caller named the files, so partitioning them would answer a
    different question.
    """

    finding_rows: list[Any]
    lead_rows: list[Any]
    legend_rows: list[Any]
    # The ranked, dimension-filtered set, before any cap. The first row is the
    # one ``refactoring_plans_status`` points at when no plan exists.
    emitted: list[Any]
    # A total describes the list it sits beside (#1337): the post-exclusion,
    # dimension-filtered open set, and in dashboard mode its production half.
    # With ``test_findings_total`` it sums to the whole open set.
    findings_total: int
    test_finding_rows: list[Any] = field(default_factory=list)
    test_findings_total: int = 0
    #: Row id -> why the finding can wait, for each lower-priority row.
    lower_priority: dict[Any, str] = field(default_factory=dict)


def _tiered(rows: list[Any], measured_over: list[Any]) -> tuple[list[Any], dict[Any, str]]:
    """Ranked *rows* with those worth doing first ahead, and each later row's
    reason. Shapes are measured over *measured_over*, the set before any
    dimension filter, so a filter never changes a function's tier."""
    reasons = dict(zip((r.id for r in measured_over), finding_priorities(measured_over), strict=True))
    ranked = sorted(_rank_emitted(rows), key=lambda r: reasons.get(r.id) is not None)
    labels = {r.id: LOW_PRIORITY_LABEL[why] for r in ranked if (why := reasons.get(r.id))}
    return ranked, labels


def _open_findings(repository: Any, req: HealthRequest) -> tuple[Any, ...]:
    return (
        HealthFinding.repository_id == repository.id,
        HealthFinding.status == "open",
        HealthFinding.biomarker_type.not_in(req.withheld_types),
    )


async def load_targeted_findings(
    session: Any, repository: Any, pop: Population, req: HealthRequest
) -> FindingSets:
    """Every open finding on the named files: one full read serves both sets."""
    finding_rows = pop.in_counts_findings(pop.in_scope_rows(
        list(
            (
                await session.execute(
                    select(HealthFinding)
                    .where(*_open_findings(repository, req))
                    .where(HealthFinding.file_path.in_(pop.effective_targets))
                    .order_by(HealthFinding.health_impact.desc())
                )
            )
            .scalars()
            .all()
        ),
        "file_path",
    ))
    emitted, labels = _tiered(
        [f for f in finding_rows if _in_dimensions(f, req.ranked_dimensions)], finding_rows
    )
    return FindingSets(
        finding_rows=emitted,
        lead_rows=finding_rows,
        legend_rows=emitted,
        emitted=emitted,
        findings_total=len(emitted),
        lower_priority=labels,
    )


async def load_dashboard_findings(
    session: Any,
    repository: Any,
    pop: Population,
    req: HealthRequest,
    test_paths: set[str],
) -> FindingSets:
    """A narrow ranked read of every open finding, then the two heads by id."""
    lead_rows = pop.in_counts_findings(
        pop.in_scope_rows(await _read_lite_findings(session, repository, req))
    )
    # ``lead_rows`` stays unfiltered: the leads and performance KPI must not
    # change because the caller asked to see one dimension.
    emitted, labels = _tiered(
        [r for r in lead_rows if _in_dimensions(r, req.ranked_dimensions)], lead_rows
    )
    # Test findings get their own bucket instead of crowding the headline
    # list. Split before the cap, so each list is the top ``limit`` of its
    # own population.
    prod_emitted = [r for r in emitted if r.file_path not in test_paths]
    test_emitted = [r for r in emitted if r.file_path in test_paths]
    limit = req.limit
    # Fetch the head by id: exactly ``limit`` rows whatever the exclusions,
    # where an over-fetch margin on the ranked query could approach a full read.
    head_ids = [r.id for r in prod_emitted[:limit]] if req.wants_findings else []
    test_head_ids = [r.id for r in test_emitted[:limit]] if req.wants_test_findings else []
    by_id = await _read_findings_by_id(session, head_ids + test_head_ids)
    return FindingSets(
        # Re-imposed from the id lists; ``IN`` does not preserve order.
        finding_rows=[by_id[i] for i in head_ids if i in by_id],
        lead_rows=lead_rows,
        # Decided here so the legend is a pure function of the ranked set,
        # which no projection can change.
        legend_rows=prod_emitted[:limit] + test_emitted[:limit],
        emitted=emitted,
        findings_total=len(prod_emitted),
        test_finding_rows=[by_id[i] for i in test_head_ids if i in by_id],
        test_findings_total=len(test_emitted),
        lower_priority=labels,
    )


async def _read_lite_findings(session: Any, repository: Any, req: HealthRequest) -> list[Any]:
    """Narrow read over every open finding.

    The columns ``_leads_by_file`` reads, plus ``dimension`` and ``id``, and
    what the worth tier measures a function by (``function_name``, the span,
    ``severity``, ``details_json``). A SQLAlchemy ``Row`` exposes them as
    attributes, so the reduction and the exclude filter run against it
    unchanged. Ceiling: ``details_json`` is read for every open finding, for
    the tier; storing the tier on the row would make this read narrow again.
    """
    lite_cols = [
        HealthFinding.id,
        HealthFinding.file_path,
        HealthFinding.health_impact,
        HealthFinding.biomarker_type,
        HealthFinding.reason,
        HealthFinding.dimension,
        HealthFinding.severity,
        HealthFinding.details_json,
        HealthFinding.function_name,
        HealthFinding.line_start,
        HealthFinding.line_end,
    ]
    return list(
        (
            await session.execute(
                select(*lite_cols)
                .where(*_open_findings(repository, req))
                .order_by(HealthFinding.health_impact.desc())
            )
        ).all()
    )


async def _read_findings_by_id(session: Any, ids: list[str]) -> dict[str, Any]:
    """One read for both heads, which partition the same ranked set."""
    if not ids:
        return {}
    return {
        f.id: f
        for f in (
            await session.execute(select(HealthFinding).where(HealthFinding.id.in_(ids)))
        )
        .scalars()
        .all()
    }


async def read_accuracy_rows(
    session: Any, repository: Any, pop: Population, req: HealthRequest
) -> list[Any]:
    """The ``prior_defect`` labels the accuracy block scores the ranking against.

    ``compute_defect_accuracy`` reads only ``prior_defect``, so selecting those
    directly keeps the whole-repo denominator without a full read.

    Not routed through ``in_counts_findings``: these are the ground truth, not a
    deduction, and dropping them under ``code_shape`` would leave no labels.
    """
    if "accuracy" not in req.include_set or pop.scoped:
        return []
    return pop.in_scope_rows(
        list(
            (
                await session.execute(
                    select(HealthFinding)
                    .where(*_open_findings(repository, req))
                    .where(HealthFinding.biomarker_type == "prior_defect")
                )
            )
            .scalars()
            .all()
        ),
        "file_path",
    )
