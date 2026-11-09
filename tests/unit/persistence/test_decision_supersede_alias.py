"""A superseded id names its own record, never its successor.

The sequence a store actually went through: an ADR is superseded, the id
migration later moves the superseded record onto its derived id, and a
committed ``decisions.yaml`` listing both entries is imported. The supersede
used to leave an alias ``old -> successor``, the move then found the old id
taken and wrote no alias of its own, and the import followed the stale alias
and wrote the superseded record over the active successor.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest
from sqlalchemy import event, select

from repowise.core.analysis.decisions.manifest import load_manifest, write_manifest
from repowise.core.persistence import decision_id_migration
from repowise.core.persistence.crud import bulk_upsert_decisions
from repowise.core.persistence.crud.authority import (
    latest_acceptance,
    resolve_decision_id,
    supersede_decision,
)
from repowise.core.persistence.decision_id_migration import (
    apply_id_migration,
    repair_superseded_aliases,
)
from repowise.core.persistence.decision_manifest import export_manifest, import_manifest
from repowise.core.persistence.models import (
    MERGED_REASON,
    REKEYED_REASON,
    SUPERSEDED_REASON,
    DecisionAlias,
    DecisionRecord,
)
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


async def _move_as_pre_fix(session, repo_id, monkeypatch) -> None:
    """The id migration as the old version ran it: no supersede repair first,
    so the move finds its old id taken and writes no alias."""

    async def _pre_fix(*_args, **_kwargs):
        return [], []

    with monkeypatch.context() as m:
        m.setattr(decision_id_migration, "repair_superseded_aliases", _pre_fix)
        await apply_id_migration(session, repo_id)


async def _target(session, alias_id) -> tuple[str, str]:
    alias = await session.get(DecisionAlias, alias_id)
    return alias.decision_id, alias.reason


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


async def test_a_legacy_supersede_pointer_gives_way_to_the_move(async_session):
    """A store written before the fix already holds ``old -> successor``."""
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    old = await legacy_adr(async_session, repo.id)
    await supersede_decision(async_session, old, successor_id=successor.id, accepter="tester")
    old_id = old.id
    await supersede_pointer(async_session, repo.id, old_id, successor.id)

    await apply_id_migration(async_session, repo.id)

    moved = await _by_title(async_session, repo.id, "One user-pickable agent per plugin")
    assert await _target(async_session, old_id) == (moved.id, REKEYED_REASON)


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


async def test_import_refuses_an_alias_onto_a_record_the_file_lists(async_session, tmp_path):
    """Whatever wrote the alias, it must not carry one entry over another."""
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    await export_manifest(async_session, repo.id, tmp_path)
    (successor_entry,) = load_manifest(tmp_path)[0]
    retired = "a" * 32
    async_session.add(
        DecisionAlias(
            alias_id=retired,
            repository_id=repo.id,
            decision_id=successor.id,
            reason=REKEYED_REASON,
        )
    )
    await async_session.flush()
    write_manifest(
        tmp_path,
        [
            successor_entry,
            replace(successor_entry, id=retired, title="Something else"),
        ],
    )

    outcome = await import_manifest(async_session, repo.id, tmp_path)

    assert outcome.skipped == [
        (retired, f"its alias resolves to {successor.id[:8]}, which the file lists")
    ]
    assert (await async_session.get(DecisionRecord, successor.id)).title == (
        "Agents ship in group plugins"
    )


async def test_the_repair_deletes_live_pointers_and_leaves_moved_ones_unplaced(async_session):
    """The state a pre-fix store is left in, rebuilt row by row."""
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    # Already moved: its old id is gone, and its pointer names the successor.
    stranded, _ = await _moved_with_pointer(async_session, repo.id, successor)
    # Superseded under its current id: a bare pointer.
    await bulk_upsert_decisions(async_session, repo.id, [_dict("Older plugin layout")])
    direct = await accept(
        async_session, (await _by_title(async_session, repo.id, "Older plugin layout")).id
    )
    await supersede_decision(async_session, direct, successor_id=successor.id, accepter="tester")
    await supersede_pointer(async_session, repo.id, direct.id, successor.id)
    direct_id, successor_id = direct.id, successor.id

    planned = await repair_superseded_aliases(async_session, repo.id, apply=False)
    assert planned == ([direct_id], [stranded])
    assert (await _target(async_session, direct_id))[1] == SUPERSEDED_REASON

    assert await repair_superseded_aliases(async_session, repo.id) == planned

    assert await _target(async_session, stranded) == (successor_id, SUPERSEDED_REASON)
    assert await resolve_decision_id(async_session, stranded) is None
    assert await async_session.get(DecisionAlias, direct_id) is None
    assert (await async_session.get(DecisionRecord, direct_id)).superseded_by == successor_id
    assert await repair_superseded_aliases(async_session, repo.id) == ([], [stranded])


async def test_the_repair_costs_a_clean_store_one_statement(async_engine, async_session):
    """It runs on every index save, and a store with no pointers has nothing to look up."""
    repo = await insert_repo(async_session)
    statements: list[str] = []

    def _count(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    event.listen(async_engine.sync_engine, "before_cursor_execute", _count)
    try:
        assert await repair_superseded_aliases(async_session, repo.id) == ([], [])
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", _count)
    assert len(statements) == 1, statements


async def test_the_repair_reads_live_records_past_the_parameter_cap(async_session):
    """A pre-fix store with more supersede pointers than SQLite binds per statement.

    The cap is lowered to 600 on the test connection, so 700 pointers go past it
    the way 1000 go past the 999 of a SQLite build older than 3.32.
    """
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    stranded = [f"{i:032x}" for i in range(700)]
    for alias_id in stranded:
        await supersede_pointer(async_session, repo.id, alias_id, successor.id)
    raw = await (await async_session.connection()).get_raw_connection()
    raw.driver_connection._conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 600)

    deleted, unplaced = await repair_superseded_aliases(async_session, repo.id, apply=False)

    assert (deleted, sorted(unplaced)) == ([], stranded)


async def test_import_reports_a_deleted_pointer(async_session, tmp_path):
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    old = await legacy_adr(async_session, repo.id)
    await supersede_decision(async_session, old, successor_id=successor.id, accepter="tester")
    old_id = old.id
    await supersede_pointer(async_session, repo.id, old_id, successor.id)
    await export_manifest(async_session, repo.id, tmp_path)

    preview = await import_manifest(async_session, repo.id, tmp_path, dry_run=True)
    assert preview.as_dict()["repaired"] == [old_id]
    assert await async_session.get(DecisionAlias, old_id) is not None

    outcome = await import_manifest(async_session, repo.id, tmp_path)

    assert outcome.as_dict()["repaired"] == [old_id]
    assert await async_session.get(DecisionAlias, old_id) is None


async def test_supersede_writes_no_alias(async_session):
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    old = await legacy_adr(async_session, repo.id)

    await supersede_decision(async_session, old, successor_id=successor.id, accepter="tester")

    assert await async_session.get(DecisionAlias, old.id) is None


async def test_readers_skip_a_pointer_the_repair_has_not_reached(async_session, tmp_path):
    """Before any repair runs, the old id is not the successor's."""
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    stranded, _ = await _moved_with_pointer(async_session, repo.id, successor)

    assert await resolve_decision_id(async_session, stranded) is None
    await export_manifest(async_session, repo.id, tmp_path)
    entries = {e.id: e for e in load_manifest(tmp_path)[0]}
    assert stranded not in entries[successor.id].aliases


