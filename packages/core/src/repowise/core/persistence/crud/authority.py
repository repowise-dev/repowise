"""Acceptance, candidate review, and id continuity.

The write side of the three-entity contract. ``decisions.py`` owns extraction's
accretion path and knows nothing about authority; everything that turns a
candidate into a decision, or moves one through review, is here.

One rule shapes the module: :func:`record_acceptance` is the only writer of
``decision_acceptances``, and it refuses anything the acceptance contract does
not cover. Every review action goes through it, so no caller can invent a
shortcut past the reason/scope/evidence/identity requirement.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decisions.lifecycle import (
    ACCEPTANCE_ACTIONS,
    STORED_CURRENCIES,
    AcceptanceRequirement,
    acceptance_blockers,
    effective_currency,
    legacy_status_for_currency,
)

from ..decision_graph import upsert_decision_edge
from ..models import (
    DecisionAcceptance,
    DecisionAlias,
    DecisionCandidateMeta,
    DecisionRecord,
    _now_utc,
)

__all__ = [
    "ACCEPTED_SQL_PREDICATE",
    "AcceptanceRefusedError",
    "accept_decision",
    "accepted_decision_ids",
    "accepted_predicate",
    "current_currency",
    "dismiss_candidate",
    "is_accepted",
    "latest_acceptance",
    "list_candidates",
    "merge_candidate",
    "reaffirm_decision",
    "record_acceptance",
    "request_split",
    "resolve_decision_id",
    "return_to_review",
    "supersede_decision",
    "upsert_candidate_meta",
]


#: Retries for the read-then-write ``seq`` allocation. Two is enough for the
#: only contention this has: two acceptances of one decision landing together.
_SEQ_RETRIES = 3


class AcceptanceRefusedError(ValueError):
    """An acceptance was attempted without what acceptance requires.

    Carries the individual blockers so a caller can tell the user which of the
    four is missing rather than restating the whole contract.
    """

    def __init__(self, blockers: list[str]) -> None:
        self.blockers = blockers
        super().__init__("; ".join(blockers))


# ---------------------------------------------------------------------------
# Reading authority
# ---------------------------------------------------------------------------


def accepted_predicate() -> Any:
    """A ``WHERE`` clause restricting a ``DecisionRecord`` query to decisions.

    Every governance read adds this. Written once rather than per caller
    because the previous arrangement — each surface spelling out its own
    ``status == "active"`` — is how the same question came to have four
    different answers across the CLI, the MCP tools and two web surfaces.
    """
    return select(DecisionAcceptance.id).where(
        DecisionAcceptance.decision_id == DecisionRecord.id
    ).exists()


#: The same predicate for the hook path, which opens the store with stdlib
#: sqlite3 and cannot import the ORM. Correlated on ``decision_records.id``, so
#: it drops into an existing ``WHERE`` unchanged.
ACCEPTED_SQL_PREDICATE = (
    "EXISTS (SELECT 1 FROM decision_acceptances a "
    "WHERE a.decision_id = decision_records.id)"
)


async def latest_acceptance(
    session: AsyncSession, decision_id: str
) -> DecisionAcceptance | None:
    """The acceptance row that currently governs *decision_id*, if any.

    ``None`` means the record is a candidate. This is the one predicate that
    separates the two entities; every governance read is a join onto it.
    """
    result = await session.execute(
        select(DecisionAcceptance)
        .where(DecisionAcceptance.decision_id == decision_id)
        .order_by(DecisionAcceptance.seq.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def is_accepted(session: AsyncSession, decision_id: str) -> bool:
    """Whether *decision_id* has ever been accepted."""
    return await latest_acceptance(session, decision_id) is not None


async def accepted_decision_ids(
    session: AsyncSession,
    repository_id: str,
    *,
    governing_only: bool = False,
) -> set[str]:
    """Ids of every accepted decision in *repository_id*.

    With *governing_only*, drops the ones whose authority has been withdrawn
    (``superseded``, ``dismissed``) so the caller is left with what still binds.
    The subquery picks the highest ``seq`` per decision, which is the append-only
    log's way of saying "current".
    """
    latest_seq = (
        select(
            DecisionAcceptance.decision_id.label("did"),
            func.max(DecisionAcceptance.seq).label("seq"),
        )
        .where(DecisionAcceptance.repository_id == repository_id)
        .group_by(DecisionAcceptance.decision_id)
        .subquery()
    )
    q = select(DecisionAcceptance.decision_id, DecisionAcceptance.currency).join(
        latest_seq,
        (DecisionAcceptance.decision_id == latest_seq.c.did)
        & (DecisionAcceptance.seq == latest_seq.c.seq),
    )
    rows = (await session.execute(q)).all()
    if not governing_only:
        return {did for did, _ in rows}
    return {did for did, currency in rows if currency not in ("superseded", "dismissed")}


async def current_currency(session: AsyncSession, record: DecisionRecord) -> str | None:
    """Effective currency for *record*, or ``None`` if it is a candidate."""
    acceptance = await latest_acceptance(session, record.id)
    if acceptance is None:
        return None
    has_scope = bool(_record_scope(record))
    return effective_currency(
        acceptance.currency,
        has_scope=has_scope,
        staleness=record.staleness_score,
    )


async def resolve_decision_id(session: AsyncSession, decision_id: str) -> str | None:
    """Resolve an id that may be a retired alias to the live record's id.

    Merging and superseding retire ids that are already in circulation — in a
    committed manifest, in an agent's notes. Every id-taking entry point routes
    through here so those keep resolving instead of reading as deleted.
    """
    alias = await session.get(DecisionAlias, decision_id)
    if alias is not None and alias.reason == "merged":
        # A merged candidate's row survives, because its evidence is worth
        # keeping, so the record still resolves by id. Following the alias is
        # what stops the same constraint being accepted a second time under the
        # id that was folded away. A superseded decision keeps resolving to
        # itself: it is still a decision, and its history is what you asked for.
        return alias.decision_id
    if await session.get(DecisionRecord, decision_id) is not None:
        return decision_id
    return alias.decision_id if alias is not None else None


# ---------------------------------------------------------------------------
# Writing authority
# ---------------------------------------------------------------------------


def _json_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value or "[]")
        except (TypeError, ValueError):
            return []
        return [str(v) for v in parsed] if isinstance(parsed, list) else []
    return [str(v) for v in (value or [])]


def _record_scope(record: DecisionRecord) -> list[str]:
    return _json_list(record.affected_files_json) or _json_list(
        record.affected_modules_json
    )


def _record_evidence(record: DecisionRecord) -> list[str]:
    evidence = _json_list(record.evidence_commits_json)
    if record.evidence_file:
        evidence.append(record.evidence_file)
    return evidence


async def record_acceptance(
    session: AsyncSession,
    record: DecisionRecord,
    *,
    action: str,
    currency: str,
    reason: str = "",
    scope: list[str] | None = None,
    evidence: list[str] | None = None,
    accepter: str = "",
    artifact: str = "",
    note: str = "",
) -> DecisionAcceptance:
    """Append one acceptance row for *record*, or refuse.

    The only writer of ``decision_acceptances``. *reason*, *scope* and
    *evidence* default to what the record already carries, so an accept of a
    well-formed candidate needs no arguments beyond the accepter; a record
    missing any of them is refused with the specific gap named rather than
    accepted with a blank.
    """
    if action not in ACCEPTANCE_ACTIONS:
        raise ValueError(f"Unknown acceptance action {action!r}.")
    if currency not in STORED_CURRENCIES:
        raise ValueError(f"Unknown stored currency {currency!r}.")

    # A record somebody typed is its own provenance: the accepter did not read
    # an inference, they wrote the claim. Everything mined from a transcript, a
    # commit or a document still has to say what it rests on.
    self_authored = record.source == "cli" and bool(accepter.strip())
    resolved_evidence = evidence if evidence is not None else _record_evidence(record)
    if not resolved_evidence and self_authored:
        resolved_evidence = [f"accepted by {accepter}"]

    req = AcceptanceRequirement(
        reason=reason or record.rationale or record.decision,
        scope=scope if scope is not None else _record_scope(record),
        evidence=resolved_evidence,
        accepter=accepter,
        artifact=artifact,
        self_authored=self_authored,
    )
    blockers = acceptance_blockers(req)
    if blockers:
        raise AcceptanceRefusedError(blockers)

    acceptance = await _append_acceptance(
        session,
        record,
        action=action,
        currency=currency,
        req=req,
        accepter=accepter,
        artifact=artifact,
        note=note,
    )

    # The legacy column is a projection of the acceptance, never its source.
    # Keeping it in step is what lets readers that predate the split stay
    # correct instead of merely stale.
    record.status = legacy_status_for_currency(currency)
    record.updated_at = _now_utc()
    await session.flush()
    return acceptance


async def _append_acceptance(
    session: AsyncSession,
    record: DecisionRecord,
    *,
    action: str,
    currency: str,
    req: AcceptanceRequirement,
    accepter: str,
    artifact: str,
    note: str,
) -> DecisionAcceptance:
    """Insert the next log row, retrying the sequence on a concurrent append.

    ``seq`` is allocated read-then-write, so two acceptances of one decision
    landing together both read the same maximum and one loses the unique
    constraint. Retrying is the whole fix: the loser re-reads and takes the
    next number, which is what serializing them would have produced anyway.
    """
    for attempt in range(_SEQ_RETRIES):
        next_seq = (
            await session.execute(
                select(func.coalesce(func.max(DecisionAcceptance.seq), 0)).where(
                    DecisionAcceptance.decision_id == record.id
                )
            )
        ).scalar_one() + 1
        acceptance = DecisionAcceptance(
            repository_id=record.repository_id,
            decision_id=record.id,
            seq=next_seq,
            action=action,
            currency=currency,
            reason=req.reason,
            scope_json=json.dumps(list(req.scope)),
            evidence_json=json.dumps(list(req.evidence)),
            accepter=accepter,
            artifact=artifact,
            note=note,
        )
        session.add(acceptance)
        try:
            await session.flush()
        except IntegrityError:
            if attempt == _SEQ_RETRIES - 1:
                raise
            await session.rollback()
            continue
        return acceptance
    raise RuntimeError("unreachable")  # pragma: no cover


async def accept_decision(
    session: AsyncSession,
    record: DecisionRecord,
    *,
    accepter: str = "",
    artifact: str = "",
    reason: str = "",
    scope: list[str] | None = None,
    evidence: list[str] | None = None,
    note: str = "",
) -> DecisionAcceptance:
    """Accept a candidate, optionally editing its reason and scope on the way.

    The edit is applied to the record too: accepting a claim with a corrected
    scope must not leave the record describing the uncorrected one. *evidence*
    supplies what a candidate that cites nothing is missing, and is recorded on
    the acceptance rather than rewritten onto the record: it is what the
    accepter went on, not something extraction found.
    """
    meta = await session.get(DecisionCandidateMeta, record.id)
    if meta is not None and meta.review_state == "merged":
        raise AcceptanceRefusedError(
            [f"already merged into {meta.merged_into or 'another decision'}"]
        )
    if reason:
        record.rationale = reason
    if scope is not None:
        record.affected_files_json = json.dumps(scope)
    acceptance = await record_acceptance(
        session,
        record,
        action="accepted",
        currency="active",
        reason=reason,
        scope=scope,
        evidence=evidence,
        accepter=accepter,
        artifact=artifact,
        note=note,
    )
    await _set_review_state(session, record, "accepted")
    return acceptance


async def reaffirm_decision(
    session: AsyncSession,
    record: DecisionRecord,
    *,
    accepter: str = "",
    artifact: str = "",
    note: str = "",
) -> DecisionAcceptance:
    """Re-accept a decision after review, clearing a ``needs_review`` state."""
    return await record_acceptance(
        session,
        record,
        action="reaffirmed",
        currency="active",
        accepter=accepter,
        artifact=artifact,
        note=note,
    )


async def return_to_review(
    session: AsyncSession,
    record: DecisionRecord,
    *,
    accepter: str = "",
    artifact: str = "",
    note: str = "",
) -> DecisionAcceptance:
    """Send an accepted decision back to review without erasing its history.

    Appends rather than deletes, so the record keeps the acceptance that made it
    govern in the first place. It remains a decision, not a candidate: it was
    accepted once, and pretending otherwise would lose that.
    """
    return await record_acceptance(
        session,
        record,
        action="returned_to_review",
        currency="needs_review",
        accepter=accepter,
        artifact=artifact,
        note=note,
    )


async def supersede_decision(
    session: AsyncSession,
    record: DecisionRecord,
    *,
    successor_id: str,
    accepter: str = "",
    artifact: str = "",
    note: str = "",
) -> DecisionAcceptance:
    """Retire *record* in favour of *successor_id*, with an explicit edge.

    Similarity never reaches here: an edge is written because someone named the
    successor. The retired id becomes an alias of the successor so references to
    it keep resolving to the decision that now governs.
    """
    successor = await session.get(DecisionRecord, successor_id)
    if successor is None:
        raise ValueError(f"Unknown successor decision {successor_id!r}.")
    if successor.id == record.id:
        raise ValueError("A decision cannot supersede itself.")

    acceptance = await record_acceptance(
        session,
        record,
        action="superseded",
        currency="superseded",
        accepter=accepter,
        artifact=artifact,
        note=note,
    )
    record.superseded_by = successor.id
    await upsert_decision_edge(
        session,
        repository_id=record.repository_id,
        src_decision_id=successor.id,
        dst_decision_id=record.id,
        kind="supersedes",
        confidence=1.0,
        evidence=f"superseded by {accepter or artifact}",
    )
    await _add_alias(session, record.id, successor.id, reason="superseded")
    return acceptance


async def merge_candidate(
    session: AsyncSession,
    candidate: DecisionRecord,
    *,
    into_id: str,
    accepter: str = "",
    artifact: str = "",
    note: str = "",
) -> DecisionAcceptance:
    """Fold *candidate* into an existing decision instead of accepting it twice.

    The target must already be a decision: merging into a candidate would create
    authority out of two things that have none. The candidate's evidence stays
    on its own row and its id becomes an alias of the target.
    """
    target = await session.get(DecisionRecord, into_id)
    if target is None:
        raise ValueError(f"Unknown merge target {into_id!r}.")
    if target.id == candidate.id:
        raise ValueError("A candidate cannot be merged into itself.")
    if not await is_accepted(session, target.id):
        raise ValueError(
            f"Merge target {into_id!r} is a candidate; accept it before merging into it."
        )
    if await is_accepted(session, candidate.id):
        raise ValueError(
            f"{candidate.id} is an accepted decision, not a candidate. "
            "Supersede it instead, so the retirement is recorded."
        )

    acceptance = await record_acceptance(
        session,
        target,
        action="merged",
        currency="active",
        evidence=_record_evidence(target) + _record_evidence(candidate),
        accepter=accepter,
        artifact=artifact,
        note=note or f"merged candidate {candidate.id}",
    )
    await _set_review_state(session, candidate, "merged", merged_into=target.id)
    await _add_alias(session, candidate.id, target.id, reason="merged")
    return acceptance


# ---------------------------------------------------------------------------
# Candidate review
# ---------------------------------------------------------------------------


async def upsert_candidate_meta(
    session: AsyncSession,
    record: DecisionRecord,
    *,
    lane: str = "",
    grounding: dict[str, Any] | None = None,
    extractor_version: str = "",
    review_priority: float | None = None,
    needs_split: bool | None = None,
    scope_unresolved: bool | None = None,
) -> DecisionCandidateMeta:
    """Create or refresh the review row for a candidate.

    Never touches ``review_state``: a re-extraction refreshing grounding must
    not resurrect a dismissed candidate, which is the whole point of the
    tombstone. State moves only through the review actions below.
    """
    meta = await session.get(DecisionCandidateMeta, record.id)
    if meta is None:
        meta = DecisionCandidateMeta(
            decision_id=record.id,
            repository_id=record.repository_id,
            review_state="open",
        )
        session.add(meta)
    if lane:
        meta.lane = lane
    if grounding is not None:
        meta.grounding_json = json.dumps(grounding, sort_keys=True)
    if extractor_version:
        meta.extractor_version = extractor_version
    if review_priority is not None:
        meta.review_priority = review_priority
    if needs_split is not None:
        meta.needs_split = needs_split
    if scope_unresolved is not None:
        meta.scope_unresolved = scope_unresolved
    meta.last_seen = _now_utc()
    await session.flush()
    return meta


async def dismiss_candidate(
    session: AsyncSession,
    record: DecisionRecord,
    *,
    reason: str = "",
    accepter: str = "",
) -> DecisionCandidateMeta:
    """Tombstone a candidate so re-extraction never proposes it again.

    On a record that *was* accepted this is a withdrawal, and a withdrawal is an
    authority change: it appends to the acceptance log rather than only flipping
    the status column. Without that row the manifest would keep exporting the
    decision as governing, and a colleague's import would undo the dismissal.
    """
    meta = await _set_review_state(session, record, "dismissed")
    meta.dismissed_reason = reason
    if await latest_acceptance(session, record.id) is not None:
        await record_acceptance(
            session,
            record,
            action="dismissed",
            currency="dismissed",
            accepter=accepter or "dismissed",
            note=reason,
        )
    record.status = "dismissed"
    record.updated_at = _now_utc()
    await session.flush()
    return meta


async def request_split(
    session: AsyncSession,
    record: DecisionRecord,
    *,
    reason: str = "",
) -> DecisionCandidateMeta:
    """Flag a candidate as bundling two choices. Never splits it by machine."""
    meta = await _set_review_state(session, record, "needs_split")
    meta.needs_split = True
    meta.dismissed_reason = reason
    await session.flush()
    return meta


async def list_candidates(
    session: AsyncSession,
    repository_id: str,
    *,
    review_state: str | None = "open",
    lane: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[tuple[DecisionRecord, DecisionCandidateMeta | None]]:
    """Records with no acceptance, newest and highest-priority first.

    The left join keeps candidates that predate the review table visible; the
    ``NOT IN`` is what makes this a candidate read rather than a status read.
    """
    accepted = select(DecisionAcceptance.decision_id).where(
        DecisionAcceptance.repository_id == repository_id
    )
    q = (
        select(DecisionRecord, DecisionCandidateMeta)
        .outerjoin(
            DecisionCandidateMeta,
            DecisionCandidateMeta.decision_id == DecisionRecord.id,
        )
        .where(
            DecisionRecord.repository_id == repository_id,
            DecisionRecord.id.notin_(accepted),
        )
    )
    if review_state is not None:
        # A candidate with no meta row has never been reviewed, which is
        # exactly what ``open`` means.
        if review_state == "open":
            q = q.where(
                (DecisionCandidateMeta.review_state == "open")
                | (DecisionCandidateMeta.decision_id.is_(None))
            )
        else:
            q = q.where(DecisionCandidateMeta.review_state == review_state)
    if lane is not None:
        q = q.where(DecisionCandidateMeta.lane == lane)
    q = q.order_by(
        DecisionCandidateMeta.review_priority.desc().nullslast(),
        DecisionRecord.confidence.desc(),
        DecisionRecord.created_at.desc(),
    ).limit(limit).offset(offset)
    return [(rec, meta) for rec, meta in (await session.execute(q)).all()]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _set_review_state(
    session: AsyncSession,
    record: DecisionRecord,
    state: str,
    *,
    merged_into: str | None = None,
) -> DecisionCandidateMeta:
    meta = await session.get(DecisionCandidateMeta, record.id)
    if meta is None:
        meta = DecisionCandidateMeta(
            decision_id=record.id, repository_id=record.repository_id
        )
        session.add(meta)
    meta.review_state = state
    if merged_into is not None:
        meta.merged_into = merged_into
    meta.last_seen = _now_utc()
    await session.flush()
    return meta


async def _add_alias(
    session: AsyncSession, alias_id: str, decision_id: str, *, reason: str
) -> None:
    # An alias chain would leave a two-hop lookup nobody performs, so anything
    # already pointing at the retired id is repointed at its successor first —
    # for the update branch as much as the insert one, or A -> B followed by
    # B -> C strands A at an id that no longer resolves.
    stale = (
        (
            await session.execute(
                select(DecisionAlias).where(DecisionAlias.decision_id == alias_id)
            )
        )
        .scalars()
        .all()
    )
    for row in stale:
        row.decision_id = decision_id

    existing = await session.get(DecisionAlias, alias_id)
    if existing is not None:
        existing.decision_id = decision_id
        existing.reason = reason
        await session.flush()
        return
    record = await session.get(DecisionRecord, decision_id)
    session.add(
        DecisionAlias(
            alias_id=alias_id,
            repository_id=record.repository_id if record else "",
            decision_id=decision_id,
            reason=reason,
        )
    )
    await session.flush()
