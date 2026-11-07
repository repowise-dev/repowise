"""A superseded id names its own record, never its successor.

The sequence a store actually went through: an ADR is superseded, the id
migration later moves the superseded record onto its derived id, and a
committed ``decisions.yaml`` listing both entries is imported. The supersede
used to leave an alias ``old -> successor``, the move then found the old id
taken and wrote no alias of its own, and the import followed the stale alias
and wrote the superseded record over the active successor.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from repowise.core.analysis.decisions.manifest import load_manifest, write_manifest
from repowise.core.persistence import decision_id_migration
from repowise.core.persistence.crud import bulk_upsert_decisions
from repowise.core.persistence.crud.authority import (
    accept_decision,
    latest_acceptance,
    resolve_decision_id,
    supersede_decision,
)
from repowise.core.persistence.decision_id_migration import (
    ALIAS_REASON,
    apply_id_migration,
    repair_superseded_aliases,
)
from repowise.core.persistence.decision_manifest import export_manifest, import_manifest
from repowise.core.persistence.models import DecisionAlias, DecisionRecord
from tests.unit.persistence.helpers import accept, insert_repo


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


async def _legacy_adr(
    session,
    repo_id: str,
    legacy_id: str = "8d34b37b" + "0" * 24,
    title: str = "One user-pickable agent per plugin",
):
    """An accepted ADR on a random id, the way pre-derivation stores hold them."""
    rec = DecisionRecord(
        id=legacy_id,
        repository_id=repo_id,
        title=title,
        decision=f"{title}: do the thing",
        source="adr",
        evidence_file="docs/adr/0001.md",
    )
    session.add(rec)
    await session.flush()
    await accept_decision(
        session,
        rec,
        accepter="tester",
        reason="one agent keeps the picker short",
        scope=["src/app.py"],
        evidence=["deadbeef"],
    )
    return rec


async def _superseded_then_moved(session, tmp_path):
    """Supersede, export the yaml a colleague commits, then move. Returns the ids."""
    repo = await insert_repo(session)
    successor = await _successor(session, repo.id)
    old = await _legacy_adr(session, repo.id)
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
    old = await _legacy_adr(async_session, repo.id)
    await supersede_decision(async_session, old, successor_id=successor.id, accepter="tester")
    old_id = old.id
    at = (await latest_acceptance(async_session, old_id)).created_at
    await _pointer(async_session, repo.id, old_id, successor.id, at)

    await apply_id_migration(async_session, repo.id)

    moved = await _by_title(async_session, repo.id, "One user-pickable agent per plugin")
    alias = await async_session.get(DecisionAlias, old_id)
    assert (alias.decision_id, alias.reason) == (moved.id, ALIAS_REASON)


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


async def test_the_export_does_not_list_a_superseded_id_as_an_alias(async_session, tmp_path):
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    old = await _legacy_adr(async_session, repo.id)
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
            reason=ALIAS_REASON,
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

    assert [i for i, _ in outcome.skipped] == [retired]
    assert (await async_session.get(DecisionRecord, successor.id)).title == (
        "Agents ship in group plugins"
    )


async def test_the_repair_repoints_a_stranded_old_id_at_its_moved_record(async_session):
    """The state a pre-fix store is left in, rebuilt row by row."""
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    # Already moved: its old id is gone, and its alias points at the successor.
    moved = await _legacy_adr(async_session, repo.id, legacy_id="c6bc656a" + "0" * 24)
    await supersede_decision(async_session, moved, successor_id=successor.id, accepter="tester")
    # Superseded under its current id: a bare pointer, nothing to repoint.
    await bulk_upsert_decisions(async_session, repo.id, [_dict("Older plugin layout")])
    direct = await accept(
        async_session, (await _by_title(async_session, repo.id, "Older plugin layout")).id
    )
    await supersede_decision(async_session, direct, successor_id=successor.id, accepter="tester")
    stranded = "8d34b37b" + "f" * 24
    for alias_id, rec in ((stranded, moved), (direct.id, direct)):
        at = (await latest_acceptance(async_session, rec.id)).created_at
        await _pointer(async_session, repo.id, alias_id, successor.id, at)
    moved_id, direct_id = moved.id, direct.id

    planned = await repair_superseded_aliases(async_session, repo.id, apply=False)
    assert sorted(planned) == sorted([(stranded, moved_id), (direct_id, "")])
    assert (await async_session.get(DecisionAlias, stranded)).reason == "superseded"

    await repair_superseded_aliases(async_session, repo.id)

    alias = await async_session.get(DecisionAlias, stranded)
    assert (alias.decision_id, alias.reason) == (moved_id, ALIAS_REASON)
    assert await resolve_decision_id(async_session, stranded) == moved_id
    assert await async_session.get(DecisionAlias, direct_id) is None
    assert (await async_session.get(DecisionRecord, direct_id)).superseded_by == successor.id
    assert await repair_superseded_aliases(async_session, repo.id) == []


async def test_supersede_writes_no_alias(async_session):
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    old = await _legacy_adr(async_session, repo.id)

    await supersede_decision(async_session, old, successor_id=successor.id, accepter="tester")

    assert await async_session.get(DecisionAlias, old.id) is None


_T0 = datetime(2026, 1, 1, tzinfo=UTC)


async def _moved_with_pointer(session, repo_id, successor, n: int, at: datetime, *, pointer=True):
    """A superseded record already off its old id, and the pre-fix pointer from that id.

    The acceptance is stamped at *at* and the pointer 10 ms later, as one
    supersede call wrote them. Returns ``(stranded_id, record_id)``.
    """
    rec = await _legacy_adr(session, repo_id, legacy_id=f"{n:x}" * 32, title=f"Decision {n}")
    await supersede_decision(session, rec, successor_id=successor.id, accepter="tester")
    (await latest_acceptance(session, rec.id)).created_at = at
    stranded = f"{n + 8:x}" * 32
    if pointer:
        await _pointer(session, repo_id, stranded, successor.id, at + timedelta(milliseconds=10))
    await session.flush()
    return stranded, rec.id


async def _pointer(session, repo_id, alias_id, successor_id, at) -> None:
    session.add(
        DecisionAlias(
            alias_id=alias_id,
            repository_id=repo_id,
            decision_id=successor_id,
            reason="superseded",
            created_at=at,
        )
    )
    await session.flush()


async def _move_as_pre_fix(session, repo_id, monkeypatch) -> None:
    """The id migration as the old version ran it: no supersede repair first."""

    async def _pre_fix(*_args, **_kwargs):
        return []

    with monkeypatch.context() as m:
        m.setattr(decision_id_migration, "repair_superseded_aliases", _pre_fix)
        await apply_id_migration(session, repo_id)


async def _target(session, alias_id) -> tuple[str, str]:
    alias = await session.get(DecisionAlias, alias_id)
    return alias.decision_id, alias.reason


async def test_readers_skip_a_pointer_the_repair_has_not_reached(async_session, tmp_path):
    """Before any repair runs, the old id is not the successor's."""
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    stranded, _ = await _moved_with_pointer(async_session, repo.id, successor, 1, _T0)

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
    old = await _legacy_adr(async_session, repo.id)
    await supersede_decision(async_session, old, successor_id=successor.id, accepter="tester")
    old_id = old.id
    await _pointer(async_session, repo.id, old_id, successor.id, datetime.now(UTC))
    await export_manifest(async_session, repo.id, tmp_path)

    await _move_as_pre_fix(async_session, repo.id, monkeypatch)
    moved = await _by_title(async_session, repo.id, "One user-pickable agent per plugin")
    moved_id = moved.id
    assert moved_id != old_id
    assert (await _target(async_session, old_id))[1] == "superseded"

    preview = await import_manifest(async_session, repo.id, tmp_path, dry_run=True)
    assert old_id not in preview.created
    await import_manifest(async_session, repo.id, tmp_path)

    titled = (
        await async_session.execute(
            select(DecisionRecord.id).where(
                DecisionRecord.repository_id == repo.id,
                DecisionRecord.title == "One user-pickable agent per plugin",
            )
        )
    ).scalars()
    assert list(titled) == [moved_id]
    assert await resolve_decision_id(async_session, old_id) == moved_id


