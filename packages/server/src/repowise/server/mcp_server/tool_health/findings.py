"""Which open findings one get_health call ranks, emits, and totals."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

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


def _open_findings(repository: Any) -> tuple[Any, Any]:
    return (
        HealthFinding.repository_id == repository.id,
        HealthFinding.status == "open",
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
                    .where(*_open_findings(repository))
                    .where(HealthFinding.file_path.in_(pop.effective_targets))
                    .order_by(HealthFinding.health_impact.desc())
                )
            )
            .scalars()
            .all()
        ),
        "file_path",
    ))
    emitted = _rank_emitted(
        [f for f in finding_rows if _in_dimensions(f, req.ranked_dimensions)]
    )
    return FindingSets(
        finding_rows=emitted,
        lead_rows=finding_rows,
        legend_rows=emitted,
        emitted=emitted,
        findings_total=len(emitted),
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
    emitted = _rank_emitted(
        [r for r in lead_rows if _in_dimensions(r, req.ranked_dimensions)]
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
    )


async def _read_lite_findings(session: Any, repository: Any, req: HealthRequest) -> list[Any]:
    """Narrow read over every open finding.

    The columns ``_leads_by_file`` reads, plus ``dimension`` and ``id``. A
    SQLAlchemy ``Row`` exposes them as attributes, so the reduction and the
    exclude filter run against it unchanged.

    ``details_json`` joins only when the performance dimension is asked for:
    perf findings carry ``health_impact: 0``, so only then can one reach the
    head, and the column is not worth its read cost otherwise.
    """
    lite_cols = [
        HealthFinding.id,
        HealthFinding.file_path,
        HealthFinding.health_impact,
        HealthFinding.biomarker_type,
        HealthFinding.reason,
        HealthFinding.dimension,
    ]
    if "performance" in req.dimension_filter:
        lite_cols.append(HealthFinding.details_json)
    if req.wants_performance_opportunities:
        lite_cols.extend(
            [
                HealthFinding.function_name,
                HealthFinding.line_start,
                HealthFinding.line_end,
            ]
        )
    return list(
        (
            await session.execute(
                select(*lite_cols)
                .where(*_open_findings(repository))
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
                    .where(*_open_findings(repository))
                    .where(HealthFinding.biomarker_type == "prior_defect")
                )
            )
            .scalars()
            .all()
        ),
        "file_path",
    )