async def test_import_before_a_full_index_does_not_recreate_a_moved_record(
    async_session, tmp_path, monkeypatch
):
    """The upgrade path: a store the old supersede and move left stranded, then an import."""
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    old = await legacy_adr(async_session, repo.id)
    await supersede_decision(async_session, old, successor_id=successor.id, accepter="tester")
    old_id = old.id
    await supersede_pointer(async_session, repo.id, old_id, successor.id)
    await export_manifest(async_session, repo.id, tmp_path)

    await _move_as_pre_fix(async_session, repo.id, monkeypatch)
    moved = await _by_title(async_session, repo.id, "One user-pickable agent per plugin")
    moved_id = moved.id
    assert moved_id != old_id
    assert (await _target(async_session, old_id))[1] == SUPERSEDED_REASON

    preview = await import_manifest(async_session, repo.id, tmp_path, dry_run=True)
    outcome = await import_manifest(async_session, repo.id, tmp_path)

    assert preview.as_dict() == outcome.as_dict()
    assert outcome.skipped == [(old_id, "retired id, record moved, resolves to nothing")]
    assert not outcome.created and not outcome.repaired

    titled = (
        await async_session.execute(
            select(DecisionRecord.id).where(
                DecisionRecord.repository_id == repo.id,
                DecisionRecord.title == "One user-pickable agent per plugin",
            )
        )
    ).scalars()
    assert list(titled) == [moved_id]
    assert await resolve_decision_id(async_session, old_id) is None


