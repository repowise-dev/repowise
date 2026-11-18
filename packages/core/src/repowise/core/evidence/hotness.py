"""``symbol_hotness``: which production functions the stored profile runs measured, and how firmly.

Rebuilt whole from the stored profile runs after every profile ingest and at
the end of every index and update, so it always describes the current code.
One row per current production function any run measured; the numbers come
from one basis run. No row means unknown, never cold.

Per run (thresholds from the ``evidence.*`` settings, :class:`HotnessRules`):

- *Split*: where the run's time went, stored by the ingest
  (``evidence.status.time_split``): production repo code itself, waits,
  libraries.
- *Cost*: a function's self time as the converter charges it
  (``evidence.profile_convert``): its own body and the built-ins and
  libraries it called, never time blocked in a wait. Inclusive time is kept
  as context and never ranks: a function that only calls things is on every
  stack and costs nothing itself.
- ``ranked``: among the run's production functions, in the top
  ``rank_top_n`` by cost, with at least ``rank_min_samples`` self samples on
  the function. A run that counted no samples (a traced or an evented
  profile) cannot be checked against the floor and ranks by its order alone.
  Otherwise the row is ``measured``: not enough samples in repo code, or
  outside the top.
- ``lead_candidate``: ranked in a workload class that may lead (never
  ``unit_test``), and either the function's share of all samples is at least
  ``lead_min_share`` with the interval's lower bound at least
  ``lead_min_lower_bound``, or it is ranked in ``lead_min_runs`` or more
  runs. Internal only until the thresholds are calibrated: no surface may
  word it as a claim, which is why the tier is not named for one.
- A deterministic tracer (a run whose rows carry call counts) orders
  functions within the run only: rank, never a share, never a lead.
- *Freshness*: ``current`` at the indexed commit; ``body_unchanged`` from an
  older commit whose function body is unchanged since (the digest stored at
  ingest equals the indexed one); otherwise ``stale``: kept, flagged, not
  ranked. A row measured under another symbol id follows its lineage to the
  current id; evidence is never re-mapped by line or name.

Only ``production`` code (``code_origin``) gets rows: test, tooling and
generated code stays in the evidence tables and is never listed; each row
counts how many such functions its run measured (``run_unlisted``). A
``unit_test`` workload is ranked like any other; readers list it apart.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from repowise.core.analysis.health.coverage.freshness import coverage_freshness
from repowise.core.ingestion.symbol_identity import file_of_symbol
from repowise.core.stats import wilson_interval

from .status import run_share, time_split

#: Tier values, weakest first. None is user copy: a surface words its own claim.
TIERS = ("measured", "ranked", "lead_candidate")
#: Workload classes whose evidence may lead the queue (``unit_test`` never does).
LEAD_CLASSES = frozenset({"benchmark", "entry_point", "load_test", "production"})


@dataclass(frozen=True)
class HotnessRules:
    """The ``evidence.*`` thresholds the derivation applies."""

    top_n: int
    min_samples: int
    lead_min_share: float
    lead_min_lower_bound: float
    lead_min_runs: int


def hotness_rules(config: Mapping[str, Any] | None) -> HotnessRules:
    """The rules from a repo's ``config.yaml`` mapping, defaults where unset or invalid."""
    from repowise.core.settings import resolve

    def value(key: str) -> Any:
        return resolve(f"evidence.{key}", config).value

    return HotnessRules(
        top_n=value("rank_top_n"),
        min_samples=value("rank_min_samples"),
        lead_min_share=value("lead_min_share"),
        lead_min_lower_bound=value("lead_min_lower_bound"),
        lead_min_runs=value("lead_min_runs"),
    )


