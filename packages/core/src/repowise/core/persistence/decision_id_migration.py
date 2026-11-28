"""Move existing decisions onto ids derived from their own identity.

A record minted before :func:`derive_decision_id` carries a random id, so a
store that is rebuilt rather than updated gives the same decision a different
id, and every reference held outside the row stops resolving. This walks a
repository's records, computes the id each one would have today, and moves it
there.

A runtime repair rather than an Alembic step, for the reason
:mod:`decision_migration` gives: a data fix that lives in a migration and one
that lives in the code eventually disagree, and only one of them runs on an
existing store. This one has a second reason. The decision vector keys are part
of the rewrite, and Alembic has no handle on the vector store.

No record is deleted. A record moves by being copied to its derived id, having
every dependent row repointed at the copy, and only then releasing the id it
came from; an alias records where it went, so an id already written down
somewhere keeps resolving. Stale supersede pointers are the exception:
:func:`repair_superseded_aliases` first deletes those on records that never
moved. Idempotent: a second run finds every id already derived and moves nothing.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import Select, Update, event, or_, select, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text as _sql_text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.orm import Session, SessionTransaction, aliased
from sqlalchemy.util import await_only

from repowise.core.analysis.decisions.semantic_match import (
    DECISION_VECTOR_PREFIX,
    decision_vector_item,
    upsert_decision_vectors,
)
from repowise.core.fsutils import atomic_write_text

from .batches import chunked
from .crud.authority import accepted_decision_ids
from .crud.decision_identity import (
    can_fold_away,
    duplicate_commits,
    duplicate_text,
    keeper_order,
)
from .crud.decision_lane_fold import RewordingIndex
from .crud.decisions import derive_decision_id
from .models import (
    MERGED_REASON,
    REKEYED_REASON,
    SUPERSEDED_REASON,
    DecisionAcceptance,
    DecisionAlias,
    DecisionCandidateMeta,
    DecisionEdge,
    DecisionEvidence,
    DecisionNodeLink,
    DecisionRecord,
    Repository,
    _now_utc,
)
from .vector_store.lancedb_store import LanceDBVectorStore

logger = structlog.get_logger(__name__)

_VECTOR_ROLLBACKS = "repowise.decision_vector_rollbacks"
_VECTOR_JOURNALS = "repowise.decision_vector_journals"
_VECTOR_ROLLBACK_FAILED = "repowise.decision_vector_rollback_failed"
_VECTOR_BACKUP_PREFIX = "decision-rekey-backup:"
_RestoreVectors = Callable[[bool], Awaitable[None]]


class DecisionVectorRecoveryError(RuntimeError):
    """The pending vector update must recover before another index write."""


@dataclass
class _VectorUndo:
    revision: str
    previous: str | None
    rows: dict[str, dict | None] = field(default_factory=dict)
    settle: Callable[[], Awaitable[None]] | None = field(default=None, init=False)

    @classmethod
    def read(cls, path: Path) -> _VectorUndo:
        undo = cls(**json.loads(path.read_text(encoding="utf-8")))
        if UUID(hex=undo.revision).hex != undo.revision or (
            undo.previous is not None and UUID(hex=undo.previous).hex != undo.previous
        ):
            raise ValueError("Invalid decision-vector revision")
        if (
            not isinstance(undo.rows, dict)
            or not undo.rows
            or any(
                not key.startswith((DECISION_VECTOR_PREFIX, _VECTOR_BACKUP_PREFIX))
                or (row is not None and (not isinstance(row, dict) or row.get("page_id") != key))
                for key, row in undo.rows.items()
            )
        ):
            raise ValueError("Invalid decision-vector undo rows")
        return undo


def _vector_revision(repository_id: str) -> Select[tuple[str | None]]:
    return select(Repository.decision_vector_revision).where(Repository.id == repository_id)


def _write_vector_revision(repository_id: str) -> Update:
    return update(Repository).where(Repository.id == repository_id)


async def _recover_vector_undo(
    session: AsyncSession,
    repository_id: str,
    store: LanceDBVectorStore,
    *,
    expected: str | None = None,
) -> None:
    path = store.decision_vector_journal(repository_id)
    if path in session.info.get(_VECTOR_JOURNALS, {}):
        return

    # Serialize cooperating writers. On SQLite this also pins the outer
    # transaction before a savepoint might become the first database write.
    try:
        await session.execute(
            _write_vector_revision(repository_id).values(
                decision_vector_revision=Repository.decision_vector_revision
            )
        )
        undo = _VectorUndo.read(path)
    except FileNotFoundError:
        return
    except Exception as exc:
        raise DecisionVectorRecoveryError("Cannot read decision-vector undo") from exc
    if expected is not None and undo.revision != expected:
        return
    try:
        revision = await session.scalar(_vector_revision(repository_id))
        if revision != undo.revision:
            if revision != undo.previous:
                raise DecisionVectorRecoveryError(
                    "Decision-vector undo does not match this repository revision"
                )
            await store.refresh_vector_snapshot()
            await store.restore_vectors(undo.rows)
        path.unlink()
    except DecisionVectorRecoveryError:
        raise
    except Exception as exc:
        raise DecisionVectorRecoveryError("Cannot settle decision-vector undo") from exc


async def recover_decision_vector_undo(
    session: AsyncSession, repository_id: str, store: LanceDBVectorStore
) -> None:
    """Recover the routed store while the caller holds the repository update lock."""
    await _recover_vector_undo(session, repository_id, store)
    if store.decision_rekey_pending():
        raise DecisionVectorRecoveryError("A pending decision rekey belongs to another repository")
    await store.refresh_vector_snapshot()


async def _journal_vector_undo(
    session: AsyncSession, store: LanceDBVectorStore, repository_id: str, keys: set[str]
) -> _RestoreVectors:
    path = store.decision_vector_journal(repository_id)
    journals: dict[Path, _VectorUndo] = session.info.setdefault(_VECTOR_JOURNALS, {})
    undo = journals.get(path)
    is_first = undo is None
    if undo is None:
        # Recovery already locked the same SQL row used by every rekey.
        previous = await session.scalar(_vector_revision(repository_id))
        undo = _VectorUndo(uuid4().hex, previous)
        await session.execute(
            _write_vector_revision(repository_id).values(decision_vector_revision=undo.revision)
        )
    before = await store.snapshot_vectors(keys)
    rows = before | undo.rows
    atomic_write_text(
        path,
        json.dumps(
            {
                "revision": undo.revision,
                "previous": undo.previous,
                "rows": rows,
            }
        ),
        fsync=True,
    )
    undo.rows = rows
    journals[path] = undo

    async def settle() -> None:
        # The original SQL lock has been released. Reacquire it and match
        # the revision so a late callback cannot touch another writer's undo.
        bind = session.bind
        if isinstance(bind, AsyncConnection):
            bind = bind.engine
        async with AsyncSession(bind) as recovery:
            await _recover_vector_undo(recovery, repository_id, store, expected=undo.revision)

    undo.settle = settle

    async def restore(outer: bool) -> None:
        if outer:
            return
        try:
            await store.restore_vectors(before)
            if is_first:
                path.unlink(missing_ok=True)
                journals.pop(path, None)
        except Exception as exc:
            session.info[_VECTOR_ROLLBACK_FAILED] = True
            raise DecisionVectorRecoveryError("Cannot roll back decision vectors") from exc

    return restore


def _require_vector_recovery(session: Session) -> None:
    if session.info.get(_VECTOR_ROLLBACK_FAILED):
        raise DecisionVectorRecoveryError("Roll back the transaction to recover decision vectors")


def _commit_vector_changes(session: Session) -> None:
    transaction = session.get_nested_transaction() or session.get_transaction()
    pending = session.info[_VECTOR_ROLLBACKS]
    restores = pending.pop(transaction, [])
    if restores and transaction is not None and transaction.parent is not None:
        pending.setdefault(transaction.parent, []).extend(restores)


def _restore_uncommitted_vectors(session: Session, transaction: SessionTransaction) -> None:
    """A transaction that ends without committing may also be closing its session."""
    pending: list[_RestoreVectors] = session.info[_VECTOR_ROLLBACKS].pop(transaction, [])
    for restore in reversed(pending):
        await_only(restore(transaction.parent is None))
    if transaction.parent is None:
        session.info.pop(_VECTOR_ROLLBACK_FAILED, None)
        for undo in session.info.pop(_VECTOR_JOURNALS, {}).values():
            try:
                await_only(undo.settle())
            except DecisionVectorRecoveryError as exc:
                # The file still carries the exact rows. Never report an
                # already committed SQL transaction as rolled back.
                logger.warning("decision_vector_undo_pending", error=str(exc))


async def _preserve_vectors_until_commit(
    session: AsyncSession,
    repository_id: str,
    vector_store: Any,
    decision_ids: set[str],
    *,
    backup_ids: set[str] | None = None,
) -> None:
    if not decision_ids:
        return
    keys = {f"{DECISION_VECTOR_PREFIX}{decision_id}" for decision_id in decision_ids}
    keys.update(f"{_VECTOR_BACKUP_PREFIX}{decision_id}" for decision_id in backup_ids or ())
    if isinstance(vector_store, LanceDBVectorStore):
        restore = await _journal_vector_undo(session, vector_store, repository_id, keys)
    else:
        preserve = getattr(vector_store, "preserve_vectors", None)
        restore_in_memory = await preserve(keys) if preserve is not None else None
        if restore_in_memory is None:
            return

        async def restore(_outer: bool) -> None:
            await restore_in_memory()

    sync = session.sync_session
    if _VECTOR_ROLLBACKS not in sync.info:
        sync.info[_VECTOR_ROLLBACKS] = {}
        event.listen(sync, "before_commit", _require_vector_recovery)
        event.listen(sync, "after_commit", _commit_vector_changes)
        event.listen(sync, "after_transaction_end", _restore_uncommitted_vectors)
    transaction = sync.get_nested_transaction() or sync.get_transaction()
    sync.info[_VECTOR_ROLLBACKS].setdefault(transaction, []).append(restore)


#: ``(table, column)`` pairs a fold drops instead of repointing, with the
#: reason. Everything else in :data:`_DEPENDENT_COLUMNS` moves to the keeper.
#:
#: Note the pair, not the table: ``decision_candidate_meta`` appears in
#: ``_DEPENDENT_COLUMNS`` twice, and only one of the two is a singleton.
#: ``decision_id`` is the table's primary key, so the loser's own review row
#: cannot move onto a keeper that already has one. ``merged_into`` is a plain
#: nullable column on some *other* candidate's row, and that candidate is
#: still alive; dropping its row would take a live decision's whole review
#: state with it, so it is repointed like anything else.
_DROPPED_ON_FOLD: frozenset[tuple[str, str]] = frozenset(
    {
        ("decision_candidate_meta", "decision_id"),
        # Keyed ``(decision_id, node_id, link_type)``, so the loser's links
        # would collide with the keeper's wherever the two governed the same
        # file. Dropping is safe because ``sync_decision_node_links`` rebuilds
        # a decision's links from ``affected_files_json`` on every index.
        ("decision_node_links", "decision_id"),
    }
)

#: Unique constraints a fold can walk into, as ``table -> the other columns
#: that make a row unique beside the decision id``. Two records that fold were
#: duplicates, so they are exactly the pair most likely to hold the same
#: evidence row, the same acceptance sequence, or the same edge. Repointing
#: blindly raises ``IntegrityError`` out of a migration that runs at the head
#: of every index, so the loser's row is dropped when the keeper already has
#: its equivalent and moved when it does not.
_FOLD_CONFLICT_KEYS: dict[str, tuple[str, ...]] = {
    "decision_evidence": ("source", "evidence_file", "evidence_commit"),
    "decision_acceptances": ("seq",),
    "decision_edges": ("src_decision_id", "dst_decision_id", "kind"),
}

#: Every column that carries a decision id, including the two that hold one
#: without a foreign key and so are swept along by nothing the database does on
#: its own.
_DEPENDENT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("decision_evidence", "decision_id"),
    ("decision_edges", "src_decision_id"),
    ("decision_edges", "dst_decision_id"),
    ("decision_node_links", "decision_id"),
    ("decision_acceptances", "decision_id"),
    ("decision_candidate_meta", "decision_id"),
    ("decision_candidate_meta", "merged_into"),
    ("decision_aliases", "decision_id"),
    ("decision_records", "superseded_by"),
)


@dataclass(frozen=True)
class IdRowPlan:
    """What this migration would do to one record."""

    old_id: str
    new_id: str
    title: str
    outcome: str
    reason: str = ""


@dataclass
class IdMigrationPlan:
    """The per-record outcomes for one repository."""

    rows: list[IdRowPlan] = field(default_factory=list)
    #: The quote each record's identity is keyed on, backfilled for records
    #: captured before the column existed so ``apply`` can write it down.
    pinned_quotes: dict[str, str] = field(default_factory=dict)

    def rewrites(self) -> list[IdRowPlan]:
        return [row for row in self.rows if row.outcome == "rewrite"]

    def folds(self) -> list[IdRowPlan]:
        return [row for row in self.rows if row.outcome == "fold"]

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.rows:
            counts[row.outcome] = counts.get(row.outcome, 0) + 1
        return counts


async def plan_id_migration(session: AsyncSession, repository_id: str) -> IdMigrationPlan:
    """Classify every record in *repository_id*. Writes nothing.

    Three outcomes. ``stable`` is a record whose id already derives from its
    own identity, which is what a second run sees for everything. ``rewrite``
    is a move. ``fold`` is a record whose identity another record already
    holds, which under evidence-keyed identity is not a collision but the
    answer: the two are one decision worded twice, so the later one is merged
    into the earlier and leaves an alias where it was.
    """
    result = await session.execute(
        select(DecisionRecord)
        .where(DecisionRecord.repository_id == repository_id)
        .order_by(DecisionRecord.created_at, DecisionRecord.id)
    )
    records = list(result.scalars().all())
    quotes = await _pinned_quotes(session, records)
    flagged = await _flagged_ids(session, repository_id)

    # Derived first, for every record, before anything is classified. A
    # record's outcome depends on what the others derive, not on what they
    # hold now, so a single pass in creation order would call the first
    # arrival a collision with an id its holder is about to vacate.
    derived = {
        rec.id: derive_decision_id(
            rec.repository_id,
            rec.title,
            source=rec.source,
            evidence_file=rec.evidence_file,
            affected_files=_json_list(rec.affected_files_json),
            evidence_line=rec.evidence_line,
            identity_quote=quotes.get(rec.id, ""),
            needs_split=rec.id in flagged,
        )
        for rec in records
    }
    # The record that keeps each derived id: the first in creation order, so
    # the folded group ends up under the oldest reading of the decision. A
    # record already sitting on the id keeps it instead, because nothing can
    # be copied onto an id that is still occupied.
    keeper: dict[str, str] = {}
    for rec in records:
        keeper.setdefault(derived[rec.id], rec.id)
    for new_id in keeper:
        if derived.get(new_id) == new_id:
            keeper[new_id] = new_id

    reworded = await _reworded_folds(session, repository_id, records, derived, flagged)

    rows: list[IdRowPlan] = []
    for rec in records:
        new_id = derived[rec.id]
        if rec.id in reworded:
            rows.append(
                IdRowPlan(
                    rec.id,
                    reworded[rec.id],
                    rec.title,
                    "fold",
                    f"same commit and decision text as {reworded[rec.id]}",
                )
            )
        elif keeper[new_id] != rec.id:
            rows.append(
                IdRowPlan(
                    rec.id,
                    new_id,
                    rec.title,
                    "fold",
                    f"same identity as {keeper[new_id]}",
                )
            )
        elif new_id == rec.id:
            rows.append(IdRowPlan(rec.id, new_id, rec.title, "stable"))
        else:
            rows.append(IdRowPlan(rec.id, new_id, rec.title, "rewrite"))

    return IdMigrationPlan(rows=rows, pinned_quotes=quotes)


async def _reworded_folds(
    session: AsyncSession,
    repository_id: str,
    records: list[DecisionRecord],
    derived: dict[str, str],
    flagged: set[str],
) -> dict[str, str]:
    """Records that reword another one mined from the same commit: ``{loser: keeper}``.

    The store-side twin of the ingest check, for the copies stored before it
    existed: one PR body re-mined on every update left a record per wording.

    Only records whose id is already settled take part, either side: one that
    moves or folds by identity this run is judged next run, from where it
    lands. Chaining a second fold onto a move in the same pass is what the
    parking dance above exists to avoid, and the repair runs every index.

    A record a person accepted, set a status on, or reviewed as a candidate
    is never folded away; it is the keeper its rewordings fold into. A flagged
    bundle is neither.
    """
    # An id derived by one record only, and already held by it: nothing moves
    # onto it or off it this run.
    holders = Counter(derived.values())
    settled = [
        rec
        for rec in records
        if derived[rec.id] == rec.id and holders[rec.id] == 1 and rec.id not in flagged
    ]
    if not settled:
        return {}
    accepted = await accepted_decision_ids(session, repository_id)
    reviewed = set(
        (
            await session.execute(
                select(DecisionCandidateMeta.decision_id).where(
                    DecisionCandidateMeta.repository_id == repository_id,
                    DecisionCandidateMeta.review_state != "open",
                )
            )
        )
        .scalars()
        .all()
    )
    index = RewordingIndex()
    folds: dict[str, str] = {}
    for rec in keeper_order(settled, accepted):
        commits, text = duplicate_commits(rec), duplicate_text(rec)
        if can_fold_away(rec, accepted) and rec.id not in reviewed:
            target = index.match(rec.source, commits, text)
            if target is not None:
                folds[rec.id] = target
                continue
        index.add(rec.id, rec.source, commits, text)
    return folds


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


async def _flagged_ids(session: AsyncSession, repository_id: str) -> set[str]:
    """Records whose review row says the claim bundles two decisions.

    Their identity keeps the title, so they are held out of the fold: a
    bundled claim shares files and evidence with the decisions it bundles,
    and folding them would file two decisions under a third one's name.
    """
    result = await session.execute(
        select(DecisionCandidateMeta.decision_id).where(
            DecisionCandidateMeta.repository_id == repository_id,
            DecisionCandidateMeta.needs_split.is_(True),
        )
    )
    return set(result.scalars().all())


async def _pinned_quotes(session: AsyncSession, records: list[DecisionRecord]) -> dict[str, str]:
    """The quote each record's identity is keyed on.

    A record captured since the column exists carries its own, written once.
    One captured before it has an empty column and is backfilled here from
    the strongest evidence row it already holds, which is the span it would
    have pinned had the column existed. Falling back to the decision text
    keeps a record with no evidence row from keying on the empty string,
    which would fold every such record together.
    """
    pinned = {rec.id: rec.identity_quote for rec in records if rec.identity_quote}
    missing = [rec for rec in records if not rec.identity_quote]
    if not missing:
        return pinned
    result = await session.execute(
        select(
            DecisionEvidence.decision_id,
            DecisionEvidence.source_quote,
        )
        .where(DecisionEvidence.decision_id.in_([rec.id for rec in missing]))
        .order_by(DecisionEvidence.source_rank.desc(), DecisionEvidence.id)
    )
    strongest: dict[str, str] = {}
    for decision_id, quote in result.all():
        if quote and decision_id not in strongest:
            strongest[decision_id] = quote
    for rec in missing:
        pinned[rec.id] = strongest.get(rec.id) or rec.decision or rec.title
    return pinned


def _table_names(sync_connection: Any) -> set[str]:
    return set(sa_inspect(sync_connection).get_table_names())


async def _existing_tables(session: AsyncSession) -> set[str]:
    """Which decision tables this store actually has.

    Introspected on the session's own connection. Reaching for the engine
    instead takes a second connection, and closing it rolls back the
    transaction this migration is running in.
    """
    connection = await session.connection()
    return await connection.run_sync(_table_names)


def _parking_id(old_id: str) -> str:
    """A temporary id of the same length that no hex id can equal."""
    return f"~{old_id[1:]}"


async def _move(
    session: AsyncSession,
    from_id: str,
    to_id: str,
    title: str,
    tables: set[str],
) -> None:
    """Move one record to *to_id* without ever orphaning a dependent.

    The order is forced by the foreign keys, which cascade on delete and do
    nothing on update: a dependent cannot point at an id that does not exist
    yet, and a record cannot change a primary key that dependents still point
    at. So the copy comes first and the old row is released last, and between
    those two the decision exists under both ids.

    The copy lands under a placeholder title because the real title is part of
    ``uq_decision_record``, and for the moment both rows exist the real one
    would make them the same decision twice.
    """
    columns = [column.name for column in DecisionRecord.__table__.columns]
    placeholder = f"repowise:id-migration:{from_id}"
    projected = ", ".join(
        ":new_id" if name == "id" else (":placeholder" if name == "title" else name)
        for name in columns
    )
    column_list = ", ".join(columns)
    await session.execute(
        _sql_text(
            f"INSERT INTO decision_records ({column_list}) "
            f"SELECT {projected} FROM decision_records WHERE id = :old_id"
        ),
        {"new_id": to_id, "placeholder": placeholder, "old_id": from_id},
    )

    for table, column in _DEPENDENT_COLUMNS:
        if table not in tables:
            continue
        await session.execute(
            _sql_text(f"UPDATE {table} SET {column} = :new_id WHERE {column} = :old_id"),
            {"new_id": to_id, "old_id": from_id},
        )

    await session.execute(
        _sql_text("DELETE FROM decision_records WHERE id = :old_id"),
        {"old_id": from_id},
    )
    await session.execute(
        _sql_text("UPDATE decision_records SET title = :title WHERE id = :new_id"),
        {"title": title, "new_id": to_id},
    )


async def _write_alias(
    session: AsyncSession,
    repository_id: str,
    row: IdRowPlan,
    reason: str,
    tables: set[str],
) -> None:
    """Record that *row*'s original id now resolves to its final id."""
    if "decision_aliases" not in tables:
        return
    if await session.get(DecisionAlias, row.old_id) is not None:
        # This id was already retired once, and the row saying where it went is
        # not ours to rewrite. A merged candidate keeps its record, so it is
        # reachable here: repointing its alias at the moved candidate would
        # undo the merge and leave nothing recording that it happened.
        return
    session.add(
        DecisionAlias(
            alias_id=row.old_id,
            repository_id=repository_id,
            decision_id=row.new_id,
            reason=reason,
            created_at=_now_utc(),
        )
    )


