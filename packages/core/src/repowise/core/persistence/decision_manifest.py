"""Round-tripping accepted decisions between the store and ``decisions.yaml``.

Export projects the accepted rows onto the tracked file. Import reads that file
back and reconciles the store to it, because the file is what a colleague's
commit changes and the store is the copy. Neither side invents authority: an
import only recognizes decisions the file already presents as accepted, and it
records the file itself as the acceptance artifact.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import or_, select
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

from .batches import chunked
from .crud.authority import (
    accepted_decision_ids,
    latest_acceptance,
    record_acceptance,
    resolve_decision_id,
)
from .crud.decisions import _rederive_headline, list_decision_evidence
from .decision_graph import set_record_scope
from .decision_id_migration import repair_superseded_aliases
from .models import (
    REKEYED_REASON,
    SUPERSEDED_REASON,
    DecisionAcceptance,
    DecisionAlias,
    DecisionRecord,
)

__all__ = ["ImportOutcome", "export_manifest", "import_manifest", "refresh_manifest"]


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
        select(DecisionAlias.alias_id).where(
            DecisionAlias.decision_id == decision_id,
            # A pre-fix supersede pointer names another record, not this one.
            DecisionAlias.reason != SUPERSEDED_REASON,
        )
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

    __slots__ = ("accepted", "created", "reaffirmed", "repaired", "skipped", "unchanged")

    def __init__(self) -> None:
        self.created: list[str] = []
        self.accepted: list[str] = []
        self.reaffirmed: list[str] = []
        self.unchanged: list[str] = []
        self.skipped: list[tuple[str, str]] = []
        #: The stale supersede pointers the repair deleted.
        self.repaired: list[str] = []

    def as_dict(self) -> dict[str, object]:
        return {
            "created": self.created,
            "accepted": self.accepted,
            "reaffirmed": self.reaffirmed,
            "unchanged": self.unchanged,
            "skipped": [{"id": i, "reason": r} for i, r in self.skipped],
            "repaired": self.repaired,
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
    # The file is hand-authored and version controlled, so its scope is
    # stated: a record narrowed here binds to what the file says.
    await set_record_scope(
        session, record, sorted(_scope_files(entry.scope)), basis=SCOPE_BASIS_STATED
    )
    # The successor is an id the file wrote down, and the file can be older
    # than the store it is being read into. Storing it unresolved would put a
    # retired id back into the column.
    successor = entry.superseded_by or None
    if successor is not None:
        successor = await resolve_decision_id(session, successor) or successor
    record.superseded_by = successor


async def _alias_reasons(session: AsyncSession, ids: list[str]) -> dict[str, str]:
    """Each of *ids* that holds an alias row, and its reason, read in ``chunked()`` pieces."""
    reasons: dict[str, str] = {}
    for piece in chunked(ids):
        rows = await session.execute(
            select(DecisionAlias.alias_id, DecisionAlias.reason).where(
                DecisionAlias.alias_id.in_(piece)
            )
        )
        reasons.update(rows.tuples().all())
    return reasons


@dataclass
class _ImportTargets:
    """Where each listed id lands in the store, resolved after the repair."""

    repository_id: str
    #: Each listed id's entry, the first where the file repeats an id.
    entries: dict[str, ManifestDecision]
    #: Each listed id's record, after the repair: a file written before the id
    #: migration lists records under ids they no longer sit on, so two entries
    #: can reach one record without either naming it.
    targets: dict[str, str | None]
    #: Skipped entries still count: else a rival becomes the sole claimant and overwrites.
    claims: Counter[str]
    #: Listed ids not on their own record that hold an alias row, and its reason.
    aliases: dict[str, str]

    async def place(
        self, session: AsyncSession, entry: ManifestDecision
    ) -> tuple[DecisionRecord | None, str | None]:
        """The stored record *entry* updates, or why the entry is skipped."""
        reason = _missing_field(entry) or self._unplaceable(entry.id)
        if reason:
            return None, reason
        resolved = self.targets[entry.id]
        record = await session.get(DecisionRecord, resolved) if resolved else None
        reason = await self._contested(session, entry.id, record)
        if reason is None and record is not None and resolved != entry.id:
            reason = await _superseded_wording(session, entry, record)
        return record, reason

    def _unplaceable(self, entry_id: str) -> str | None:
        resolved = self.targets[entry_id]
        if resolved and resolved != entry_id and resolved in self.entries:
            # The alias lands on a record the file holds as its own entry, so
            # following it would write one decision over a different one.
            return f"its alias resolves to {resolved[:8]}, which the file lists"
        if resolved is None and entry_id in self.aliases:
            # A moved record's supersede pointer: creating the id would copy a
            # record that lives on under another id.
            return "retired id, record moved, resolves to nothing"
        return None

    async def _contested(
        self, session: AsyncSession, entry_id: str, record: DecisionRecord | None
    ) -> str | None:
        if record is not None and record.repository_id != self.repository_id:
            # One store can hold several repositories. An id that resolves into
            # a different one is not this file's decision.
            return "belongs to another repository"
        resolved = self.targets[entry_id]
        if resolved and resolved != entry_id and self.claims[resolved] > 1:
            # Checked after the repository so a multi-repo store skips as before.
            return await self._rival(session, entry_id, resolved, record)
        return None

    async def _rival(
        self,
        session: AsyncSession,
        entry_id: str,
        resolved: str,
        record: DecisionRecord | None,
    ) -> str | None:
        """Why *entry_id* yields *record*, stored on *resolved*, to the other
        entries that reach it, ``None`` when it is the one that applies."""
        claimants = [i for i, t in self.targets.items() if t == resolved]
        reasons = [self.aliases.get(i) for i in claimants]
        # No record (a dangling alias): no keeper to fold into, so every claimant yields.
        # Every claimant is ``rekeyed`` or ``merged``: ``superseded`` rows resolve to
        # nothing or their own id.
        if record is not None and reasons.count(REKEYED_REASON) == 1:
            # The id migration moved one record onto *resolved* and folded the
            # rest into it, so the re-keyed id's entry is the keeper's own,
            # unless it is the wording of a record the keeper superseded.
            kept = claimants[reasons.index(REKEYED_REASON)]
            if entry_id == kept:
                return None
            if not await _superseded_wording(session, self.entries[kept], record):
                return f"folded into {resolved[:8]}, whose own entry the file lists"
        # Two entries reach one record without either naming it, and nothing
        # stored tells them apart: two re-keys, or folds with no re-key.
        return f"its alias resolves to {resolved[:8]}, which another entry also reaches"


async def _superseded_wording(
    session: AsyncSession, entry: ManifestDecision, record: DecisionRecord
) -> str | None:
    """Why *entry*, which reaches *record* through an alias, is the wording of a
    record *record* superseded, ``None`` when it is not.

    A pre-fix supersede moved the superseded record's aliases onto its successor
    and left the record in place, still naming the successor. One query, walking
    the ``superseded_by`` chain back from *record*.
    """
    # Only wording the successor does not share tells its predecessor's entry apart.
    clues = [
        column == value
        for column, value, own in (
            (DecisionRecord.title, entry.title, record.title),
            (DecisionRecord.decision, entry.decision, record.decision),
        )
        if value and value != own
    ]
    if not clues:
        return None
    chain = (
        select(DecisionRecord.id)
        .where(DecisionRecord.superseded_by == record.id)
        .cte("superseded_chain", recursive=True)
    )
    # UNION, not UNION ALL: a ``superseded_by`` cycle then ends the walk.
    chain = chain.union(select(DecisionRecord.id).where(DecisionRecord.superseded_by == chain.c.id))
    match = (
        await session.execute(
            select(DecisionRecord.id, DecisionRecord.superseded_by)
            .where(DecisionRecord.id.in_(select(chain.c.id)), or_(*clues))
            .order_by(DecisionRecord.id)
            .limit(1)
        )
    ).first()
    if match is None:
        return None
    match_id, via = match
    # Down a chain the record did not supersede the match itself: name the link that did.
    by = "superseded" if via == record.id else f"replaced via {via[:8]}"
    return (
        f"its alias resolves to {record.id[:8]}, but it matches {match_id[:8]},"
        f" which {record.id[:8]} {by}"
    )


def _missing_field(entry: ManifestDecision) -> str | None:
    """Why the file's entry is too incomplete to import, ``None`` when it is not."""
    if not entry.id or not entry.title:
        return "missing id or title"
    if not entry.reason.strip():
        return "no reason recorded"
    if not [s for s in _entry_scope(entry) if s.strip()]:
        return "no scope recorded"
    return None


