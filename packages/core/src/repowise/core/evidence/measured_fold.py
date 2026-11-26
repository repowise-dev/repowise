"""What stored runtime evidence feeds the performance queue, folded from plain rows.

Two pure folds over rows a host already holds, with no store or session:

- :func:`fold_measured`: each workload's newest ranking run with its ranked functions
  (the ``workload_hotness`` read model) into what
  ``analysis.health.perf.opportunities.build_performance_opportunities``
  takes as ``measured``, ``hot``, ``in_tests``, ``hot_in_tests`` and ``ranks``;
- :func:`fold_query_readings`: ``query_count`` runs and their rows into each
  call site's strongest reading (``evidence.query_counts``), its ``queries``;
- :func:`fold_statements`: each production ``statement_stats`` workload's newest
  run and its statements into what each issuing function's statements cost in
  that window (``crud.measured_costs``'s ``statements`` group).

The engine's own writer reads the rows from its store and calls these, so a
host that keeps evidence elsewhere builds the same queue from the same rows.
Rows are read by attribute (an ORM row or a ``SimpleNamespace`` both work);
query points are plain tuples.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, NamedTuple

from .hotness import LEAD_CLASSES, _age_key, newest_runs
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
    """Each function a leading workload's newest ranking run ranks, with that workload's
    name, and the ones any such run puts in ``evidence.hotness``'s
    ``lead_candidate`` tier (the share and interval rule, or ranked in
    several runs).

    *ranked* is ``(run, rows)`` per workload's newest ranking run, as
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


#: What a statement the join did not put on one function is, by its stored
#: ``issuer``: several functions could issue it, only a ``cron.schedule`` job
#: runs it, or nothing matched (``evidence.statement_join``).
UNJOINED = "unjoined"


class StatementCost(NamedTuple):
    """One function's production statements in one run: the ``measured`` row
    ``evidence.action_row.action_row`` reads, plus what the statements did.

    ``self_share`` is the function's statement time as a share of all the
    statement time the run stored (the window's top statements when the
    source cut them); a statement window has no interval, samples or
    freshness, so those are ``None``.
    """

    symbol_id: str
    run_id: str
    #: 1-based place among the run's functions by statement time.
    rank: int
    self_share: float | None
    total_ms: float
    calls: int
    #: Distinct statements the function issues in the run.
    statements: int
    share_low: float | None = None
    share_high: float | None = None
    total_share: float | None = None
    samples: int | None = None
    freshness: str | None = None


class StatementRun(NamedTuple):
    """One production statement run, folded."""

    run: Any
    #: Every function exactly one statement site names, costliest first.
    rows: list[StatementCost]
    #: All the statement time and statements the run stored.
    total_ms: float
    statements: int
    #: Statements on no single function, by what the join found
    #: (``ambiguous``, ``database_schedule``, :data:`UNJOINED`):
    #: ``{"statements", "total_ms", "share"}`` each.
    unattributed: dict[str, dict[str, Any]]


#: One ``evidence_statement_stats`` row: ``(run_id, symbol_id, issuer, calls, total_ms)``.
StatementPoint = tuple[str, "str | None", "str | None", int, "float | None"]


def fold_statements(runs: Iterable[Any], points: Iterable[StatementPoint]) -> list[StatementRun]:
    """Each production statement workload's newest run, newest first, with what
    each issuing function's statements cost in its window.

    *runs* are the repository's ``statement_stats`` runs (``id``,
    ``workload_name``, ``environment``, ``measured_at``, ``ingested_at``,
    ``mapping_partial``); *points* their rows as :data:`StatementPoint`
    tuples. Only each workload's newest complete run speaks
    (:func:`query_runs_to_read`): a window's counters are the workload's
    latest word, and an older window never adds to it.

    A statement counts toward a function only when the join named exactly
    that function (``issuer == "function"``): an ``ambiguous`` statement
    (several functions could issue it), a ``database_schedule`` one (only a
    ``cron.schedule`` job runs it) or an unjoined one is never put on a
    function; each is counted under its label in ``unattributed``. Shares
    are of all the statement time the run stored, so they sum to at most 1
    with ``unattributed``. A run that names no function still folds (rows
    empty), so a reader can tell joined-nothing from no run.
    """
    runs = list(runs)
    keep = set(query_runs_to_read(runs))
    if not keep:
        return []
    by_fn: dict[str, dict[str, list[float]]] = {rid: {} for rid in keep}
    other: dict[str, dict[str, list[float]]] = {rid: {} for rid in keep}
    totals: dict[str, list[float]] = {rid: [0.0, 0] for rid in keep}
    for run_id, symbol_id, issuer, calls, total_ms in points:
        if run_id not in keep:
            continue
        ms = float(total_ms or 0.0)
        totals[run_id][0] += ms
        totals[run_id][1] += 1
        if issuer == "function" and symbol_id:
            acc = by_fn[run_id].setdefault(symbol_id, [0.0, 0, 0])
            acc[1] += calls or 0
        else:
            acc = other[run_id].setdefault(issuer or UNJOINED, [0.0, 0])
        acc[0] += ms
        acc[-1] += 1

    def share(ms: float, whole: float) -> float | None:
        return ms / whole if whole > 0 else None

    out = []
    for run in sorted((r for r in runs if r.id in keep), key=_age_key, reverse=True):
        whole, count = totals[run.id]
        ranked = sorted(by_fn[run.id].items(), key=lambda kv: (-kv[1][0], kv[0]))
        rows = [
            StatementCost(sid, run.id, rank, share(ms, whole), ms, int(calls), int(n))
            for rank, (sid, (ms, calls, n)) in enumerate(ranked, start=1)
        ]
        unattributed = {
            label: {"statements": int(n), "total_ms": ms, "share": share(ms, whole)}
            for label, (ms, n) in sorted(other[run.id].items())
        }
        out.append(StatementRun(run, rows, whole, int(count), unattributed))
    return out


__all__ = [
    "UNJOINED",
    "MeasuredFold",
    "QueryPoint",
    "StatementCost",
    "StatementPoint",
    "StatementRun",
    "fold_measured",
    "fold_query_readings",
    "fold_statements",
    "query_runs_to_read",
]
