"""Idempotent store-wide decision repairs and purges; each is a no-op once applied."""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decisions.provenance import SOURCE_RANK, rank_for_source

from ..models import (
    DecisionEdge,
    DecisionEvidence,
    DecisionNodeLink,
    DecisionRecord,
    _now_utc,
)
from .authority import accepted_predicate
from .decision_evidence import _rederive_headline, list_decision_evidence


def _stale_rank_filter() -> Any:
    """SQL matching evidence rows whose stored rank disagrees with the ladder.

    Derived from ``SOURCE_RANK`` rather than hardcoded, so a future ladder edit
    is picked up here automatically instead of needing a second place updated.
    Bounded by the size of the ladder (a dozen terms), and it keeps the common
    case — a store already on the current ladder — to an indexed match on
    ``source`` that returns nothing, rather than hydrating the whole table.
    """
    return or_(
        *[
            (DecisionEvidence.source == name) & (DecisionEvidence.source_rank != rank)
            for name, rank in SOURCE_RANK.items()
        ]
    )


async def reconcile_source_ranks(session: AsyncSession) -> int:
    """Re-stamp evidence rows whose stored rank predates a ``SOURCE_RANK`` edit.

    ``DecisionEvidence.source_rank`` is written once at insert time, and two
    ``ORDER BY`` clauses read it straight from the column, so it cannot simply be
    derived on read. That makes the ladder a value copied into every row: editing
    ``SOURCE_RANK`` leaves existing rows on the previous ladder, and because
    headline confidence comes from ``max(source_rank)`` across a decision's
    evidence, a store would end up scoring headlines off a mixture of two ladders
    without anything looking wrong.

    **This is the only repair path, deliberately.** An Alembic data migration was
    written first and removed: hosted runs the same persist pipeline as a local
    store, so the migration was redundant, and worse, it fixed the ranks *without*
    re-deriving confidence — which made this function's own no-op check pass on
    the next run and left hosted confidences on the old ladder permanently. One
    path cannot disagree with itself.

    Not repo-scoped, on purpose: the ladder is global, so in a workspace store
    holding several repositories every one of them is on the same ladder and all
    of them need the same repair.

    Idempotent. Returns the number of evidence rows re-stamped (0 when already
    reconciled, which is the steady state after the first run).
    """
    moved = (
        (await session.execute(select(DecisionEvidence).where(_stale_rank_filter())))
        .scalars()
        .all()
    )
    if not moved:
        return 0

    for row in moved:
        row.source_rank = rank_for_source(row.source)
    await session.flush()

    now = _now_utc()
    for decision_id in {row.decision_id for row in moved}:
        rec = await session.get(DecisionRecord, decision_id)
        if rec is None:
            continue
        _rederive_headline(rec, await list_decision_evidence(session, decision_id))
        rec.updated_at = now

    # Flush the re-scored headlines too, not just the ranks. Without this the
    # function returns with confidence still pending in the session, so whether
    # the repair survives depends on what the caller does next.
    await session.flush()

    structlog.get_logger(__name__).info(
        "decisions.source_ranks_reconciled", evidence_rows=len(moved)
    )
    return len(moved)


async def reconcile_decision_confidence(session: AsyncSession) -> int:
    """Re-score headlines whose stored confidence predates a formula edit.

    The sibling of :func:`reconcile_source_ranks`, for the other half of the
    same problem. That one repairs a stale *input*, a rank copied into a row
    before the ladder moved, so it can find its work with an indexed
    filter. A change to the formula itself leaves every input valid and every
    stored score wrong, and nothing in a row marks which formula produced it,
    so the only way to find the work is to recompute and compare.

    Ceiling: two selects that hydrate the whole decision table, which is
    small; a store large enough for that to hurt would want the comparison
    pushed into SQL.

    Not repo-scoped, for the reason the ladder is not: the formula is global.
    Idempotent: 0 once reconciled, which is the steady state. Returns the
    number of records re-scored, and re-derives verification alongside it.

    A record with no evidence rows is left alone, deliberately: this pass
    re-derives, it does not invent. The rows in that state are the ones no
    extractor wrote, manual entries and manifest imports, and both score
    themselves where they are created.
    """
    evidence_by_id: dict[str, list[DecisionEvidence]] = {}
    for row in (await session.execute(select(DecisionEvidence))).scalars().all():
        evidence_by_id.setdefault(row.decision_id, []).append(row)
    if not evidence_by_id:
        return 0

    now = _now_utc()
    rescored = 0
    for rec in (await session.execute(select(DecisionRecord))).scalars().all():
        evidence = evidence_by_id.get(rec.id)
        if not evidence:
            continue  # see the docstring: re-derive, never invent
        before = (rec.confidence, rec.verification)
        _rederive_headline(rec, evidence)
        if (rec.confidence, rec.verification) != before:
            rec.updated_at = now
            rescored += 1

    if rescored:
        await session.flush()
        structlog.get_logger(__name__).info(
            "decisions.confidence_reconciled", records=rescored
        )
    return rescored


#: Prefix ``detect_supersessions_and_conflicts`` stamps on every edge it writes
#: (``auto-detected: <signal> (sim=0.81)``). It is the only marker that
#: separates a machine retirement from a human one, so the repair below keys on
#: it rather than on "has a supersedes edge".
_AUTO_EDGE_EVIDENCE_PREFIX = "auto-detected:"