async def repair_superseded_aliases(
    session: AsyncSession, repository_id: str, *, apply: bool = True
) -> tuple[list[str], list[str]]:
    """Delete the supersede pointers a pre-fix store left on records still on their id.

    Returns ``(deleted, unplaced)`` alias ids. A pointer whose record moved names
    the successor, so it stays and resolves to nothing. ``apply=False`` only reports.
    """
    pointers = (
        (
            await session.execute(
                select(DecisionAlias).where(
                    DecisionAlias.repository_id == repository_id,
                    DecisionAlias.reason == SUPERSEDED_REASON,
                )
            )
        )
        .scalars()
        .all()
    )
    # No pointers, no pieces: a clean store pays for the one query above.
    live: set[str] = set()
    for piece in chunked([p.alias_id for p in pointers]):
        live.update(
            (await session.execute(select(DecisionRecord.id).where(DecisionRecord.id.in_(piece))))
            .scalars()
            .all()
        )
    stale = [p for p in pointers if p.alias_id in live]
    deleted = [p.alias_id for p in stale]
    unplaced = [p.alias_id for p in pointers if p.alias_id not in live]
    if apply and stale:
        for pointer in stale:
            await session.delete(pointer)
        await session.flush()
        logger.info(
            "decision_supersede_aliases_repaired",
            repository_id=repository_id,
            deleted=len(deleted),
            unplaced=len(unplaced),
        )
    return deleted, unplaced


