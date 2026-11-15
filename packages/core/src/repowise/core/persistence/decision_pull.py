"""Mirror the decisions in force on repowise.dev into the local store.

A pulled decision is one the repository's copy on repowise.dev holds and this
store did not record: learned by the PR bot from a reviewer's reply, recorded
through the hosted ``decision`` tool, or pushed from another machine. Each
lands under its hosted id with an acceptance signed ``import``, the hosted
accepter named, and :data:`PULL_ARTIFACT` as the artifact, the same shape a
``decisions.yaml`` import writes. That artifact is also how a later pull tells
its own copies from everything else in the store.

The version of a pulled copy is the hosted acceptance ``seq`` it was last
granted at, kept in that acceptance's note. A higher ``seq`` upstream updates
the copy; a copy no longer listed is retired, but only when the answer says
it read the whole index (``index_read``), since a partial read cannot tell a
retirement from an omission.

Deterministic: no model, no network. The transport is the CLI's.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decisions.lifecycle import (
    ACCEPTER_SESSION_MAX,
    AGREEMENT_KIND,
    AGREEMENT_SCOPE,
    ARCHITECTURAL_KIND,
    GRANTING_ACTIONS,
    acceptance_blockers,
    requirement,
)
from repowise.core.analysis.decisions.provenance import compute_confidence, rank_for_source
from repowise.core.analysis.decisions.scope import SCOPE_BASIS_STATED

from .crud.authority import (
    AcceptanceRefusedError,
    decision_fields,
    latest_acceptance,
    record_acceptance,
)
from .decision_graph import set_record_scope
from .models import DecisionAcceptance, DecisionRecord

__all__ = ["PULL_ARTIFACT", "PullOutcome", "PulledDecision", "apply_pull", "pulled_copies"]

#: The artifact every acceptance a pull writes names.
PULL_ARTIFACT = "repowise.dev"

_GRANT_NOTE = "pulled from repowise.dev at version {seq}"
_GRANT_NOTE_RE = re.compile(r"^pulled from repowise\.dev at version (\d+)$")
_RETIRE_REASON = "No longer in force on repowise.dev."

#: Currencies a pulled acceptance may carry: the route lists only these.
_IN_FORCE = frozenset({"active", "needs_review"})


@dataclass(frozen=True)
class PulledDecision:
    """One in-force decision from the pull route, already checked by the caller."""

    id: str
    title: str
    decision: str
    origin: str
    seq: int
    context: str = ""
    rationale: str = ""
    alternatives: list[str] = field(default_factory=list)
    consequences: list[str] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    evidence_commits: list[str] = field(default_factory=list)
    kind: str = ARCHITECTURAL_KIND
    source: str = ""
    source_quote: str = ""
    evidence_file: str | None = None
    evidence_line: int | None = None
    action: str = "accepted"
    currency: str = "active"
    reason: str = ""
    scope: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    accepter: str = ""
    accepter_kind: str = ""
    accepter_session: str = ""
    accepted_at: datetime | None = None


@dataclass
class PullOutcome:
    """What reconciling the store against one pull answer did."""

    inserted: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.inserted or self.updated or self.retired)

    def as_dict(self) -> dict[str, object]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "retired": self.retired,
            "unchanged": self.unchanged,
            "skipped": [{"id": i, "reason": r} for i, r in self.skipped],
        }


async def pulled_copies(session: AsyncSession, repository_id: str) -> dict[str, int]:
    """Each pulled copy in *repository_id* and the hosted version it last took."""
    rows = await session.execute(
        select(DecisionAcceptance.decision_id, DecisionAcceptance.note)
        .where(
            DecisionAcceptance.repository_id == repository_id,
            DecisionAcceptance.artifact == PULL_ARTIFACT,
        )
        .order_by(DecisionAcceptance.decision_id, DecisionAcceptance.seq)
    )
    copies: dict[str, int] = {}
    for decision_id, note in rows.all():
        match = _GRANT_NOTE_RE.match(note or "")
        if match:
            copies[decision_id] = int(match.group(1))
    return copies


def _scope_files(scope: list[str]) -> list[str]:
    # The repo-wide marker is a claim, not a path; see decision_manifest.
    return sorted(s for s in scope if s != AGREEMENT_SCOPE)


def _fill(record: DecisionRecord, pulled: PulledDecision) -> None:
    record.title = pulled.title
    record.decision = pulled.decision
    record.context = pulled.context
    record.rationale = pulled.rationale
    record.alternatives_json = json.dumps(pulled.alternatives)
    record.consequences_json = json.dumps(pulled.consequences)
    record.evidence_commits_json = json.dumps(pulled.evidence_commits)
    record.kind = pulled.kind if pulled.kind in (AGREEMENT_KIND, ARCHITECTURAL_KIND) else (
        ARCHITECTURAL_KIND
    )
    record.affected_files_json = json.dumps(_scope_files(pulled.affected_files))


def _grant_terms(record: DecisionRecord, pulled: PulledDecision) -> dict[str, object]:
    """The acceptance a pulled copy is granted with, mirroring the hosted one."""
    scope = pulled.scope or pulled.affected_files
    if record.kind != AGREEMENT_KIND:
        scope = _scope_files(scope)
    return {
        "reason": pulled.reason or pulled.rationale or pulled.context,
        "scope": sorted(scope),
        "evidence": pulled.evidence or pulled.evidence_commits or [PULL_ARTIFACT],
        "accepter": pulled.accepter,
        "artifact": PULL_ARTIFACT,
    }


async def _grant(
    session: AsyncSession, record: DecisionRecord, pulled: PulledDecision, *, action: str
) -> None:
    terms = _grant_terms(record, pulled)
    acceptance = await record_acceptance(
        session,
        record,
        action=action,
        currency=pulled.currency if pulled.currency in _IN_FORCE else "active",
        # The hosted accepter signed there, not in this store.
        kind="import",
        accepter_session=pulled.accepter_session[:ACCEPTER_SESSION_MAX],
        note=_GRANT_NOTE.format(seq=pulled.seq),
        **terms,
    )
    if pulled.accepted_at is not None:
        # When it was accepted, not when this machine heard about it: the
        # tracked manifest exports this stamp.
        acceptance.created_at = pulled.accepted_at
        await session.flush()


def _refusal(record: DecisionRecord, pulled: PulledDecision) -> str | None:
    """Why the acceptance contract would refuse *pulled*, read before any write."""
    terms = _grant_terms(record, pulled)
    req = requirement(
        decision_fields(record),
        reason=str(terms["reason"]),
        scope=list(terms["scope"]),  # type: ignore[arg-type]
        evidence=list(terms["evidence"]),  # type: ignore[arg-type]
        accepter=str(terms["accepter"]),
        artifact=PULL_ARTIFACT,
        grant=True,
    )
    blockers = acceptance_blockers(req)
    return "; ".join(blockers) if blockers else None


async def _clashes(session: AsyncSession, repository_id: str, pulled: PulledDecision) -> bool:
    """Whether another record here already holds this decision's natural key."""
    found = await session.execute(
        select(DecisionRecord.id)
        .where(
            DecisionRecord.repository_id == repository_id,
            DecisionRecord.id != pulled.id,
            DecisionRecord.title == pulled.title,
            DecisionRecord.source == (pulled.source or "cli"),
            DecisionRecord.evidence_file.is_(None)
            if pulled.evidence_file is None
            else DecisionRecord.evidence_file == pulled.evidence_file,
        )
        .limit(1)
    )
    return found.first() is not None