async def _resolve_targets(
    session: AsyncSession,
    repository_id: str,
    entries: list[ManifestDecision],
) -> _ImportTargets:
    """Resolve every listed id once, so one alias lookup serves each entry."""
    targets: dict[str, str | None] = {}
    for entry in entries:
        if entry.id and entry.id not in targets:
            targets[entry.id] = await resolve_decision_id(session, entry.id)
    # One query per chunk, not per entry.
    aliases = await _alias_reasons(
        session, [listed_id for listed_id, target in targets.items() if target != listed_id]
    )
    return _ImportTargets(
        repository_id=repository_id,
        entries={entry.id: entry for entry in reversed(entries)},
        targets=targets,
        claims=Counter(target for target in targets.values() if target),
        aliases=aliases,
    )


async def _create_record(
    session: AsyncSession, entry: ManifestDecision, repository_id: str
) -> DecisionRecord:
    """Insert the decision the file is the only record of."""
    record = DecisionRecord(
        id=entry.id,
        repository_id=repository_id,
        title=entry.title,
        decision=entry.decision,
        rationale=entry.reason,
        source=entry.source or "cli",
        kind=entry.kind or ARCHITECTURAL_KIND,
        evidence_commits_json=json.dumps(sorted(entry.evidence)),
        superseded_by=entry.superseded_by or None,
        confidence=_entry_confidence(entry),
        verification="unverified",
    )
    session.add(record)
    await session.flush()
    await set_record_scope(
        session, record, sorted(_scope_files(entry.scope)), basis=SCOPE_BASIS_STATED
    )
    return record