async def _fold_one(
    session: AsyncSession,
    from_id: str,
    row: IdRowPlan,
    tables: set[str],
) -> None:
    """Merge one record into the record its identity already names.

    The dependents move first and the record is released last, for the same
    reason a rewrite copies before it deletes: a foreign key cascades on
    delete and does nothing on update, so a row pointed at a decision that
    stops existing goes with it.

    Two records only fold because they were duplicates, which makes them the
    pair most likely to hold the same evidence row, the same acceptance
    sequence, or the same edge. So a row the keeper already has an equivalent
    of is dropped rather than moved: repointing it would raise on the unique
    constraint, out of a migration that runs at the head of every index.
    """
    row = IdRowPlan(from_id, row.new_id, row.title, row.outcome, row.reason)
    for table, column in _DEPENDENT_COLUMNS:
        if table not in tables:
            continue
        if (table, column) in _DROPPED_ON_FOLD:
            await session.execute(
                _sql_text(f"DELETE FROM {table} WHERE {column} = :old_id"),
                {"old_id": row.old_id},
            )
            continue
        conflict = _FOLD_CONFLICT_KEYS.get(table)
        if conflict:
            await _drop_rows_the_keeper_already_has(session, table, column, conflict, row)
        await session.execute(
            _sql_text(f"UPDATE {table} SET {column} = :new_id WHERE {column} = :old_id"),
            {"new_id": row.new_id, "old_id": row.old_id},
        )

    await session.execute(
        _sql_text("DELETE FROM decision_records WHERE id = :old_id"),
        {"old_id": row.old_id},
    )


