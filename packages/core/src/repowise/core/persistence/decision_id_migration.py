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

Nothing is deleted. A record moves by being copied to its derived id, having
every dependent row repointed at the copy, and only then releasing the id it
came from; an alias records where it went, so an id already written down
somewhere keeps resolving. Idempotent: a second run finds every id already
derived and does nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy import text as _sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decisions.semantic_match import (
    DECISION_VECTOR_PREFIX,
    decision_vector_item,
    upsert_decision_vectors,
)

from .crud.decisions import derive_decision_id
from .models import (
    DecisionAcceptance,
    DecisionAlias,
    DecisionCandidateMeta,
    DecisionEdge,
    DecisionEvidence,
    DecisionNodeLink,
    DecisionRecord,
    _now_utc,
)

logger = structlog.get_logger(__name__)

#: Written on the alias left behind by a rewrite. Deliberately not "merged":
#: ``resolve_decision_id`` follows a merge even when the aliased record still
#: exists, which is right for a fold and wrong here, where the old id names the
#: same decision rather than a different one that was folded into it.
ALIAS_REASON = "rekeyed"

#: Written on the alias left behind by a fold. ``merged`` is right here in a
#: way it is not for a rekey: the old id names a record that no longer exists,
#: because another record was the same decision.
FOLD_REASON = "merged"

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

    rows: list[IdRowPlan] = []
    for rec in records:
        new_id = derived[rec.id]
        if keeper[new_id] != rec.id:
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


async def _pinned_quotes(
    session: AsyncSession, records: list[DecisionRecord]
) -> dict[str, str]:
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
            _sql_text(
                f"DELETE FROM {table} WHERE {column} = :old_id AND {name} = :new_id"
            ),
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


async def _rekey_vectors(
    vector_store: Any,
    session: AsyncSession,
    rewrites: list[IdRowPlan],
) -> int:
    """Write each moved decision's embedding under its new key, then drop the old.

    The store cannot hand back a stored vector, so the new key is embedded
    rather than moved. An old key is deleted only once its new key is
    demonstrably in the store: a re-key that half-succeeded should leave a
    stale vector for ``doctor`` to report, not a decision with no vector at
    all.
    """
    if not rewrites:
        return 0
    new_ids = [row.new_id for row in rewrites]
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
    if not items:
        return 0

    await upsert_decision_vectors(vector_store, items)

    try:
        present = await vector_store.list_page_ids()
    except Exception:
        return 0
    landed = {
        row.old_id
        for row in rewrites
        if f"{DECISION_VECTOR_PREFIX}{row.new_id}" in present
        and f"{DECISION_VECTOR_PREFIX}{row.old_id}" in present
    }
    if not landed:
        return 0
    await vector_store.delete_many([f"{DECISION_VECTOR_PREFIX}{old}" for old in sorted(landed)])
    return len(landed)


async def apply_id_migration(
    session: AsyncSession,
    repository_id: str,
    *,
    plan: IdMigrationPlan | None = None,
    vector_store: Any = None,
) -> IdMigrationPlan:
    """Settle every record onto its derived id, and re-key its vector.

    Three things happen, in this order, and the order is forced. The pinned
    quote is written down first, so the identity a record derives stops
    depending on a backfill being recomputed the same way next run. Rewrites
    come next, so every keeper is sitting on its derived id. Folds come last,
    because a fold points dependents at the keeper's *new* id.

    A record sitting on another's target is parked on a temporary id first,
    so chains and cycles of occupied ids settle in one run. Aliases name the
    original id and the final one, never the parking id.

    Idempotent: a second run classifies every id as ``stable``, finds no
    rewrites and no folds, and touches nothing.
    """
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
        return plan

    # The rewrites are raw SQL, so a record already loaded keeps the id it was
    # loaded under and could write it back. Flush what is pending first, and
    # detach the moved records afterwards.
    await session.flush()

    tables = await _existing_tables(session)
    targets = {row.new_id for row in rewrites}
    current = {row.old_id: row.old_id for row in (*rewrites, *folds)}
    # One savepoint for every move. Both callers swallow the exception and
    # commit later, so a partial run would keep a placeholder copy or a
    # parked record with no alias naming the id it came from.
    async with session.begin_nested():
        for row in (*rewrites, *folds):
            if row.old_id in targets:
                current[row.old_id] = _parking_id(row.old_id)
                await _move(session, row.old_id, current[row.old_id], row.title, tables)
        for row in rewrites:
            await _move(session, current[row.old_id], row.new_id, row.title, tables)
            await _write_alias(session, repository_id, row, ALIAS_REASON, tables)
        for row in folds:
            await _fold_one(session, current[row.old_id], row, tables)
            await _write_alias(session, repository_id, row, FOLD_REASON, tables)
        await session.flush()
    _detach_moved(session)

    rekeyed = 0
    dropped = 0
    if vector_store is not None:
        rekeyed = await _rekey_vectors(vector_store, session, rewrites)
        dropped = await _drop_folded_vectors(vector_store, folds)

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


async def _drop_folded_vectors(vector_store: Any, folds: list[IdRowPlan]) -> int:
    """Delete the vector of every record a fold removed.

    The keeper's vector stays and still describes the decision. The loser's
    describes a record that no longer exists, so leaving it would let search
    return an id nothing resolves to.
    """
    if not folds:
        return 0
    try:
        present = await vector_store.list_page_ids()
    except Exception:
        return 0
    stale = [
        f"{DECISION_VECTOR_PREFIX}{row.old_id}"
        for row in folds
        if f"{DECISION_VECTOR_PREFIX}{row.old_id}" in present
    ]
    if not stale:
        return 0
    await vector_store.delete_many(sorted(stale))
    return len(stale)
