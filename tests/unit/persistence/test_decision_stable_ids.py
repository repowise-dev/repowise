"""A decision's id derives from its identity, so a rebuild does not move it.

Two contracts. The id of a record is a function of the four columns
``uq_decision_record`` already declares unique, at every site that mints one;
and a store whose records predate that keeps working, because the migration
moves them rather than re-minting them, and leaves an alias behind.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy import text as sql_text

from repowise.core.analysis.decisions.semantic_match import DECISION_VECTOR_PREFIX
from repowise.core.persistence.crud import bulk_upsert_decisions, upsert_decision
from repowise.core.persistence.crud.authority import resolve_decision_id
from repowise.core.persistence.crud.decisions import derive_decision_id
from repowise.core.persistence.decision_graph import sync_decision_node_links
from repowise.core.persistence.decision_id_migration import (
    ALIAS_REASON,
    apply_id_migration,
    plan_id_migration,
)
from repowise.core.persistence.models import (
    DecisionAlias,
    DecisionEvidence,
    DecisionNodeLink,
    DecisionRecord,
)
from tests.unit.persistence.helpers import insert_repo

# Applied per test: two of these are plain functions.
pytestmark: list = []


def _dict(title: str, **overrides) -> dict:
    base = {
        "title": title,
        "decision": f"{title}: do the thing",
        "rationale": "because the alternative was measured slower",
        "source": "session",
        "status": "proposed",
        "affected_files": ["src/app.py"],
        "evidence_file": "src/app.py",
        "confidence": 0.7,
        "verification": "exact",
        "source_quote": f"{title}: do the thing",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# The derivation itself
# ---------------------------------------------------------------------------


def test_the_id_is_a_function_of_the_four_columns_the_schema_calls_unique():
    args = ("repo1", "Prefer the boring option")
    kwargs = {"source": "session", "evidence_file": "src/app.py"}
    first = derive_decision_id(*args, **kwargs)

    assert first == derive_decision_id(*args, **kwargs)
    assert len(first) == 32
    assert all(char in "0123456789abcdef" for char in first)

    # Each of the four is load-bearing.
    assert first != derive_decision_id("repo2", args[1], **kwargs)
    assert first != derive_decision_id(args[0], "Another title", **kwargs)
    assert first != derive_decision_id(*args, source="pr", evidence_file="src/app.py")
    assert first != derive_decision_id(*args, source="session", evidence_file="src/b.py")


def test_a_null_evidence_file_is_not_an_empty_one():
    """The dedupe query treats those as different records, so this must too."""
    absent = derive_decision_id("repo1", "T", source="cli", evidence_file=None)
    empty = derive_decision_id("repo1", "T", source="cli", evidence_file="")
    assert absent != empty


@pytest.mark.asyncio
async def test_upsert_derives_the_id_it_stores(async_session):
    repo = await insert_repo(async_session)
    rec = await upsert_decision(
        async_session,
        repository_id=repo.id,
        title="Prefer the boring option",
        source="cli",
        evidence_file="src/app.py",
    )
    assert rec.id == derive_decision_id(
        repo.id, "Prefer the boring option", source="cli", evidence_file="src/app.py"
    )


@pytest.mark.asyncio
async def test_bulk_upsert_derives_the_id_it_stores(async_session):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_dict("Cache the parse tree")])
    rec = (
        await async_session.execute(
            select(DecisionRecord).where(DecisionRecord.repository_id == repo.id)
        )
    ).scalar_one()
    assert rec.id == derive_decision_id(
        repo.id, rec.title, source=rec.source, evidence_file=rec.evidence_file
    )


@pytest.mark.asyncio
async def test_a_record_built_anywhere_else_still_derives_its_id(async_session):
    """The column default catches construction paths the two callers do not."""
    repo = await insert_repo(async_session)
    rec = DecisionRecord(
        repository_id=repo.id,
        title="Built without an explicit id",
        source="git_archaeology",
        evidence_file=None,
    )
    async_session.add(rec)
    await async_session.flush()

    assert rec.id == derive_decision_id(
        repo.id, "Built without an explicit id", source="git_archaeology", evidence_file=None
    )


@pytest.mark.asyncio
async def test_an_explicit_id_still_wins(async_session):
    """The manifest importer carries ids in from a tracked file."""
    repo = await insert_repo(async_session)
    rec = await upsert_decision(
        async_session,
        repository_id=repo.id,
        title="Imported from the manifest",
        source="cli",
        decision_id="0" * 32,
    )
    assert rec.id == "0" * 32


# ---------------------------------------------------------------------------
# The property the whole change exists for
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_store_rebuilt_from_scratch_gives_every_decision_the_same_id(
    session_factory, async_engine
):
    """Index, rebuild from nothing, and the ids have to match.

    The two repositories are seeded identically and independently. Before the
    id derived from the record, this failed on the first row.
    """
    titles = ["Cache the parse tree", "Ship the CLI first", "Keep the schema flat"]

    async def build() -> dict[str, str]:
        async with session_factory() as session:
            repo = await insert_repo(session, name="rebuilt", local_path="/tmp/rebuilt")
            await bulk_upsert_decisions(
                session, repo.id, [_dict(t) for t in titles]
            )
            await session.commit()
            rows = (
                await session.execute(
                    select(DecisionRecord.title, DecisionRecord.id).where(
                        DecisionRecord.repository_id == repo.id
                    )
                )
            ).all()
            # Drop the records, keep nothing, and build the same repo again.
            await session.execute(sql_text("DELETE FROM decision_node_links"))
            await session.execute(sql_text("DELETE FROM decision_evidence"))
            await session.execute(sql_text("DELETE FROM decision_records"))
            await session.commit()
            return {title: rec_id for title, rec_id in rows}

    first = await build()
    second = await build()

    assert first == second
    assert len(first) == len(titles)


# ---------------------------------------------------------------------------
# Migrating a store whose records predate the derivation
# ---------------------------------------------------------------------------


async def _legacy_record(session, repo_id: str, title: str, **overrides) -> DecisionRecord:
    """A record carrying a random id, the way every store already holds them."""
    rec = DecisionRecord(
        id=overrides.pop("id", None) or f"legacy{title.replace(' ', '')[:10]:0<26}"[:32],
        repository_id=repo_id,
        title=title,
        source="session",
        evidence_file=overrides.pop("evidence_file", "src/app.py"),
        decision=f"{title}: do the thing",
        **overrides,
    )
    session.add(rec)
    await session.flush()
    return rec


@pytest.mark.asyncio
async def test_the_plan_reports_what_it_would_move_and_writes_nothing(async_session):
    repo = await insert_repo(async_session)
    legacy = await _legacy_record(async_session, repo.id, "Legacy one")

    plan = await plan_id_migration(async_session, repo.id)

    assert plan.counts() == {"rewrite": 1}
    assert plan.rewrites()[0].old_id == legacy.id
    assert plan.rewrites()[0].title == "Legacy one"
    # Nothing moved.
    assert (await async_session.get(DecisionRecord, legacy.id)) is not None


@pytest.mark.asyncio
async def test_the_migration_moves_the_record_and_takes_its_dependents_with_it(async_session):
    repo = await insert_repo(async_session)
    legacy = await _legacy_record(async_session, repo.id, "Legacy one")
    async_session.add(
        DecisionEvidence(
            decision_id=legacy.id, source="session", evidence_file="src/app.py", source_quote="q"
        )
    )
    await sync_decision_node_links(
        async_session, repo.id, legacy.id, files=["src/app.py"], modules=[]
    )
    await async_session.flush()
    old_id = legacy.id
    new_id = derive_decision_id(
        repo.id, "Legacy one", source="session", evidence_file="src/app.py"
    )

    await apply_id_migration(async_session, repo.id)

    assert (await async_session.get(DecisionRecord, new_id)) is not None
    assert (await async_session.get(DecisionRecord, old_id)) is None

    evidence_ids = (
        await async_session.execute(select(DecisionEvidence.decision_id))
    ).scalars().all()
    link_ids = (
        await async_session.execute(select(DecisionNodeLink.decision_id))
    ).scalars().all()
    assert set(evidence_ids) == {new_id}
    assert set(link_ids) == {new_id}
    # The title survived the placeholder it was parked under.
    moved = await async_session.get(DecisionRecord, new_id)
    assert moved.title == "Legacy one"


@pytest.mark.asyncio
async def test_the_old_id_still_resolves_afterwards(async_session):
    repo = await insert_repo(async_session)
    legacy = await _legacy_record(async_session, repo.id, "Legacy one")
    old_id = legacy.id

    await apply_id_migration(async_session, repo.id)

    alias = await async_session.get(DecisionAlias, old_id)
    assert alias is not None
    assert alias.reason == ALIAS_REASON
    # The point of the alias: an id written down elsewhere keeps working.
    assert await resolve_decision_id(async_session, old_id) == alias.decision_id


@pytest.mark.asyncio
async def test_the_migration_never_deletes_a_decision(async_session):
    repo = await insert_repo(async_session)
    for title in ("One", "Two", "Three"):
        await _legacy_record(async_session, repo.id, title)
    before = (await async_session.execute(select(func.count(DecisionRecord.id)))).scalar_one()

    await apply_id_migration(async_session, repo.id)

    after = (await async_session.execute(select(func.count(DecisionRecord.id)))).scalar_one()
    assert after == before == 3


@pytest.mark.asyncio
async def test_a_second_run_finds_every_id_already_derived(async_session):
    repo = await insert_repo(async_session)
    await _legacy_record(async_session, repo.id, "Legacy one")

    await apply_id_migration(async_session, repo.id)
    ids_after_first = set(
        (await async_session.execute(select(DecisionRecord.id))).scalars().all()
    )
    aliases_after_first = (
        await async_session.execute(select(func.count(DecisionAlias.alias_id)))
    ).scalar_one()

    second = await apply_id_migration(async_session, repo.id)

    assert second.counts() == {"stable": 1}
    assert (
        set((await async_session.execute(select(DecisionRecord.id))).scalars().all())
        == ids_after_first
    )
    assert (
        await async_session.execute(select(func.count(DecisionAlias.alias_id)))
    ).scalar_one() == aliases_after_first


@pytest.mark.asyncio
async def test_two_records_that_derive_the_same_id_keep_the_second_one(async_session):
    """A real collision, and neither record is ours to discard.

    ``uq_decision_record`` does not fire when ``evidence_file`` is NULL,
    because SQL calls two NULLs distinct, so a store can already hold two rows
    that derive one id. The first moves; the second stays exactly where it is
    rather than being folded into it.
    """
    repo = await insert_repo(async_session)
    first = await _legacy_record(
        async_session, repo.id, "Same identity", id="a" * 32, evidence_file=None
    )
    second = await _legacy_record(
        async_session, repo.id, "Same identity", id="b" * 32, evidence_file=None
    )
    derived = derive_decision_id(
        repo.id, "Same identity", source="session", evidence_file=None
    )

    plan = await apply_id_migration(async_session, repo.id)

    assert plan.counts() == {"rewrite": 1, "collision": 1}
    assert (await async_session.get(DecisionRecord, derived)) is not None
    # The loser kept its row and its id; nothing was merged away.
    survivor = second.id if first.id == "a" * 32 else first.id
    assert (await async_session.get(DecisionRecord, survivor)) is not None
    assert (
        await async_session.execute(select(func.count(DecisionRecord.id)))
    ).scalar_one() == 2


@pytest.mark.asyncio
async def test_the_rewrite_holds_the_foreign_keys_at_every_step(async_session):
    """The ordering is the whole risk: a wrong one cascades rows away.

    The unit-test engine leaves ``foreign_keys`` off, so this turns it on to
    exercise the constraint the real store enforces on every connection.
    """
    await async_session.execute(sql_text("PRAGMA foreign_keys=ON"))
    repo = await insert_repo(async_session)
    legacy = await _legacy_record(async_session, repo.id, "Legacy one")
    async_session.add(
        DecisionEvidence(
            decision_id=legacy.id, source="session", evidence_file="src/app.py", source_quote="q"
        )
    )
    await async_session.flush()

    await apply_id_migration(async_session, repo.id)

    # A cascade would have taken the evidence row with it.
    assert (
        await async_session.execute(select(func.count(DecisionEvidence.id)))
    ).scalar_one() == 1


@pytest.mark.asyncio
async def test_the_decision_vectors_move_to_the_new_key(async_session, in_memory_vector_store):
    repo = await insert_repo(async_session)
    legacy = await _legacy_record(async_session, repo.id, "Legacy one")
    old_id = legacy.id
    await in_memory_vector_store.embed_and_upsert(
        f"{DECISION_VECTOR_PREFIX}{old_id}",
        "Legacy one: do the thing",
        {"title": "Legacy one", "page_type": "decision_record"},
    )
    new_id = derive_decision_id(
        repo.id, "Legacy one", source="session", evidence_file="src/app.py"
    )

    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    keys = await in_memory_vector_store.list_page_ids()
    assert f"{DECISION_VECTOR_PREFIX}{new_id}" in keys
    assert f"{DECISION_VECTOR_PREFIX}{old_id}" not in keys


@pytest.mark.asyncio
async def test_a_merge_alias_survives_the_move(async_session):
    """A merged candidate keeps its record, so the migration reaches it.

    Repointing its alias at the moved candidate would undo the merge and leave
    nothing recording that it happened.
    """
    repo = await insert_repo(async_session)
    folded = await _legacy_record(async_session, repo.id, "Folded away")
    target = await _legacy_record(async_session, repo.id, "The survivor")
    async_session.add(
        DecisionAlias(
            alias_id=folded.id,
            repository_id=repo.id,
            decision_id=target.id,
            reason="merged",
        )
    )
    await async_session.flush()
    folded_id, target_id = folded.id, target.id

    await apply_id_migration(async_session, repo.id)

    alias = await async_session.get(DecisionAlias, folded_id)
    assert alias.reason == "merged"
    # Still points at the survivor, which itself moved, rather than at the
    # candidate that was folded away.
    assert alias.decision_id != folded_id
    assert alias.decision_id == derive_decision_id(
        repo.id, "The survivor", source="session", evidence_file="src/app.py"
    )
    assert target_id != alias.decision_id


@pytest.mark.asyncio
async def test_a_failed_rewrite_leaves_no_half_moved_record(async_session, monkeypatch):
    """Both callers swallow the exception and commit later.

    Without a savepoint the store would keep the copy, under its placeholder
    title, as a phantom decision that every count and search picks up.
    """
    repo = await insert_repo(async_session)
    await _legacy_record(async_session, repo.id, "Legacy one")

    import repowise.core.persistence.decision_id_migration as mod

    real = mod._rewrite_one

    async def _explode(session, repository_id, row, tables):
        await real(session, repository_id, row, tables)
        raise RuntimeError("boom")

    monkeypatch.setattr(mod, "_rewrite_one", _explode)

    with pytest.raises(RuntimeError):
        await apply_id_migration(async_session, repo.id)

    titles = (
        (await async_session.execute(select(DecisionRecord.title))).scalars().all()
    )
    assert titles == ["Legacy one"]