async def _drop_rows_the_keeper_already_has(
    session: AsyncSession,
    table: str,
    column: str,
    conflict: tuple[str, ...],
    row: IdRowPlan,
) -> None:
    """Delete the loser's rows that would collide once they are repointed.

    Compares on the columns that make a row unique beside the decision id.
    ``IS NOT DISTINCT FROM`` is not available on SQLite and those columns are
    nullable, so the comparison is spelled out: equal, or both absent.

    An edge between the two records being folded is dropped as well. Once they
    are one record it points at itself, which is a relationship the graph has
    no reading for.
    """
    others = [name for name in conflict if name != column]
    matches = "".join(
        f" AND (keeper.{name} = {table}.{name}"
        f" OR (keeper.{name} IS NULL AND {table}.{name} IS NULL))"
        for name in others
    )
    await session.execute(
        _sql_text(
            f"DELETE FROM {table} WHERE {column} = :old_id AND EXISTS ("
            f" SELECT 1 FROM {table} AS keeper"
            f" WHERE keeper.{column} = :new_id{matches})"
        ),
        {"new_id": row.new_id, "old_id": row.old_id},
    )
    for name in others:
        if not name.endswith("decision_id"):
            continue
        await session.execute(
            _sql_text(f"DELETE FROM {table} WHERE {column} = :old_id AND {name} = :new_id"),
            {"new_id": row.new_id, "old_id": row.old_id},
        )


def _detach_moved(session: AsyncSession) -> None:
    """Drop every loaded decision row from the session's identity map.

    The rewrites are raw SQL, so anything already loaded still holds the id it
    was loaded under and a later flush would write that departed id back. The
    dependents matter as much as the records: ``DecisionCandidateMeta`` is
    keyed *by* ``decision_id``, so a stale instance flushes an UPDATE matching
    no rows. Detaching rather than expiring, because expiring makes every one
    of them reload on next access, including for a caller that only wanted a
    field it already had.
    """
    detachable = (
        DecisionRecord,
        DecisionAlias,
        DecisionAcceptance,
        DecisionCandidateMeta,
        DecisionEdge,
        DecisionEvidence,
        DecisionNodeLink,
    )
    for obj in list(session.sync_session.identity_map.values()):
        if isinstance(obj, detachable):
            session.expunge(obj)


