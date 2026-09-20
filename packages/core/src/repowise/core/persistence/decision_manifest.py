"""Round-tripping accepted decisions between the store and ``decisions.yaml``.

Export projects the accepted rows onto the tracked file. Import reads that file
back and reconciles the store to it, because the file is what a colleague's
commit changes and the store is the copy. Neither side invents authority: an
import only recognizes decisions the file already presents as accepted, and it
records the file itself as the acceptance artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decisions.lifecycle import (
    AGREEMENT_KIND,
    AGREEMENT_SCOPE,
    ARCHITECTURAL_KIND,
    STORED_CURRENCIES,
)
from repowise.core.analysis.decisions.manifest import (
    MANIFEST_FILENAME,
    ManifestDecision,
    load_manifest,
    write_manifest,
)
from repowise.core.analysis.decisions.provenance import compute_confidence, rank_for_source
from repowise.core.analysis.decisions.scope import SCOPE_BASIS_STATED

from .crud.authority import (
    accepted_decision_ids,
    latest_acceptance,
    record_acceptance,
    resolve_decision_id,
)
from .crud.decisions import _rederive_headline, list_decision_evidence
from .models import DecisionAlias, DecisionRecord

__all__ = ["ImportOutcome", "export_manifest", "import_manifest"]


def _stored_currency(value: str) -> str:
    """Map a manifest currency onto one an acceptance row may hold.

    ``uncheckable`` is derived from the code rather than declared, so a file
    carrying it is read as the ``active`` it was derived from.
    """
    return value if value in STORED_CURRENCIES else "active"


def _scope_files(scope: list[str]) -> list[str]:
    """The entries of *scope* that name code.

    An agreement's scope is the repository, which is a claim rather than a
    path. Storing the marker in ``affected_files_json`` would put it in front of
    staleness and git archaeology as a file that has never existed, and every
    such file counts as changed.
    """
    return [s for s in scope if s != AGREEMENT_SCOPE]


def _entry_scope(entry: ManifestDecision) -> list[str]:
    """The scope *entry* states, read against the noun it claims to be.

    The repo-wide marker is a scope for an agreement and nothing at all for a
    decision. Read once here so the skip gate and the acceptance cannot
    disagree: taking the marker as a scope for an architectural entry would
    accept a record governing no code, which is the one thing the contract is
    there to refuse. A file naming the marker without saying ``kind:
    agreement`` is the realistic version of that, since the marker is now
    written into every export that holds one.
    """
    if entry.kind == AGREEMENT_KIND:
        return sorted(entry.scope)
    return sorted(_scope_files(entry.scope))


def _json_list(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(v) for v in parsed] if isinstance(parsed, list) else []


async def _aliases_for(session: AsyncSession, decision_id: str) -> list[str]:
    rows = await session.execute(
        select(DecisionAlias.alias_id).where(DecisionAlias.decision_id == decision_id)
    )
    return [r[0] for r in rows.all()]


async def export_manifest(
    session: AsyncSession,
    repository_id: str,
    repo_path: Path | str,
    *,
    expected_raw: str | None = None,
    allow_empty: bool = False,
) -> tuple[int, bool]:
    """Write every accepted decision to ``decisions.yaml``.

    Returns the number exported and whether the file's bytes changed. Dismissed
    decisions are exported too: a withdrawn constraint is part of the record a
    reviewer needs, and dropping it would make the file re-import as though the
    withdrawal never happened.
    """
    # Read before writing: the version gate and the empty-overwrite guard both
    # live on what is already committed, and a blind write is how a rebuilt
    # store erases a colleague's file.
    if expected_raw is None:
        _, expected_raw = load_manifest(repo_path)

    accepted = await accepted_decision_ids(session, repository_id)
    if not accepted:
        return 0, write_manifest(
            repo_path, [], expected_raw=expected_raw, allow_empty=allow_empty
        )

    records = (
        (
            await session.execute(
                select(DecisionRecord).where(
                    DecisionRecord.repository_id == repository_id,
                    DecisionRecord.id.in_(accepted),
                )
            )
        )
        .scalars()
        .all()
    )

    entries: list[ManifestDecision] = []
    for rec in records:
        acceptance = await latest_acceptance(session, rec.id)
        if acceptance is None:  # pragma: no cover - accepted set says otherwise
            continue
        entries.append(
            ManifestDecision(
                id=rec.id,
                title=rec.title,
                decision=rec.decision,
                reason=acceptance.reason,
                scope=_json_list(acceptance.scope_json),
                accepted_at=acceptance.created_at.isoformat(),
                accepted_by=acceptance.accepter,
                accepted_artifact=acceptance.artifact,
                currency=acceptance.currency,
                source=rec.source,
                kind=rec.kind,
                evidence=_json_list(acceptance.evidence_json),
                superseded_by=rec.superseded_by or "",
                aliases=await _aliases_for(session, rec.id),
            )
        )
    changed = write_manifest(
        repo_path, entries, expected_raw=expected_raw, allow_empty=allow_empty
    )
    return len(entries), changed


class ImportOutcome:
    """What reconciling the store against the manifest did."""

    __slots__ = ("accepted", "created", "reaffirmed", "skipped", "unchanged")

    def __init__(self) -> None:
        self.created: list[str] = []
        self.accepted: list[str] = []
        self.reaffirmed: list[str] = []
        self.unchanged: list[str] = []
        self.skipped: list[tuple[str, str]] = []

    def as_dict(self) -> dict[str, object]:
        return {
            "created": self.created,
            "accepted": self.accepted,
            "reaffirmed": self.reaffirmed,
            "unchanged": self.unchanged,
            "skipped": [{"id": i, "reason": r} for i, r in self.skipped],
        }


def _differs(
    entry: ManifestDecision, record: DecisionRecord, acceptance: object
) -> bool:
    """Whether the file says something the store's acceptance does not."""
    return (
        entry.reason.strip() != (acceptance.reason or "").strip()
        or sorted(entry.scope) != sorted(_json_list(acceptance.scope_json))
        or _stored_currency(entry.currency) != acceptance.currency
        or entry.title != record.title
        or entry.decision != record.decision
        # Without this a file whose only edit is the noun reads as unchanged,
        # and the committed file becomes unable to express the one thing this
        # column exists for.
        or entry.kind != record.kind
    )


