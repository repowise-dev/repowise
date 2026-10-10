"""Payoff rows: what happened to a plan the writer resolved as no longer detected.

Written by :func:`~.refactoring.finalize_refactoring_suggestions` in the same
call that resolves the plans, before the run's ``function_facts`` replace the
stored ones: the stored rows are then still the measures the plan was detected
against, and the run's own fact rows are the measures after.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....analysis.execution_graph import file_of_symbol
from ...models import RefactoringPayoff, RefactoringSuggestion, _now_utc
from .._shared import _BATCH_SIZE
from .function_facts import get_file_facts

if TYPE_CHECKING:
    from ....analysis.health.refactoring.payoff import FunctionMeasures


@dataclass(frozen=True)
class PayoffContext:
    """What the resolving run knows: its fact rows (``None`` when it had no
    graph to key them on), the files it saw alive, and the commit it analysed."""

    fact_rows: list[Mapping[str, Any]] | None
    live_paths: frozenset[str]
    commit: str | None = None


def _by_file(measures: Iterable[FunctionMeasures]) -> dict[str, dict[str, FunctionMeasures]]:
    out: dict[str, dict[str, FunctionMeasures]] = {}
    for item in measures:
        out.setdefault(file_of_symbol(item.symbol_id), {})[item.symbol_id] = item
    return out


def _measures(symbol_id: str, row: Any) -> FunctionMeasures:
    from ....analysis.health.refactoring.payoff import FunctionMeasures

    get = row.get if isinstance(row, Mapping) else lambda key: getattr(row, key, None)
    return FunctionMeasures(symbol_id, get("ccn"), get("nloc"), get("params"))


def _loads(text: str | None) -> dict:
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _payoff_row(row: RefactoringSuggestion, payoff: Any, ctx: PayoffContext, now: Any) -> dict:
    before, after = payoff.before, payoff.after
    return {
        "suggestion_id": row.id,
        "repository_id": row.repository_id,
        "outcome": payoff.outcome,
        "resolved_commit": ctx.commit,
        "resolved_at": now,
        "before_ccn": before.ccn if before else None,
        "before_nloc": before.nloc if before else None,
        "before_params": before.params if before else None,
        "after_ccn": after.ccn if after else None,
        "after_nloc": after.nloc if after else None,
        "after_params": after.params if after else None,
        "new_symbol": payoff.new_symbol,
        "stage": None,
    }


async def record_refactoring_payoffs(
    session: AsyncSession,
    repository_id: str,
    resolved: list[RefactoringSuggestion],
    ctx: PayoffContext,
    detected: Collection[tuple[str, str, str]] = (),
) -> int:
    """Classify and store the payoff of each plan in *resolved*. Returns rows written.

    *detected* is ``(refactoring_type, file_path, target_symbol)`` of every plan
    the run emitted, so a plan re-detected under a new id reads as superseded.
    """
    # Deferred: the refactoring package imports the persistence layer.
    from ....analysis.health.refactoring.payoff import classify_payoff

    if not resolved:
        return 0
    live = {row.file_path for row in resolved if row.file_path in ctx.live_paths}
    stored = await get_file_facts(session, repository_id, live)
    before = _by_file(_measures(fact.symbol_id, fact) for fact in stored)
    after = None
    if ctx.fact_rows is not None:
        after = _by_file(
            _measures(r["symbol_id"], r)
            for r in ctx.fact_rows
            if file_of_symbol(r["symbol_id"]) in live
        )
    now = _now_utc()
    values = []
    for row in resolved:
        plan = _loads(row.plan_json)
        payoff = classify_payoff(
            refactoring_type=row.refactoring_type,
            target=row.target_symbol,
            evidence=_loads(row.evidence_json),
            file_live=row.file_path in ctx.live_paths,
            before=before.get(row.file_path, {}),
            after=None if after is None else after.get(row.file_path, {}),
            suggested_name=plan.get("suggested_name"),
            redetected=(row.refactoring_type, row.file_path, row.target_symbol) in detected,
        )
        values.append(_payoff_row(row, payoff, ctx, now))
    await clear_refactoring_payoffs(session, [row.id for row in resolved])
    for index in range(0, len(values), _BATCH_SIZE):
        await session.execute(
            insert(RefactoringPayoff.__table__), values[index : index + _BATCH_SIZE]
        )
    return len(values)


async def clear_refactoring_payoffs(session: AsyncSession, suggestion_ids: list[str]) -> None:
    """Drop the payoff of each plan in *suggestion_ids* (reopened or re-resolved)."""
    for index in range(0, len(suggestion_ids), _BATCH_SIZE):
        await session.execute(
            delete(RefactoringPayoff).where(
                RefactoringPayoff.suggestion_id.in_(suggestion_ids[index : index + _BATCH_SIZE])
            )
        )


def _measure_dict(ccn: int | None, nloc: int | None, params: int | None) -> dict[str, int]:
    return {
        key: value
        for key, value in (("ccn", ccn), ("nloc", nloc), ("params", params))
        if value is not None
    }


async def plan_payoff(session: AsyncSession, row: RefactoringSuggestion) -> dict | None:
    """The stored payoff of a resolved plan, for its detail; ``None`` otherwise.

    ``realised`` is what left the target, beside the plan's own prediction in
    its ``evidence`` (``ccn_removed``, ``slice_nloc``).
    """
    if row.status != "resolved":
        return None
    payoff = (
        await session.execute(
            select(RefactoringPayoff).where(RefactoringPayoff.suggestion_id == row.id)
        )
    ).scalar_one_or_none()
    if payoff is None:
        return None
    before = _measure_dict(payoff.before_ccn, payoff.before_nloc, payoff.before_params)
    after = _measure_dict(payoff.after_ccn, payoff.after_nloc, payoff.after_params)
    realised = {
        f"{key}_removed": before[key] - after[key]
        for key in ("ccn", "nloc")
        if key in before and key in after
    }
    out: dict[str, Any] = {
        "outcome": payoff.outcome,
        "resolved_commit": payoff.resolved_commit,
        "resolved_at": payoff.resolved_at.isoformat() if payoff.resolved_at else None,
        "before": before,
        "after": after,
        "realised": realised,
        "new_symbol": payoff.new_symbol,
        "stage": payoff.stage,
    }
    return {key: value for key, value in out.items() if value not in (None, {})}


__all__ = [
    "PayoffContext",
    "clear_refactoring_payoffs",
    "plan_payoff",
    "record_refactoring_payoffs",
]