@pytest.mark.parametrize("blank_reason", [False, True], ids=["listed", "successor-skipped"])
async def test_import_refuses_an_alias_onto_a_successor_listed_under_its_legacy_id(
    async_session, tmp_path, blank_reason
):
    """The known-limit row ``X_old -> S`` must not overwrite S when the file
    lists S under its own pre-migration id rather than the id it lives on, even
    when S's own entry is skipped."""
    s_old, x_old = "a" * 32, "b" * 32
    repo = await insert_repo(async_session)
    await legacy_adr(async_session, repo.id, legacy_id=s_old, title="Successor rule")
    x = await legacy_adr(async_session, repo.id, legacy_id=x_old, title="Old rule")
    x.evidence_file = "docs/adr/0002.md"
    await async_session.flush()
    await export_manifest(async_session, repo.id, tmp_path)
    await apply_id_migration(async_session, repo.id)
    s = await _by_title(async_session, repo.id, "Successor rule")
    x = await _by_title(async_session, repo.id, "Old rule")
    s_id = s.id
    await supersede_decision(async_session, x, successor_id=s_id, accepter="tester")
    # The old ``_add_alias(X, S)`` repointed X's re-key row onto S.
    (await async_session.get(DecisionAlias, x_old)).decision_id = s_id
    await async_session.flush()
    assert await resolve_decision_id(async_session, x_old) == s_id
    if blank_reason:
        entries = load_manifest(tmp_path)[0]
        write_manifest(tmp_path, [replace(e, reason="") if e.id == s_old else e for e in entries])

    outcome = await import_manifest(async_session, repo.id, tmp_path)

    async_session.expire_all()
    assert (await async_session.get(DecisionRecord, s_id)).title == "Successor rule"
    # Nothing stored tells the two claimants apart, so neither is applied.
    rival = f"its alias resolves to {s_id[:8]}, which another entry also reaches"
    assert dict(outcome.skipped) == {
        s_old: "no reason recorded" if blank_reason else rival,
        x_old: rival,
    }


async def _folded_into_keeper(session, tmp_path, repo_id: str, *alias_ids: str):
    """An accepted keeper with each alias id folded into it. Returns the keeper
    and its exported entry, to relist under the alias ids."""
    keeper = await _successor(session, repo_id)
    for alias_id in alias_ids:
        session.add(
            DecisionAlias(
                alias_id=alias_id,
                repository_id=repo_id,
                decision_id=keeper.id,
                reason=MERGED_REASON,
            )
        )
    await session.flush()
    await export_manifest(session, repo_id, tmp_path)
    (entry,) = load_manifest(tmp_path)[0]
    return keeper, entry


