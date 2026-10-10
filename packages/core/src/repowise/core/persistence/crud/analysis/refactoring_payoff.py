"""Payoff rows: what happened to a plan the writer resolved as no longer detected.

Written by :func:`~.refactoring.finalize_refactoring_suggestions` in the same
call that resolves the plans, before the run's ``function_facts`` replace the
stored ones: the stored rows are then still the measures the plan was detected
against, and the run's own fact rows are the measures after.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....analysis.execution_graph import file_of_symbol
from ...models import RefactoringPayoff, RefactoringSuggestion, _now_utc
from .._shared import _BATCH_SIZE
from .function_facts import get_file_facts

_MEASURES = ("ccn", "nloc", "params")


@dataclass(frozen=True)
class PayoffContext:
    """What the resolving run knows.

    *fact_rows* are its ``function_facts`` rows (``None`` when it had no graph to
    key them on); *is_live* says whether a path is still on disk or tracked by
    git, and without it no plan reads as ``file_deleted``; *commit* is the
    commit the run analysed.
    """

    fact_rows: Iterable[Mapping[str, Any]] | None
    is_live: Callable[[str], bool] | None = None
    commit: str | None = None


def _loads(text: str | None) -> dict:
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _payoff_row(row: RefactoringSuggestion, payoff: Any, ctx: PayoffContext, now: Any) -> dict:
    out = {
        "suggestion_id": row.id,
        "repository_id": row.repository_id,
        "outcome": payoff.outcome,
        "resolved_commit": ctx.commit,
        "resolved_at": now,
        "new_symbol": payoff.new_symbol,
    }
    for side in ("before", "after"):
        measures = getattr(payoff, side)
        for key in _MEASURES:
            out[f"{side}_{key}"] = getattr(measures, key) if measures else None
    return out


async def settle_refactoring_payoffs(
    session: AsyncSession,
    repository_id: str,
    *,
    resolved: list[RefactoringSuggestion],
    reopened: list[str],
    detected: set[tuple[str, str, str]],
    ctx: PayoffContext,
) -> None:
    """Drop the payoffs of *reopened* plans and record those of *resolved* ones.

    *detected* is ``(refactoring_type, file_path, target_symbol)`` of every plan
    the run emitted, so a plan re-detected under a new id reads as superseded.
    """
    # Deferred: the refactoring package imports the persistence layer.
    from ....analysis.health.refactoring.payoff import classify_payoff, measures_by_file

    await clear_refactoring_payoffs(session, reopened + [row.id for row in resolved])
    if not resolved:
        return
    paths = {row.file_path for row in resolved}
    stored = await get_file_facts(session, repository_id, paths)
    # ORM rows become plain rows here, the one shape the classifier reads.
    before = measures_by_file(
        {"symbol_id": fact.symbol_id, **{key: getattr(fact, key) for key in _MEASURES}}
        for fact in stored
    )
    after = measures_by_file(
        r for r in (ctx.fact_rows or ()) if file_of_symbol(r["symbol_id"]) in paths
    )
    now = _now_utc()
    values = []
    for row in resolved:
        payoff = classify_payoff(
            refactoring_type=row.refactoring_type,
            target=row.target_symbol,
            evidence=_loads(row.evidence_json),
            file_live=ctx.is_live(row.file_path) if ctx.is_live else None,
            before=before.get(row.file_path, {}),
            after=after.get(row.file_path, {}),
            suggested_name=_loads(row.plan_json).get("suggested_name"),
            redetected=(row.refactoring_type, row.file_path, row.target_symbol) in detected,
        )
        values.append(_payoff_row(row, payoff, ctx, now))
    for index in range(0, len(values), _BATCH_SIZE):
        await session.execute(
            insert(RefactoringPayoff.__table__), values[index : index + _BATCH_SIZE]
        )


async def clear_refactoring_payoffs(session: AsyncSession, suggestion_ids: list[str]) -> None:
    """Drop the payoff of each plan in *suggestion_ids*."""
    for index in range(0, len(suggestion_ids), _BATCH_SIZE):
        await session.execute(
            delete(RefactoringPayoff).where(
                RefactoringPayoff.suggestion_id.in_(suggestion_ids[index : index + _BATCH_SIZE])
            )
        )


def _side(payoff: RefactoringPayoff, side: str) -> dict[str, int]:
    values = ((key, getattr(payoff, f"{side}_{key}")) for key in _MEASURES)
    return {key: value for key, value in values if value is not None}


async def plan_payoff(session: AsyncSession, row: RefactoringSuggestion) -> dict | None:
    """The stored payoff of a plan the writer resolved, for its detail.

    ``None`` for any other plan, a person's resolution included. ``realised``
    is what left the target, beside the plan's own prediction in its
    ``evidence`` (``ccn_removed``, ``slice_nloc``).
    """
    if row.status != "resolved" or row.status_reason != "no_longer_detected":
        return None
    payoff = (
        await session.execute(
            select(RefactoringPayoff).where(RefactoringPayoff.suggestion_id == row.id)
        )
    ).scalar_one_or_none()
    if payoff is None:
        return None
    before, after = _side(payoff, "before"), _side(payoff, "after")
    out: dict[str, Any] = {
        "outcome": payoff.outcome,
        "resolved_commit": payoff.resolved_commit,
        "resolved_at": payoff.resolved_at.isoformat() if payoff.resolved_at else None,
        "before": before,
        "after": after,
        "realised": {
            f"{key}_removed": before[key] - after[key]
            for key in ("ccn", "nloc")
            if key in before and key in after
        },
        "new_symbol": payoff.new_symbol,
    }
    return {key: value for key, value in out.items() if value not in (None, {})}


__all__ = [
    "PayoffContext",
    "clear_refactoring_payoffs",
    "plan_payoff",
    "settle_refactoring_payoffs",
]