async def _insert(
    session: AsyncSession, repository_id: str, pulled: PulledDecision, outcome: PullOutcome
) -> None:
    if await _clashes(session, repository_id, pulled):
        outcome.skipped.append((pulled.id, "a decision recorded here already has its title"))
        return
    source = pulled.source or "cli"
    record = DecisionRecord(
        id=pulled.id,
        repository_id=repository_id,
        source=source,
        identity_quote=pulled.source_quote,
        evidence_file=pulled.evidence_file,
        evidence_line=pulled.evidence_line,
        confidence=compute_confidence(rank_for_source(source), 1, "exact"),
        verification="unverified",
    )
    _fill(record, pulled)
    refusal = _refusal(record, pulled)
    if refusal:
        outcome.skipped.append((pulled.id, refusal))
        return
    try:
        async with session.begin_nested():
            session.add(record)
            await session.flush()
            await set_record_scope(
                session, record, _scope_files(pulled.affected_files), basis=SCOPE_BASIS_STATED
            )
            await _grant(session, record, pulled, action="accepted")
    except AcceptanceRefusedError as exc:
        outcome.skipped.append((pulled.id, str(exc)))
        return
    outcome.inserted.append(pulled.id)


async def _refresh(
    session: AsyncSession,
    record: DecisionRecord,
    pulled: PulledDecision,
    version: int,
    outcome: PullOutcome,
) -> None:
    latest = await latest_acceptance(session, record.id)
    if latest is not None and latest.artifact != PULL_ARTIFACT:
        # Someone here acted on it since; their action stands.
        outcome.unchanged.append(pulled.id)
        return
    retired_here = latest is not None and latest.currency not in _IN_FORCE
    if pulled.seq <= version and not retired_here:
        outcome.unchanged.append(pulled.id)
        return
    if pulled.title != record.title and await _clashes(session, record.repository_id, pulled):
        outcome.skipped.append((pulled.id, "a decision recorded here already has its new title"))
        return
    action = pulled.action if pulled.action in GRANTING_ACTIONS - {"accepted"} else "reaffirmed"
    try:
        # One copy that cannot be written must not roll back the whole pull.
        async with session.begin_nested():
            _fill(record, pulled)
            refusal = _refusal(record, pulled)
            if refusal:
                raise AcceptanceRefusedError([refusal])
            await set_record_scope(
                session, record, _scope_files(pulled.affected_files), basis=SCOPE_BASIS_STATED
            )
            await _grant(session, record, pulled, action=action)
    except (AcceptanceRefusedError, IntegrityError) as exc:
        await session.refresh(record)
        outcome.skipped.append((pulled.id, _why(exc)))
        return
    outcome.updated.append(pulled.id)