async def test_the_repair_does_not_hand_a_reverted_sibling_its_neighbours_record(
    async_session,
):
    """A then B superseded by S 2 s apart; A is later restored. A's id stays A's."""
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    a_old, a_id = await _moved_with_pointer(async_session, repo.id, successor, 1, _T0)
    b_old, b_id = await _moved_with_pointer(
        async_session, repo.id, successor, 2, _T0 + timedelta(seconds=2)
    )
    (await async_session.get(DecisionRecord, a_id)).superseded_by = None
    await async_session.flush()

    await repair_superseded_aliases(async_session, repo.id)

    assert await _target(async_session, a_old) == (a_id, ALIAS_REASON)
    assert await _target(async_session, b_old) == (b_id, ALIAS_REASON)


async def test_the_repair_leaves_a_pointer_with_two_records_in_its_window(async_session):
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    await _moved_with_pointer(async_session, repo.id, successor, 1, _T0, pointer=False)
    b_old, _ = await _moved_with_pointer(
        async_session, repo.id, successor, 2, _T0 + timedelta(milliseconds=100)
    )

    await repair_superseded_aliases(async_session, repo.id)

    assert await _target(async_session, b_old) == (successor.id, "superseded")


async def test_the_repair_lets_no_two_pointers_claim_one_record(async_session):
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    first, _ = await _moved_with_pointer(async_session, repo.id, successor, 1, _T0)
    second = "e" * 32
    await _pointer(async_session, repo.id, second, successor.id, _T0 + timedelta(milliseconds=20))

    await repair_superseded_aliases(async_session, repo.id)

    assert await _target(async_session, first) == (successor.id, "superseded")
    assert await _target(async_session, second) == (successor.id, "superseded")


