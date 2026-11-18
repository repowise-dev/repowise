"""Decision vector migration resumes after SQL has already changed the IDs."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from repowise.core.analysis.decisions.semantic_match import DECISION_VECTOR_PREFIX
from repowise.core.persistence.crud.decisions import derive_decision_id
from repowise.core.persistence.decision_id_migration import apply_id_migration, plan_id_migration
from repowise.core.persistence.models import REKEYED_REASON, DecisionAlias, DecisionRecord
from tests.unit.persistence.helpers import insert_repo
from tests.unit.persistence.test_decision_stable_ids import _legacy_record


@pytest.mark.asyncio
async def test_vector_rekey_resumes_after_sql_only_migration(
    async_session, in_memory_vector_store, mock_embedder, monkeypatch
):
    repo = await insert_repo(async_session)
    legacy = await _legacy_record(async_session, repo.id, "Legacy one")
    old_key = f"{DECISION_VECTOR_PREFIX}{legacy.id}"
    vector = [0.25] * mock_embedder.dimensions
    metadata = {"title": "Retained wording", "page_type": "decision_record", "extra": "keep"}
    await in_memory_vector_store.upsert_vectors([(old_key, vector, metadata)])
    embed = AsyncMock(side_effect=AssertionError("Identity changes do not need embedding"))
    monkeypatch.setattr(mock_embedder, "embed", embed)

    first = await apply_id_migration(async_session, repo.id)
    new_key = f"{DECISION_VECTOR_PREFIX}{first.rewrites()[0].new_id}"
    await async_session.commit()
    second = await apply_id_migration(
        async_session, repo.id, vector_store=in_memory_vector_store
    )

    assert second.counts() == {"stable": 1}
    assert await in_memory_vector_store.list_page_ids() == {new_key}
    assert in_memory_vector_store._store[new_key] == (vector, metadata)
    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)
    assert in_memory_vector_store._store == {new_key: (vector, metadata)}
    embed.assert_not_called()


@pytest.mark.asyncio
async def test_vector_rekey_preserves_an_existing_canonical_embedding(
    async_session, in_memory_vector_store, mock_embedder, monkeypatch
):
    repo = await insert_repo(async_session)
    legacy = await _legacy_record(async_session, repo.id, "Legacy one")
    plan = await apply_id_migration(async_session, repo.id)
    old_key = f"{DECISION_VECTOR_PREFIX}{legacy.id}"
    new_key = f"{DECISION_VECTOR_PREFIX}{plan.rewrites()[0].new_id}"
    canonical = ([0.75] * mock_embedder.dimensions, {"title": "Current wording"})
    await in_memory_vector_store.upsert_vectors([
        (old_key, [0.25] * mock_embedder.dimensions, {"title": "Old wording"}),
        (new_key, *canonical),
    ])
    embed = AsyncMock(side_effect=AssertionError("Canonical embedding already exists"))
    monkeypatch.setattr(mock_embedder, "embed", embed)

    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    assert in_memory_vector_store._store == {new_key: canonical}
    embed.assert_not_called()


@pytest.mark.asyncio
async def test_fresh_rekey_replaces_a_stale_destination(
    async_session, in_memory_vector_store, mock_embedder, monkeypatch
):
    repo = await insert_repo(async_session)
    legacy = await _legacy_record(async_session, repo.id, "Legacy one")
    plan = await plan_id_migration(async_session, repo.id)
    old_key = f"{DECISION_VECTOR_PREFIX}{legacy.id}"
    new_key = f"{DECISION_VECTOR_PREFIX}{plan.rewrites()[0].new_id}"
    retained = ([0.25] * mock_embedder.dimensions, {"title": "Legacy one"})
    await in_memory_vector_store.upsert_vectors([
        (old_key, *retained),
        (new_key, [0.75] * mock_embedder.dimensions, {"title": "Retired record"}),
    ])
    embed = AsyncMock(side_effect=AssertionError("The retained vector can be copied"))
    monkeypatch.setattr(mock_embedder, "embed", embed)

    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    assert in_memory_vector_store._store == {new_key: retained}
    embed.assert_not_called()


@pytest.mark.asyncio
async def test_vector_rekey_retries_a_failed_copy(
    async_session, mock_embedder, monkeypatch
):
    from repowise.core.persistence.vector_store import InMemoryVectorStore

    class InterruptedStore(InMemoryVectorStore):
        fail = True

        async def copy_vectors(self, mapping):
            if self.fail:
                raise OSError("Store temporarily unavailable")
            return await super().copy_vectors(mapping)

    store = InterruptedStore(mock_embedder)
    repo = await insert_repo(async_session)
    legacy = await _legacy_record(async_session, repo.id, "Legacy one")
    old_key = f"{DECISION_VECTOR_PREFIX}{legacy.id}"
    retained = ([0.25] * mock_embedder.dimensions, {"title": "Retained wording"})
    await store.upsert_vectors([(old_key, *retained)])
    embed = AsyncMock(side_effect=AssertionError("Retained vector must be copied"))
    monkeypatch.setattr(mock_embedder, "embed", embed)

    first = await apply_id_migration(async_session, repo.id, vector_store=store)
    new_key = f"{DECISION_VECTOR_PREFIX}{first.rewrites()[0].new_id}"
    assert store._store == {old_key: retained}
    store.fail = False
    await apply_id_migration(async_session, repo.id, vector_store=store)

    assert store._store == {new_key: retained}
    embed.assert_not_called()


@pytest.mark.asyncio
async def test_vector_rekey_keeps_the_source_until_destination_readback(
    async_session, in_memory_vector_store, mock_embedder, monkeypatch
):
    repo = await insert_repo(async_session)
    legacy = await _legacy_record(async_session, repo.id, "Legacy one")
    old_key = f"{DECISION_VECTOR_PREFIX}{legacy.id}"
    retained = ([0.25] * mock_embedder.dimensions, {"title": "Retained"})
    await in_memory_vector_store.upsert_vectors([(old_key, *retained)])

    async def acknowledged_without_write(mapping):
        return set(mapping)

    monkeypatch.setattr(in_memory_vector_store, "copy_vectors", acknowledged_without_write)
    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    assert in_memory_vector_store._store == {old_key: retained}


@pytest.mark.asyncio
async def test_vector_rekey_falls_back_when_a_store_cannot_copy(
    async_session, mock_embedder
):
    from repowise.core.persistence.vector_store import InMemoryVectorStore

    class EmbeddingOnlyStore(InMemoryVectorStore):
        async def copy_vectors(self, mapping):
            return set()

    store = EmbeddingOnlyStore(mock_embedder)
    repo = await insert_repo(async_session)
    legacy = await _legacy_record(async_session, repo.id, "Legacy one")
    old_key = f"{DECISION_VECTOR_PREFIX}{legacy.id}"
    await store.upsert_vectors([(old_key, [0.25] * mock_embedder.dimensions, {})])
    first = await apply_id_migration(async_session, repo.id)
    new_key = f"{DECISION_VECTOR_PREFIX}{first.rewrites()[0].new_id}"

    await apply_id_migration(async_session, repo.id, vector_store=store)

    assert await store.list_page_ids() == {new_key}


@pytest.mark.asyncio
async def test_vector_rekey_does_not_reembed_an_alias_without_a_retained_vector(
    async_session, in_memory_vector_store, mock_embedder, monkeypatch
):
    repo = await insert_repo(async_session)
    await _legacy_record(async_session, repo.id, "Legacy one")
    await apply_id_migration(async_session, repo.id)
    embed = AsyncMock(side_effect=AssertionError("No unfinished vector move"))
    monkeypatch.setattr(mock_embedder, "embed", embed)

    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    assert await in_memory_vector_store.list_page_ids() == set()
    embed.assert_not_called()


@pytest.mark.asyncio
async def test_vector_rekey_snapshots_an_occupied_key_chain(
    async_session, in_memory_vector_store, mock_embedder
):
    repo = await insert_repo(async_session)
    first = await _legacy_record(async_session, repo.id, "First")
    first_new_id = derive_decision_id(
        repo.id, "First", source="session", evidence_file="src/app.py",
        identity_quote="First: do the thing",
    )
    second = await _legacy_record(async_session, repo.id, "Second", id=first_new_id)
    second_new_id = derive_decision_id(
        repo.id, "Second", source="session", evidence_file="src/app.py",
        identity_quote="Second: do the thing",
    )
    first_value = ([0.25] * mock_embedder.dimensions, {"title": "First"})
    second_value = ([0.75] * mock_embedder.dimensions, {"title": "Second"})
    await in_memory_vector_store.upsert_vectors([
        (f"{DECISION_VECTOR_PREFIX}{first.id}", *first_value),
        (f"{DECISION_VECTOR_PREFIX}{second.id}", *second_value),
    ])

    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    assert in_memory_vector_store._store == {
        f"{DECISION_VECTOR_PREFIX}{first_new_id}": first_value,
        f"{DECISION_VECTOR_PREFIX}{second_new_id}": second_value,
    }


@pytest.mark.asyncio
async def test_vector_rekey_replaces_the_folded_occupant_of_a_destination(
    async_session, in_memory_vector_store, mock_embedder
):
    repo = await insert_repo(async_session)
    keeper = await _legacy_record(async_session, repo.id, "Keeper", identity_quote="keep")
    destination = derive_decision_id(
        repo.id, "Keeper", source="session", evidence_file="src/app.py", identity_quote="keep"
    )
    fold_target = derive_decision_id(
        repo.id, "Stable", source="session", evidence_file="src/app.py", identity_quote="fold"
    )
    await _legacy_record(async_session, repo.id, "Stable", id=fold_target, identity_quote="fold")
    await _legacy_record(async_session, repo.id, "Folded", id=destination, identity_quote="fold")
    old_key = f"{DECISION_VECTOR_PREFIX}{keeper.id}"
    new_key = f"{DECISION_VECTOR_PREFIX}{destination}"
    keeper_value = ([0.25] * mock_embedder.dimensions, {"title": "Keeper"})
    await in_memory_vector_store.upsert_vectors([
        (old_key, *keeper_value),
        (new_key, [0.75] * mock_embedder.dimensions, {"title": "Folded"}),
    ])

    plan = await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    assert plan.counts() == {"rewrite": 1, "stable": 1, "fold": 1}
    assert in_memory_vector_store._store == {new_key: keeper_value}


@pytest.mark.asyncio
async def test_vector_rekey_preserves_a_live_alias_source(
    async_session, in_memory_vector_store, mock_embedder, monkeypatch
):
    repo = await insert_repo(async_session)
    await _legacy_record(async_session, repo.id, "First")
    await _legacy_record(async_session, repo.id, "Second")
    await apply_id_migration(async_session, repo.id)
    records = list((await async_session.execute(select(DecisionRecord))).scalars())
    first, second = records
    async_session.add(DecisionAlias(
        alias_id=first.id, repository_id=repo.id, decision_id=second.id, reason=REKEYED_REASON
    ))
    await async_session.flush()
    key = f"{DECISION_VECTOR_PREFIX}{first.id}"
    retained = ([0.25] * mock_embedder.dimensions, {"title": "Still live"})
    await in_memory_vector_store.upsert_vectors([(key, *retained)])
    embed = AsyncMock(side_effect=AssertionError("Live source is not a retired vector"))
    monkeypatch.setattr(mock_embedder, "embed", embed)

    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    assert in_memory_vector_store._store == {key: retained}
    embed.assert_not_called()


@pytest.mark.asyncio
async def test_vector_rekey_coalesces_retained_aliases_to_one_destination(
    async_session, in_memory_vector_store, mock_embedder, monkeypatch
):
    repo = await insert_repo(async_session)
    await _legacy_record(async_session, repo.id, "Legacy one")
    plan = await apply_id_migration(async_session, repo.id)
    row = plan.rewrites()[0]
    async_session.add(DecisionAlias(
        alias_id="another-old-id", repository_id=repo.id, decision_id=row.new_id,
        reason=REKEYED_REASON,
    ))
    await async_session.flush()
    vector = [0.25] * mock_embedder.dimensions
    await in_memory_vector_store.upsert_vectors([
        (f"{DECISION_VECTOR_PREFIX}{old}", vector, {"title": "Retained"})
        for old in (row.old_id, "another-old-id")
    ])
    embed = AsyncMock(side_effect=AssertionError("Retained aliases can be copied"))
    monkeypatch.setattr(mock_embedder, "embed", embed)

    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    assert in_memory_vector_store._store == {
        f"{DECISION_VECTOR_PREFIX}{row.new_id}": (vector, {"title": "Retained"})
    }
    embed.assert_not_called()


@pytest.mark.asyncio
async def test_vector_rekey_resumes_with_a_persistent_lancedb_store(
    async_session, mock_embedder, tmp_path, monkeypatch
):
    pytest.importorskip("lancedb")
    from repowise.core.persistence.vector_store import LanceDBVectorStore

    store = LanceDBVectorStore(str(tmp_path / "decision-vectors"), mock_embedder)
    repo = await insert_repo(async_session)
    legacy = await _legacy_record(async_session, repo.id, "Legacy one")
    old_key = f"{DECISION_VECTOR_PREFIX}{legacy.id}"
    try:
        await store.upsert_vectors([(
            old_key, [0.25] * mock_embedder.dimensions,
            {"title": "Retained wording", "content": "Original decision"},
        )])
        before = (await store._table.query().to_list())[0]
        plan = await apply_id_migration(async_session, repo.id)
        await async_session.commit()
        new_key = f"{DECISION_VECTOR_PREFIX}{plan.rewrites()[0].new_id}"
        await store.close()
        store = LanceDBVectorStore(str(tmp_path / "decision-vectors"), mock_embedder)
        embed = AsyncMock(side_effect=AssertionError("Persisted vector already exists"))
        monkeypatch.setattr(mock_embedder, "embed", embed)
        list_all = AsyncMock(wraps=store.list_page_ids)
        monkeypatch.setattr(store, "list_page_ids", list_all)

        await apply_id_migration(async_session, repo.id, vector_store=store)
        await apply_id_migration(async_session, repo.id, vector_store=store)

        assert await store._table.query().to_list() == [{**before, "page_id": new_key}]
        list_all.assert_not_called()
        embed.assert_not_called()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_vector_rekey_retains_an_interrupted_occupied_chain(
    async_session, in_memory_vector_store, mock_embedder, monkeypatch
):
    repo = await insert_repo(async_session)
    first = await _legacy_record(async_session, repo.id, "First")
    target = derive_decision_id(
        repo.id, "First", source="session", evidence_file="src/app.py",
        identity_quote="First: do the thing",
    )
    second = await _legacy_record(async_session, repo.id, "Second", id=target)
    values = {
        f"{DECISION_VECTOR_PREFIX}{first.id}": ([0.25] * mock_embedder.dimensions, {}),
        f"{DECISION_VECTOR_PREFIX}{second.id}": ([0.75] * mock_embedder.dimensions, {}),
    }
    await in_memory_vector_store.upsert_vectors([(key, *value) for key, value in values.items()])
    await apply_id_migration(async_session, repo.id)
    embed = AsyncMock(side_effect=AssertionError("Occupied retry is ambiguous"))
    monkeypatch.setattr(mock_embedder, "embed", embed)

    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    assert in_memory_vector_store._store == values
    embed.assert_not_called()