@dataclass(frozen=True)
class RunFacts:
    """The ``evidence_runs`` columns the derivation reads, for one profile run."""

    id: str
    workload_name: str
    workload_class: str
    value_kind: str | None
    unit: str | None
    commit_sha: str | None
    measured_at: datetime | None
    total_value: float | None
    explained_value: float | None
    total_samples: int | None
    #: Rows outside production code counted at ingest, before the cap.
    unlisted_symbols: int | None = None
    #: The run's split (``evidence.status.time_split``); None when not recorded.
    repo_self_value: float | None = None
    wait_value: float | None = None
    library_value: float | None = None


@dataclass(frozen=True)
class CostFacts:
    """The ``evidence_symbol_costs`` columns the derivation reads, for one row."""

    symbol_id: str
    lineage_id: str | None
    body_digest: str | None
    total_value: float | None
    self_value: float | None
    total_samples: int | None
    call_count: int | None
    self_samples: int | None = None


@dataclass(frozen=True)
class CurrentFunctions:
    """The indexed functions: id -> body digest, and lineage -> current id."""

    digest: Mapping[str, str | None]
    by_lineage: Mapping[str, str]

    @classmethod
    def from_bodies(cls, bodies: Iterable[Any]) -> CurrentFunctions:
        """From ``crud.analysis.function_facts.stored_bodies`` rows."""
        digest: dict[str, str | None] = {}
        lineages: dict[str, list[str]] = {}
        for body in bodies:
            digest[body.symbol_id] = body.digest
            if body.lineage_id:
                lineages.setdefault(body.lineage_id, []).append(body.symbol_id)
        # A lineage names one function; two ids under one would be a guess.
        by_lineage = {lineage: ids[0] for lineage, ids in lineages.items() if len(ids) == 1}
        return cls(digest, by_lineage)

    def current_id(self, cost: CostFacts) -> str:
        """*cost*'s symbol now: its own id while indexed, else where its lineage went."""
        if cost.symbol_id in self.digest or not cost.lineage_id:
            return cost.symbol_id
        return self.by_lineage.get(cost.lineage_id, cost.symbol_id)


@dataclass
class _Row:
    run: RunFacts
    cost: CostFacts
    symbol_id: str
    freshness: str
    ordering_only: bool
    rank: int | None = None
    samples: int | None = None
    tier: str = "measured"
    share: Any = None
    lineage_id: str | None = None
    #: Functions the run measured outside production code.
    unlisted: int = 0


def derive_hotness(
    runs: Sequence[RunFacts],
    costs: Mapping[str, Sequence[CostFacts]],
    current: CurrentFunctions,
    origins: Mapping[str, str],
    *,
    head_commit: str | None,
    rules: HotnessRules,
) -> list[dict[str, Any]]:
    """The ``symbol_hotness`` rows (without ``repository_id``) for *runs*.

    *costs* holds each run's cost rows by run id, *origins* the
    ``code_origin`` of every current symbol's file and *head_commit* the
    indexed commit. Pure, so the rules are tested without a store.
    """
    rows_by_symbol: dict[str, list[_Row]] = {}
    for run in runs:
        for row in _run_rows(run, costs.get(run.id, ()), current, origins, head_commit, rules):
            rows_by_symbol.setdefault(row.symbol_id, []).append(row)

    out: list[dict[str, Any]] = []
    for symbol_id in sorted(rows_by_symbol):
        rows = rows_by_symbol[symbol_id]
        leading = [r for r in rows if r.tier != "measured" and _may_lead(r)]
        if len({r.run.id for r in leading}) >= rules.lead_min_runs:
            for r in leading:
                r.tier = "lead_candidate"
        basis = min(rows, key=_basis_order)
        out.append(_stored(basis, run_count=len({r.run.id for r in rows})))
    return out