async def _vector_rewrites(
    session: AsyncSession,
    repository_id: str,
    rewrites: list[IdRowPlan],
    *,
    include_live_sources: set[str] | None = None,
) -> tuple[dict[str, str], set[str]]:
    """Include prior SQL moves whose retained vector can still be recovered.

    A target with a historical alias may still hold its former occupant's
    vector. Mark it so retries can fill an empty key but verify an occupied one.
    """
    source_record = aliased(DecisionRecord)
    target_alias = aliased(DecisionAlias)
    returned_ids = {row.new_id for row in rewrites}
    aliases = await session.execute(
        select(DecisionAlias.alias_id, DecisionAlias.decision_id, target_alias.alias_id)
        .join(DecisionRecord, DecisionRecord.id == DecisionAlias.decision_id)
        .outerjoin(source_record, source_record.id == DecisionAlias.alias_id)
        .outerjoin(
            target_alias,
            (target_alias.alias_id == DecisionAlias.decision_id)
            & target_alias.reason.in_((MERGED_REASON, REKEYED_REASON)),
        )
        .where(
            DecisionAlias.repository_id == repository_id,
            DecisionAlias.reason == REKEYED_REASON,
            DecisionRecord.repository_id == repository_id,
            or_(
                source_record.id.is_(None),
                DecisionAlias.alias_id.in_(include_live_sources or ()),
                (DecisionAlias.alias_id == DecisionAlias.decision_id)
                & DecisionAlias.alias_id.in_(returned_ids),
            ),
        )
    )
    rows = aliases.all()
    return {old: new for old, new, _ in rows}, {new for _, new, marker in rows if marker}


async def _existing_vector_ids(vector_store: Any, keys: set[str]) -> set[str]:
    if not keys:
        return set()
    lookup = getattr(vector_store, "existing_page_ids", None)
    if lookup is not None:
        return await lookup(keys)
    return keys & await vector_store.list_page_ids()


async def _preserve_reused_vector_sources(
    session: AsyncSession,
    repository_id: str,
    vector_store: Any,
    fresh_moves: dict[str, str],
) -> None:
    """Rescue retired vectors before their keys become live for another record."""
    aliases, _ = await _vector_rewrites(session, repository_id, [])
    targets = set(fresh_moves.values())
    reused = {
        old: new for old, new in aliases.items() if old in targets and fresh_moves.get(new) != old
    }
    present = await _existing_vector_ids(
        vector_store,
        {f"{DECISION_VECTOR_PREFIX}{key}" for key in reused.keys() | set(reused.values())},
    )
    for old, new in reused.items():
        source_key = f"{DECISION_VECTOR_PREFIX}{old}"
        target_key = f"{DECISION_VECTOR_PREFIX}{new}"
        if source_key not in present:
            continue
        if target_key not in present:
            candidates = {
                f"{DECISION_VECTOR_PREFIX}{alias}"
                for alias, destination in aliases.items()
                if destination == new
            }
            retained = await _existing_vector_ids(vector_store, candidates)
            if len(retained) > 1 and not await vector_store.vectors_match(retained):
                raise RuntimeError("Multiple retained vector sources for a reused decision ID")
            await vector_store.copy_vectors({source_key: target_key})
            present.add(target_key)
        if not await vector_store.vectors_match({source_key, target_key}):
            raise RuntimeError(
                "Retained vector could not be verified before reusing its decision ID"
            )


async def _recover_occupied_sources(
    session: AsyncSession,
    repository_id: str,
    vector_store: Any,
    fresh_moves: dict[str, str],
) -> set[str]:
    """A live source can still hold its former occupant after a SQL-only move."""
    sources = set(fresh_moves)
    aliases, _ = await _vector_rewrites(session, repository_id, [], include_live_sources=sources)
    prior_targets = {old: new for old, new in aliases.items() if old in sources and new != old}
    incoming = {
        old: new for old, new in aliases.items() if old not in sources and new in prior_targets
    }
    keys = set(incoming) | set(prior_targets) | set(prior_targets.values())
    present = await _existing_vector_ids(
        vector_store, {f"{DECISION_VECTOR_PREFIX}{key}" for key in keys}
    )
    recovered: set[str] = set()
    for old, prior in prior_targets.items():
        source_key = f"{DECISION_VECTOR_PREFIX}{old}"
        prior_key = f"{DECISION_VECTOR_PREFIX}{prior}"
        if source_key not in present:
            continue
        retained = {
            f"{DECISION_VECTOR_PREFIX}{alias}"
            for alias, target in incoming.items()
            if target == old
        } & present
        if not retained:
            continue
        if len(retained) > 1 and not await vector_store.vectors_match(retained):
            raise RuntimeError("Multiple retained vector sources for a rewritten decision")
        if await _recover_prior_vector(vector_store, source_key, prior_key, prior_key in present):
            recovered.add(old)
    return recovered


async def _recover_prior_vector(
    vector_store: Any, source_key: str, prior_key: str, prior_present: bool
) -> bool:
    if not prior_present and await vector_store.copy_vectors({source_key: prior_key}) != {
        source_key
    }:
        raise RuntimeError("Prior decision vector could not be copied before moving its key")
    matches = await vector_store.vectors_match({source_key, prior_key})
    if not matches and not prior_present:
        raise RuntimeError("Prior decision vector could not be verified before moving its key")
    return matches


async def _clear_stale_rewrite_destinations(
    vector_store: Any,
    moved: dict[str, str],
    former_ids: set[str],
    fresh_moves: dict[str, str],
    folded_ids: set[str],
) -> set[str]:
    """Clear a fresh target only when retry can recover a different source."""
    targets = set(fresh_moves.values()) - former_ids
    mapping = {
        f"{DECISION_VECTOR_PREFIX}{old}": f"{DECISION_VECTOR_PREFIX}{new}"
        for old, new in moved.items()
        if new in targets and (old not in former_ids or fresh_moves.get(old) == new)
    }
    present = await _existing_vector_ids(vector_store, mapping.keys() | set(mapping.values()))
    retained = {old: new for old, new in mapping.items() if old in present}
    if await _conflicting_vector_destinations(vector_store, retained):
        raise RuntimeError("Multiple retained vector sources for a rewritten decision")
    stale = (set(retained.values()) & present) - retained.keys()
    if not stale:
        return set()
    # A folded occupant still needs its vector if the SQL savepoint rolls
    # back. Return the backup keys for cleanup after release of the savepoint.
    backups = {
        key: f"{_VECTOR_BACKUP_PREFIX}{key[len(DECISION_VECTOR_PREFIX) :]}"
        for key in stale
        if key[len(DECISION_VECTOR_PREFIX) :] in folded_ids
    }
    if backups:
        await _preserve_folded_vectors(vector_store, backups)
    try:
        await vector_store.delete_many(sorted(stale))
        if await _existing_vector_ids(vector_store, stale):
            raise RuntimeError("Stale decision vector destinations could not be cleared")
    except Exception:
        if backups:
            await _restore_folded_vectors(vector_store, backups)
        raise
    return set(backups.values())


