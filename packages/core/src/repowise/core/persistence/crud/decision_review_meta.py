"""The review-queue row every open decision candidate is owed."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core import __version__

from ..models import DecisionCandidateMeta, DecisionRecord
from .authority import candidate_predicate, candidate_review_signals, upsert_candidate_meta


async def _write_candidate_meta(
    session: AsyncSession,
    repository_id: str,
    captured: dict[str, tuple[str, bool]],
    *,
    only: set[str] | None = None,
) -> None:
    """Refresh the review row every open candidate in the repository is owed.

    Not only the ones this run touched: the staging store does not re-emit an
    already-promoted decision, so a backlog would otherwise stay unjudged until
    something happened to re-extract each record individually. The signals come
    from the record, so an unchanged candidate costs a comparison and no write.
    """
    rows = (
        await session.execute(
            select(DecisionRecord, DecisionCandidateMeta)
            .outerjoin(
                DecisionCandidateMeta,
                DecisionCandidateMeta.decision_id == DecisionRecord.id,
            )
            .where(
                DecisionRecord.repository_id == repository_id,
                candidate_predicate(),
                *([DecisionRecord.id.in_(only)] if only is not None else []),
            )
        )
    ).all()

    for rec, meta in rows:
        priority, scope_unresolved = candidate_review_signals(rec)
        capture = captured.get(rec.id)
        if capture is None:
            # Not extracted this run, so the provenance is not ours to write:
            # only the contract-derived signals are refreshed, and a row with
            # no lane yet takes the record's own source.
            if (
                meta is not None
                and meta.lane
                and meta.review_priority == priority
                and meta.scope_unresolved == scope_unresolved
            ):
                continue
            await upsert_candidate_meta(
                session,
                rec,
                lane=meta.lane if meta is not None and meta.lane else rec.source,
                review_priority=priority,
                scope_unresolved=scope_unresolved,
            )
            continue

        lane, needs_split = capture
        await upsert_candidate_meta(
            session,
            rec,
            lane=lane or rec.source,
            extractor_version=__version__,
            review_priority=priority,
            # Raised, never cleared: a re-extraction must not walk back a split
            # somebody asked for.
            needs_split=True if needs_split else None,
            scope_unresolved=scope_unresolved,
        )
