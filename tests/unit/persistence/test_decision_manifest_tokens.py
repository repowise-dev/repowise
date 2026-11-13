"""An exported base token distinguishes a file edit from a stale store snapshot."""

from __future__ import annotations

from dataclasses import replace

import pytest

from repowise.core.analysis.decisions.manifest import load_manifest, write_manifest
from repowise.core.persistence.crud.authority import (
    accept_decision,
    latest_acceptance,
    record_acceptance,
)
from repowise.core.persistence.decision_manifest import export_manifest, import_manifest
from repowise.core.persistence.models import DecisionRecord
from tests.unit.persistence.helpers import insert_repo, legacy_adr

CHANGES = [
    pytest.param({"title": "A corrected title"}, id="title"),
    pytest.param({"decision": "Take the new path"}, id="decision"),
    pytest.param({"reason": "The alternative is now faster"}, id="reason"),
    pytest.param({"scope": ["src/other.py"]}, id="scope"),
    pytest.param({"currency": "dismissed"}, id="currency"),
    pytest.param({"kind": "agreement"}, id="kind"),
]


@pytest.fixture
async def exported(async_session, tmp_path):
    repo = await insert_repo(async_session)
    record = await legacy_adr(async_session, repo.id)
    acceptance = await latest_acceptance(async_session, record.id)
    await accept_decision(
        async_session, record, accepter="tester", reason=acceptance.reason,
        scope=["src/b.py", "src/a.py"],
    )
    await export_manifest(async_session, repo.id, tmp_path)
    (entry,), _ = load_manifest(tmp_path)
    return repo, record, entry


async def _change_store(session, record, entry, **changes):
    for field in ("title", "decision", "kind"):
        if field in changes:
            setattr(record, field, changes[field])
    return await record_acceptance(
        session,
        record,
        action="accepted",
        kind="person",
        reason=changes.get("reason", entry.reason),
        scope=changes.get("scope", entry.scope),
        currency=changes.get("currency", entry.currency),
        evidence=entry.evidence,
        accepter="another reviewer",
    )


@pytest.mark.parametrize("changes", CHANGES)
async def test_matching_token_allows_an_edit(async_session, tmp_path, exported, changes):
    repo, record, entry = exported
    before = await latest_acceptance(async_session, record.id)
    assert len(entry.base_token) == 16
    write_manifest(tmp_path, [replace(entry, **changes)])

    preview = await import_manifest(async_session, repo.id, tmp_path, dry_run=True)
    assert (await latest_acceptance(async_session, record.id)).id == before.id
    outcome = await import_manifest(async_session, repo.id, tmp_path)

    assert preview.as_dict() == outcome.as_dict()
    assert outcome.reaffirmed == [entry.id]
    assert not outcome.skipped
    assert (await latest_acceptance(async_session, record.id)).id != before.id
    await export_manifest(async_session, repo.id, tmp_path)
    (updated,), _ = load_manifest(tmp_path)
    for field, value in changes.items():
        assert getattr(updated, field) == value
    assert updated.base_token != entry.base_token


@pytest.mark.parametrize("changes", CHANGES)
async def test_store_change_skips_a_stale_entry_under_its_own_id(
    async_session, tmp_path, exported, changes
):
    repo, record, entry = exported
    current = await _change_store(async_session, record, entry, **changes)

    preview = await import_manifest(async_session, repo.id, tmp_path, dry_run=True)
    outcome = await import_manifest(async_session, repo.id, tmp_path)

    assert preview.as_dict() == outcome.as_dict()
    assert outcome.as_dict()["skipped"] == [
        {"id": entry.id, "reason": "changed in the store since this file was exported"}
    ]
    assert not outcome.reaffirmed
    assert (await latest_acceptance(async_session, record.id)).id == current.id
    await export_manifest(async_session, repo.id, tmp_path)
    (kept,), _ = load_manifest(tmp_path)
    for field, value in changes.items():
        assert getattr(kept, field) == value