def _run_rows(
    run: RunFacts,
    costs: Sequence[CostFacts],
    current: CurrentFunctions,
    origins: Mapping[str, str],
    head_commit: str | None,
    rules: HotnessRules,
) -> list[_Row]:
    """One run's production rows, ranked and tiered within the run."""
    ordering_only = any(c.call_count is not None for c in costs)
    at_head = coverage_freshness(run.commit_sha, head_commit) == "current"
    rows: list[_Row] = []
    for cost in costs:
        symbol_id = current.current_id(cost)
        if origins.get(file_of_symbol(symbol_id)) != "production":
            continue
        rows.append(
            _Row(
                run,
                cost,
                symbol_id,
                freshness=_freshness(at_head, cost.body_digest, current.digest.get(symbol_id)),
                ordering_only=ordering_only,
                samples=_samples(run, cost),
                lineage_id=cost.lineage_id,
                unlisted=len(costs),
            )
        )
    # Ingest counted before the cap; a run stored without that count falls
    # back to the rows kept.
    for row in rows:
        row.unlisted = (
            run.unlisted_symbols if run.unlisted_symbols is not None else row.unlisted - len(rows)
        )
    kept = _one_per_symbol(rows)
    _rank(kept, _run_samples(run), rules)
    return kept


def _one_per_symbol(rows: list[_Row]) -> list[_Row]:
    """Costliest first (self, then inclusive); a symbol reached twice (two old
    ids, one lineage) keeps its costlier row."""
    rows = sorted(
        rows,
        key=lambda r: (-(r.cost.self_value or 0.0), -(r.cost.total_value or 0.0), r.cost.symbol_id),
    )
    seen: set[str] = set()
    kept: list[_Row] = []
    for row in rows:
        if row.symbol_id not in seen:
            seen.add(row.symbol_id)
            kept.append(row)
    return kept


def _rank(rows: list[_Row], n: int | None, rules: HotnessRules) -> None:
    """Place, tier and interval for *rows* (costliest first).

    Stale rows take no place, and neither does a row that cost nothing itself
    (a function that only calls others): it stays measured, never ranked, even
    in a run with fewer costly functions than the top. A tracer's rows order
    by what the tracer gave and are exempt.
    """
    place = 0
    for row in rows:
        if row.freshness == "stale":
            continue
        if not row.ordering_only and not (row.cost.self_value or 0.0) > 0:
            continue
        place += 1
        row.rank = place
        counted = row.samples is not None
        if place <= rules.top_n and (not counted or row.samples >= rules.min_samples):
            row.tier = "ranked"
        if not row.ordering_only and n is not None and counted and row.samples <= n:
            row.share = wilson_interval(row.samples, n)
        if row.tier == "ranked" and _may_lead(row) and _clears_share_rule(row, rules):
            row.tier = "lead_candidate"


def _freshness(at_head: bool, measured_digest: str | None, current_digest: str | None) -> str:
    """Not at the indexed commit (or either commit unknown): the stored body decides."""
    if at_head:
        return "current"
    if measured_digest and measured_digest == current_digest:
        return "body_unchanged"
    return "stale"


def _samples(run: RunFacts, cost: CostFacts) -> int | None:
    """Self samples on the function (the cost it is ranked by): counted, or its
    self value when the unit is samples."""
    if cost.self_samples is not None:
        return cost.self_samples
    if run.unit == "samples" and cost.self_value is not None:
        return round(cost.self_value)
    return None


def _run_samples(run: RunFacts) -> int | None:
    """Samples in the whole run: counted, or its total when the unit is samples."""
    if run.total_samples is not None:
        return run.total_samples
    if run.unit == "samples" and run.total_value is not None:
        return round(run.total_value)
    return None


def _may_lead(row: _Row) -> bool:
    return row.run.workload_class in LEAD_CLASSES and not row.ordering_only and row.freshness != "stale"


def _clears_share_rule(row: _Row, rules: HotnessRules) -> bool:
    share = row.share
    return (
        share is not None
        and share.estimate >= rules.lead_min_share
        and share.low >= rules.lead_min_lower_bound
    )


