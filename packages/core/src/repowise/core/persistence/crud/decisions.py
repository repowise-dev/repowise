"""CRUD operations for the decisions domain (repowise persistence layer).

Split out of the former monolithic ``crud.py``; ``crud/__init__.py`` re-exports
every public name, so existing imports are unaffected. Single-record reads and
writes live here; the ``decision_*`` siblings hold the rest and are re-exported.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decisions.lifecycle import DECISION_STATUS_ORDER, RETIRED_STATUSES
from repowise.core.analysis.decisions.provenance import compute_confidence, rank_for_source
from repowise.core.analysis.decisions.scope import SCOPE_BASIS_STATED

from ..decision_graph import scope_modules, sync_links_from_record
from ..models import DecisionCandidateMeta, DecisionRecord, _now_utc
from .authority import accepted_predicate

# Re-exported: these moved to sibling modules and are still imported from here.
from .decision_evidence import (  # noqa: F401
    _json_list,
    _rederive_headline,
    list_decision_evidence,
    record_completeness,
)
from .decision_health import get_decision_health_summary  # noqa: F401
from .decision_identity import (  # noqa: F401
    _extraction_kind,
    _extraction_status,
    _merge_status,
    derive_decision_id,
    identity_quote_for,
)
from .decision_ingest import bulk_upsert_decisions  # noqa: F401
from .decision_repairs import (  # noqa: F401
    purge_proposed_decisions_by_source,
    purge_proposed_decisions_outside_files,
    reconcile_decision_confidence,
    reconcile_source_ranks,
    unretire_auto_superseded,
)
from .decision_review_meta import _write_candidate_meta
from .decision_staleness import get_stale_decisions, recompute_decision_staleness  # noqa: F401

# ---------------------------------------------------------------------------
# DecisionRecord CRUD
# ---------------------------------------------------------------------------

_VALID_DECISION_STATUSES = frozenset(
    {"proposed", "active", "deprecated", "superseded", "dismissed"}
)


def _dedup_query(
    repository_id: str,
    title: str,
    *,
    source: str,
    evidence_file: str | None,
) -> Any:
    """The identity :func:`upsert_decision` deduplicates on.

    ``evidence_file`` may be NULL, which no equality test matches, so the two
    cases are separate clauses. Shared with :func:`find_decision_by_title` so a
    caller can ask what an upsert would land on before it lands on it.
    """
    q = select(DecisionRecord).where(
        DecisionRecord.repository_id == repository_id,
        DecisionRecord.title == title,
        DecisionRecord.source == source,
    )
    if evidence_file is not None:
        return q.where(DecisionRecord.evidence_file == evidence_file)
    return q.where(DecisionRecord.evidence_file.is_(None))


async def find_decision_by_title(
    session: AsyncSession,
    repository_id: str,
    title: str,
    *,
    source: str = "cli",
    evidence_file: str | None = None,
) -> DecisionRecord | None:
    """The record an :func:`upsert_decision` with these arguments would update.

    Exists so a caller can tell "this creates a record" from "this overwrites
    one" before writing. The create endpoint needs the difference: overwriting
    an accepted decision with a scopeless body would clear the scope it governs
    and leave its acceptance row behind.
    """
    result = await session.execute(
        _dedup_query(repository_id, title, source=source, evidence_file=evidence_file)
    )
    return result.scalar_one_or_none()


async def upsert_decision(
    session: AsyncSession,
    *,
    repository_id: str,
    title: str,
    status: str = "proposed",
    kind: str | None = None,
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
    confidence: float | None = None,
    verification: str = "unverified",
    last_code_change: datetime | None = None,
    staleness_score: float = 0.0,
    superseded_by: str | None = None,
    decision_id: str | None = None,
) -> DecisionRecord:
    """Create or update a decision record.

    Dedup key: ``(repository_id, title, source, evidence_file)``.

    This is the manual-entry path, the CLI's ``decision add`` and the HTTP
    create route. It writes no evidence rows, so nothing re-derives the score
    later unless a mined decision with the same normalised title lands on the
    record and brings evidence with it. ``confidence=None`` therefore scores
    it here. Both call sites used to pass a literal ``1.0``, which is above
    the formula's own ``0.99`` ceiling and so was never a score at all.

    ``kind`` is written only when it is given. ``None`` leaves an existing
    record's noun alone rather than defaulting it back to ``architectural``:
    a caller that does not know about the split must not silently un-agree an
    accepted agreement by re-stating the record without it.
    """
    # Normalise text fields — LLM extractors may return explicit None
    rationale = rationale or ""
    context = context or ""
    decision = decision or ""

    if confidence is None:
        # Full rank credit, and no completeness term: a person wrote this, and
        # how many prompts they answered is not evidence about whether the
        # decision holds. Scored as verified for the same reason, since the
        # decay it skips discounts a quote that may be hallucinated and this
        # path takes no quote. ``verification`` still stores what it was given.
        confidence = compute_confidence(rank_for_source(source), 1, "exact")

    async def _restate(rec: DecisionRecord) -> DecisionRecord:
        """Write this call's body onto an existing record.

        Shared by the two ways of finding one: the title dedupe query, and
        the derived id, which catches a second wording of the same decision
        that the title query cannot see. ``identity_quote`` is not among the
        fields, because it is pinned at first capture.
        """
        rec.status = status
        if kind is not None:
            rec.kind = _extraction_kind(kind)
        rec.context = context
        rec.decision = decision
        rec.rationale = rationale
        rec.alternatives_json = json.dumps(alternatives or [])
        rec.consequences_json = json.dumps(consequences or [])
        rec.affected_files_json = json.dumps(affected_files or [])
        # The caller supplied these files, so they are its claim and not
        # any footprint the row carried.
        rec.scope_basis = SCOPE_BASIS_STATED if affected_files else ""
        rec.affected_modules_json = json.dumps(
            scope_modules(affected_files or [], affected_modules)
        )
        rec.tags_json = json.dumps(tags or [])
        # ``None`` leaves the commits alone, like ``kind`` above: a caller
        # restating a record without naming them has not disowned them, and
        # the capture hook's suppression reads this column — wiping it asks
        # the agent again for a decision it has already recorded.
        if evidence_commits is not None:
            rec.evidence_commits_json = json.dumps(evidence_commits)
        rec.evidence_line = evidence_line
        rec.confidence = confidence
        rec.verification = verification
        rec.last_code_change = last_code_change
        rec.staleness_score = staleness_score
        rec.superseded_by = superseded_by
        rec.updated_at = _now_utc()
        await session.flush()
        await sync_links_from_record(session, rec)
        await _write_candidate_meta(session, repository_id, {}, only={rec.id})
        return rec

    q = _dedup_query(
        repository_id, title, source=source, evidence_file=evidence_file
    )

    # No write path creates authority. ``active`` here would be a status a
    # caller asserted rather than an acceptance anyone performed; the caller
    # accepts explicitly afterwards if it means to.
    status = _extraction_status(status)

    result = await session.execute(q)
    existing = result.scalar_one_or_none()

    if existing is not None:
        return await _restate(existing)

    # No quote reaches this path: it is manual entry and the CLI's ``decision
    # add``, where the decision text is the only verbatim thing the person
    # wrote. Pinned once, like every other capture path.
    identity_quote = decision or title
    # An explicit id still wins: the manifest importer carries ids in from a
    # tracked file and those are the record's identity, not ours.
    derived = decision_id or derive_decision_id(
        repository_id,
        title,
        source=source,
        evidence_file=evidence_file,
        affected_files=affected_files or [],
        evidence_line=evidence_line,
        identity_quote=identity_quote,
    )
    # A title the dedupe query did not match can still be the same decision:
    # identity is the evidence, and two wordings of one choice derive one id.
    # Inserting over it would collide on the primary key.
    folded = await session.get(DecisionRecord, derived)
    if folded is not None and folded.repository_id == repository_id:
        return await _restate(folded)

    rec = DecisionRecord(
        id=derived,
        identity_quote=identity_quote,
        repository_id=repository_id,
        title=title,
        status=status,
        kind=_extraction_kind(kind),
        context=context,
        decision=decision,
        rationale=rationale,
        alternatives_json=json.dumps(alternatives or []),
        consequences_json=json.dumps(consequences or []),
        affected_files_json=json.dumps(affected_files or []),
        # Same claim as the restate arm: files the caller supplied are stated.
        scope_basis=SCOPE_BASIS_STATED if affected_files else "",
        affected_modules_json=json.dumps(
            scope_modules(affected_files or [], affected_modules)
        ),
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
    await sync_links_from_record(session, rec)
    await _write_candidate_meta(session, repository_id, {}, only={rec.id})
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
    accepted: bool | None = None,
    include_dismissed: bool = False,
    limit: int = 100,
    offset: int = 0,
    sort: str = "priority",
) -> list[DecisionRecord]:
    """Return decision records with optional filters.

    Dismissed records are tombstones and only show up when explicitly asked
    for via ``status="dismissed"``.

    ``accepted`` filters on the acceptance join rather than the status column:
    ``False`` is every candidate, ``True`` every decision. It is applied in SQL,
    before the page is cut, because a caller paging a lane needs the page to be
    of that lane. Filtering after the cut returned an empty Candidates page on a
    store whose first fifty rows by priority were all statused ``active``.

    ``include_dismissed`` keeps the tombstones in without narrowing to them.
    A candidate that was dismissed should stay hidden, but a *decision* that was
    accepted and then withdrawn is history somebody needs to be able to read,
    and ``dismiss_candidate`` writes ``status="dismissed"`` for both.

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
        if not include_dismissed:
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
    if accepted is not None:
        predicate = accepted_predicate()
        q = q.where(predicate if accepted else ~predicate)
    order = decision_priority_order(sort)
    if accepted is False and sort != "recent":
        # A candidates page is a review queue, so it leads with the rows the
        # acceptance contract would take rather than the highest-confidence
        # ones a reviewer cannot act on.
        q = q.outerjoin(
            DecisionCandidateMeta,
            DecisionCandidateMeta.decision_id == DecisionRecord.id,
        )
        order = (
            func.coalesce(DecisionCandidateMeta.review_priority, 0.0).desc(),
            *order,
        )
    q = q.order_by(*order).limit(limit).offset(offset)
    result = await session.execute(q)
    return list(result.scalars().all())