def _entry_confidence(entry: ManifestDecision) -> float:
    """Score a decision the file is the only record of.

    Same shape as the CLI's manual entry: full rank credit, no completeness
    term, and scored as verified, because a person wrote it and the file holds
    no quote to check.
    """
    return compute_confidence(rank_for_source(entry.source or "cli"), 1, "exact")


async def _apply_entry(
    session: AsyncSession, entry: ManifestDecision, record: DecisionRecord
) -> None:
    """Copy the file's version of a decision onto the stored record."""
    record.title = entry.title
    record.decision = entry.decision
    record.rationale = entry.reason
    # The file exports every accepted decision, most of which were mined and
    # carry evidence, so re-derive from that where it exists and only score
    # the entry itself where the file is the whole record. Writing the entry's
    # score unconditionally would fight the reconcile pass every index.
    evidence = await list_decision_evidence(session, record.id)
    if evidence:
        _rederive_headline(record, evidence)
    else:
        record.confidence = _entry_confidence(entry)
    record.kind = entry.kind or ARCHITECTURAL_KIND
    record.affected_files_json = json.dumps(sorted(_scope_files(entry.scope)))
    # The file is hand-authored and version controlled, so its scope is
    # stated: a record narrowed here binds to what the file says.
    record.scope_basis = SCOPE_BASIS_STATED
    # The successor is an id the file wrote down, and the file can be older
    # than the store it is being read into. Storing it unresolved would put a
    # retired id back into the column.
    successor = entry.superseded_by or None
    if successor is not None:
        successor = await resolve_decision_id(session, successor) or successor
    record.superseded_by = successor


async def import_manifest(
    session: AsyncSession,
    repository_id: str,
    repo_path: Path | str,
    *,
    dry_run: bool = False,
) -> ImportOutcome:
    """Reconcile the store to ``decisions.yaml``.

    The file is the authority, so a decision present there and absent here is
    created and accepted, with the file as the acceptance artifact: it is
    version controlled and reviewable, which is the only reason a non-human
    accepter is allowed at all, and an entry the file does not present as
    accepted is skipped rather than guessed at. An entry that differs from the
    store is re-accepted from the file, because the difference is somebody's
    commit; an entry the file no longer holds is left alone, since a missing
    line is as likely a bad merge as a deliberate removal.
    """
    outcome = ImportOutcome()
    entries, _ = load_manifest(repo_path)
    artifact = f".repowise/{MANIFEST_FILENAME}"

    for entry in entries:
        if not entry.id or not entry.title:
            outcome.skipped.append((entry.id or "<no id>", "missing id or title"))
            continue
        if not entry.reason.strip():
            outcome.skipped.append((entry.id, "no reason recorded"))
            continue
        if not [s for s in _entry_scope(entry) if s.strip()]:
            outcome.skipped.append((entry.id, "no scope recorded"))
            continue

        resolved = await resolve_decision_id(session, entry.id)
        record = await session.get(DecisionRecord, resolved) if resolved else None
        if record is not None and record.repository_id != repository_id:
            # One store can hold several repositories. An id that resolves into
            # a different one is not this file's decision.
            outcome.skipped.append((entry.id, "belongs to another repository"))
            continue

        if record is not None:
            acceptance = await latest_acceptance(session, record.id)
            if acceptance is not None:
                if not _differs(entry, record, acceptance):
                    outcome.unchanged.append(entry.id)
                    continue
                # The file changed: a colleague edited the reason, corrected
                # the scope, or withdrew the decision. Their edit is the
                # authority, so it is re-accepted rather than left disagreeing
                # with the store — appended to the log, so the history of both
                # acceptances survives.
                outcome.reaffirmed.append(entry.id)
                if dry_run:
                    continue
                await _apply_entry(session, entry, record)

        if record is None:
            outcome.created.append(entry.id)
            if dry_run:
                continue
            record = DecisionRecord(
                id=entry.id,
                repository_id=repository_id,
                title=entry.title,
                decision=entry.decision,
                rationale=entry.reason,
                source=entry.source or "cli",
                kind=entry.kind or ARCHITECTURAL_KIND,
                affected_files_json=json.dumps(sorted(_scope_files(entry.scope))),
                scope_basis=SCOPE_BASIS_STATED,
                evidence_commits_json=json.dumps(sorted(entry.evidence)),
                superseded_by=entry.superseded_by or None,
                confidence=_entry_confidence(entry),
                verification="unverified",
            )
            session.add(record)
            await session.flush()
        elif entry.id not in outcome.reaffirmed:
            outcome.accepted.append(entry.id)
            if dry_run:
                continue

        await record_acceptance(
            session,
            record,
            action="accepted",
            currency=_stored_currency(entry.currency),
            reason=entry.reason,
            scope=_entry_scope(entry),
            evidence=sorted(entry.evidence) or [artifact],
            accepter=entry.accepted_by,
            artifact=entry.accepted_artifact or artifact,
            # The named accepter signed the manifest, not this store.
            kind="import",
            note="imported from the tracked manifest",
        )
    return outcome
