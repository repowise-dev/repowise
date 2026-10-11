"""A decision's id derives from its identity, so a rebuild does not move it.

Identity is the evidence: the files a decision governs, the span it was read
from, and the quote pinned when it was first captured. Not the title, which
two extractions of one choice word differently. Three contracts follow. Every
site that mints an id derives the same one; two records that are the same
decision worded twice fold into one, and the id of the one that goes away
keeps resolving; and a record flagged as bundling two decisions is held out of
that fold.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

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
    FOLD_REASON,
    apply_id_migration,
    plan_id_migration,
)
from repowise.core.persistence.models import (
    DecisionAlias,
    DecisionCandidateMeta,
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


def _derived_for(rec: DecisionRecord) -> str:
    """The id *rec* would be minted with, read off the row it is stored in."""
    return derive_decision_id(
        rec.repository_id,
        rec.title,
        source=rec.source,
        evidence_file=rec.evidence_file,
        affected_files=json.loads(rec.affected_files_json or "[]"),
        evidence_line=rec.evidence_line,
        identity_quote=rec.identity_quote,
    )


# ---------------------------------------------------------------------------
# The derivation itself
# ---------------------------------------------------------------------------


_KEY = {
    "source": "session",
    "evidence_file": "src/app.py",
    "evidence_line": 12,
    "affected_files": ["src/app.py", "src/b.py"],
    "identity_quote": "we chose sqlite because hook writes must not contend",
}


def test_the_id_is_a_function_of_the_evidence():
    first = derive_decision_id("repo1", "Prefer the boring option", **_KEY)

    assert first == derive_decision_id("repo1", "Prefer the boring option", **_KEY)
    assert len(first) == 32
    assert all(char in "0123456789abcdef" for char in first)

    assert first != derive_decision_id("repo2", "Prefer the boring option", **_KEY)
    assert first != derive_decision_id(
        "repo1", "Prefer the boring option", **{**_KEY, "evidence_file": "src/b.py"}
    )
    assert first != derive_decision_id(
        "repo1", "Prefer the boring option", **{**_KEY, "evidence_line": 13}
    )
    assert first != derive_decision_id(
        "repo1", "Prefer the boring option", **{**_KEY, "affected_files": ["src/app.py"]}
    )
    assert first != derive_decision_id(
        "repo1", "Prefer the boring option", **{**_KEY, "identity_quote": "something else"}
    )


def test_the_title_leaves_the_key():
    """The whole change. Two wordings of one choice are one decision."""
    assert derive_decision_id("repo1", "Prefer the boring option", **_KEY) == (
        derive_decision_id("repo1", "Choose the dull option", **_KEY)
    )


def test_the_lane_that_mined_it_leaves_the_key_too():
    """Two lanes reading the same sentence recorded one decision, not two."""
    assert derive_decision_id("repo1", "T", **_KEY) == derive_decision_id(
        "repo1", "T", **{**_KEY, "source": "pr"}
    )


def test_the_scope_is_a_set_rather_than_a_list():
    """Order and repetition are how a scope was assembled, not what it is."""
    assert derive_decision_id(
        "repo1", "T", **{**_KEY, "affected_files": ["src/b.py", "src/app.py"]}
    ) == derive_decision_id(
        "repo1", "T", **{**_KEY, "affected_files": ["src/app.py", "src/b.py", "src/app.py"]}
    )


def test_a_reworded_quote_does_not_move_the_id():
    """Only whitespace and case; the pin is what handles a real rewording."""
    assert derive_decision_id("repo1", "T", **_KEY) == derive_decision_id(
        "repo1",
        "T",
        **{**_KEY, "identity_quote": "  We Chose SQLite because hook writes" + chr(10) + " must not contend"},
    )


def test_a_bundled_claim_keeps_its_title_in_the_key():
    """Held out of the fold: it shares evidence with what it bundles."""
    plain = derive_decision_id("repo1", "Enable WAL; bound the busy timeout", **_KEY)
    flagged = derive_decision_id(
        "repo1", "Enable WAL; bound the busy timeout", **_KEY, needs_split=True
    )
    assert plain != flagged
    # And two flagged claims over the same evidence stay apart by title.
    assert flagged != derive_decision_id(
        "repo1", "Enable WAL only", **_KEY, needs_split=True
    )


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
        repo.id,
        "Prefer the boring option",
        source="cli",
        evidence_file="src/app.py",
        identity_quote=rec.identity_quote,
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
    assert rec.id == _derived_for(rec)


@pytest.mark.asyncio
async def test_a_record_built_anywhere_else_still_derives_its_id(async_session):
    """The column default catches construction paths the two callers do not."""
    repo = await insert_repo(async_session)
    rec = DecisionRecord(
        repository_id=repo.id,
        title="Built without an explicit id",
        source="git_archaeology",
        evidence_file=None,
        decision="keep the loader separate",
        affected_files_json='["src/app.py"]',
    )
    async_session.add(rec)
    await async_session.flush()

    # The default pins the decision text, the same fallback ``upsert_decision``
    # uses, so the two paths cannot derive different ids for one record.
    assert rec.id == derive_decision_id(
        repo.id,
        "Built without an explicit id",
        source="git_archaeology",
        evidence_file=None,
        affected_files=["src/app.py"],
        identity_quote="keep the loader separate",
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
        repo.id,
        "Legacy one",
        source="session",
        evidence_file="src/app.py",
        identity_quote="q",
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
async def test_two_records_with_one_identity_fold_into_one(async_session):
    """The point of evidence identity: one decision, worded twice.

    Under title identity these stayed two records forever, because nothing
    but a person ever noticed they were the same. The later one merges into
    the earlier and leaves an alias, so an id written down still resolves.
    """
    repo = await insert_repo(async_session)
    first = await _legacy_record(async_session, repo.id, "Cache the parse tree", id="a" * 32)
    second = await _legacy_record(async_session, repo.id, "Reuse the parsed tree", id="b" * 32)
    # Same evidence, same scope, same quote: one decision said twice.
    for rec in (first, second):
        rec.decision = "cache the parse tree because reparsing dominated the tail"
        async_session.add(
            DecisionEvidence(
                decision_id=rec.id,
                source="session",
                evidence_file="src/app.py",
                source_quote="reparsing dominated the tail",
            )
        )
    await async_session.flush()

    plan = await apply_id_migration(async_session, repo.id)

    assert plan.counts() == {"rewrite": 1, "fold": 1}
    kept = derive_decision_id(
        repo.id,
        "Cache the parse tree",
        source="session",
        evidence_file="src/app.py",
        identity_quote="reparsing dominated the tail",
    )
    assert plan.rewrites()[0].new_id == kept
    assert (await async_session.get(DecisionRecord, kept)) is not None
    assert (await async_session.execute(select(func.count(DecisionRecord.id)))).scalar_one() == 1
    # The oldest reading is the one the group ends up under.
    assert (await async_session.get(DecisionRecord, kept)).title == "Cache the parse tree"
    # The evidence the loser carried came with it.
    assert set(
        (await async_session.execute(select(DecisionEvidence.decision_id))).scalars().all()
    ) == {kept}
    # And the id that went away still resolves.
    alias = await async_session.get(DecisionAlias, "b" * 32)
    assert alias is not None and alias.reason == FOLD_REASON
    assert await resolve_decision_id(async_session, "b" * 32) == kept


@pytest.mark.asyncio
async def test_a_fold_drops_the_evidence_the_keeper_already_has(async_session):
    """Two records only fold because they were duplicates.

    That makes them the pair most likely to hold the same evidence row, and
    ``uq_decision_evidence`` names ``(decision_id, source, evidence_file,
    evidence_commit)``. Repointing blindly raises out of a migration that runs
    at the head of every index.
    """
    repo = await insert_repo(async_session)
    first = await _legacy_record(async_session, repo.id, "Cache the parse tree", id="a" * 32)
    second = await _legacy_record(async_session, repo.id, "Reuse the parsed tree", id="b" * 32)
    for rec in (first, second):
        rec.decision = "cache the parse tree because reparsing dominated the tail"
        async_session.add(
            DecisionEvidence(
                decision_id=rec.id,
                source="session",
                source_rank=8,
                evidence_file="src/app.py",
                evidence_commit="c0ffee",
                source_quote="reparsing dominated the tail",
            )
        )
        # A second row the keeper has no equivalent of, which must survive.
        async_session.add(
            DecisionEvidence(
                decision_id=rec.id,
                source="pr",
                evidence_file=f"src/{rec.id[0]}.py",
                source_quote="a distinct span",
            )
        )
    await async_session.flush()

    plan = await apply_id_migration(async_session, repo.id)

    assert plan.counts() == {"rewrite": 1, "fold": 1}
    kept = plan.rewrites()[0].new_id
    rows = (
        await async_session.execute(
            select(DecisionEvidence.source, DecisionEvidence.evidence_file).where(
                DecisionEvidence.decision_id == kept
            )
        )
    ).all()
    # The colliding pair collapsed to one; the two distinct rows both survived.
    assert sorted(rows) == [
        ("pr", "src/a.py"),
        ("pr", "src/b.py"),
        ("session", "src/app.py"),
    ]


@pytest.mark.asyncio
async def test_a_fold_keeps_the_review_row_of_a_candidate_merged_into_the_loser(async_session):
    """``merged_into`` is another live candidate's row, not the loser's own.

    Deleting it would take a decision that still exists out of review
    entirely, and that decision was never part of the fold.
    """
    repo = await insert_repo(async_session)
    first = await _legacy_record(async_session, repo.id, "Cache the parse tree", id="a" * 32)
    second = await _legacy_record(async_session, repo.id, "Reuse the parsed tree", id="b" * 32)
    bystander = await _legacy_record(
        async_session, repo.id, "Something else entirely", id="c" * 32
    )
    for rec in (first, second):
        rec.decision = "cache the parse tree because reparsing dominated the tail"
    bystander.decision = "an unrelated decision about an unrelated file"
    async_session.add(
        DecisionCandidateMeta(
            decision_id=bystander.id,
            repository_id=repo.id,
            review_state="merged",
            merged_into=second.id,
        )
    )
    await async_session.flush()

    plan = await apply_id_migration(async_session, repo.id)
    kept = next(r.new_id for r in plan.rewrites() if r.old_id == "a" * 32)
    moved_bystander = next(r.new_id for r in plan.rewrites() if r.old_id == "c" * 32)

    meta = await async_session.get(DecisionCandidateMeta, moved_bystander)
    assert meta is not None, "the bystander's review row was deleted with the fold"
    assert meta.review_state == "merged"
    # And it now names the record the merge target folded into.
    assert meta.merged_into == kept


@pytest.mark.asyncio
async def test_a_bundled_claim_is_held_out_of_the_fold(async_session):
    """A bundle shares its evidence with the decisions it bundles.

    Folding them would file two decisions under a third one's name, so the
    flagged record keeps its title in the key and stays its own record.
    """
    repo = await insert_repo(async_session)
    first = await _legacy_record(async_session, repo.id, "Enable WAL", id="a" * 32)
    bundle = await _legacy_record(
        async_session, repo.id, "Enable WAL; bound the busy timeout", id="b" * 32
    )
    for rec in (first, bundle):
        rec.decision = "enable wal and bound the busy timeout"
    async_session.add(
        DecisionCandidateMeta(decision_id=bundle.id, repository_id=repo.id, needs_split=True)
    )
    await async_session.flush()

    plan = await apply_id_migration(async_session, repo.id)

    assert plan.counts() == {"rewrite": 2}
    assert (await async_session.execute(select(func.count(DecisionRecord.id)))).scalar_one() == 2


@pytest.mark.asyncio
async def test_the_quote_is_pinned_rather_than_re_derived(async_session):
    """A later extraction rewording the same sentence must not move the id."""
    repo = await insert_repo(async_session)
    legacy = await _legacy_record(async_session, repo.id, "Legacy one")
    async_session.add(
        DecisionEvidence(
            decision_id=legacy.id,
            source="session",
            evidence_file="src/app.py",
            source_quote="the original wording",
        )
    )
    await async_session.flush()

    await apply_id_migration(async_session, repo.id)
    moved = (
        await async_session.execute(
            select(DecisionRecord).where(DecisionRecord.repository_id == repo.id)
        )
    ).scalar_one()
    pinned_id, pinned_quote = moved.id, moved.identity_quote
    assert pinned_quote == "the original wording"

    # Re-mine the same decision under a rewritten quote.
    async_session.add(
        DecisionEvidence(
            decision_id=moved.id,
            source="session",
            evidence_file="src/app.py",
            source_quote="a completely different phrasing of the same thing",
        )
    )
    await async_session.flush()

    second = await apply_id_migration(async_session, repo.id)

    assert second.counts() == {"stable": 1}
    again = await async_session.get(DecisionRecord, pinned_id)
    assert again is not None and again.identity_quote == pinned_quote


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
        repo.id,
        "Legacy one",
        source="session",
        evidence_file="src/app.py",
        identity_quote="Legacy one: do the thing",
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

    survivor_new = derive_decision_id(
        repo.id,
        "The survivor",
        source="session",
        evidence_file="src/app.py",
        identity_quote="The survivor: do the thing",
    )

    await apply_id_migration(async_session, repo.id)

    alias = await async_session.get(DecisionAlias, folded_id)
    assert alias.reason == "merged"
    # Still points at the survivor, which itself moved, rather than at the
    # candidate that was folded away.
    assert alias.decision_id != folded_id
    assert alias.decision_id == survivor_new
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

    real = mod._move

    async def _explode(session, from_id, to_id, title, tables):
        await real(session, from_id, to_id, title, tables)
        raise RuntimeError("boom")

    monkeypatch.setattr(mod, "_move", _explode)

    with pytest.raises(RuntimeError):
        await apply_id_migration(async_session, repo.id)

    titles = (
        (await async_session.execute(select(DecisionRecord.title))).scalars().all()
    )
    assert titles == ["Legacy one"]


def _occupant_id(repo_id: str, quote: str) -> str:
    return derive_decision_id(
        repo_id, "any title", source="session", evidence_file="src/app.py", identity_quote=quote
    )


@pytest.mark.asyncio
async def test_a_record_already_on_the_derived_id_keeps_it(async_session):
    """An older duplicate folds into the record sitting on the id.

    Keeping the older one would copy it onto an id that is still occupied,
    which raises on the primary key on every run.
    """
    repo = await insert_repo(async_session)
    occupied = _occupant_id(repo.id, "q")
    await _legacy_record(
        async_session,
        repo.id,
        "Older wording",
        id="a" * 32,
        identity_quote="q",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await _legacy_record(
        async_session,
        repo.id,
        "Newer wording",
        id=occupied,
        identity_quote="q",
        created_at=datetime(2026, 2, 1, tzinfo=UTC),
    )

    plan = await apply_id_migration(async_session, repo.id)

    assert plan.counts() == {"fold": 1, "stable": 1}
    assert (await async_session.execute(select(DecisionRecord.id))).scalars().all() == [occupied]
    assert await resolve_decision_id(async_session, "a" * 32) == occupied
    assert (await apply_id_migration(async_session, repo.id)).counts() == {"stable": 1}


async def _seat(session, repo_id: str, quote: str, sits_on: str) -> None:
    """A record whose identity is *quote*, sitting on the id *sits_on*."""
    await _legacy_record(session, repo_id, f"Decided {quote}", id=sits_on, identity_quote=quote)
    session.add(
        DecisionEvidence(
            decision_id=sits_on, source="session", evidence_file="src/app.py", source_quote=quote
        )
    )
    await session.flush()


async def _assert_settled_in_one_run(session, repo_id: str, seats: dict[str, str]) -> None:
    """*seats* maps each quote to the id its record sat on before the run."""
    plan = await apply_id_migration(session, repo_id)
    assert plan.counts() == {"rewrite": len(seats)}

    for quote, old_id in seats.items():
        final = _occupant_id(repo_id, quote)
        rec = await session.get(DecisionRecord, final)
        assert rec is not None and rec.title == f"Decided {quote}"
        evidence = (
            await session.execute(
                select(DecisionEvidence.decision_id).where(DecisionEvidence.source_quote == quote)
            )
        ).scalar_one()
        assert evidence == final
        # Straight from the original id to the final one, never via a parking id.
        alias = await session.get(DecisionAlias, old_id)
        assert (alias.decision_id, alias.reason) == (final, ALIAS_REASON)
    ids = (await session.execute(select(DecisionRecord.id))).scalars().all()
    assert sorted(ids) == sorted(_occupant_id(repo_id, q) for q in seats)
    aliases = (await session.execute(select(DecisionAlias.decision_id))).scalars().all()
    assert not [a for a in aliases if a.startswith("~")]
    assert (await apply_id_migration(session, repo_id)).counts() == {"stable": len(seats)}


@pytest.mark.asyncio
async def test_a_chain_of_occupied_ids_settles_in_one_run(async_session):
    """Each target is held by the next record, and the last target is free."""
    repo = await insert_repo(async_session)
    seats = {
        "q1": "a" * 32,
        "q2": _occupant_id(repo.id, "q1"),
        "q3": _occupant_id(repo.id, "q2"),
    }
    for quote, sits_on in seats.items():
        await _seat(async_session, repo.id, quote, sits_on)

    await _assert_settled_in_one_run(async_session, repo.id, seats)


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [2, 3])
async def test_records_on_each_others_ids_settle_in_one_run(async_session, size):
    """A cycle has no free target to start from, so one member is parked first."""
    repo = await insert_repo(async_session)
    quotes = [f"q{n}" for n in range(size)]
    seats = {q: _occupant_id(repo.id, quotes[(n + 1) % size]) for n, q in enumerate(quotes)}
    for quote, sits_on in seats.items():
        await _seat(async_session, repo.id, quote, sits_on)

    await _assert_settled_in_one_run(async_session, repo.id, seats)