# Lower sorts first, from the one ladder in lifecycle. ``dismissed`` is in the
# ladder but not here: this rank also zero-fills ``count_decisions_by_status``,
# which excludes dismissed rows, so a key for it would report a count of zero
# for rows the query never looked at.
_STATUS_RANK = {
    status: rank
    for rank, status in enumerate(DECISION_STATUS_ORDER)
    if status != "dismissed"
}


def decision_priority_order(sort: str = "priority") -> tuple[Any, ...]:
    """ORDER BY terms for :func:`list_decisions`.

    Public because it is the order the Decisions page renders, and an agent
    surface that serves the same records in a different order is a divergence
    a reader has to reconcile by hand. ``get_context`` imports it rather than
    re-spelling the three terms, so the two cannot drift apart silently.
    """
    if sort == "recent":
        return (DecisionRecord.created_at.desc(),)
    rank = case(_STATUS_RANK, value=DecisionRecord.status, else_=len(DECISION_STATUS_ORDER))
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

    Each argument left as ``None`` is preserved, except that supplying files
    without modules re-derives the modules from those files: a scope whose two
    halves describe different code links the record to a module its files are
    not in. Pass an empty list to clear either. Returns the updated record, or
    ``None`` if the id was not found.
    """
    rec = await session.get(DecisionRecord, decision_id)
    if rec is None:
        return None
    if affected_files is not None:
        rec.affected_files_json = json.dumps(affected_files)
        # A scope set by hand is stated, whatever the row held before:
        # otherwise the new files are stored and then ignored everywhere.
        rec.scope_basis = SCOPE_BASIS_STATED
        # Modules follow the files they describe. Replacing one and keeping
        # the other links the record to a module its files are not in.
        affected_modules = scope_modules(affected_files, affected_modules)
    if affected_modules is not None:
        rec.affected_modules_json = json.dumps(affected_modules)
    rec.updated_at = _now_utc()
    await session.flush()
    await sync_links_from_record(session, rec)
    return rec


async def update_decision_status(
    session: AsyncSession,
    decision_id: str,
    status: str,
    *,
    superseded_by: str | None = None,
    accepter: str = "",
    kind: str = "person",
) -> DecisionRecord | None:
    """Move a decision record between statuses, recording authority changes.

    The status column is a projection, so a caller asking for ``active`` is
    asking for an acceptance and gets one, stamped with *accepter*; a caller
    retiring an accepted decision gets a withdrawal appended to the same log.
    Writing the column alone would leave the two disagreeing, which is how a
    dismissal survives in one surface and not another.

    Raises ValueError for an invalid status, and for an acceptance the record
    cannot support. Returns None if not found.
    """
    if status not in _VALID_DECISION_STATUSES:
        raise ValueError(
            f"Unknown decision status {status!r}. Valid values: {sorted(_VALID_DECISION_STATUSES)}"
        )
    rec = await session.get(DecisionRecord, decision_id)
    if rec is None:
        return None

    from .authority import (
        AcceptanceRefusedError,
        accept_decision,
        is_accepted,
        record_acceptance,
    )

    accepted = await is_accepted(session, rec.id)
    try:
        if status == "active":
            if not accepted:
                await accept_decision(
                    session, rec, accepter=accepter or "unrecorded", kind=kind
                )
        elif accepted and status in RETIRED_STATUSES:
            await record_acceptance(
                session,
                rec,
                action="superseded" if status == "superseded" else "dismissed",
                currency="superseded" if status == "superseded" else "dismissed",
                accepter=accepter or "unrecorded",
                kind=kind,
            )
    except AcceptanceRefusedError as exc:
        raise ValueError(str(exc)) from exc

    rec.status = status
    if superseded_by is not None:
        rec.superseded_by = superseded_by
    rec.updated_at = _now_utc()
    # Both directions: a retirement drops the links and a revival rebuilds
    # them, rather than either waiting for the next index.
    await sync_links_from_record(session, rec)
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
    if "affected_files" in fields:
        # Same rule as every other path that takes a scope from its caller:
        # the basis and the modules have to move with the files they describe.
        rec.scope_basis = SCOPE_BASIS_STATED if fields["affected_files"] else ""
        fields["affected_modules"] = scope_modules(
            fields["affected_files"], fields.get("affected_modules")
        )
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
    await sync_links_from_record(session, rec)
    return rec


async def delete_decision(session: AsyncSession, decision_id: str) -> bool:
    """Delete a decision record. Returns True if deleted, False if not found."""
    rec = await session.get(DecisionRecord, decision_id)
    if rec is None:
        return False
    await session.delete(rec)
    await session.flush()
    return True