async def _preserve_folded_vectors(vector_store: Any, backups: dict[str, str]) -> None:
    existing = await _existing_vector_ids(vector_store, set(backups.values()))
    missing = {key: backup for key, backup in backups.items() if backup not in existing}
    if missing and await vector_store.copy_vectors(missing) != missing.keys():
        raise RuntimeError("Could not preserve the folded decision vectors")
    for key, backup in backups.items():
        if not await vector_store.vectors_match({key, backup}):
            raise RuntimeError("Could not verify the folded decision vector copy")


async def _restore_folded_vectors(vector_store: Any, backups: dict[str, str]) -> None:
    await vector_store.copy_vectors({backup: key for key, backup in backups.items()})
    for key, backup in backups.items():
        if not await vector_store.vectors_match({key, backup}):
            raise RuntimeError("Could not restore the folded decision vector") from None
        await vector_store.delete(backup)


@dataclass(frozen=True)
class _RekeyCopy:
    """One rekey pass after its copy step: the keys it moves and what still needs embedding."""

    mapping: dict[str, str]
    present: set[str]
    fresh_keys: set[str]
    occupied_keys: set[str]
    missing: set[str]


async def _rekey_vectors(
    vector_store: Any,
    session: AsyncSession,
    moved: dict[str, str],
    fresh_sources: set[str] | None = None,
    reused_ids: set[str] | None = None,
    *,
    require_durable_copy: bool = False,
) -> int:
    """Preserve stored values; release old keys only after destination readback."""
    if not moved:
        return 0
    try:
        copy = await _copy_moved_vectors(
            vector_store, moved, fresh_sources or set(), reused_ids or set(), require_durable_copy
        )
    except DecisionVectorRecoveryError:
        raise
    except Exception as exc:
        if require_durable_copy:
            raise DecisionVectorRecoveryError(
                "Cannot verify durable decision vector copies"
            ) from exc
        # A failed copy must remain retryable without recomputing its source.
        logger.warning("decision_vector_copy_pending", error_type=type(exc).__name__)
        return 0
    if copy is None:
        return 0
    occupied_missing = await _clear_occupied_destinations(vector_store, copy, require_durable_copy)
    await _embed_rekeyed_vectors(
        vector_store, session, {new[len(DECISION_VECTOR_PREFIX) :] for new in copy.missing}
    )
    present = await _read_back_destinations(
        vector_store, copy, occupied_missing, require_durable_copy
    )
    if present is None:
        return 0
    landed = await _landed_sources(vector_store, copy, present)
    if landed:
        await vector_store.delete_many(sorted(landed))
    return len(landed)


async def _copy_moved_vectors(
    vector_store: Any,
    moved: dict[str, str],
    fresh: set[str],
    reused_ids: set[str],
    require_durable_copy: bool,
) -> _RekeyCopy | None:
    """Copy each retained source to its new key; None when nothing is pending."""
    keys = {f"{DECISION_VECTOR_PREFIX}{key}" for key in moved.keys() | set(moved.values())}
    present = await _existing_vector_ids(vector_store, keys)
    # A reused ID with a vector may still hold the former occupant's
    # value. Only the current SQL move knows the source for that target.
    occupied_keys = {
        f"{DECISION_VECTOR_PREFIX}{target}"
        for target in reused_ids
        if f"{DECISION_VECTOR_PREFIX}{target}" in present
    }
    mapping = _pending_vector_mapping(moved, present, fresh)
    if not mapping:
        return None
    fresh_keys = {f"{DECISION_VECTOR_PREFIX}{old}" for old in fresh}
    mapping = await _drop_conflicting_destinations(
        vector_store, mapping, present, fresh_keys, occupied_keys, require_durable_copy
    )
    missing = await _copy_rekeyed_vectors(
        vector_store, mapping, present, fresh_keys, require_verified=require_durable_copy
    )
    return _RekeyCopy(mapping, present, fresh_keys, occupied_keys, missing)


async def _drop_conflicting_destinations(
    vector_store: Any,
    mapping: dict[str, str],
    present: set[str],
    fresh_keys: set[str],
    occupied_keys: set[str],
    require_durable_copy: bool,
) -> dict[str, str]:
    """Leave out destinations whose retained sources disagree."""
    candidates = {old: new for old, new in mapping.items() if old in present}
    conflicts = await _conflicting_vector_destinations(
        vector_store, candidates, fresh_keys, present & set(mapping.values()), occupied_keys
    )
    fresh_destinations = {mapping[old] for old in fresh_keys & mapping.keys()}
    if require_durable_copy and conflicts & fresh_destinations:
        raise DecisionVectorRecoveryError("Ambiguous retained source for a moved decision vector")
    if not conflicts:
        return mapping
    logger.warning("decision_vector_sources_conflict", destinations=len(conflicts))
    return {old: new for old, new in mapping.items() if new not in conflicts}


async def _clear_occupied_destinations(
    vector_store: Any, copy: _RekeyCopy, require_durable_copy: bool
) -> set[str]:
    """Clear an occupant before embedding, so readback verifies a replacement.

    A destination can still hold a moved or folded decision's vector.
    """
    if not require_durable_copy:
        return set()
    occupied_missing = copy.missing & copy.present & (copy.mapping.keys() | copy.occupied_keys)
    if occupied_missing:
        await vector_store.delete_many(sorted(occupied_missing))
        if await _existing_vector_ids(vector_store, occupied_missing):
            raise DecisionVectorRecoveryError("Cannot clear the previous decision vector")
    return occupied_missing


async def _read_back_destinations(
    vector_store: Any, copy: _RekeyCopy, occupied_missing: set[str], require_durable_copy: bool
) -> set[str] | None:
    """The keys present after copy and embedding; None when a readback must be retried."""
    try:
        present = await _existing_vector_ids(
            vector_store, copy.mapping.keys() | set(copy.mapping.values())
        )
    except Exception as exc:
        if require_durable_copy:
            raise DecisionVectorRecoveryError("Cannot read back durable decision vectors") from exc
        logger.warning("decision_vector_readback_pending", error_type=type(exc).__name__)
        return None
    if require_durable_copy and (set(copy.mapping.values()) - copy.missing) - present:
        raise DecisionVectorRecoveryError(
            "A verified decision vector destination is no longer present"
        )
    if occupied_missing - present:
        raise DecisionVectorRecoveryError("Cannot replace the previous decision vector")
    return present


