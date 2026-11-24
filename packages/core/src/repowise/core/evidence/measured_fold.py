"""What stored runtime evidence feeds the performance queue, folded from plain rows.

Two pure folds over rows a host already holds, with no store or session:

- :func:`fold_measured`: each workload's newest run with its ranked functions
  (the ``workload_hotness`` read model) into what
  ``analysis.health.perf.opportunities.build_performance_opportunities``
  takes as ``measured``, ``hot``, ``in_tests``, ``hot_in_tests`` and ``ranks``;
- :func:`fold_query_readings`: ``query_count`` runs and their rows into each
  call site's strongest reading (``evidence.query_counts``), its ``queries``.

The engine's own writer reads the rows from its store and calls these, so a
host that keeps evidence elsewhere builds the same queue from the same rows.
Rows are read by attribute (an ORM row or a ``SimpleNamespace`` both work);
query points are plain tuples.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, NamedTuple

from .hotness import LEAD_CLASSES, newest_runs
from .query_counts import QueryReading, read_statement, strongest


class MeasuredFold(NamedTuple):
    """What the leading and the ``unit_test`` workloads measured, per function."""

    #: Function -> the leading workload that ranks it best.
    measured: dict[str, str]
    #: Functions a leading workload measured in the ``lead_candidate`` tier.
    hot: frozenset[str]
    #: The leading workloads whose runs measured any function: what a cause
    #: no facet marks was not exercised in.
    workloads: tuple[str, ...]
    #: The same two for ``unit_test`` runs, kept apart as measured in tests.
    in_tests: dict[str, str]
    hot_in_tests: frozenset[str]
    #: Function -> its best place in a leading workload's run (1 is the top).
    ranks: dict[str, int]


def fold_measured(ranked: Iterable[tuple[Any, Sequence[Any]]]) -> MeasuredFold:
    """Each function a leading workload's newest run ranks, with that workload's
    name, and the ones any such run puts in ``evidence.hotness``'s
    ``lead_candidate`` tier (the share and interval rule, or ranked in
    several runs).

    *ranked* is ``(run, rows)`` per workload's newest run, as
    ``crud.ranked_by_workload`` returns it: a run carries ``workload_class``
    and ``workload_name``, a row ``symbol_id``, ``rank``, ``tier`` and
    ``ordering_only`` (``workload_hotness`` columns).

    Workload classes that may lead (``LEAD_CLASSES``) fill ``measured`` and
    ``hot``; ``unit_test`` runs fill ``in_tests`` and ``hot_in_tests`` apart,
    since a test run shows what the code costs, not what production runs,
    and never marks a cause not exercised. A deterministic tracer's order is
    not a measured cost (``ordering_only``), so it never counts, as it never
    leads in ``evidence.hotness``. A function two workloads rank takes the
    one that ranks it best, ties by name.
    """
    best: dict[bool, dict[str, tuple[int, str]]] = {True: {}, False: {}}
    hot: dict[bool, set[str]] = {True: set(), False: set()}
    workloads: set[str] = set()
    for run, rows in ranked:
        leads = run.workload_class in LEAD_CLASSES
        if not leads and run.workload_class != "unit_test":
            continue
        for row in rows:
            if row.ordering_only:
                continue
            if leads:
                workloads.add(run.workload_name)
            if row.tier == "lead_candidate":
                hot[leads].add(row.symbol_id)
            key = (row.rank, run.workload_name)
            if row.symbol_id not in best[leads] or key < best[leads][row.symbol_id]:
                best[leads][row.symbol_id] = key

    def names(by: dict[str, tuple[int, str]]) -> dict[str, str]:
        return {sid: name for sid, (_rank, name) in by.items()}

    return MeasuredFold(
        names(best[True]),
        frozenset(hot[True]),
        tuple(sorted(workloads)),
        names(best[False]),
        frozenset(hot[False]),
        {sid: rank for sid, (rank, _name) in best[True].items()},
    )


def query_runs_to_read(runs: Iterable[Any]) -> list[str]:
    """The ids of the ``query_count`` runs that speak for the current code, sorted.

    Each workload's newest complete run (``newest_runs`` over runs whose
    ``mapping_partial`` is unset): a superseded run never speaks for the
    current code, and a partly mapped one, which is never a basis for ranking
    either, never decides. *runs* are one repository's ``query_count`` runs.
    """
    return sorted(newest_runs(r for r in runs if not r.mapping_partial))


#: One ``evidence_query_counts`` row: ``(run_id, scope_kind, site_symbol_id,
#: statement_fp, scope_items, query_count)``.
QueryPoint = tuple[str, str, str, str, "int | None", int]


def fold_query_readings(
    runs: Iterable[Any], points: Iterable[QueryPoint]
) -> dict[str, QueryReading | None]:
    """Each call site's strongest query-count reading, by symbol id.

    *runs* are the repository's ``query_count`` runs (``id``,
    ``workload_name``, ``environment``, ``measured_at``, ``mapping_partial``);
    *points* their rows as :data:`QueryPoint` tuples. Only the runs
    :func:`query_runs_to_read` keeps are read, so *points* may hold every
    run's rows. Sizes are compared within one run and one scope kind only: a
    test and a request, or two workloads, are not the same measurement.
    Ceiling: a newest run measured before the site's body changed still
    counts; query rows carry no body digest to tell.
    """
    keep = set(query_runs_to_read(runs))
    if not keep:
        return {}
    grouped: dict[tuple[str, str, str, str], list[tuple[int | None, int]]] = {}
    for run_id, kind, site, fp, items, count in points:
        if run_id in keep:
            grouped.setdefault((run_id, kind, site, fp), []).append((items, count))
    by_site: dict[str, list[QueryReading]] = {}
    for (_run, _kind, site, _fp), seen in grouped.items():
        by_site.setdefault(site, []).append(read_statement(seen))
    return {site: strongest(readings) for site, readings in by_site.items()}


__all__ = [
    "MeasuredFold",
    "QueryPoint",
    "fold_measured",
    "fold_query_readings",
    "query_runs_to_read",
]
