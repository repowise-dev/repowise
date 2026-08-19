"""Un-retiring what the semantic supersession detector (3B) hid.

Turning 3B off stops the next bad retirement and does nothing about the ones
already made: ``superseded`` is a protected status, so re-extraction will never
walk one back. ``unretire_auto_superseded`` is the other half of that change —
it restores the records the detector flipped and deletes the edges it wrote,
while leaving retirements a human made alone.
"""

from __future__ import annotations

from repowise.core.persistence.crud import get_decision, unretire_auto_superseded
from repowise.core.persistence.decision_graph import get_decision_edges, upsert_decision_edge
from repowise.core.persistence.models import DecisionRecord
from tests.unit.persistence.helpers import insert_repo


async def _record(session, repo_id: str, title: str, **kwargs) -> DecisionRecord:
    rec = DecisionRecord(repository_id=repo_id, title=title, source="pr", **kwargs)
    session.add(rec)
    await session.flush()
    return rec


async def test_auto_retired_record_is_restored_and_its_edge_deleted(async_session):
    repo = await insert_repo(async_session)
    newer = await _record(async_session, repo.id, "Adopt JWT", status="active")
    older = await _record(
        async_session,
        repo.id,
        "Use sessions",
        status="superseded",
        superseded_by=newer.id,
    )
    await upsert_decision_edge(
        async_session,
        repository_id=repo.id,
        src_decision_id=newer.id,
        dst_decision_id=older.id,
        kind="supersedes",
        confidence=0.9,
        evidence="auto-detected: instead of (sim=0.81)",
    )

    assert await unretire_auto_superseded(async_session) == 1

    restored = await get_decision(async_session, older.id)
    assert restored.status == "proposed"
    assert restored.superseded_by is None
    # The lineage the detector fabricated goes with it.
    assert await get_decision_edges(async_session, newer.id) == []


async def test_human_retirement_is_left_alone(async_session):
    """No ``auto-detected:`` edge → not the detector's doing → not ours to undo."""
    repo = await insert_repo(async_session)
    newer = await _record(async_session, repo.id, "Adopt JWT", status="active")
    older = await _record(
        async_session,
        repo.id,
        "Use sessions",
        status="superseded",
        superseded_by=newer.id,
    )
    await upsert_decision_edge(
        async_session,
        repository_id=repo.id,
        src_decision_id=newer.id,
        dst_decision_id=older.id,
        kind="supersedes",
        confidence=1.0,
        evidence="repowise decision deprecate --superseded-by",
    )

    assert await unretire_auto_superseded(async_session) == 0
    assert (await get_decision(async_session, older.id)).status == "superseded"
    assert len(await get_decision_edges(async_session, newer.id)) == 1


async def test_auto_edge_to_a_record_retired_by_someone_else_only_drops_the_edge(async_session):
    """The edge is still the detector's noise; the retirement is not its doing."""
    repo = await insert_repo(async_session)
    detector_pick = await _record(async_session, repo.id, "Adopt JWT", status="active")
    real_successor = await _record(async_session, repo.id, "Adopt OAuth", status="active")
    older = await _record(
        async_session,
        repo.id,
        "Use sessions",
        status="superseded",
        superseded_by=real_successor.id,
    )
    await upsert_decision_edge(
        async_session,
        repository_id=repo.id,
        src_decision_id=detector_pick.id,
        dst_decision_id=older.id,
        kind="supersedes",
        confidence=0.9,
        evidence="auto-detected: replace (sim=0.83)",
    )

    assert await unretire_auto_superseded(async_session) == 0
    assert (await get_decision(async_session, older.id)).status == "superseded"
    assert await get_decision_edges(async_session, detector_pick.id) == []


async def test_fabricated_conflicts_are_dropped_too(async_session):
    """``conflicts_with`` retires nothing but is counted as a governance smell."""
    repo = await insert_repo(async_session)
    a = await _record(async_session, repo.id, "Sync worker IO", status="active")
    b = await _record(async_session, repo.id, "Async worker IO", status="active")
    await upsert_decision_edge(
        async_session,
        repository_id=repo.id,
        src_decision_id=a.id,
        dst_decision_id=b.id,
        kind="conflicts_with",
        confidence=0.8,
        evidence="auto-detected: opposing-verbs (sim=0.82)",
    )

    assert await unretire_auto_superseded(async_session) == 0
    assert await get_decision_edges(async_session, a.id) == []
    assert (await get_decision(async_session, a.id)).status == "active"


async def test_is_idempotent(async_session):
    repo = await insert_repo(async_session)
    newer = await _record(async_session, repo.id, "Adopt JWT", status="active")
    older = await _record(
        async_session,
        repo.id,
        "Use sessions",
        status="superseded",
        superseded_by=newer.id,
    )
    await upsert_decision_edge(
        async_session,
        repository_id=repo.id,
        src_decision_id=newer.id,
        dst_decision_id=older.id,
        kind="supersedes",
        confidence=0.9,
        evidence="auto-detected: instead of (sim=0.81)",
    )

    assert await unretire_auto_superseded(async_session) == 1
    # The edges are gone, so the second pass has nothing to match — and in
    # particular it does not re-restore a record a human has since re-retired.
    assert await unretire_auto_superseded(async_session) == 0


async def test_restoring_a_retired_source_row_hands_it_to_the_purge(async_session):
    """``proposed`` is what ``purge_proposed_decisions_by_source`` deletes.

    So a restored ``changelog``/``readme_mining`` row is on its way out, and the
    persist paths run this repair *before* the purge for that reason: one run
    then ends in a single consistent state instead of leaving the row alive for
    one run and silently deleting it on the next.
    """
    from repowise.core.analysis.decision_provenance import RETIRED_SOURCES
    from repowise.core.persistence.crud import purge_proposed_decisions_by_source

    repo = await insert_repo(async_session)
    newer = await _record(async_session, repo.id, "Adopt JWT", status="active")
    older = await _record(async_session, repo.id, "Changelog line", status="superseded")
    older.source = "changelog"
    older.superseded_by = newer.id
    await async_session.flush()
    await upsert_decision_edge(
        async_session,
        repository_id=repo.id,
        src_decision_id=newer.id,
        dst_decision_id=older.id,
        kind="supersedes",
        confidence=0.9,
        evidence="auto-detected: replace (sim=0.82)",
    )

    assert "changelog" in RETIRED_SOURCES
    assert await unretire_auto_superseded(async_session) == 1
    assert await purge_proposed_decisions_by_source(async_session, repo.id, "changelog") == 1
    assert await get_decision(async_session, older.id) is None
