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

    def rewrites(self) -> list[IdRowPlan]:
        return [row for row in self.rows if row.outcome == "rewrite"]

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.rows:
            counts[row.outcome] = counts.get(row.outcome, 0) + 1
        return counts


async def plan_id_migration(session: AsyncSession, repository_id: str) -> IdMigrationPlan:
    """Classify every record in *repository_id*. Writes nothing.

    Three outcomes. ``stable`` is a record whose id already derives from its
    own identity, which is what a second run sees for everything. ``rewrite``
    is a move. ``collision`` is a record whose derived id is already spoken
    for, and it is left exactly as it is: two records cannot share a primary
    key, and picking a winner would be resolving a duplicate on the user's
    behalf.
    """
    result = await session.execute(
        select(DecisionRecord)
        .where(DecisionRecord.repository_id == repository_id)
        .order_by(DecisionRecord.created_at, DecisionRecord.id)
    )
    records = list(result.scalars().all())
    existing_ids = {rec.id for rec in records}
    claimed: dict[str, str] = {}
    rows: list[IdRowPlan] = []

    for rec in records:
        new_id = derive_decision_id(
            rec.repository_id,
            rec.title,
            source=rec.source,
            evidence_file=rec.evidence_file,
        )
        if new_id == rec.id:
            rows.append(IdRowPlan(rec.id, new_id, rec.title, "stable"))
            continue
        if new_id in existing_ids:
            rows.append(
                IdRowPlan(
                    rec.id,
                    new_id,
                    rec.title,
                    "collision",
                    "derived id already belongs to another record",
                )
            )
            continue
        if new_id in claimed:
            rows.append(
                IdRowPlan(
                    rec.id,
                    new_id,
                    rec.title,
                    "collision",
                    f"another record derives the same id ({claimed[new_id]})",
                )
            )
            continue
        claimed[new_id] = rec.id
        rows.append(IdRowPlan(rec.id, new_id, rec.title, "rewrite"))

    return IdMigrationPlan(rows=rows)


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


async def _rewrite_one(
    session: AsyncSession,
    repository_id: str,
    row: IdRowPlan,
    tables: set[str],
) -> None:
    """Move one record to its derived id without ever orphaning a dependent.

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
    placeholder = f"repowise:id-migration:{row.old_id}"
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
        {"new_id": row.new_id, "placeholder": placeholder, "old_id": row.old_id},
    )

    for table, column in _DEPENDENT_COLUMNS:
        if table not in tables:
            continue
        await session.execute(
            _sql_text(f"UPDATE {table} SET {column} = :new_id WHERE {column} = :old_id"),
            {"new_id": row.new_id, "old_id": row.old_id},
        )

    await session.execute(
        _sql_text("DELETE FROM decision_records WHERE id = :old_id"),
        {"old_id": row.old_id},
    )
    await session.execute(
        _sql_text("UPDATE decision_records SET title = :title WHERE id = :new_id"),
        {"title": row.title, "new_id": row.new_id},
    )

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
            reason=ALIAS_REASON,
            created_at=_now_utc(),
        )
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
    """Move every rewritable record onto its derived id, and re-key its vector.

    Idempotent, because the plan classifies an already-derived id as ``stable``
    and this touches nothing else.
    """
    plan = plan or await plan_id_migration(session, repository_id)
    rewrites = plan.rewrites()
    if not rewrites:
        return plan

    # The rewrites are raw SQL, so a record already loaded keeps the id it was
    # loaded under and could write it back. Flush what is pending first, and
    # detach the moved records afterwards.
    await session.flush()

    tables = await _existing_tables(session)
    for row in rewrites:
        # A savepoint per record, because a rewrite that fails halfway has
        # already inserted the copy under a placeholder title. Both callers
        # swallow the exception and commit later, so without this the store
        # keeps a phantom decision that every count and search would pick up,
        # and that the next run would faithfully rekey rather than clean.
        async with session.begin_nested():
            await _rewrite_one(session, repository_id, row, tables)
    await session.flush()
    _detach_moved(session)

    rekeyed = 0
    if vector_store is not None:
        rekeyed = await _rekey_vectors(vector_store, session, rewrites)

    # Collisions are left where they are, so name them rather than letting a
    # count imply everything moved.
    collisions = [row for row in plan.rows if row.outcome == "collision"]
    logger.info(
        "decision_ids_derived",
        repository_id=repository_id,
        rewritten=len(rewrites),
        vectors_rekeyed=rekeyed,
        unchanged_collisions=[row.old_id for row in collisions],
    )
    return plan