async def unretire_auto_superseded(session: AsyncSession) -> int:
    """Undo retirements made by the semantic supersession detector (3B).

    That detector scoped a conflict by cosine similarity and matched unrelated
    records; it is now off (``SEMANTIC_SUPERSESSION_ENABLED``). Turning it off
    only stops the *next* bad retirement — the rows it already flipped stay
    ``superseded``, which is a protected status, so re-extraction will never
    walk one back and a store would go on reporting a quarter of its corpus as
    retired-by-nothing. This is the other half of the same change.

    A row is repaired only when all three hold: status ``superseded``,
    ``superseded_by`` set, and an ``auto-detected:`` supersedes edge from that
    same successor. ``run_update_evolution`` sets the status without the
    pairing, the CLI's ``decision deprecate`` writes ``deprecated``, and
    ``upsert_decision_edge`` has no caller outside 3B — so nothing else in the
    codebase produces all three.

    One case is knowingly in range: a human can set both fields through
    ``PATCH /api/repos/{id}/decisions/{id}``, and if they did so by accepting
    one of this detector's own proposals in the UI, the auto edge is still
    there and the row is restored to ``proposed``. Accepted rather than
    guarded: the retirement they confirmed rests on the same bad match as the
    74, ``proposed`` keeps the record readable, and ``decision confirm`` or the
    same PATCH puts it back in one step. The inverse — leaving a wrongly
    retired record hidden because a human once clicked through — is not
    recoverable at all.

    Restored to ``proposed``, not ``active``: the flip overwrote the previous
    status without recording it, and 3B fired on both. ``proposed`` is the
    lower claim of the two and is recoverable by ``decision confirm``; guessing
    ``active`` would mint governance a human never granted.

    The edges go too — both kinds it wrote. They are the same artifact from the
    same detector: ``supersedes`` is what ``build_lineage_chain`` walks, so
    leaving those would hand ``get_why`` a lineage that still presents the
    un-retired record as replaced, and ``conflicts_with`` is what the health
    dashboard counts as a governance smell.

    Not repo-scoped, like ``reconcile_source_ranks``: a workspace store holds
    several repositories and the detector ran over all of them.

    Idempotent — once repaired the edges are gone, so the next scan matches
    nothing. Returns the number of records restored.
    """
    auto_edges = (
        (
            await session.execute(
                select(DecisionEdge).where(
                    DecisionEdge.kind.in_(("supersedes", "conflicts_with")),
                    DecisionEdge.evidence.startswith(_AUTO_EDGE_EVIDENCE_PREFIX),
                )
            )
        )
        .scalars()
        .all()
    )
    if not auto_edges:
        return 0

    successors_by_target: dict[str, set[str]] = {}
    for edge in auto_edges:
        if edge.kind == "supersedes":
            successors_by_target.setdefault(edge.dst_decision_id, set()).add(edge.src_decision_id)

    now = _now_utc()
    restored = 0
    for target_id, successor_ids in successors_by_target.items():
        rec = await session.get(DecisionRecord, target_id)
        if rec is None or rec.status != "superseded" or rec.superseded_by is None:
            continue
        if rec.superseded_by not in successor_ids:
            # Retired by something else. Leave the *status* alone — the edge
            # still goes, below, because it is this detector's noise either way.
            continue
        rec.status = "proposed"
        rec.superseded_by = None
        rec.updated_at = now
        restored += 1

    for edge in auto_edges:
        await session.delete(edge)
    await session.flush()

    structlog.get_logger(__name__).info(
        "decisions.auto_supersessions_reverted",
        records_restored=restored,
        edges_deleted=len(auto_edges),
    )
    return restored


async def purge_proposed_decisions_by_source(
    session: AsyncSession,
    repository_id: str,
    source: str,
) -> int:
    """Delete still-``proposed`` records of *source* plus their child rows.

    One-shot cleanup for retired extraction sources (today: the removed
    ``code_comment`` harvest, #751). Only ``proposed`` rows go — anything the
    user confirmed, deprecated, or dismissed is kept. Child rows are deleted
    explicitly rather than trusting the FK cascade, which on SQLite depends on
    the ``foreign_keys`` pragma. Returns the number of records deleted.
    """
    result = await session.execute(
        select(DecisionRecord.id).where(
            DecisionRecord.repository_id == repository_id,
            DecisionRecord.source == source,
            DecisionRecord.status == "proposed",
            ~accepted_predicate(),
        )
    )
    ids = [row[0] for row in result.all()]
    if not ids:
        return 0

    await _delete_decisions(session, ids)
    structlog.get_logger(__name__).info("decision_purge_by_source", source=source, deleted=len(ids))
    return len(ids)


async def purge_proposed_decisions_outside_files(
    session: AsyncSession,
    repository_id: str,
    current_file_paths: set[str],
) -> int:
    """Delete unreviewed extracted decisions whose evidence file left scope."""
    result = await session.execute(
        select(DecisionRecord.id, DecisionRecord.evidence_file).where(
            DecisionRecord.repository_id == repository_id,
            DecisionRecord.status == "proposed",
            DecisionRecord.evidence_file.is_not(None),
            ~accepted_predicate(),
        )
    )
    ids = [row[0] for row in result.all() if row[1] not in current_file_paths]
    if not ids:
        return 0

    await _delete_decisions(session, ids)
    return len(ids)


async def _delete_decisions(session: AsyncSession, ids: list[str]) -> None:
    """Delete decision records and their child rows.

    Child rows are deleted explicitly rather than trusting the FK cascade,
    which on SQLite depends on the ``foreign_keys`` pragma.
    """
    await session.execute(delete(DecisionEvidence).where(DecisionEvidence.decision_id.in_(ids)))
    await session.execute(
        delete(DecisionEdge).where(
            or_(
                DecisionEdge.src_decision_id.in_(ids),
                DecisionEdge.dst_decision_id.in_(ids),
            )
        )
    )
    await session.execute(delete(DecisionNodeLink).where(DecisionNodeLink.decision_id.in_(ids)))
    await session.execute(delete(DecisionRecord).where(DecisionRecord.id.in_(ids)))
    await session.flush()