async def test_import_applies_an_entry_through_a_merged_alias(async_session, tmp_path):
    folded = "a" * 32
    repo = await insert_repo(async_session)
    keeper, entry = await _folded_into_keeper(async_session, tmp_path, repo.id, folded)
    keeper_id = keeper.id
    write_manifest(tmp_path, [replace(entry, id=folded, title="Agents ship in one plugin")])

    outcome = await import_manifest(async_session, repo.id, tmp_path)

    async_session.expire_all()
    assert (await async_session.get(DecisionRecord, keeper_id)).title == "Agents ship in one plugin"
    assert outcome.reaffirmed == [folded] and not outcome.skipped


async def test_import_skips_aliases_into_another_repository(async_session, tmp_path):
    """Another repository's file whose two ids both resolve to one record of
    this one: the record is not that file's, whatever else the ids reach."""
    a, b = "a" * 32, "b" * 32
    repo = await insert_repo(async_session)
    other = await insert_repo(async_session, name="other", local_path="/tmp/other")
    _, entry = await _folded_into_keeper(async_session, tmp_path, repo.id, a, b)
    write_manifest(tmp_path, [replace(entry, id=a), replace(entry, id=b)])

    outcome = await import_manifest(async_session, other.id, tmp_path)

    assert dict(outcome.skipped) == {
        a: "belongs to another repository",
        b: "belongs to another repository",
    }


async def test_import_creates_a_new_id_listed_twice_once(async_session, tmp_path):
    """The loader keeps duplicate ids, so the second copy must find the record
    the first one created rather than insert the same id again."""
    new_id = "c" * 32
    repo = await insert_repo(async_session)
    _, entry = await _folded_into_keeper(async_session, tmp_path, repo.id)
    write_manifest(tmp_path, [replace(entry, id=new_id), replace(entry, id=new_id)])

    outcome = await import_manifest(async_session, repo.id, tmp_path)

    assert outcome.created == [new_id] and outcome.unchanged == [new_id]
    assert await async_session.get(DecisionRecord, new_id) is not None


async def test_import_looks_up_each_new_id_once(async_engine, async_session, tmp_path):
    """A new entry resolves once, before the loop, plus one batched alias-reason read."""
    new_ids = [c * 32 for c in "cde"]
    repo = await insert_repo(async_session)
    _, entry = await _folded_into_keeper(async_session, tmp_path, repo.id)
    write_manifest(tmp_path, [replace(entry, id=i) for i in new_ids])
    lookups: list[str] = []

    def _count(conn, cursor, statement, params, context, executemany):
        if statement.startswith("SELECT decision_aliases.") and (
            "alias_id = ?" in statement or "alias_id IN (" in statement
        ):
            lookups.append(statement)

    event.listen(async_engine.sync_engine, "before_cursor_execute", _count)
    try:
        outcome = await import_manifest(async_session, repo.id, tmp_path, dry_run=True)
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", _count)

    assert outcome.created == new_ids
    assert len(lookups) == len(new_ids) + 1


async def test_import_skips_a_rekeyed_and_a_folded_alias_to_a_missing_record(
    async_session, tmp_path
):
    """A re-keyed and a folded alias row left naming a deleted record: both
    entries are skipped and neither is created."""
    a, b, gone = "a" * 32, "b" * 32, "9" * 32
    repo = await insert_repo(async_session)
    _, entry = await _folded_into_keeper(async_session, tmp_path, repo.id)
    for alias_id, why in ((a, REKEYED_REASON), (b, MERGED_REASON)):
        async_session.add(
            DecisionAlias(alias_id=alias_id, repository_id=repo.id, decision_id=gone, reason=why)
        )
    await async_session.flush()
    write_manifest(tmp_path, [replace(entry, id=a), replace(entry, id=b)])

    outcome = await import_manifest(async_session, repo.id, tmp_path)

    rival = f"its alias resolves to {gone[:8]}, which another entry also reaches"
    assert dict(outcome.skipped) == {a: rival, b: rival}
    assert not outcome.created