async def test_a_legacy_file_still_reaccepts_its_edit(async_session, tmp_path, exported):
    repo, record, entry = exported
    current = await _change_store(async_session, record, entry, reason="Changed in the store")
    legacy = replace(entry, base_token="", title="A legacy file edit")
    assert "base_token" not in legacy.to_mapping()
    write_manifest(tmp_path, [legacy])
    assert load_manifest(tmp_path)[0][0].base_token == ""

    outcome = await import_manifest(async_session, repo.id, tmp_path)

    assert outcome.reaffirmed == [entry.id]
    assert outcome.unchecked == [entry.id]
    assert not outcome.skipped
    accepted = await latest_acceptance(async_session, record.id)
    assert accepted.id != current.id
    assert accepted.reason == entry.reason
    assert record.title == legacy.title


async def test_force_reaccepts_a_stale_entry_and_dry_run_writes_nothing(
    async_session, tmp_path, exported
):
    repo, record, entry = exported
    current = await _change_store(async_session, record, entry, reason="Changed in the store")
    write_manifest(tmp_path, [replace(entry, title="An intentional overwrite")])

    preview = await import_manifest(async_session, repo.id, tmp_path, force=True, dry_run=True)
    assert (await latest_acceptance(async_session, record.id)).id == current.id
    assert record.title == entry.title
    outcome = await import_manifest(async_session, repo.id, tmp_path, force=True)

    assert preview.as_dict() == outcome.as_dict()
    assert outcome.reaffirmed == [entry.id]
    assert not outcome.skipped
    assert record.title == "An intentional overwrite"
    assert (await latest_acceptance(async_session, record.id)).reason == entry.reason


async def test_a_stale_token_is_unchanged_when_the_contents_already_match(
    async_session, tmp_path, exported
):
    repo, record, entry = exported
    reason = "The same edit reached the store already"
    current = await _change_store(async_session, record, entry, reason=reason)
    write_manifest(tmp_path, [replace(entry, reason=reason)])

    for force in (False, True):
        outcome = await import_manifest(async_session, repo.id, tmp_path, force=force)
        assert outcome.unchanged == [entry.id]
        assert not outcome.skipped and not outcome.reaffirmed
        assert (await latest_acceptance(async_session, record.id)).id == current.id


async def test_a_new_acceptance_with_equivalent_content_keeps_the_token(
    async_session, tmp_path, exported
):
    repo, record, entry = exported
    current = await _change_store(
        async_session, record, entry,
        reason=f"  {entry.reason}  ", scope=list(reversed(entry.scope)),
    )
    await export_manifest(async_session, repo.id, tmp_path)
    (equivalent,), _ = load_manifest(tmp_path)
    assert equivalent.base_token == entry.base_token
    write_manifest(tmp_path, [replace(entry, reason="An edit based on the same content")])

    outcome = await import_manifest(async_session, repo.id, tmp_path)

    assert outcome.reaffirmed == [entry.id]
    assert (await latest_acceptance(async_session, record.id)).id != current.id


async def test_exported_token_survives_rebuilding_a_store(async_session, tmp_path, exported):
    repo, record, entry = exported
    original = await latest_acceptance(async_session, record.id)
    await async_session.delete(record)
    await async_session.flush()

    outcome = await import_manifest(async_session, repo.id, tmp_path)
    assert outcome.created == [entry.id]
    rebuilt = await async_session.get(DecisionRecord, entry.id)
    assert (await latest_acceptance(async_session, rebuilt.id)).id != original.id
    await export_manifest(async_session, repo.id, tmp_path)
    (imported,), _ = load_manifest(tmp_path)
    assert imported.base_token == entry.base_token

    write_manifest(tmp_path, [replace(entry, reason="A colleague's edit from the original store")])
    assert (await import_manifest(async_session, repo.id, tmp_path)).reaffirmed == [entry.id]


async def test_a_change_to_one_entry_does_not_block_another(async_session, tmp_path, exported):
    repo, record, entry = exported
    other = await legacy_adr(async_session, repo.id, legacy_id="b" * 32, title="Another rule")
    await export_manifest(async_session, repo.id, tmp_path)
    entries, _ = load_manifest(tmp_path)
    current = await _change_store(async_session, record, entry, reason="Changed in the store")
    write_manifest(tmp_path, [replace(e, reason="An intentional edit") for e in entries])

    outcome = await import_manifest(async_session, repo.id, tmp_path)

    assert outcome.reaffirmed == [other.id]
    assert [decision_id for decision_id, _ in outcome.skipped] == [record.id]
    assert (await latest_acceptance(async_session, record.id)).id == current.id
    assert (await latest_acceptance(async_session, other.id)).reason == "An intentional edit"
