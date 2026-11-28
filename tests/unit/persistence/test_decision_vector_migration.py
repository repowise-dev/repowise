"""Decision vector migration resumes after SQL has already changed the IDs."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.core.analysis.decisions.semantic_match import DECISION_VECTOR_PREFIX
from repowise.core.persistence.crud.decisions import derive_decision_id
from repowise.core.persistence.database import init_db
from repowise.core.persistence.decision_id_migration import (
    DecisionVectorRecoveryError,
    _recover_vector_undo,
    apply_id_migration,
    plan_id_migration,
)
from repowise.core.persistence.models import REKEYED_REASON, DecisionAlias, DecisionRecord
from repowise.core.persistence.vector_store import LanceDBVectorStore
from repowise.core.providers.embedding.base import MockEmbedder
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
    second = await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

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
    await in_memory_vector_store.upsert_vectors(
        [
            (old_key, [0.25] * mock_embedder.dimensions, {"title": "Old wording"}),
            (new_key, *canonical),
        ]
    )
    embed = AsyncMock(side_effect=AssertionError("Canonical embedding already exists"))
    monkeypatch.setattr(mock_embedder, "embed", embed)

    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    assert in_memory_vector_store._store == {new_key: canonical}
    embed.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", [None, "copy", "delete"])
async def test_fresh_rekey_replaces_a_stale_destination(
    async_session, in_memory_vector_store, mock_embedder, monkeypatch, interruption
):
    repo = await insert_repo(async_session)
    legacy = await _legacy_record(async_session, repo.id, "Legacy one")
    plan = await plan_id_migration(async_session, repo.id)
    old_key = f"{DECISION_VECTOR_PREFIX}{legacy.id}"
    new_key = f"{DECISION_VECTOR_PREFIX}{plan.rewrites()[0].new_id}"
    retained = ([0.25] * mock_embedder.dimensions, {"title": "Legacy one"})
    stale = ([0.75] * mock_embedder.dimensions, {"title": "Retired record"})
    await in_memory_vector_store.upsert_vectors(
        [
            (old_key, *retained),
            (new_key, *stale),
        ]
    )
    embed = AsyncMock(side_effect=AssertionError("The retained vector can be copied"))
    monkeypatch.setattr(mock_embedder, "embed", embed)

    if interruption == "copy":
        with monkeypatch.context() as interrupted:
            copy = AsyncMock(side_effect=OSError("Store temporarily unavailable"))
            interrupted.setattr(in_memory_vector_store, "copy_vectors", copy)
            await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)
            copy.assert_awaited_once_with({old_key: new_key})
        await async_session.commit()
        assert in_memory_vector_store._store == {old_key: retained}
    elif interruption == "delete":
        with monkeypatch.context() as interrupted:
            delete = AsyncMock(side_effect=OSError("Store temporarily unavailable"))
            interrupted.setattr(in_memory_vector_store, "delete_many", delete)
            with pytest.raises(OSError, match="Store temporarily unavailable"):
                await apply_id_migration(
                    async_session, repo.id, vector_store=in_memory_vector_store
                )
            delete.assert_awaited_once_with([new_key])
        await async_session.commit()
        assert (await plan_id_migration(async_session, repo.id)).rewrites() == plan.rewrites()
        assert in_memory_vector_store._store == {old_key: retained, new_key: stale}

    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)
    assert in_memory_vector_store._store == {new_key: retained}
    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)
    assert in_memory_vector_store._store == {new_key: retained}
    embed.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("return_to_original_id", "first_copy_succeeds"),
    [(False, False), (True, True), (True, False)],
    ids=["new-id", "original-id-after-copy", "original-id-before-copy"],
)
async def test_vector_rekey_retries_after_consecutive_identity_changes(
    async_session,
    in_memory_vector_store,
    mock_embedder,
    monkeypatch,
    return_to_original_id,
    first_copy_succeeds,
):
    repo = await insert_repo(async_session)
    title = "Decision"
    original_id = derive_decision_id(
        repo.id, title, source="session", evidence_file="src/app.py", identity_quote="Original"
    )
    await _legacy_record(async_session, repo.id, title, id=original_id, identity_quote="Second")
    original_key = f"{DECISION_VECTOR_PREFIX}{original_id}"
    retained = ([0.25] * mock_embedder.dimensions, {"title": "Retained"})
    await in_memory_vector_store.upsert_vectors([(original_key, *retained)])
    embed = AsyncMock(side_effect=AssertionError("The retained vector can be copied"))
    monkeypatch.setattr(mock_embedder, "embed", embed)
    first = await apply_id_migration(
        async_session,
        repo.id,
        vector_store=in_memory_vector_store if first_copy_succeeds else None,
    )
    await async_session.commit()

    intermediate_id = first.rewrites()[0].new_id
    current = await async_session.get(DecisionRecord, intermediate_id)
    current.identity_quote = "Original" if return_to_original_id else "Third"
    plan = await plan_id_migration(async_session, repo.id)
    final_id = plan.rewrites()[0].new_id
    final_key = f"{DECISION_VECTOR_PREFIX}{final_id}"
    source_key = (
        f"{DECISION_VECTOR_PREFIX}{intermediate_id}" if first_copy_succeeds else original_key
    )
    if not return_to_original_id:
        await in_memory_vector_store.upsert_vectors(
            [
                (final_key, [0.75] * mock_embedder.dimensions, {"title": "Stale"}),
            ]
        )

    with monkeypatch.context() as interrupted:
        interrupted.setattr(
            in_memory_vector_store,
            "copy_vectors",
            AsyncMock(side_effect=OSError("Copy interrupted")),
        )
        await apply_id_migration(
            async_session, repo.id, plan=plan, vector_store=in_memory_vector_store
        )
    await async_session.commit()
    after_failure = dict(in_memory_vector_store._store)
    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    assert in_memory_vector_store._store == {final_key: retained}
    assert after_failure == {source_key: retained}
    embed.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("return_to_original_id", [False, True], ids=["new-id", "original-id"])
async def test_vector_rekey_keeps_both_sources_when_ownership_is_ambiguous(
    async_session, in_memory_vector_store, mock_embedder, monkeypatch, return_to_original_id
):
    repo = await insert_repo(async_session)
    title = "Decision"
    original_id = derive_decision_id(
        repo.id, title, source="session", evidence_file="src/app.py", identity_quote="Original"
    )
    await _legacy_record(async_session, repo.id, title, id=original_id, identity_quote="Second")
    first = await apply_id_migration(async_session, repo.id)
    intermediate_id = first.rewrites()[0].new_id
    await async_session.commit()

    current = await async_session.get(DecisionRecord, intermediate_id)
    current.identity_quote = "Original" if return_to_original_id else "Third"
    await async_session.flush()
    await in_memory_vector_store.upsert_vectors(
        [
            (
                f"{DECISION_VECTOR_PREFIX}{original_id}",
                [0.25] * mock_embedder.dimensions,
                {"title": "A"},
            ),
            (
                f"{DECISION_VECTOR_PREFIX}{intermediate_id}",
                [0.75] * mock_embedder.dimensions,
                {"title": "B"},
            ),
        ]
    )
    before = dict(in_memory_vector_store._store)
    copy = AsyncMock(side_effect=OSError("Copy interrupted"))
    monkeypatch.setattr(in_memory_vector_store, "copy_vectors", copy)
    plan = await plan_id_migration(async_session, repo.id)

    with pytest.raises(RuntimeError, match="Multiple retained vector sources"):
        await apply_id_migration(
            async_session, repo.id, plan=plan, vector_store=in_memory_vector_store
        )
    await async_session.commit()

    assert (await plan_id_migration(async_session, repo.id)).rewrites() == plan.rewrites()
    assert in_memory_vector_store._store == before
    copy.assert_not_called()


@pytest.mark.asyncio
async def test_vector_rekey_retries_a_failed_copy(async_session, mock_embedder, monkeypatch):
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
async def test_vector_rekey_falls_back_when_a_store_cannot_copy(async_session, mock_embedder):
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
        repo.id,
        "First",
        source="session",
        evidence_file="src/app.py",
        identity_quote="First: do the thing",
    )
    second = await _legacy_record(async_session, repo.id, "Second", id=first_new_id)
    second_new_id = derive_decision_id(
        repo.id,
        "Second",
        source="session",
        evidence_file="src/app.py",
        identity_quote="Second: do the thing",
    )
    first_value = ([0.25] * mock_embedder.dimensions, {"title": "First"})
    second_value = ([0.75] * mock_embedder.dimensions, {"title": "Second"})
    await in_memory_vector_store.upsert_vectors(
        [
            (f"{DECISION_VECTOR_PREFIX}{first.id}", *first_value),
            (f"{DECISION_VECTOR_PREFIX}{second.id}", *second_value),
        ]
    )

    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    assert in_memory_vector_store._store == {
        f"{DECISION_VECTOR_PREFIX}{first_new_id}": first_value,
        f"{DECISION_VECTOR_PREFIX}{second_new_id}": second_value,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", [None, "copy", "delete-after-write", "backup-after-write"])
async def test_vector_rekey_replaces_the_folded_occupant_of_a_destination(
    async_session, in_memory_vector_store, mock_embedder, monkeypatch, interruption
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
    await in_memory_vector_store.upsert_vectors(
        [
            (old_key, *keeper_value),
            (new_key, [0.75] * mock_embedder.dimensions, {"title": "Folded"}),
        ]
    )

    embed = AsyncMock(side_effect=AssertionError("The retained vector can be copied"))
    monkeypatch.setattr(mock_embedder, "embed", embed)
    if interruption == "backup-after-write":
        copy = in_memory_vector_store.copy_vectors

        async def backup_then_disconnect(mapping):
            await copy(mapping)
            raise OSError("Connection closed after backup copy")

        with monkeypatch.context() as interrupted:
            interrupted.setattr(in_memory_vector_store, "copy_vectors", backup_then_disconnect)
            with pytest.raises(OSError, match="Connection closed after backup copy"):
                await apply_id_migration(
                    async_session, repo.id, vector_store=in_memory_vector_store
                )
        await async_session.commit()
        assert (await plan_id_migration(async_session, repo.id)).counts() == {
            "rewrite": 1,
            "stable": 1,
            "fold": 1,
        }
    if interruption == "delete-after-write":
        original = dict(in_memory_vector_store._store)
        delete = in_memory_vector_store.delete_many

        async def delete_then_disconnect(keys):
            await delete(keys)
            raise OSError("Connection closed after deletion")

        with monkeypatch.context() as interrupted:
            interrupted.setattr(in_memory_vector_store, "delete_many", delete_then_disconnect)
            with pytest.raises(OSError, match="Connection closed after deletion"):
                await apply_id_migration(
                    async_session, repo.id, vector_store=in_memory_vector_store
                )
        await async_session.commit()
        assert (await plan_id_migration(async_session, repo.id)).counts() == {
            "rewrite": 1,
            "stable": 1,
            "fold": 1,
        }
        assert in_memory_vector_store._store == original

    with monkeypatch.context() as interrupted:
        if interruption == "copy":
            copy = in_memory_vector_store.copy_vectors

            async def fail_copy_to_keeper(mapping):
                if mapping.get(old_key) == new_key:
                    raise OSError("Copy interrupted")
                return await copy(mapping)

            interrupted.setattr(in_memory_vector_store, "copy_vectors", fail_copy_to_keeper)
        plan = await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)
    await async_session.commit()
    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    assert plan.counts() == {"rewrite": 1, "stable": 1, "fold": 1}
    assert in_memory_vector_store._store == {new_key: keeper_value}
    embed.assert_not_called()


@pytest.mark.asyncio
async def test_vector_rekey_recovers_a_retired_key_before_reusing_it(
    async_session, in_memory_vector_store, mock_embedder, monkeypatch
):
    repo = await insert_repo(async_session)
    reused_id = derive_decision_id(
        repo.id, "Claimant", source="session", evidence_file="src/app.py", identity_quote="claim"
    )
    await _legacy_record(async_session, repo.id, "Prior", id=reused_id, identity_quote="prior")
    first = await apply_id_migration(async_session, repo.id)
    prior_id = first.rewrites()[0].new_id
    claimant = await _legacy_record(async_session, repo.id, "Claimant", identity_quote="claim")
    prior = ([0.25] * mock_embedder.dimensions, {"title": "Prior"})
    incoming = ([0.75] * mock_embedder.dimensions, {"title": "Claimant"})
    reused_key = f"{DECISION_VECTOR_PREFIX}{reused_id}"
    prior_key = f"{DECISION_VECTOR_PREFIX}{prior_id}"
    await in_memory_vector_store.upsert_vectors(
        [
            (reused_key, *prior),
            (f"{DECISION_VECTOR_PREFIX}{claimant.id}", *incoming),
        ]
    )
    embed = AsyncMock(side_effect=AssertionError("Both retained vectors can be copied"))
    monkeypatch.setattr(mock_embedder, "embed", embed)

    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    expected = {reused_key: incoming, prior_key: prior}
    assert in_memory_vector_store._store == expected
    await async_session.commit()
    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)
    assert in_memory_vector_store._store == expected
    embed.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("end_transaction", ["rollback", "close", "commit"])
async def test_vector_rekey_preserves_both_records_when_outer_sql_rolls_back(
    async_session, in_memory_vector_store, mock_embedder, end_transaction
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
    repository_id = repo.id
    keeper_id = keeper.id
    old_key = f"{DECISION_VECTOR_PREFIX}{keeper_id}"
    new_key = f"{DECISION_VECTOR_PREFIX}{destination}"
    before = {
        old_key: ([0.25] * mock_embedder.dimensions, {"title": "Keeper"}),
        new_key: ([0.75] * mock_embedder.dimensions, {"title": "Folded"}),
    }
    await in_memory_vector_store.upsert_vectors([(key, *value) for key, value in before.items()])
    await async_session.commit()

    async_session.add(
        DecisionAlias(
            alias_id="ordinary-enclosing-write",
            repository_id=repository_id,
            decision_id=keeper_id,
            reason=REKEYED_REASON,
        )
    )
    await async_session.flush()
    await apply_id_migration(async_session, repository_id, vector_store=in_memory_vector_store)
    if end_transaction == "commit":
        await async_session.commit()
        assert await async_session.get(DecisionRecord, keeper_id) is None
        assert (await async_session.get(DecisionRecord, destination)).title == "Keeper"
        assert in_memory_vector_store._store == {new_key: before[old_key]}
        return
    await getattr(async_session, end_transaction)()

    assert (await async_session.get(DecisionRecord, keeper_id)).title == "Keeper"
    assert (await async_session.get(DecisionRecord, destination)).title == "Folded"
    assert await async_session.get(DecisionAlias, "ordinary-enclosing-write") is None
    assert in_memory_vector_store._store == before


@pytest.mark.asyncio
async def test_lancedb_failed_embedding_preserves_folded_occupant(
    tmp_path,
    mock_embedder,
    monkeypatch,
):
    pytest.importorskip("lancedb")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'decisions.db'}")
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = LanceDBVectorStore(str(tmp_path / "vectors"), mock_embedder)
    try:
        async with factory() as session:
            repo = await insert_repo(session, local_path=str(tmp_path / "repo"))
            keeper = await _legacy_record(session, repo.id, "Keeper", identity_quote="keep")
            destination = derive_decision_id(
                repo.id,
                "Keeper",
                source="session",
                evidence_file="src/app.py",
                identity_quote="keep",
            )
            fold_target = derive_decision_id(
                repo.id,
                "Stable",
                source="session",
                evidence_file="src/app.py",
                identity_quote="fold",
            )
            await _legacy_record(session, repo.id, "Stable", id=fold_target, identity_quote="fold")
            await _legacy_record(session, repo.id, "Folded", id=destination, identity_quote="fold")
            repo_id, keeper_id = repo.id, keeper.id
            await session.commit()

        destination_key = f"{DECISION_VECTOR_PREFIX}{destination}"
        keys = {f"{DECISION_VECTOR_PREFIX}{keeper_id}", destination_key}
        await store.upsert_vectors(
            [
                (destination_key, [0.75] * mock_embedder.dimensions, {"title": "Folded"}),
            ]
        )
        before = await store.snapshot_vectors(keys)
        with monkeypatch.context() as interrupted:
            interrupted.setattr(store, "embed_texts", AsyncMock(side_effect=OSError("Offline")))
            interrupted.setattr(store, "embed_batch", AsyncMock(side_effect=OSError("Offline")))
            async with factory() as session:
                with pytest.raises(DecisionVectorRecoveryError):
                    await apply_id_migration(session, repo_id, vector_store=store)
                await session.commit()

        async with factory() as session:
            assert (await session.get(DecisionRecord, keeper_id)).title == "Keeper"
            assert (await session.get(DecisionRecord, destination)).title == "Folded"
            assert await store.snapshot_vectors(keys) == before
            await apply_id_migration(session, repo_id, vector_store=store)
            await session.commit()

        assert (await store.snapshot_vectors(keys))[destination_key]["title"] == "Keeper"
        assert not store.decision_vector_journal(repo_id).exists()
    finally:
        await store.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "copy_failure",
    ["before-write", "after-write", "unconfirmed", "no-write", "embedding"],
)
async def test_lancedb_copy_failure_does_not_commit_an_occupied_rekey(
    tmp_path,
    mock_embedder,
    monkeypatch,
    copy_failure,
):
    pytest.importorskip("lancedb")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'decisions.db'}")
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = LanceDBVectorStore(str(tmp_path / "vectors"), mock_embedder)
    try:
        async with factory() as session:
            repo = await insert_repo(session, local_path=str(tmp_path / "repo"))
            first = await _legacy_record(session, repo.id, "A", identity_quote="first")
            second_id = derive_decision_id(
                repo.id,
                "A",
                source="session",
                evidence_file="src/app.py",
                identity_quote="first",
            )
            final_id = derive_decision_id(
                repo.id,
                "B",
                source="session",
                evidence_file="src/app.py",
                identity_quote="second",
            )
            await _legacy_record(session, repo.id, "B", id=second_id, identity_quote="second")
            repo_id, first_id = repo.id, first.id
            await session.commit()
        first_key, second_key, final_key = (
            f"{DECISION_VECTOR_PREFIX}{key}" for key in (first_id, second_id, final_id)
        )
        keys = {first_key, second_key, final_key}
        await store.upsert_vectors(
            [
                (second_key, [0.75] * mock_embedder.dimensions, {"title": "B"}),
            ]
        )
        if copy_failure != "embedding":
            await store.upsert_vectors(
                [(first_key, [0.25] * mock_embedder.dimensions, {"title": "A"})]
            )
        before = await store.snapshot_vectors(keys)
        copy = store.copy_vectors

        async def interrupt(mapping):
            if final_key not in mapping.values():
                return await copy(mapping)
            if copy_failure == "unconfirmed":
                return set()
            if copy_failure == "no-write":
                return set(mapping)
            if copy_failure == "after-write":
                await copy(mapping)
            raise OSError("Copy interrupted")

        with monkeypatch.context() as interrupted:
            if copy_failure == "embedding":
                interrupted.setattr(store, "embed_texts", AsyncMock(side_effect=OSError("Offline")))
                interrupted.setattr(store, "embed_batch", AsyncMock(side_effect=OSError("Offline")))
            else:
                interrupted.setattr(store, "copy_vectors", interrupt)
            async with factory() as session:
                with pytest.raises(DecisionVectorRecoveryError):
                    await apply_id_migration(session, repo_id, vector_store=store)
                # Even a caller that catches the failure cannot commit half a move.
                await session.commit()

        async with factory() as session:
            assert (await session.get(DecisionRecord, first_id)).title == "A"
            assert (await session.get(DecisionRecord, second_id)).title == "B"
            assert await session.get(DecisionRecord, final_id) is None
            assert await store.snapshot_vectors(keys) == before
            await apply_id_migration(session, repo_id, vector_store=store)
            await session.commit()

        if copy_failure == "embedding":
            landed = await store.snapshot_vectors(keys)
            assert landed[first_key] is None
            assert landed[second_key]["title"] == "A"
            assert landed[final_key] == {**before[second_key], "page_id": final_key}
            return
        expected = {
            first_key: None,
            second_key: {**before[first_key], "page_id": second_key},
            final_key: {**before[second_key], "page_id": final_key},
        }
        assert await store.snapshot_vectors(keys) == expected
        async with factory() as session:
            assert (await apply_id_migration(session, repo_id, vector_store=store)).counts() == {
                "stable": 2
            }
            await session.commit()
        assert await store.snapshot_vectors(keys) == expected
    finally:
        await store.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_second_savepoint_recovery_prevents_outer_commit(
    tmp_path, mock_embedder, monkeypatch
):
    pytest.importorskip("lancedb")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'decisions.db'}")
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = LanceDBVectorStore(str(tmp_path / "vectors"), mock_embedder)
    try:
        async with factory() as session:
            repo = await insert_repo(session, local_path=str(tmp_path / "repo"))
            legacy = await _legacy_record(session, repo.id, "A", identity_quote="first")
            repo_id, original_id = repo.id, legacy.id
            await session.commit()
        key = f"{DECISION_VECTOR_PREFIX}{original_id}"
        await store.upsert_vectors([(key, [0.25] * mock_embedder.dimensions, {"title": "A"})])
        before = await store.snapshot_vectors({key})
        copy = store.copy_vectors
        restore = store.restore_vectors

        async def copy_then_disconnect(mapping):
            await copy(mapping)
            raise OSError("Copy acknowledgement lost")

        async def refuse_savepoint_restore(rows):
            raise OSError("Savepoint restore unavailable")

        async with factory() as session:
            first = await apply_id_migration(session, repo_id, vector_store=store)
            intermediate_id = first.rewrites()[0].new_id
            current = await session.get(DecisionRecord, intermediate_id)
            current.identity_quote = "next"
            second = await plan_id_migration(session, repo_id)
            final_id = second.rewrites()[0].new_id
            with monkeypatch.context() as interrupted:
                interrupted.setattr(store, "copy_vectors", copy_then_disconnect)
                interrupted.setattr(store, "restore_vectors", refuse_savepoint_restore)
                with pytest.raises(DecisionVectorRecoveryError, match="Cannot roll back"):
                    await apply_id_migration(session, repo_id, plan=second, vector_store=store)
                assert store.decision_vector_journal(repo_id).is_file()
                with pytest.raises(DecisionVectorRecoveryError, match="Roll back the transaction"):
                    await session.commit()
            assert store.restore_vectors == restore
            await session.rollback()

        async with factory() as session:
            assert await session.get(DecisionRecord, original_id) is not None
            assert await session.get(DecisionRecord, intermediate_id) is None
            assert await session.get(DecisionRecord, final_id) is None
        assert await store.snapshot_vectors(
            {
                key,
                f"{DECISION_VECTOR_PREFIX}{intermediate_id}",
                f"{DECISION_VECTOR_PREFIX}{final_id}",
            }
        ) == {
            **before,
            f"{DECISION_VECTOR_PREFIX}{intermediate_id}": None,
            f"{DECISION_VECTOR_PREFIX}{final_id}": None,
        }
        assert not store.decision_rekey_pending()
    finally:
        await store.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exit_at",
    ["during-copy", "before-commit", "after-sql-commit", "after-commit", "rollback", "close"],
)
async def test_vector_rekey_survives_exit_at_outer_commit(
    tmp_path,
    mock_embedder,
    monkeypatch,
    exit_at,
):
    pytest.importorskip("lancedb")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'decision-records.db'}")
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = LanceDBVectorStore(str(tmp_path / "vectors"), mock_embedder)
    try:
        async with factory() as session:
            repo = await insert_repo(session, local_path=str(tmp_path / "repo"))
            keeper = await _legacy_record(session, repo.id, "Keeper", identity_quote="keep")
            destination = derive_decision_id(
                repo.id,
                "Keeper",
                source="session",
                evidence_file="src/app.py",
                identity_quote="keep",
            )
            fold_target = derive_decision_id(
                repo.id,
                "Stable",
                source="session",
                evidence_file="src/app.py",
                identity_quote="fold",
            )
            await _legacy_record(session, repo.id, "Stable", id=fold_target, identity_quote="fold")
            await _legacy_record(session, repo.id, "Folded", id=destination, identity_quote="fold")
            keeper_id = keeper.id
            repo_id = repo.id
            await session.commit()

        await store.upsert_vectors(
            [
                (
                    f"{DECISION_VECTOR_PREFIX}{keeper_id}",
                    [0.25] * mock_embedder.dimensions,
                    {"title": "Keeper"},
                ),
                (
                    f"{DECISION_VECTOR_PREFIX}{destination}",
                    [0.75] * mock_embedder.dimensions,
                    {"title": "Folded"},
                ),
            ]
        )
        before = await store._table.query().to_list()
        await store.close()

        worker = subprocess.run(
            [sys.executable, "-m", __name__, str(tmp_path), repo_id, keeper_id, exit_at],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            timeout=30,
        )
        assert worker.returncode == (88 if exit_at == "during-copy" else 87), worker.stderr.decode()

        committed = exit_at in ("after-sql-commit", "after-commit")
        journal = store.decision_vector_journal(repo_id)
        async with factory() as session:
            keeper = await session.get(DecisionRecord, keeper_id)
            occupant = await session.get(DecisionRecord, destination)
            marker = await session.get(DecisionAlias, "ordinary-enclosing-write")
            if committed:
                assert keeper is None and occupant.title == "Keeper"
                assert marker.decision_id == destination
            else:
                assert keeper.title == "Keeper" and occupant.title == "Folded"
                assert marker is None

            if exit_at == "during-copy":

                async def disconnect_after_restore(rows):
                    await restore(rows)
                    raise OSError("Recovery acknowledgement lost")

                restore = store.restore_vectors
                with monkeypatch.context() as interrupted:
                    interrupted.setattr(store, "restore_vectors", disconnect_after_restore)
                    with pytest.raises(DecisionVectorRecoveryError, match="Cannot settle"):
                        await _recover_vector_undo(session, repo_id, store)
                assert journal.is_file()
                await session.rollback()
            await _recover_vector_undo(session, repo_id, store)
            await _recover_vector_undo(session, repo_id, store)
            assert not journal.exists()

        await store.list_page_ids()
        if committed:
            keeper_row = next(
                row for row in before if row["page_id"] == f"{DECISION_VECTOR_PREFIX}{keeper_id}"
            )
            before = [{**keeper_row, "page_id": f"{DECISION_VECTOR_PREFIX}{destination}"}]
        assert sorted(
            await store._table.query().to_list(), key=lambda row: row["page_id"]
        ) == sorted(before, key=lambda row: row["page_id"])
    finally:
        await store.close()
        await engine.dispose()


async def _exit_at_commit(root: Path, repository_id: str, keeper_id: str, exit_at: str) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{root / 'decision-records.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = LanceDBVectorStore(str(root / "vectors"), MockEmbedder())
    if exit_at == "during-copy":
        copy = store.copy_vectors

        async def exit_after_copy(mapping):
            copied = await copy(mapping)
            if f"{DECISION_VECTOR_PREFIX}{keeper_id}" in copied:
                os._exit(88)
            return copied

        store.copy_vectors = exit_after_copy
    async with factory() as session:
        session.add(
            DecisionAlias(
                alias_id="ordinary-enclosing-write",
                repository_id=repository_id,
                decision_id=keeper_id,
                reason=REKEYED_REASON,
            )
        )
        await session.flush()
        await apply_id_migration(session, repository_id, vector_store=store)
        if exit_at == "after-sql-commit":

            def exit_after_root_commit(sync_session):
                if sync_session.get_nested_transaction() is None:
                    os._exit(87)

            event.listen(session.sync_session, "after_commit", exit_after_root_commit)
        if exit_at in ("after-sql-commit", "after-commit"):
            await session.commit()
        elif exit_at in ("rollback", "close"):
            await getattr(session, exit_at)()
        if exit_at in ("after-commit", "rollback", "close"):
            assert not store.decision_vector_journal(repository_id).exists()
        os._exit(87)


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect_after_recovery", [False, True])
@pytest.mark.parametrize("move_both", [False, True])
async def test_vector_rekey_recovers_the_previous_occupant_before_moving_current_record(
    async_session,
    in_memory_vector_store,
    mock_embedder,
    monkeypatch,
    disconnect_after_recovery,
    move_both,
):
    repo = await insert_repo(async_session)
    first = await _legacy_record(async_session, repo.id, "First")
    shared_id = derive_decision_id(
        repo.id,
        "First",
        source="session",
        evidence_file="src/app.py",
        identity_quote="First: do the thing",
    )
    await _legacy_record(async_session, repo.id, "Second", id=shared_id)
    first_value = ([0.25] * mock_embedder.dimensions, {"title": "First"})
    second_value = ([0.75] * mock_embedder.dimensions, {"title": "Second"})
    await in_memory_vector_store.upsert_vectors(
        [
            (f"{DECISION_VECTOR_PREFIX}{first.id}", *first_value),
            (f"{DECISION_VECTOR_PREFIX}{shared_id}", *second_value),
        ]
    )
    embed = AsyncMock(side_effect=AssertionError("Both retained vectors can be copied"))
    monkeypatch.setattr(mock_embedder, "embed", embed)
    with monkeypatch.context() as interrupted:
        interrupted.setattr(
            in_memory_vector_store,
            "copy_vectors",
            AsyncMock(side_effect=OSError("Copy interrupted")),
        )
        first_plan = await apply_id_migration(
            async_session, repo.id, vector_store=in_memory_vector_store
        )
    second_id = next(row.new_id for row in first_plan.rewrites() if row.old_id == shared_id)
    await async_session.commit()

    current = await async_session.get(DecisionRecord, shared_id)
    current.identity_quote = "Revised identity"
    if move_both:
        previous = await async_session.get(DecisionRecord, second_id)
        previous.identity_quote = "Revised previous occupant identity"
    if disconnect_after_recovery:
        copy = in_memory_vector_store.copy_vectors

        async def recover_then_disconnect(mapping):
            await copy(mapping)
            raise OSError("Connection closed after recovery copy")

        with monkeypatch.context() as interrupted:
            interrupted.setattr(in_memory_vector_store, "copy_vectors", recover_then_disconnect)
            with pytest.raises(OSError, match="Connection closed after recovery copy"):
                await apply_id_migration(
                    async_session, repo.id, vector_store=in_memory_vector_store
                )
        await async_session.commit()

    second_plan = await apply_id_migration(
        async_session, repo.id, vector_store=in_memory_vector_store
    )

    destinations = {row.old_id: row.new_id for row in second_plan.rewrites()}
    expected = {
        f"{DECISION_VECTOR_PREFIX}{destinations[shared_id]}": first_value,
        f"{DECISION_VECTOR_PREFIX}{destinations.get(second_id, second_id)}": second_value,
    }
    assert in_memory_vector_store._store == expected
    await async_session.commit()
    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)
    assert in_memory_vector_store._store == expected
    embed.assert_not_called()


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
    async_session.add(
        DecisionAlias(
            alias_id=first.id, repository_id=repo.id, decision_id=second.id, reason=REKEYED_REASON
        )
    )
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
    async_session.add(
        DecisionAlias(
            alias_id="another-old-id",
            repository_id=repo.id,
            decision_id=row.new_id,
            reason=REKEYED_REASON,
        )
    )
    await async_session.flush()
    vector = [0.25] * mock_embedder.dimensions
    await in_memory_vector_store.upsert_vectors(
        [
            (f"{DECISION_VECTOR_PREFIX}{old}", vector, {"title": "Retained"})
            for old in (row.old_id, "another-old-id")
        ]
    )
    embed = AsyncMock(side_effect=AssertionError("Retained aliases can be copied"))
    monkeypatch.setattr(mock_embedder, "embed", embed)

    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    assert in_memory_vector_store._store == {
        f"{DECISION_VECTOR_PREFIX}{row.new_id}": (vector, {"title": "Retained"})
    }
    embed.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("occupied", [False, True], ids=["absent-target", "occupied-target"])
async def test_vector_rekey_preserves_conflicting_aliases_after_sql_only_moves(
    async_session, in_memory_vector_store, mock_embedder, monkeypatch, occupied
):
    repo = await insert_repo(async_session)
    original = await _legacy_record(async_session, repo.id, "Decision", id="0" * 32)
    original_key = f"{DECISION_VECTOR_PREFIX}{original.id}"
    first = await apply_id_migration(async_session, repo.id)
    current_id = first.rewrites()[0].new_id
    current = await async_session.get(DecisionRecord, current_id)
    current.identity_quote = "Changed quote"
    final = await apply_id_migration(async_session, repo.id)
    await async_session.commit()
    await in_memory_vector_store.upsert_vectors(
        [
            (original_key, [0.25] * mock_embedder.dimensions, {"title": "Original"}),
            (
                f"{DECISION_VECTOR_PREFIX}{current_id}",
                [0.75] * mock_embedder.dimensions,
                {"title": "Conflict"},
            ),
        ]
    )
    if occupied:
        final_key = f"{DECISION_VECTOR_PREFIX}{final.rewrites()[0].new_id}"
        await in_memory_vector_store.upsert_vectors(
            [
                (final_key, [0.5] * mock_embedder.dimensions, {"title": "Existing target"}),
            ]
        )
    before = dict(in_memory_vector_store._store)
    embed = AsyncMock(side_effect=AssertionError("Conflicting vectors need ownership evidence"))
    monkeypatch.setattr(mock_embedder, "embed", embed)

    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    assert in_memory_vector_store._store == before
    embed.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt_copy", [False, True], ids=["sql-only", "copy-failure"])
async def test_vector_rekey_resumes_with_a_persistent_lancedb_store(
    async_session, mock_embedder, tmp_path, monkeypatch, interrupt_copy
):
    pytest.importorskip("lancedb")
    store = LanceDBVectorStore(str(tmp_path / "decision-vectors"), mock_embedder)
    repo = await insert_repo(async_session)
    repository_id = repo.id
    legacy = await _legacy_record(async_session, repository_id, "Legacy one")
    old_key = f"{DECISION_VECTOR_PREFIX}{legacy.id}"
    try:
        await store.upsert_vectors(
            [
                (
                    old_key,
                    [0.25] * mock_embedder.dimensions,
                    {"title": "Retained wording", "content": "Original decision"},
                )
            ]
        )
        before = (await store._table.query().to_list())[0]
        plan = await plan_id_migration(async_session, repository_id)
        new_key = f"{DECISION_VECTOR_PREFIX}{plan.rewrites()[0].new_id}"
        if interrupt_copy:
            await store.upsert_vectors(
                [
                    (
                        new_key,
                        [0.75] * mock_embedder.dimensions,
                        {"title": "Retired record"},
                    )
                ]
            )
            before_copy = await store.snapshot_vectors({old_key, new_key})
            with monkeypatch.context() as interrupted:
                copy = AsyncMock(side_effect=OSError("Store temporarily unavailable"))
                interrupted.setattr(store, "copy_vectors", copy)
                with pytest.raises(DecisionVectorRecoveryError, match="Cannot verify durable"):
                    await apply_id_migration(
                        async_session, repository_id, plan=plan, vector_store=store
                    )
                copy.assert_awaited_once_with({old_key: new_key})
            assert await store.snapshot_vectors({old_key, new_key}) == before_copy
            assert (
                await plan_id_migration(async_session, repository_id)
            ).rewrites() == plan.rewrites()
        else:
            await apply_id_migration(async_session, repository_id, plan=plan)
        await async_session.commit()
        await store.close()
        store = LanceDBVectorStore(str(tmp_path / "decision-vectors"), mock_embedder)
        embed = AsyncMock(side_effect=AssertionError("Persisted vector already exists"))
        monkeypatch.setattr(mock_embedder, "embed", embed)
        list_all = AsyncMock(wraps=store.list_page_ids)
        monkeypatch.setattr(store, "list_page_ids", list_all)

        await apply_id_migration(async_session, repository_id, vector_store=store)
        await apply_id_migration(async_session, repository_id, vector_store=store)

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
        repo.id,
        "First",
        source="session",
        evidence_file="src/app.py",
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


@pytest.mark.asyncio
async def test_vector_rekey_preserves_an_alias_after_an_interrupted_id_swap(
    async_session, in_memory_vector_store, mock_embedder, monkeypatch
):
    repo = await insert_repo(async_session)
    first_id, second_id, retired_id = (
        derive_decision_id(
            repo.id, "Decision", source="session", evidence_file="src/app.py", identity_quote=quote
        )
        for quote in ("First quote", "Second quote", "Retired quote")
    )
    await _legacy_record(
        async_session, repo.id, "Decision", id=retired_id, identity_quote="First quote"
    )
    await _legacy_record(
        async_session, repo.id, "Other", id=second_id, identity_quote="Second quote"
    )
    await apply_id_migration(async_session, repo.id)
    await async_session.commit()
    await in_memory_vector_store.upsert_vectors(
        [
            (f"{DECISION_VECTOR_PREFIX}{record_id}", [value] * mock_embedder.dimensions, {})
            for record_id, value in ((retired_id, 0.25), (first_id, 0.5), (second_id, 0.75))
        ]
    )

    first = await async_session.get(DecisionRecord, first_id)
    second = await async_session.get(DecisionRecord, second_id)
    first.identity_quote, second.identity_quote = "Second quote", "First quote"
    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)
    await async_session.commit()

    first = await async_session.get(DecisionRecord, second_id)
    second = await async_session.get(DecisionRecord, first_id)
    first.identity_quote, second.identity_quote = "First quote", "Second quote"
    with monkeypatch.context() as interrupted:
        interrupted.setattr(
            in_memory_vector_store,
            "copy_vectors",
            AsyncMock(side_effect=OSError("Copy interrupted")),
        )
        await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)
    await async_session.commit()
    after_failure = dict(in_memory_vector_store._store)

    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    assert f"{DECISION_VECTOR_PREFIX}{retired_id}" in after_failure
    assert in_memory_vector_store._store == after_failure


@pytest.mark.asyncio
async def test_vector_rekey_retires_a_matching_alias_after_return_copy(
    async_session, in_memory_vector_store, mock_embedder, monkeypatch
):
    repo = await insert_repo(async_session)
    original_id = derive_decision_id(
        repo.id, "Decision", source="session", evidence_file="src/app.py", identity_quote="Original"
    )
    await _legacy_record(
        async_session, repo.id, "Decision", id=original_id, identity_quote="Second"
    )
    original_key = f"{DECISION_VECTOR_PREFIX}{original_id}"
    value = ([0.25] * mock_embedder.dimensions, {"title": "Retained"})
    await in_memory_vector_store.upsert_vectors([(original_key, *value)])
    first = await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)
    await async_session.commit()
    current_id = first.rewrites()[0].new_id
    current_key = f"{DECISION_VECTOR_PREFIX}{current_id}"
    current = await async_session.get(DecisionRecord, current_id)
    current.identity_quote = "Original"
    copy_vectors = in_memory_vector_store.copy_vectors

    async def copy_then_disconnect(mapping):
        await copy_vectors(mapping)
        raise OSError("Connection closed after the copy")

    with monkeypatch.context() as interrupted:
        interrupted.setattr(in_memory_vector_store, "copy_vectors", copy_then_disconnect)
        await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)
    await async_session.commit()
    assert in_memory_vector_store._store == {original_key: value, current_key: value}

    await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    assert in_memory_vector_store._store == {original_key: value}


@pytest.mark.asyncio
async def test_vector_rekey_moves_identical_retained_aliases_on_a_fresh_rewrite(
    async_session, in_memory_vector_store, mock_embedder
):
    repo = await insert_repo(async_session)
    first = await _legacy_record(async_session, repo.id, "Decision")
    first_id = first.id
    migration = await apply_id_migration(async_session, repo.id)
    current_id = migration.rewrites()[0].new_id
    current = await async_session.get(DecisionRecord, current_id)
    current.identity_quote = "Changed quote"
    value = ([0.25] * mock_embedder.dimensions, {"title": "Retained"})
    await in_memory_vector_store.upsert_vectors(
        [(f"{DECISION_VECTOR_PREFIX}{record_id}", *value) for record_id in (first_id, current_id)]
    )

    final = await apply_id_migration(async_session, repo.id, vector_store=in_memory_vector_store)

    assert in_memory_vector_store._store == {
        f"{DECISION_VECTOR_PREFIX}{final.rewrites()[0].new_id}": value
    }


if __name__ == "__main__":
    asyncio.run(_exit_at_commit(Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]))
