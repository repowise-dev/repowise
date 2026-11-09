"""A superseded id names its own record, never its successor.

The sequence a store actually went through: an ADR is superseded, the id
migration later moves the superseded record onto its derived id, and a
committed ``decisions.yaml`` listing both entries is imported. The supersede
used to leave an alias ``old -> successor``, the move then found the old id
taken and wrote no alias of its own, and the import followed the stale alias
and wrote the superseded record over the active successor.
"""

from __future__ import annotations

from sqlalchemy import select

from repowise.core.analysis.decisions.manifest import load_manifest
from repowise.core.persistence.crud import bulk_upsert_decisions
from repowise.core.persistence.crud.authority import (
    latest_acceptance,
    resolve_decision_id,
    supersede_decision,
)
from repowise.core.persistence.decision_id_migration import apply_id_migration
from repowise.core.persistence.decision_manifest import export_manifest, import_manifest
from repowise.core.persistence.models import DecisionAlias, DecisionRecord
from tests.unit.persistence.helpers import accept, insert_repo, legacy_adr, supersede_pointer


def _dict(title: str) -> dict:
    return {
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


async def _by_title(session, repo_id: str, title: str) -> DecisionRecord:
    return (
        await session.execute(
            select(DecisionRecord).where(
                DecisionRecord.repository_id == repo_id, DecisionRecord.title == title
            )
        )
    ).scalar_one()


async def _successor(session, repo_id: str) -> DecisionRecord:
    """An accepted decision already on its derived id, so it never moves."""
    await bulk_upsert_decisions(session, repo_id, [_dict("Agents ship in group plugins")])
    rec = await _by_title(session, repo_id, "Agents ship in group plugins")
    return await accept(session, rec.id)


async def _moved_with_pointer(session, repo_id, successor):
    """A superseded record already off its old id, and the pre-fix pointer from that id.

    Returns ``(stranded_id, record_id)``.
    """
    rec = await legacy_adr(session, repo_id, legacy_id="1" * 32, title="Decision 1")
    await supersede_decision(session, rec, successor_id=successor.id, accepter="tester")
    stranded = "9" * 32
    await supersede_pointer(session, repo_id, stranded, successor.id)
    return stranded, rec.id


async def _superseded_then_moved(session, tmp_path):
    """Supersede, export the yaml a colleague commits, then move. Returns the ids."""
    repo = await insert_repo(session)
    successor = await _successor(session, repo.id)
    old = await legacy_adr(session, repo.id)
    await supersede_decision(session, old, successor_id=successor.id, accepter="tester")
    await export_manifest(session, repo.id, tmp_path)
    old_id, successor_id = old.id, successor.id

    await apply_id_migration(session, repo.id)

    moved = await _by_title(session, repo.id, "One user-pickable agent per plugin")
    assert moved.id != old_id, "the fixture must actually move the superseded record"
    return repo, old_id, moved.id, successor_id


async def test_a_moved_superseded_record_keeps_its_old_id(async_session, tmp_path):
    _, old_id, moved_id, successor_id = await _superseded_then_moved(async_session, tmp_path)

    assert await resolve_decision_id(async_session, old_id) == moved_id
    assert moved_id != successor_id


async def test_importing_both_entries_leaves_the_successor_alone(async_session, tmp_path):
    repo, _, moved_id, successor_id = await _superseded_then_moved(async_session, tmp_path)

    outcome = await import_manifest(async_session, repo.id, tmp_path)

    successor = await async_session.get(DecisionRecord, successor_id)
    assert successor.title == "Agents ship in group plugins"
    assert successor.superseded_by is None
    assert (await latest_acceptance(async_session, successor_id)).currency == "active"
    moved = await async_session.get(DecisionRecord, moved_id)
    assert moved.superseded_by == successor_id
    assert not outcome.reaffirmed
    assert not outcome.skipped


async def test_the_export_does_not_list_a_superseded_id_as_an_alias(async_session, tmp_path):
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    old = await legacy_adr(async_session, repo.id)
    await supersede_decision(async_session, old, successor_id=successor.id, accepter="tester")

    await export_manifest(async_session, repo.id, tmp_path)

    entries = {e.id: e for e in load_manifest(tmp_path)[0]}
    assert old.id not in entries[successor.id].aliases
    assert entries[old.id].superseded_by == successor.id


async def test_supersede_writes_no_alias(async_session):
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    old = await legacy_adr(async_session, repo.id)

    await supersede_decision(async_session, old, successor_id=successor.id, accepter="tester")

    assert await async_session.get(DecisionAlias, old.id) is None


async def test_readers_skip_a_pre_fix_supersede_pointer(async_session, tmp_path):
    """A pointer an older supersede left does not make the old id the successor's."""
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    stranded, _ = await _moved_with_pointer(async_session, repo.id, successor)

    assert await resolve_decision_id(async_session, stranded) is None
    await export_manifest(async_session, repo.id, tmp_path)
    entries = {e.id: e for e in load_manifest(tmp_path)[0]}
    assert stranded not in entries[successor.id].aliases
