"""CRUD operations for the decisions domain (repowise persistence layer).

Split out of the former monolithic ``crud.py``; ``crud/__init__.py`` re-exports
every public name, so existing imports are unaffected.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decisions.provenance import (
    SOURCE_RANK,
    compute_confidence,
    rank_for_source,
)

from ..decision_graph import sync_decision_node_links
from ..models import (
    DecisionEdge,
    DecisionEvidence,
    DecisionNodeLink,
    DecisionRecord,
    GitMetadata,
    _new_uuid,
    _now_utc,
)

# ---------------------------------------------------------------------------
# DecisionRecord CRUD
# ---------------------------------------------------------------------------

_VALID_DECISION_STATUSES = frozenset(
    {"proposed", "active", "deprecated", "superseded", "dismissed"}
)

# Statuses a human (or the evolution judge) set deliberately. Re-extraction
# must never walk one of these back to ``proposed``: a re-harvested source
# ties or beats the stored rank, so without this guard a reindex would flip
# confirmed decisions back into the review queue and resurrect dismissed ones
# (#751). ``dismissed`` is terminal: the row is kept purely as a tombstone so
# the same content never re-proposes.
_PROTECTED_STATUSES = frozenset({"active", "deprecated", "superseded", "dismissed"})


def _merge_status(existing: str, incoming: str) -> str:
    """Resolve a re-extracted status against the stored one.

    Extraction only ever emits ``proposed`` or ``active`` (ADR files can also
    carry ``deprecated``/``superseded``). A protected stored status wins over
    an incoming ``proposed``; anything else follows the incoming value.
    """
    if incoming == "proposed" and existing in _PROTECTED_STATUSES:
        return existing
    return incoming


async def upsert_decision(
    session: AsyncSession,
    *,
    repository_id: str,
    title: str,
    status: str = "proposed",
    context: str = "",
    decision: str = "",
    rationale: str = "",
    alternatives: list[str] | None = None,
    consequences: list[str] | None = None,
    affected_files: list[str] | None = None,
    affected_modules: list[str] | None = None,
    tags: list[str] | None = None,
    source: str = "cli",
    evidence_commits: list[str] | None = None,
    evidence_file: str | None = None,
    evidence_line: int | None = None,
    confidence: float = 1.0,
    verification: str = "unverified",
    last_code_change: datetime | None = None,
    staleness_score: float = 0.0,
    superseded_by: str | None = None,
    decision_id: str | None = None,
) -> DecisionRecord:
    """Create or update a decision record.

    Dedup key: ``(repository_id, title, source, evidence_file)``.
    """
    # Normalise text fields — LLM extractors may return explicit None
    rationale = rationale or ""
    context = context or ""
    decision = decision or ""

    # Build the WHERE clause — evidence_file may be NULL
    q = select(DecisionRecord).where(
        DecisionRecord.repository_id == repository_id,
        DecisionRecord.title == title,
        DecisionRecord.source == source,
    )
    if evidence_file is not None:
        q = q.where(DecisionRecord.evidence_file == evidence_file)
    else:
        q = q.where(DecisionRecord.evidence_file.is_(None))

    result = await session.execute(q)
    existing = result.scalar_one_or_none()

    if existing is not None:
        existing.status = status
        existing.context = context
        existing.decision = decision
        existing.rationale = rationale
        existing.alternatives_json = json.dumps(alternatives or [])
        existing.consequences_json = json.dumps(consequences or [])
        existing.affected_files_json = json.dumps(affected_files or [])
        existing.affected_modules_json = json.dumps(affected_modules or [])
        existing.tags_json = json.dumps(tags or [])
        existing.evidence_commits_json = json.dumps(evidence_commits or [])
        existing.evidence_line = evidence_line
        existing.confidence = confidence
        existing.verification = verification
        existing.last_code_change = last_code_change
        existing.staleness_score = staleness_score
        existing.superseded_by = superseded_by
        existing.updated_at = _now_utc()
        await session.flush()
        return existing

    rec = DecisionRecord(
        id=decision_id or _new_uuid(),
        repository_id=repository_id,
        title=title,
        status=status,
        context=context,
        decision=decision,
        rationale=rationale,
        alternatives_json=json.dumps(alternatives or []),
        consequences_json=json.dumps(consequences or []),
        affected_files_json=json.dumps(affected_files or []),
        affected_modules_json=json.dumps(affected_modules or []),
        tags_json=json.dumps(tags or []),
        evidence_commits_json=json.dumps(evidence_commits or []),
        source=source,
        evidence_file=evidence_file,
        evidence_line=evidence_line,
        confidence=confidence,
        verification=verification,
        last_code_change=last_code_change,
        staleness_score=staleness_score,
        superseded_by=superseded_by,
    )
    session.add(rec)
    await session.flush()
    return rec


async def get_decision(session: AsyncSession, decision_id: str) -> DecisionRecord | None:
    """Return a DecisionRecord by primary key, or None."""
    return await session.get(DecisionRecord, decision_id)


async def list_decisions(
    session: AsyncSession,
    repository_id: str,
    *,
    status: str | None = None,
    source: str | None = None,
    tag: str | None = None,
    module: str | None = None,
    include_proposed: bool = True,
    limit: int = 100,
    offset: int = 0,
    sort: str = "priority",
) -> list[DecisionRecord]:
    """Return decision records with optional filters.

    Dismissed records are tombstones and only show up when explicitly asked
    for via ``status="dismissed"``.

    ``sort`` controls ordering:

    * ``"priority"`` (default) — what a reader needs first. Confirmed rules
      lead, then the proposals most likely to be real (highest confidence),
      then the records that have been retired. Newest breaks every tie.
      Ordering by ``created_at`` alone buried all eleven active decisions on
      this repo under 468 unreviewed proposals, so page one of the table was
      entirely machine guesses.
    * ``"recent"`` — the previous behaviour, newest first.
    """
    q = select(DecisionRecord).where(DecisionRecord.repository_id == repository_id)
    if status is not None:
        q = q.where(DecisionRecord.status == status)
    else:
        q = q.where(DecisionRecord.status != "dismissed")
        if not include_proposed:
            q = q.where(DecisionRecord.status != "proposed")
    if source is not None:
        q = q.where(DecisionRecord.source == source)
    if tag is not None:
        # Match exact tag value in JSON array, not substring.
        # JSON arrays store as '["tag1", "tag2"]', so we match '"tag"'
        q = q.where(DecisionRecord.tags_json.contains(f'"{tag}"'))
    if module is not None:
        # Match exact module path in JSON array
        q = q.where(DecisionRecord.affected_modules_json.contains(f'"{module}"'))
    q = q.order_by(*_decision_order(sort)).limit(limit).offset(offset)
    result = await session.execute(q)
    return list(result.scalars().all())


# Lower sorts first. Active is a rule the team stands behind; proposed is a
# candidate; superseded and deprecated are history.
_STATUS_RANK = {"active": 0, "proposed": 1, "superseded": 2, "deprecated": 3}


def _decision_order(sort: str) -> tuple[Any, ...]:
    """ORDER BY terms for :func:`list_decisions`."""
    if sort == "recent":
        return (DecisionRecord.created_at.desc(),)
    rank = case(_STATUS_RANK, value=DecisionRecord.status, else_=4)
    return (
        rank,
        DecisionRecord.confidence.desc(),
        DecisionRecord.created_at.desc(),
    )


async def count_decisions_by_status(
    session: AsyncSession,
    repository_id: str,
    *,
    source: str | None = None,
    tag: str | None = None,
    module: str | None = None,
    include_proposed: bool = True,
) -> dict[str, int]:
    """Return ``{status: count}`` for a repository, plus a ``"total"`` key.

    A grouped ``COUNT`` rather than a page of rows. The list endpoint caps at
    500, so a caller that counted what it fetched reported "97 of 100" on a
    repository holding several hundred records — a count nobody measured.
    ``get_decision_health_summary`` still loads every row to tally them; this
    is the aggregate version for callers that only need the numbers.

    Statuses absent from the table are zero-filled, so the shape is stable.
    """
    q = select(DecisionRecord.status, func.count(DecisionRecord.id)).where(
        DecisionRecord.repository_id == repository_id
    )
    q = q.where(DecisionRecord.status != "dismissed")
    if not include_proposed:
        q = q.where(DecisionRecord.status != "proposed")
    if source is not None:
        q = q.where(DecisionRecord.source == source)
    if tag is not None:
        q = q.where(DecisionRecord.tags_json.contains(f'"{tag}"'))
    if module is not None:
        q = q.where(DecisionRecord.affected_modules_json.contains(f'"{module}"'))
    q = q.group_by(DecisionRecord.status)

    counts = {status: 0 for status in _STATUS_RANK}
    total = 0
    for status, n in await session.execute(q):
        counts[status] = n
        total += n
    counts["total"] = total
    return counts


async def update_decision_metadata(
    session: AsyncSession,
    decision_id: str,
    *,
    affected_modules: list[str] | None = None,
    affected_files: list[str] | None = None,
) -> DecisionRecord | None:
    """Patch the module/file linkage on a decision record.

    Each argument left as ``None`` is preserved. Pass an empty list to clear.
    Returns the updated record, or ``None`` if the id was not found.
    """
    rec = await session.get(DecisionRecord, decision_id)
    if rec is None:
        return None
    if affected_modules is not None:
        rec.affected_modules_json = json.dumps(affected_modules)
    if affected_files is not None:
        rec.affected_files_json = json.dumps(affected_files)
    rec.updated_at = _now_utc()
    await session.flush()
    return rec


async def update_decision_status(
    session: AsyncSession,
    decision_id: str,
    status: str,
    *,
    superseded_by: str | None = None,
) -> DecisionRecord | None:
    """Update the status of a decision record.

    Raises ValueError for invalid statuses. Returns None if not found.
    """
    if status not in _VALID_DECISION_STATUSES:
        raise ValueError(
            f"Unknown decision status {status!r}. Valid values: {sorted(_VALID_DECISION_STATUSES)}"
        )
    rec = await session.get(DecisionRecord, decision_id)
    if rec is None:
        return None
    rec.status = status
    if superseded_by is not None:
        rec.superseded_by = superseded_by
    rec.updated_at = _now_utc()
    await session.flush()
    return rec


async def update_decision_by_id(
    session: AsyncSession,
    decision_id: str,
    **fields: Any,
) -> DecisionRecord | None:
    """Update content fields of a decision record by ID (partial update).

    Accepts keyword arguments for any updatable field:
    title, context, decision, rationale, alternatives, consequences,
    affected_files, affected_modules, tags, evidence_file, evidence_line,
    confidence.

    JSON list fields (alternatives, consequences, affected_files,
    affected_modules, tags) accept Python lists and are serialized to JSON.

    Returns None if the decision is not found.
    """
    rec = await session.get(DecisionRecord, decision_id)
    if rec is None:
        return None

    _json_fields = {
        "alternatives": "alternatives_json",
        "consequences": "consequences_json",
        "affected_files": "affected_files_json",
        "affected_modules": "affected_modules_json",
        "tags": "tags_json",
    }
    _scalar_fields = {
        "title",
        "context",
        "decision",
        "rationale",
        "evidence_file",
        "evidence_line",
        "confidence",
    }

    for key, value in fields.items():
        if key in _json_fields:
            setattr(rec, _json_fields[key], json.dumps(value))
        elif key in _scalar_fields:
            setattr(rec, key, value)

    rec.updated_at = _now_utc()
    await session.flush()
    return rec


async def delete_decision(session: AsyncSession, decision_id: str) -> bool:
    """Delete a decision record. Returns True if deleted, False if not found."""
    rec = await session.get(DecisionRecord, decision_id)
    if rec is None:
        return False
    await session.delete(rec)
    await session.flush()
    return True


def _normalize_title(title: str) -> str:
    """Normalize a decision title for cross-source dedup comparison."""
    import re as _re

    t = title.lower().strip()
    t = _re.sub(r"[^a-z0-9\s]", "", t)
    t = _re.sub(r"\s+", " ", t)
    return t


def _evidence_quote(d: dict) -> str:
    """Pick the verbatim span recorded as this evidence row's source quote.

    Prefers the LLM/parser-supplied ``source_quote``; falls back to the
    decision/rationale text so an evidence row is never empty.
    """
    return (d.get("source_quote") or d.get("decision") or d.get("rationale") or "").strip()


async def _upsert_decision_evidence(
    session: AsyncSession,
    decision_id: str,
    *,
    source: str,
    source_rank: int,
    evidence_file: str | None,
    evidence_line: int | None,
    evidence_commit: str | None,
    source_quote: str,
    confidence: float,
    verification: str,
) -> None:
    """Insert or update one evidence row, idempotent on its natural key.

    Natural key is ``(decision_id, source, evidence_file, evidence_commit)``.
    NULLs are matched explicitly (SQLite treats NULLs as distinct in a unique
    constraint), so re-indexing the same source converges instead of
    duplicating, while an incremental update adds genuinely new evidence.
    """
    q = select(DecisionEvidence).where(
        DecisionEvidence.decision_id == decision_id,
        DecisionEvidence.source == source,
    )
    q = (
        q.where(DecisionEvidence.evidence_file == evidence_file)
        if evidence_file is not None
        else q.where(DecisionEvidence.evidence_file.is_(None))
    )
    q = (
        q.where(DecisionEvidence.evidence_commit == evidence_commit)
        if evidence_commit is not None
        else q.where(DecisionEvidence.evidence_commit.is_(None))
    )
    existing = (await session.execute(q)).scalar_one_or_none()

    if existing is not None:
        existing.source_rank = source_rank
        existing.evidence_line = evidence_line
        existing.source_quote = source_quote
        existing.confidence = confidence
        existing.verification = verification
        return

    session.add(
        DecisionEvidence(
            decision_id=decision_id,
            source=source,
            source_rank=source_rank,
            evidence_file=evidence_file,
            evidence_line=evidence_line,
            evidence_commit=evidence_commit,
            source_quote=source_quote,
            confidence=confidence,
            verification=verification,
        )
    )


def _best_verification(values: list[str]) -> str:
    """Reduce per-evidence verdicts to the strongest: exact > fuzzy > unverified."""
    if "exact" in values:
        return "exact"
    if "fuzzy" in values:
        return "fuzzy"
    return "unverified"


def _rederive_headline(rec: DecisionRecord, evidence: list[DecisionEvidence]) -> None:
    """Set a record's confidence + verification from its full evidence set.

    The single definition of how a headline is scored. Both writers use it: the
    upsert path after accreting a run's evidence, and ``reconcile_source_ranks``
    after a ladder edit. Kept as one function because the two were briefly
    copy-pasted and nothing would have forced the copies to stay equal.

    Confidence rises with the best source rank and with the number of
    *independent* corroborating sources, so it is derived from the whole set
    rather than from whichever row happened to arrive last. No-op on empty
    evidence: a record with nothing behind it keeps whatever it had.
    """
    if not evidence:
        return
    best_ver = _best_verification([e.verification for e in evidence])
    rec.confidence = compute_confidence(
        max(e.source_rank for e in evidence),
        len({e.source for e in evidence}),
        best_ver,
    )
    rec.verification = best_ver


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


async def list_decision_evidence(
    session: AsyncSession,
    decision_id: str,
) -> list[DecisionEvidence]:
    """Return all evidence rows for a decision, highest source rank first."""
    result = await session.execute(
        select(DecisionEvidence)
        .where(DecisionEvidence.decision_id == decision_id)
        .order_by(DecisionEvidence.source_rank.desc(), DecisionEvidence.created_at.asc())
    )
    return list(result.scalars().all())


def _first_commit(d: dict) -> str | None:
    commits = d.get("evidence_commits") or []
    return commits[0] if commits else None


async def bulk_upsert_decisions(
    session: AsyncSession,
    repository_id: str,
    decisions: list[dict],
    *,
    vector_store: Any | None = None,
) -> list[str]:
    """Upsert decisions, accreting provenance instead of discarding losers.

    Decisions with near-identical normalized titles are merged into a single
    :class:`DecisionRecord`; every contributing source becomes a
    :class:`DecisionEvidence` row. The record's headline fields come from the
    highest-``source_rank`` contributor, and its confidence is recomputed from
    the best rank + the number of independently corroborating sources + the
    strongest surviving verification verdict.

    Idempotent: a full re-index converges (evidence rows upsert on their
    natural key); an incremental update adds new evidence and re-derives the
    headline + confidence from the union.

    When *vector_store* (the shared page-generator store) is supplied, a
    Phase-2C semantic pass augments the cheap normalized-title match: an
    incoming group that matched no existing record by title is looked up in the
    store, and if its nearest ``decision:`` neighbour clears the cosine
    threshold it is folded into that record as additional evidence — so
    paraphrases ("Use Redis" vs "Adopt Redis cache") collapse into one record.
    Every touched record is (re-)embedded into the store, so decisions are
    matchable next run *and* discoverable via ``search_codebase``.

    Returns the ids of every record touched (created or updated) this call, so
    a caller can run the Phase-3 supersession/conflict detection over just the
    records that changed.
    """
    # Group incoming decisions by normalized title.
    groups: dict[str, list[dict]] = {}
    for d in decisions:
        norm = _normalize_title(d.get("title", ""))
        if not norm:
            continue
        groups.setdefault(norm, []).append(d)

    if not groups:
        return []

    # Map existing records (this repo) by normalized title so cross-run merges
    # land on the same row. On a title collision keep the most authoritative
    # existing row as canonical.
    existing_rows = await session.execute(
        select(DecisionRecord).where(DecisionRecord.repository_id == repository_id)
    )
    existing_by_norm: dict[str, DecisionRecord] = {}
    for rec in existing_rows.scalars().all():
        norm = _normalize_title(rec.title)
        prior = existing_by_norm.get(norm)
        if prior is None or rank_for_source(rec.source) > rank_for_source(prior.source):
            existing_by_norm[norm] = rec

    # Phase 2C semantic dedup runs against the shared vector store. ``id_to_rec``
    # lets a store hit (which returns a decision id) resolve back to the live
    # record, and is grown as records are created so paraphrases *within* one
    # batch also collapse.
    id_to_rec: dict[str, DecisionRecord] = {rec.id: rec for rec in existing_by_norm.values()}

    # Headline candidate per group: highest source rank, tie-break by
    # confidence. Hoisted so the batched embedding below can see every
    # group's match text before the loop runs.
    headline_by_norm: dict[str, dict] = {
        norm: max(
            members,
            key=lambda d: (rank_for_source(d.get("source", "")), d.get("confidence", 0.0)),
        )
        for norm, members in groups.items()
    }

    # Batched embedding for dedup. The per-item path embeds one query per
    # residual group and one upsert per touched record — thousands of serial
    # network round-trips on a first index. Instead: embed every group's
    # match text in a few chunked requests up front, search the store by raw
    # vector, dedup *within* the batch against a local pending index, and
    # defer the store writes to one batched upsert after the loop. Stores
    # that can't hand back vectors (no embedder, or search_by_vector left at
    # the base-class default) keep the per-item flow unchanged.
    batch_mode = False
    group_vecs: dict[str, list[float]] = {}
    group_texts: dict[str, str] = {}
    if vector_store is not None:
        from repowise.core.analysis.decision_semantic_match import (
            PendingDecisionIndex,
            decision_match_text,
            decision_vector_item,
            find_duplicate_decision,
            find_duplicate_decision_by_vector,
            upsert_decision_vector,
            upsert_decision_vectors,
        )
        from repowise.core.persistence.vector_store import VectorStore

        # Static capability check: the base-class search_by_vector is the
        # "unsupported" sentinel. A runtime probe would conflate a transient
        # store error with lack of support and silently fall back to the
        # O(N)-round-trip path this branch exists to avoid.
        store_cls_search = getattr(type(vector_store), "search_by_vector", None)
        supports_vector_search = (
            callable(store_cls_search) and store_cls_search is not VectorStore.search_by_vector
        )

        ordered = [
            (norm, decision_match_text(h.get("title", ""), h.get("decision") or ""))
            for norm, h in headline_by_norm.items()
        ]
        ordered = [(norm, text) for norm, text in ordered if text]
        if ordered and supports_vector_search:
            try:
                vecs = await vector_store.embed_texts([text for _, text in ordered])
            except Exception:
                vecs = None
            if vecs is not None and len(vecs) == len(ordered):
                group_vecs = dict(zip((n for n, _ in ordered), vecs, strict=True))
                group_texts = dict(ordered)
                batch_mode = True

        structlog.get_logger(__name__).info(
            "decision_dedup_embedding",
            groups=len(groups),
            batched=batch_mode,
        )

    # Vectors of records touched this batch but not yet written to the store —
    # the in-batch side of dedup in batch mode. A record is only registered
    # when its final text equals the group text its vector came from, so the
    # index never matches against text the record doesn't actually contain.
    pending = PendingDecisionIndex() if batch_mode else None

    touched_ids: list[str] = []

    for norm, members in groups.items():
        headline = headline_by_norm[norm]
        rec = existing_by_norm.get(norm)

        # No title match → ask the store whether a semantically-equivalent
        # decision already exists, and fold into it if so (cheap title dedup
        # stays the first pass; this only runs on the residual).
        if rec is None and vector_store is not None:
            q_vec = group_vecs.get(norm)
            if batch_mode and q_vec is not None:
                match_id = await find_duplicate_decision_by_vector(
                    vector_store, q_vec, pending=pending
                )
            else:
                match_id = await find_duplicate_decision(
                    vector_store,
                    title=headline.get("title", ""),
                    decision=headline.get("decision") or "",
                )
            if match_id is not None and match_id in id_to_rec:
                rec = id_to_rec[match_id]
                existing_by_norm[norm] = rec

        # A dismissed record is a tombstone: leave it untouched (no headline
        # promotion, no new evidence, not re-embedded, not in touched_ids) so
        # dismissals survive every reindex.
        if rec is not None and rec.status == "dismissed":
            continue

        if rec is None:
            rec = DecisionRecord(
                id=_new_uuid(),
                repository_id=repository_id,
                title=headline.get("title", ""),
                status=headline.get("status", "proposed"),
                context=headline.get("context") or "",
                decision=headline.get("decision") or "",
                rationale=headline.get("rationale") or "",
                alternatives_json=json.dumps(headline.get("alternatives") or []),
                consequences_json=json.dumps(headline.get("consequences") or []),
                affected_files_json=json.dumps(headline.get("affected_files") or []),
                affected_modules_json=json.dumps(headline.get("affected_modules") or []),
                tags_json=json.dumps(headline.get("tags") or []),
                evidence_commits_json=json.dumps(headline.get("evidence_commits") or []),
                source=headline.get("source", "cli"),
                evidence_file=headline.get("evidence_file"),
                evidence_line=headline.get("evidence_line"),
                confidence=headline.get("confidence", 0.5),
            )
            session.add(rec)
            await session.flush()
            existing_by_norm[norm] = rec
            id_to_rec[rec.id] = rec
        elif rank_for_source(headline.get("source", "")) >= rank_for_source(rec.source):
            # A new contributor at least as authoritative as the current
            # headline → promote its fields (provenance still accretes below).
            rec.title = headline.get("title", rec.title)
            rec.status = _merge_status(rec.status, headline.get("status", rec.status))
            rec.context = headline.get("context") or rec.context
            rec.decision = headline.get("decision") or rec.decision
            rec.rationale = headline.get("rationale") or rec.rationale
            rec.alternatives_json = json.dumps(headline.get("alternatives") or [])
            rec.consequences_json = json.dumps(headline.get("consequences") or [])
            rec.affected_files_json = json.dumps(headline.get("affected_files") or [])
            rec.affected_modules_json = json.dumps(headline.get("affected_modules") or [])
            rec.tags_json = json.dumps(headline.get("tags") or [])
            rec.evidence_commits_json = json.dumps(headline.get("evidence_commits") or [])
            rec.source = headline.get("source", rec.source)
            rec.evidence_file = headline.get("evidence_file")
            rec.evidence_line = headline.get("evidence_line")

        # Accrete one evidence row per contributing source occurrence.
        for d in members:
            src = d.get("source", "cli")
            await _upsert_decision_evidence(
                session,
                rec.id,
                source=src,
                source_rank=rank_for_source(src),
                evidence_file=d.get("evidence_file"),
                evidence_line=d.get("evidence_line"),
                evidence_commit=_first_commit(d),
                source_quote=_evidence_quote(d),
                confidence=d.get("confidence", 0.5),
                verification=d.get("verification", "unverified"),
            )

        # Re-derive headline confidence + verification from the FULL evidence
        # set (existing + just-added), so corroboration accrues across runs.
        _rederive_headline(rec, await list_decision_evidence(session, rec.id))
        rec.updated_at = _now_utc()
        touched_ids.append(rec.id)

        # Mirror the JSON file/module arrays into first-class decision→code
        # links so the graph is traversable both directions (Phase 3A). The
        # JSON stays the cheap read cache; these rows are the queryable truth.
        await sync_decision_node_links(
            session,
            repository_id,
            rec.id,
            files=json.loads(rec.affected_files_json or "[]"),
            modules=json.loads(rec.affected_modules_json or "[]"),
        )

        # (Re-)embed the record into the shared store so it's matchable by
        # later groups in this batch + future runs, and discoverable via
        # search_codebase. Best-effort — never blocks the SQL upsert. In
        # batch mode the write is deferred to one batched upsert after the
        # loop; until then the group vector stands in for the record in the
        # pending index — but only when the record's final text IS the group
        # text (created, or promoted from this headline). A fold that kept
        # the record's own fields must not be matchable under the incoming
        # group's text.
        if vector_store is not None:
            if batch_mode:
                q_vec = group_vecs.get(norm)
                final_text = decision_match_text(rec.title, rec.decision or "")
                if (
                    q_vec is not None
                    and pending is not None
                    and rec.id not in pending.ids
                    and final_text == group_texts.get(norm)
                ):
                    pending.add(rec.id, q_vec)
            else:
                await upsert_decision_vector(
                    vector_store,
                    rec.id,
                    title=rec.title,
                    decision=rec.decision or "",
                    evidence_file=rec.evidence_file,
                )

    # Batched store write: upsert every touched record's *final* text (post
    # merge/promotion) in one go, reusing the vectors already computed for
    # unchanged texts and embedding only the delta.
    if vector_store is not None and batch_mode and touched_ids:
        items: list[tuple[str, str, dict]] = []
        for rid in dict.fromkeys(touched_ids):
            rec = id_to_rec.get(rid)
            if rec is None:
                continue
            item = decision_vector_item(
                rec.id,
                title=rec.title,
                decision=rec.decision or "",
                evidence_file=rec.evidence_file,
            )
            if item is not None:
                items.append(item)
        vectors_by_text = {
            text: group_vecs[norm] for norm, text in group_texts.items() if norm in group_vecs
        }
        await upsert_decision_vectors(vector_store, items, vectors_by_text=vectors_by_text)

    await session.flush()
    return touched_ids


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
        )
    )
    ids = [row[0] for row in result.all()]
    if not ids:
        return 0

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
    structlog.get_logger(__name__).info("decision_purge_by_source", source=source, deleted=len(ids))
    return len(ids)


async def _fill_git_meta_gaps(
    session: AsyncSession,
    repository_id: str,
    git_meta_map: dict[str, dict],
    wanted: set[str],
) -> dict[str, dict]:
    """``git_meta_map`` widened with persisted rows for the *wanted* paths.

    The caller's map is one run's git metadata, and on an incremental update
    that is only the files that changed in it. Staleness scoring reads a path
    with no entry as a file that is gone and scores it 1.00, so every decision
    over an untouched file went maximally stale for not having been touched.
    The persisted rows cover every file the index has seen, which is the set
    that answers "is this file still here"; a path missing from those too is
    genuinely untracked and still scores 1.00. The run's own entries win —
    they are this run's fresher numbers for the files it re-read.
    """
    missing = wanted - git_meta_map.keys()
    if not missing:
        return git_meta_map

    filled: dict[str, dict] = {}
    # Chunked so the IN clause stays under SQLite's bind-parameter ceiling.
    batch = sorted(missing)
    for start in range(0, len(batch), 500):
        rows = await session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repository_id,
                GitMetadata.file_path.in_(batch[start : start + 500]),
            )
        )
        for row in rows.scalars().all():
            filled[row.file_path] = {
                col.name: getattr(row, col.name)
                for col in row.__table__.columns
                if col.name not in ("id", "repository_id")
            }
    filled.update(git_meta_map)
    return filled


async def recompute_decision_staleness(
    session: AsyncSession,
    repository_id: str,
    git_meta_map: dict[str, dict],
) -> int:
    """Recompute staleness_score for all active decisions. Returns update count.

    Also re-derives ``affected_modules_json`` from the files each record names.
    The two belong in one pass because they are one repair: a record's module
    linkage used to be the first path segment, which in a ``packages/`` layout
    made almost every record claim ``packages`` or ``tests``, and the rows that
    predate the fix carry it. Deriving here rather than in a data migration
    keeps the single-repair-path rule — the one this repo already learned when
    an alembic migration and a runtime repair disagreed about confidence.
    """
    result = await session.execute(
        select(DecisionRecord).where(
            DecisionRecord.repository_id == repository_id,
            DecisionRecord.status.in_(["active", "proposed"]),
        )
    )
    decisions = list(result.scalars().all())

    affected_by_id: dict[str, list[str]] = {}
    for dec in decisions:
        affected = json.loads(dec.affected_files_json)
        if affected:
            affected_by_id[dec.id] = affected

    modules_updated = _backfill_module_nodes(decisions, affected_by_id)
    if not affected_by_id:
        if modules_updated:
            await session.flush()
        return 0

    git_meta_map = await _fill_git_meta_gaps(
        session,
        repository_id,
        git_meta_map,
        {fp for paths in affected_by_id.values() for fp in paths},
    )

    now = _now_utc()
    updated = 0
    for dec in decisions:
        affected = affected_by_id.get(dec.id)
        if not affected:
            continue

        from repowise.core.analysis.decision_extractor import DecisionExtractor

        new_score = DecisionExtractor.compute_staleness(
            dec.created_at,
            affected,
            git_meta_map,
        )
        if abs(new_score - dec.staleness_score) > 0.01:
            dec.staleness_score = round(new_score, 3)
            dec.updated_at = now
            updated += 1

    if updated or modules_updated:
        await session.flush()
    # Deliberately the staleness count alone. The callers print this as
    # "N decisions rescored"; folding a silent module repair into it would
    # report a rescore that did not happen.
    return updated


def _backfill_module_nodes(
    decisions: list[DecisionRecord],
    affected_by_id: dict[str, list[str]],
) -> int:
    """Re-derive each record's module linkage from its files. Returns rows moved.

    Records naming no file are left alone: there is nothing to derive from, and
    an invented scope is worse than an absent one.
    """
    from repowise.core.analysis.decisions.scope import resolve_module_nodes

    moved = 0
    for dec in decisions:
        affected = affected_by_id.get(dec.id)
        if not affected:
            continue
        derived = resolve_module_nodes(affected)
        if derived != json.loads(dec.affected_modules_json or "[]"):
            dec.affected_modules_json = json.dumps(derived)
            moved += 1
    return moved


async def get_stale_decisions(
    session: AsyncSession,
    repository_id: str,
    threshold: float = 0.5,
) -> list[DecisionRecord]:
    """Return active decisions with staleness_score >= threshold."""
    result = await session.execute(
        select(DecisionRecord).where(
            DecisionRecord.repository_id == repository_id,
            DecisionRecord.status.in_(["active"]),
            DecisionRecord.staleness_score >= threshold,
        )
    )
    return list(result.scalars().all())


async def get_decision_health_summary(
    session: AsyncSession,
    repository_id: str,
) -> dict:
    """Return decision health: counts by status, stale decisions, ungoverned hotspots."""
    result = await session.execute(
        select(DecisionRecord).where(
            DecisionRecord.repository_id == repository_id,
        )
    )
    all_decisions = list(result.scalars().all())

    counts = {
        "active": 0,
        "proposed": 0,
        "deprecated": 0,
        "superseded": 0,
        "dismissed": 0,
        "stale": 0,
        # Active records naming no file. They score 0.0 because the staleness
        # question cannot be asked of them, which renders identically to a
        # record whose code genuinely has not moved — so they are counted
        # separately rather than banked as fresh.
        "unscoped": 0,
    }
    stale_decisions: list[DecisionRecord] = []
    proposed_decisions: list[DecisionRecord] = []

    # Collect all governed files from active decisions
    governed_files: set[str] = set()
    for d in all_decisions:
        counts[d.status] = counts.get(d.status, 0) + 1
        if d.status == "active":
            if d.staleness_score >= 0.5:
                counts["stale"] += 1
                stale_decisions.append(d)
            affected_files = json.loads(d.affected_files_json)
            if not affected_files:
                counts["unscoped"] += 1
            for fp in affected_files:
                governed_files.add(fp)
        elif d.status == "proposed":
            proposed_decisions.append(d)

    # Find ungoverned hotspots
    hotspot_result = await session.execute(
        select(GitMetadata.file_path).where(
            GitMetadata.repository_id == repository_id,
            GitMetadata.is_hotspot == True,  # noqa: E712
        )
    )
    hotspot_files = {row[0] for row in hotspot_result.all()}
    ungoverned = sorted(hotspot_files - governed_files)

    # Phase 3B: surface contradictory active decisions (conflicts_with edges).
    from ..decision_graph import list_conflict_edges

    by_id = {d.id: d for d in all_decisions}
    conflicts: list[dict] = []
    for edge in await list_conflict_edges(session, repository_id):
        src = by_id.get(edge.src_decision_id)
        dst = by_id.get(edge.dst_decision_id)
        if src is None or dst is None:
            continue
        conflicts.append(
            {
                "src": {"id": src.id, "title": src.title, "status": src.status},
                "dst": {"id": dst.id, "title": dst.title, "status": dst.status},
                "confidence": edge.confidence,
                "evidence": edge.evidence,
            }
        )
    counts["conflicts"] = len(conflicts)

    return {
        "summary": counts,
        "stale_decisions": stale_decisions,
        "proposed_awaiting_review": proposed_decisions,
        "ungoverned_hotspots": ungoverned,
        "conflicts": conflicts,
    }