async def test_a_chained_supersede_does_not_strand_the_first_id(
    async_session, tmp_path, monkeypatch
):
    """A superseded by S, S by T: the old supersede moved A's pointer onto T."""
    repo = await insert_repo(async_session)
    s = await _successor(async_session, repo.id)
    await bulk_upsert_decisions(async_session, repo.id, [_dict("Third thing")])
    t = await accept(async_session, (await _by_title(async_session, repo.id, "Third thing")).id)
    a = await _legacy_adr(async_session, repo.id)
    a_old, s_id, t_id = a.id, s.id, t.id
    await supersede_decision(async_session, a, successor_id=s_id, accepter="tester")
    at = (await latest_acceptance(async_session, a_old)).created_at
    await _pointer(async_session, repo.id, a_old, s_id, at + timedelta(milliseconds=10))
    await supersede_decision(async_session, s, successor_id=t_id, accepter="tester")
    # A real now() can land inside A's pointer window on a fast runner; S's came later.
    (await latest_acceptance(async_session, s_id)).created_at = at + timedelta(seconds=2)
    # The old ``_add_alias(S, T)`` repointed every row naming S onto T.
    (await async_session.get(DecisionAlias, a_old)).decision_id = t_id
    await async_session.flush()
    await export_manifest(async_session, repo.id, tmp_path)

    await _move_as_pre_fix(async_session, repo.id, monkeypatch)
    moved_id = (await _by_title(async_session, repo.id, "One user-pickable agent per plugin")).id
    assert moved_id != a_old

    outcome = await import_manifest(async_session, repo.id, tmp_path)

    assert a_old not in outcome.created
    assert await _target(async_session, a_old) == (moved_id, ALIAS_REASON)
    titled = await async_session.execute(
        select(DecisionRecord.id).where(
            DecisionRecord.title == "One user-pickable agent per plugin"
        )
    )
    assert list(titled.scalars()) == [moved_id]


async def test_import_skips_a_pointer_the_repair_cannot_place(async_session, tmp_path):
    repo = await insert_repo(async_session)
    successor = await _successor(async_session, repo.id)
    await _moved_with_pointer(async_session, repo.id, successor, 1, _T0, pointer=False)
    b_old, b_id = await _moved_with_pointer(
        async_session, repo.id, successor, 2, _T0 + timedelta(milliseconds=100)
    )
    await export_manifest(async_session, repo.id, tmp_path)
    (b_entry,) = [e for e in load_manifest(tmp_path)[0] if e.id == b_id]
    write_manifest(tmp_path, [replace(b_entry, id=b_old)])

    outcome = await import_manifest(async_session, repo.id, tmp_path)

    assert [i for i, _ in outcome.skipped] == [b_old]
    assert await async_session.get(DecisionRecord, b_old) is None