def _why(exc: Exception) -> str:
    if isinstance(exc, IntegrityError):
        return "conflicts with a decision recorded here"
    return str(exc)


async def _retire(session: AsyncSession, record: DecisionRecord, outcome: PullOutcome) -> None:
    latest = await latest_acceptance(session, record.id)
    if latest is None or latest.artifact != PULL_ARTIFACT or latest.currency not in _IN_FORCE:
        return
    try:
        async with session.begin_nested():
            await record_acceptance(
                session,
                record,
                action="deprecated",
                currency="dismissed",
                kind="import",
                reason=_RETIRE_REASON,
                scope=json.loads(latest.scope_json or "[]"),
                evidence=json.loads(latest.evidence_json or "[]"),
                accepter=latest.accepter,
                artifact=PULL_ARTIFACT,
            )
    except (AcceptanceRefusedError, IntegrityError) as exc:
        outcome.skipped.append((record.id, _why(exc)))
        return
    outcome.retired.append(record.id)


async def apply_pull(
    session: AsyncSession,
    repository_id: str,
    decisions: list[PulledDecision],
    *,
    index_read: bool,
    sent_ids: set[str] | frozenset[str] = frozenset(),
    unreadable_ids: set[str] | frozenset[str] = frozenset(),
    person_only: bool = False,
) -> PullOutcome:
    """Reconcile the store's pulled copies to *decisions*, the whole in-force set.

    Only ``recorded`` decisions are taken: an ``index`` one is something this
    repository's code says, which the local index finds itself under its own
    id. *sent_ids* are hosted ids of records this store pushed, which are
    already here under their local ids. *person_only* (approval required)
    takes only decisions a person accepted. *unreadable_ids* were listed but
    could not be read: they are still in force there, so their copies stay.
    """
    outcome = PullOutcome()
    copies = await pulled_copies(session, repository_id)
    listed: set[str] = set(unreadable_ids)
    for pulled in decisions:
        listed.add(pulled.id)
        if pulled.origin != "recorded" or pulled.id in sent_ids:
            continue
        if person_only and pulled.accepter_kind != "person":
            outcome.skipped.append((pulled.id, "waits for a person: approval is required here"))
            continue
        record = await session.get(DecisionRecord, pulled.id)
        if record is not None and (
            pulled.id not in copies or record.repository_id != repository_id
        ):
            # Recorded here, or imported from decisions.yaml under the same id.
            continue
        if record is None:
            await _insert(session, repository_id, pulled, outcome)
        else:
            await _refresh(session, record, pulled, copies[pulled.id], outcome)

    if index_read:
        for decision_id in sorted(set(copies) - listed):
            record = await session.get(DecisionRecord, decision_id)
            if record is not None:
                await _retire(session, record, outcome)
    return outcome