async def _accept_from_file(
    session: AsyncSession, record: DecisionRecord, entry: ManifestDecision, artifact: str
) -> None:
    """Log the file's acceptance of *entry* against its stored record."""
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
    # Clears the inert supersede pointers, as the id migration does; placement reads past them.
    outcome.repaired, _ = await repair_superseded_aliases(session, repository_id, apply=not dry_run)
    plan = await _resolve_targets(session, repository_id, entries)

    for entry in entries:
        record, reason = await plan.place(session, entry)
        if reason:
            outcome.skipped.append((entry.id or "<no id>", reason))
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
            record = await _create_record(session, entry, repository_id)
            # An id listed twice: the second copy finds this record, not a clashing insert.
            plan.targets[entry.id] = record.id
        elif entry.id not in outcome.reaffirmed:
            outcome.accepted.append(entry.id)
            if dry_run:
                continue

        await _accept_from_file(session, record, entry, artifact)
    return outcome


def _committed_ids(repo_path: Path | str) -> set[str] | None:
    """Ids in the committed ``decisions.yaml`` at ``HEAD``, ``None`` when git has none."""
    import subprocess

    import yaml

    try:
        out = subprocess.run(
            ["git", "show", f"HEAD:.repowise/{MANIFEST_FILENAME}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        data = yaml.safe_load(out.stdout)
    except yaml.YAMLError:
        return None
    entries = data.get("decisions") if isinstance(data, dict) else None
    return {
        str(e["id"]) for e in entries or [] if isinstance(e, dict) and e.get("id")
    }


async def _store_moved_past(session: AsyncSession, entry: ManifestDecision) -> bool:
    """Whether *entry* is an older acceptance of its decision, exported earlier.

    Import reads any difference as somebody's edit. Here the other reading
    matters: the store accepted the decision again (a landing, a dismissal, a
    pull) after the file was written. The entry is then an export of an
    acceptance row the store still holds, not the latest one, and rewriting
    it loses nothing.
    """
    decision_id = await resolve_decision_id(session, entry.id)
    record = await session.get(DecisionRecord, decision_id) if decision_id else None
    if record is None or (entry.title, entry.decision, entry.kind) != (
        record.title,
        record.decision,
        record.kind,
    ):
        # The wording lives on the record, with no history to match against.
        return False
    rows = (
        await session.scalars(
            select(DecisionAcceptance)
            .where(DecisionAcceptance.decision_id == decision_id)
            .order_by(DecisionAcceptance.seq)
        )
    ).all()
    for row in rows[:-1]:
        if (
            row.created_at.isoformat() == entry.accepted_at
            and (row.reason or "").strip() == entry.reason.strip()
            and sorted(_json_list(row.scope_json)) == sorted(entry.scope)
            and row.currency == _stored_currency(entry.currency)
        ):
            return True
    return False


async def refresh_manifest(
    session: AsyncSession, repository_id: str, repo_path: Path | str
) -> tuple[str, int]:
    """Keep an existing ``decisions.yaml`` in step with the store, or leave it be.

    Returns ``(outcome, count)``. ``absent``: there is no file, and none is
    created, since only someone who exported one has opted in. ``held``: the
    file says something the store has not read, so rewriting it would lose
    that; ``decision import`` reconciles it. ``unchanged`` / ``written``: the
    store's projection, as ``decision export`` writes it.

    Held covers two cases. The file holds an entry the store would import
    (a colleague's commit, or a hand edit), short of one the store has simply
    moved past: export would overwrite it. Or the working copy dropped an
    entry the committed file has: import ignores a missing line, so only git
    shows that someone removed it on purpose.
    """
    from repowise.core.analysis.decisions.manifest import (
        ManifestConflictError,
        manifest_path,
    )

    if not manifest_path(repo_path).is_file():
        return "absent", 0
    try:
        entries, raw = load_manifest(repo_path)
        pending = await import_manifest(session, repository_id, repo_path, dry_run=True)
    except ManifestConflictError:
        return "held", 0
    if pending.created or pending.accepted:
        return "held", 0
    by_id = {e.id: e for e in entries}
    # A skip is held too, unless it is an export the store moved past: that
    # is the one skip the rewrite resolves rather than loses.
    for entry_id in [*pending.reaffirmed, *(i for i, _ in pending.skipped)]:
        entry = by_id.get(entry_id)
        if entry is None or not await _store_moved_past(session, entry):
            return "held", 0
    committed = _committed_ids(repo_path)
    if committed and committed - {e.id for e in entries}:
        return "held", 0
    try:
        count, changed = await export_manifest(
            session, repository_id, repo_path, expected_raw=raw
        )
    except ManifestConflictError:
        return "held", 0
    return ("written" if changed else "unchanged"), count
