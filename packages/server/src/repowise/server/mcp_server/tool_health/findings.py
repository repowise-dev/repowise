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

    ``dimension`` is nullable and a NULL means ``defect``: the column was added
    without a backfill, so pre-existing rows stay NULL until the next index
    recomputes them. Reading it as anything else drops real defect findings.
    """
    if not dimensions:
        return True
    return (row.dimension or "defect") in dimensions


@dataclass
class FindingSets:
    """Two row sets, deliberately split.

    ``finding_rows`` are the ones this response will serialize.
    ``lead_rows`` is the wider set the per-file dominant-cause reduction
    and the exact totals are computed from — it only ever needs four
    columns.

    Targeted mode asks about a handful of files, so one full read serves
    both. Dashboard mode does not: hydrating every open finding as a full
    ORM object to emit ``limit`` of them measured 262ms on this repo, and
    that cost is linear in finding count, so it grows with the repo the
    dashboard is describing.

    ``test_finding_rows`` is the dashboard-only test bucket (see the split
    below). Targeted mode never fills it: the caller named the files, so
    partitioning what they explicitly asked about would be answering a
    different question than the one they asked.
    """

    finding_rows: list[Any]
    lead_rows: list[Any]
    legend_rows: list[Any]
    # The ranked, dimension-filtered set, before any cap. The first row is the
    # one ``refactoring_plans_status`` points at when no plan exists.
    emitted: list[Any]
    # Counts the rows this response is about: the post-exclusion open set,
    # narrowed to the requested dimensions when one was asked for. Reporting
    # the unfiltered total beside a filtered list is what made an empty
    # ``findings`` read as "nothing here" rather than "nothing shown".
    #
    # In dashboard mode this counts the *production* half, because that is
    # the list it sits beside; ``test_findings_total`` counts the other half
    # and the two still sum to the whole open set. Same rule #1337 settled
    # for the dimension filter: a total describes the list it is a sibling
    # of, never a wider set the caller cannot see.
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
    # ``lead_rows`` stays the unfiltered open set: it feeds the per-file
    # leads and the performance KPI, neither of which should change
    # because the caller asked to *see* one dimension.
    emitted = _rank_emitted(
        [r for r in lead_rows if _in_dimensions(r, req.ranked_dimensions)]
    )
    # Test material goes in its own bucket rather than competing for
    # the repo's headline finding list. Measured on this repo, **2 of
    # the top 5** open findings by impact sit on test files, and 4-5 of
    # the top 20 — the top-20 figure is tie-dependent (ranks 14+ are all
    # at impact 2.16), which is itself the point: a fifth of the
    # most-read list was the test suite, decided partly by tie-break.
    # Splitting keeps both readable. A thrashing test suite is a real
    # signal some teams want; it is just not the same question as
    # "where is the defect risk in this codebase".
    #
    # Split *before* the cap, so each list is the top ``limit`` of its
    # own population — capping first and partitioning after would give
    # the smaller bucket whatever happened to land in the head.
    prod_emitted = [r for r in emitted if r.file_path not in test_paths]
    test_emitted = [r for r in emitted if r.file_path in test_paths]
    limit = req.limit
    # Fetch the head by id rather than re-running the ranked query with
    # an over-fetch margin. The margin had to cover every exclusion in
    # the table, so a repo excluding a large subtree turned the "capped"
    # read back into a near-full one; by id it is exactly ``limit`` rows
    # whatever the exclude config or dimension filter say.
    head_ids = [r.id for r in prod_emitted[:limit]] if req.wants_findings else []
    test_head_ids = [r.id for r in test_emitted[:limit]] if req.wants_test_findings else []
    by_id = await _read_findings_by_id(session, head_ids + test_head_ids)
    return FindingSets(
        # Re-imposed from the id lists; ``IN`` does not preserve order.
        finding_rows=[by_id[i] for i in head_ids if i in by_id],
        lead_rows=lead_rows,
        # Both heads, in one list, decided here rather than downstream: the
        # legend has to be a pure function of the ranked set so no
        # projection can change what a surviving key contains.
        legend_rows=prod_emitted[:limit] + test_emitted[:limit],
        emitted=emitted,
        findings_total=len(prod_emitted),
        test_finding_rows=[by_id[i] for i in test_head_ids if i in by_id],
        test_findings_total=len(test_emitted),
    )


async def _read_lite_findings(session: Any, repository: Any, req: HealthRequest) -> list[Any]:
    """Narrow read over every open finding.

    The four attributes ``_leads_by_file`` reads, plus ``dimension`` for the
    perf headline and ``id`` to fetch the head. SQLAlchemy ``Row`` exposes these
    as attributes, so the reduction and the exclude filter both run against it
    unchanged.

    ``details_json`` joins them only when the caller asked for the
    performance dimension, because that is the only case where a perf
    finding can reach the head at all: every one carries
    ``health_impact: 0``, so in a mixed list all ~10k defect findings
    sort above them and the rank could not move a row. Measured on
    this repo the column costs 6.6ms on the read (parsing the 697 perf
    rows out of 10,740 costs a further 1.2ms), which is worth paying
    for the one call it changes and not worth paying for the default.
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
    """One read for both heads — the split is a partition of the same
    ranked set, so paying two round-trips for it would be the N+1 this
    tool flags in itself."""
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

    ``accuracy`` scores the ranking against the whole repo rather than the
    capped head, but it reads exactly one biomarker: ``compute_defect_accuracy``
    ignores every finding whose type is not ``prior_defect``. Selecting
    those directly keeps the honest denominator without re-reading the
    ~10k rows the narrow pass above exists to avoid.
    Not routed through ``in_counts_findings``: ``prior_defect`` is the
    ground truth this block scores the number against, not a deduction
    the reading includes. Dropping it under ``code_shape`` would leave
    the accuracy block with no labels to be accurate about.
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