def _basis_order(row: _Row) -> tuple[Any, ...]:
    """Fresh first, then the strongest tier, a class that may lead, the best rank, the newest."""
    newest = row.run.measured_at.timestamp() if row.run.measured_at else 0.0
    return (
        row.freshness == "stale",
        -TIERS.index(row.tier),
        row.run.workload_class not in LEAD_CLASSES,
        row.rank if row.rank is not None else float("inf"),
        -newest,
        row.run.id,
    )


def _share(value: float | None, total: float | None) -> float | None:
    if value is None or total is None or total <= 0:
        return None
    return min(value / total, 1.0)


def _stored(row: _Row, *, run_count: int) -> dict[str, Any]:
    run, cost = row.run, row.cost
    shares = not row.ordering_only
    return {
        "symbol_id": row.symbol_id,
        "lineage_id": row.lineage_id,
        "tier": row.tier,
        "total_share": _share(cost.total_value, run.total_value) if shares else None,
        "self_share": _share(cost.self_value, run.total_value) if shares else None,
        "share_low": row.share.low if row.share is not None else None,
        "share_high": row.share.high if row.share is not None else None,
        "samples": row.samples,
        "run_samples": _run_samples(run),
        "rank": row.rank,
        "explained_share": run_share(run.total_value, run.explained_value),
        **time_split(run),
        "run_unlisted": row.unlisted,
        "workload_name": run.workload_name,
        "workload_class": run.workload_class,
        "value_kind": run.value_kind,
        "ordering_only": row.ordering_only,
        "freshness": row.freshness,
        "run_id": run.id,
        "run_count": run_count,
        "evidence_commit_sha": run.commit_sha,
        "measured_at": run.measured_at,
    }


async def rebuild_symbol_hotness(
    session: Any,
    repository_id: str,
    *,
    head_commit: str | None,
    rules: HotnessRules,
) -> int | None:
    """Rebuild the repository's ``symbol_hotness`` from its stored profile runs.

    Returns the rows written, or ``None`` when no profile run is stored: then
    nothing is read past one indexed lookup and one delete on the key prefix
    clears any rows left, so an index without evidence pays next to nothing.
    Partial runs are not a basis.
    """
    from repowise.core.persistence.crud import (
        hotness_inputs,
        replace_symbol_hotness,
        stored_bodies,
    )

    from .ingest import load_origins

    inputs = await hotness_inputs(session, repository_id)
    if inputs is None:
        # One delete on the key prefix: rows left from runs no longer stored go.
        await replace_symbol_hotness(session, repository_id, [])
        return None
    runs, costs = inputs
    current = CurrentFunctions.from_bodies(await stored_bodies(session, repository_id, None))
    paths = {
        file_of_symbol(current.current_id(cost)) for rows in costs.values() for cost in rows
    }
    origins = await load_origins(session, repository_id, paths)
    rows = derive_hotness(runs, costs, current, origins, head_commit=head_commit, rules=rules)
    await replace_symbol_hotness(session, repository_id, rows)
    return len(rows)


async def refresh_symbol_hotness(
    session: Any, repository_id: str, *, head_commit: str | None, repo_path: Any
) -> None:
    """:func:`rebuild_symbol_hotness` for an index or update run, with the repo's rules.

    In its own savepoint and best effort: a failure keeps the rows already
    stored and never fails the run that called it.
    """
    import structlog

    from repowise.core.repo_config import load_repo_config

    try:
        rules = hotness_rules(load_repo_config(repo_path) if repo_path is not None else None)
        async with session.begin_nested():
            await rebuild_symbol_hotness(
                session, repository_id, head_commit=head_commit, rules=rules
            )
    except Exception as exc:
        structlog.get_logger(__name__).warning("symbol_hotness_skipped", error=str(exc))


__all__ = [
    "LEAD_CLASSES",
    "TIERS",
    "CostFacts",
    "CurrentFunctions",
    "HotnessRules",
    "RunFacts",
    "derive_hotness",
    "hotness_rules",
    "rebuild_symbol_hotness",
    "refresh_symbol_hotness",
]