async def _landed_sources(vector_store: Any, copy: _RekeyCopy, present: set[str]) -> set[str]:
    """Old keys whose value now stands at the new key and may be released."""
    mapping = copy.mapping
    destinations = set(mapping.values())
    fresh_destinations = {mapping[old] for old in copy.fresh_keys & mapping.keys()}
    # Existence alone cannot confirm a replacement at an already occupied key.
    unconfirmed = fresh_destinations & copy.missing
    landed = {
        old
        for old, new in mapping.items()
        if old in present and new in present and old not in destinations and new not in unconfirmed
    }
    # A fresh copy establishes the new occupant, not the origin of a
    # different historical vector. Only discard confirmed duplicates.
    reused_destinations = fresh_destinations & copy.occupied_keys
    for old in landed - copy.fresh_keys:
        new = mapping[old]
        if new in reused_destinations and not await vector_store.vectors_match({old, new}):
            landed.remove(old)
    return landed


def _pending_vector_mapping(
    moved: dict[str, str], present: set[str], fresh_sources: set[str]
) -> dict[str, str]:
    """Retained alias sources, plus decisions freshly moved by this call."""
    return {
        f"{DECISION_VECTOR_PREFIX}{old}": f"{DECISION_VECTOR_PREFIX}{new}"
        for old, new in moved.items()
        if f"{DECISION_VECTOR_PREFIX}{old}" in present or old in fresh_sources
    }


async def _conflicting_vector_destinations(
    vector_store: Any,
    retained: dict[str, str],
    fresh_keys: set[str] | None = None,
    occupied: set[str] | None = None,
    occupied_reused_ids: set[str] | None = None,
) -> set[str]:
    """Find destinations whose retained values disagree or cannot be compared."""
    candidates: dict[str, set[str]] = defaultdict(set)
    for old, new in retained.items():
        candidates[new].add(old)

    blocked = set()
    matches = getattr(vector_store, "vectors_match", None)
    for new, sources in candidates.items():
        if sources & (fresh_keys or set()) or (
            len(sources) <= 1 and new not in (occupied_reused_ids or ())
        ):
            continue
        existing = {new} & (occupied or set())
        if matches is None or not await matches(sources | existing):
            blocked.add(new)
    return blocked


async def _copy_rekeyed_vectors(
    vector_store: Any,
    mapping: dict[str, str],
    present: set[str],
    fresh_keys: set[str],
    *,
    require_verified: bool = False,
) -> set[str]:
    """Copy retained sources; return destinations needing the embedding fallback."""
    pending = {
        old: new
        for old, new in mapping.items()
        if old in fresh_keys or new not in present or new in mapping
    }
    # A fresh source owns its vector. Prefer it over an older alias if
    # both point to the same destination.
    ordered = sorted(pending, key=lambda old: (old in fresh_keys, old))
    sources_by_target = {pending[old]: old for old in ordered if old in present}
    copies = {old: new for new, old in sources_by_target.items()}
    copy = getattr(vector_store, "copy_vectors", None)
    before = await vector_store.snapshot_vectors(set(copies)) if require_verified and copies else {}
    copied = await copy(copies) if copies and copy is not None else set()
    if before:
        expected = {
            new: {**before[old], "page_id": new}
            for old, new in copies.items()
            if before[old] is not None
        }
        if (
            copied != copies.keys()
            or len(expected) != len(copies)
            or await vector_store.snapshot_vectors(set(expected)) != expected
        ):
            raise DecisionVectorRecoveryError(
                "The stored decision vector copy did not match its source"
            )
    return set(pending.values()) - {copies[old] for old in copied}


async def _embed_rekeyed_vectors(
    vector_store: Any, session: AsyncSession, new_ids: set[str]
) -> None:
    """Keep the existing embedding fallback for stores without raw copies."""
    if not new_ids:
        return
    result = await session.execute(
        select(
            DecisionRecord.id,
            DecisionRecord.title,
            DecisionRecord.decision,
            DecisionRecord.evidence_file,
        ).where(DecisionRecord.id.in_(new_ids))
    )
    items = []
    for new_id, title, decision, evidence_file in result.all():
        item = decision_vector_item(
            new_id, title=title, decision=decision, evidence_file=evidence_file
        )
        if item is not None:
            items.append(item)
    await upsert_decision_vectors(vector_store, items)


async def apply_id_migration(
    session: AsyncSession,
    repository_id: str,
    *,
    plan: IdMigrationPlan | None = None,
    vector_store: Any = None,
) -> IdMigrationPlan:
    """Settle every record onto its derived id, and re-key its vector.

    The supersede pointers an older store left are repaired before anything
    else. Then three things happen, in this order, and the order is forced.
    The pinned quote is written down first, so the identity a record derives
    stops depending on a backfill being recomputed the same way next run.
    Rewrites come next, so every keeper is sitting on its derived id. Folds
    come last, because a fold points dependents at the keeper's *new* id.

    A record sitting on another's target is parked on a temporary id first,
    so chains and cycles of occupied ids settle in one run. Aliases name the
    original id and the final one, never the parking id.

    A second run classifies every id as ``stable`` and leaves SQL unchanged.
    Rekeyed aliases still finish any vector moves the earlier run could not.
    """
    if isinstance(vector_store, LanceDBVectorStore):
        _require_vector_recovery(session.sync_session)
        await _recover_vector_undo(session, repository_id, vector_store)
        await vector_store.refresh_vector_snapshot()
    # First, so a record that moves below finds its old id free for its alias.
    await repair_superseded_aliases(session, repository_id)
    plan = plan or await plan_id_migration(session, repository_id)
    rewrites = plan.rewrites()
    folds = plan.folds()

    # Written even when nothing moves: a store whose ids already derive can
    # still be carrying records that have never pinned their quote, and the
    # pin is what makes the next re-extraction leave those ids alone.
    pinned = await _pin_quotes(session, plan)

    if not rewrites and not folds:
        if pinned:
            await session.flush()
        if vector_store is not None:
            await _finish_retained_vectors(session, repository_id, vector_store)
        return plan

    # The rewrites are raw SQL, so a record already loaded keeps the id it was
    # loaded under and could write it back. Flush what is pending first, and
    # detach the moved records afterwards.
    await session.flush()

    tables = await _existing_tables(session)
    fresh_moves = {row.old_id: row.new_id for row in rewrites}
    durable = isinstance(vector_store, LanceDBVectorStore)
    # One savepoint covers every move so a failed migration cannot leave a
    # placeholder copy or a parked record in the caller's transaction.
    async with session.begin_nested():
        recovered_sources = await _prepare_vectors_for_moves(
            session, repository_id, vector_store, fresh_moves, folds
        )
        await _move_rows(session, repository_id, rewrites, folds, set(fresh_moves.values()), tables)
        after = await _vectors_after_moves(
            session, repository_id, vector_store, plan, rewrites, folds, fresh_moves
        )
        if durable:
            # Keep SQL moves and verification of existing vectors inside
            # the same savepoint so a caught copy failure cannot commit.
            rekeyed, dropped = await _finish_vector_migration(
                vector_store,
                session,
                fresh_moves,
                after.moved,
                folds,
                after.reused,
                recovered_sources,
                after.backup_keys,
                require_durable_copy=True,
            )
    _detach_moved(session)
    if not durable:
        rekeyed, dropped = await _finish_vector_migration(
            vector_store,
            session,
            fresh_moves,
            after.moved,
            folds,
            after.reused,
            recovered_sources,
            after.backup_keys,
        )

    logger.info(
        "decision_ids_derived",
        repository_id=repository_id,
        rewritten=len(rewrites),
        folded=len(folds),
        quotes_pinned=pinned,
        vectors_rekeyed=rekeyed,
        vectors_dropped=dropped,
    )
    return plan


async def _finish_retained_vectors(
    session: AsyncSession, repository_id: str, vector_store: Any
) -> None:
    """With no SQL move left, finish the vector moves an earlier run could not."""
    aliases, reused_targets = await _vector_rewrites(session, repository_id, [])
    old_keys = {f"{DECISION_VECTOR_PREFIX}{old}" for old in aliases}
    present = await _existing_vector_ids(vector_store, old_keys)
    retained = {
        old: new for old, new in aliases.items() if f"{DECISION_VECTOR_PREFIX}{old}" in present
    }
    await _preserve_vectors_until_commit(
        session, repository_id, vector_store, retained.keys() | set(retained.values())
    )
    await _rekey_vectors(vector_store, session, retained, reused_ids=reused_targets)


async def _prepare_vectors_for_moves(
    session: AsyncSession,
    repository_id: str,
    vector_store: Any,
    fresh_moves: dict[str, str],
    folds: list[IdRowPlan],
) -> set[str]:
    """Journal and rescue vectors before the SQL moves; return the recovered sources."""
    if vector_store is None or not fresh_moves:
        return set()
    aliases, _ = await _vector_rewrites(
        session, repository_id, [], include_live_sources=set(fresh_moves)
    )
    folded = {row.old_id for row in folds}
    await _preserve_vectors_until_commit(
        session,
        repository_id,
        vector_store,
        fresh_moves.keys()
        | set(fresh_moves.values())
        | aliases.keys()
        | set(aliases.values())
        | folded,
        backup_ids=folded,
    )
    await _preserve_reused_vector_sources(session, repository_id, vector_store, fresh_moves)
    return await _recover_occupied_sources(session, repository_id, vector_store, fresh_moves)


async def _move_rows(
    session: AsyncSession,
    repository_id: str,
    rewrites: list[IdRowPlan],
    folds: list[IdRowPlan],
    targets: set[str],
    tables: set[str],
) -> None:
    """Park occupants, move each rewrite onto its derived id, fold the folds, and write each alias."""
    current = {row.old_id: row.old_id for row in (*rewrites, *folds)}
    for row in (*rewrites, *folds):
        if row.old_id in targets:
            current[row.old_id] = _parking_id(row.old_id)
            await _move(session, row.old_id, current[row.old_id], row.title, tables)
    for row in rewrites:
        await _move(session, current[row.old_id], row.new_id, row.title, tables)
        await _write_alias(session, repository_id, row, REKEYED_REASON, tables)
    for row in folds:
        await _fold_one(session, current[row.old_id], row, tables)
        await _write_alias(session, repository_id, row, MERGED_REASON, tables)
    await session.flush()


@dataclass(frozen=True)
class _MovedVectors:
    """The vector moves the SQL moves call for, read inside the savepoint."""

    moved: dict[str, str] = field(default_factory=dict)
    reused: set[str] = field(default_factory=set)
    backup_keys: set[str] = field(default_factory=set)


async def _vectors_after_moves(
    session: AsyncSession,
    repository_id: str,
    vector_store: Any,
    plan: IdMigrationPlan,
    rewrites: list[IdRowPlan],
    folds: list[IdRowPlan],
    fresh_moves: dict[str, str],
) -> _MovedVectors:
    """Read the vector moves and clear stale destinations while the SQL can still roll back."""
    if vector_store is None:
        return _MovedVectors()
    moved, reused = await _vector_rewrites(session, repository_id, rewrites)
    folded = {row.old_id for row in folds}
    if not fresh_moves:
        await _preserve_vectors_until_commit(
            session, repository_id, vector_store, moved.keys() | set(moved.values()) | folded
        )
        return _MovedVectors(moved, reused)
    # Use the aliases that a later retry would find, while a
    # failed clear can still roll back the SQL move.
    backup_keys = await _clear_stale_rewrite_destinations(
        vector_store,
        moved,
        {row.old_id for row in plan.rows if row.outcome != "fold"},
        fresh_moves,
        folded,
    )
    return _MovedVectors(moved, reused, backup_keys)


async def _finish_vector_migration(
    vector_store: Any,
    session: AsyncSession,
    fresh_moves: dict[str, str],
    moved: dict[str, str],
    folds: list[IdRowPlan],
    reused: set[str],
    recovered_sources: set[str],
    backup_keys: set[str],
    *,
    require_durable_copy: bool = False,
) -> tuple[int, int]:
    if vector_store is None:
        return 0, 0

    # A source can be another decision's destination in this pass.
    moved.update({old: new for old, new in fresh_moves.items() if old not in recovered_sources})
    rekeyed = await _rekey_vectors(
        vector_store,
        session,
        moved,
        set(fresh_moves) - recovered_sources,
        reused,
        require_durable_copy=require_durable_copy,
    )
    folded_keys = {f"{DECISION_VECTOR_PREFIX}{row.old_id}" for row in folds}
    keeper_keys = {f"{DECISION_VECTOR_PREFIX}{target}" for target in fresh_moves.values()}
    dropped = await _drop_folded_vectors(vector_store, (folded_keys - keeper_keys) | backup_keys)
    return rekeyed, dropped


async def _pin_quotes(session: AsyncSession, plan: IdMigrationPlan) -> int:
    """Write each record's identity quote into the row it belongs to.

    The plan derived it, from the strongest evidence row for anything
    captured before the column existed. Storing it is what stops the
    derivation from depending on evidence that a later run may have accreted
    more of: the pin is the point, and a pin nobody writes down is a
    recomputation.
    """
    written = 0
    for row in plan.rows:
        if row.outcome == "fold":
            continue
        quote = plan.pinned_quotes.get(row.old_id)
        if not quote:
            continue
        rec = await session.get(DecisionRecord, row.old_id)
        if rec is not None and not rec.identity_quote:
            rec.identity_quote = quote
            written += 1
    return written


async def _drop_folded_vectors(vector_store: Any, keys: set[str]) -> int:
    """Delete only unoccupied keys of records removed by a fold."""
    if not keys:
        return 0
    try:
        stale = await _existing_vector_ids(vector_store, keys)
    except Exception:
        return 0
    if stale:
        await vector_store.delete_many(sorted(stale))
    return len(stale)
