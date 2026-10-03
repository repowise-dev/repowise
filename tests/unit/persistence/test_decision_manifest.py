"""``.repowise/decisions.yaml`` is the tracked source of truth for decisions.

What matters here is that the file and the store cannot drift silently: the
render is deterministic, the round trip is lossless, an empty store does not
overwrite a committed file, and a colleague's edit wins on import.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import select

from repowise.core.analysis.decisions.manifest import (
    MANIFEST_VERSION,
    ManifestConflictError,
    ManifestDecision,
    load_manifest,
    manifest_path,
    render_manifest,
    write_manifest,
)
from repowise.core.analysis.decisions.provenance import compute_confidence, rank_for_source
from repowise.core.persistence.crud import bulk_upsert_decisions
from repowise.core.persistence.crud.authority import (
    accept_decision,
    dismiss_candidate,
    latest_acceptance,
)
from repowise.core.persistence.decision_manifest import export_manifest, import_manifest
from repowise.core.persistence.models import DecisionRecord
from tests.unit.persistence.helpers import insert_repo


def _entry(decision_id: str, **overrides) -> ManifestDecision:
    fields = {
        "id": decision_id,
        "title": f"Decision {decision_id}",
        "decision": "do the thing",
        "reason": "because the alternative was slower",
        "scope": ["src/b.py", "src/a.py"],
        "accepted_at": "2026-09-01T00:00:00+00:00",
        "accepted_by": "tester",
        "evidence": ["deadbeef"],
    }
    fields.update(overrides)
    return ManifestDecision(**fields)


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
# Rendering
# ---------------------------------------------------------------------------


def test_the_render_does_not_depend_on_input_order(tmp_path):
    """Two stores holding the same decisions produce the same bytes.

    Ordering by id rather than by acceptance time is what keeps a re-accepted
    decision from moving and turning a one-line change into a whole-file diff.
    """
    a, b, c = _entry("aaa"), _entry("bbb"), _entry("ccc")
    assert render_manifest([c, a, b]) == render_manifest([a, b, c])


def test_the_round_trip_is_lossless(tmp_path):
    entries = [_entry("aaa", superseded_by="bbb", aliases=["zzz"]), _entry("bbb")]
    write_manifest(tmp_path, entries)

    parsed, _ = load_manifest(tmp_path)

    assert render_manifest(parsed) == render_manifest(entries)
    assert parsed[0].scope == sorted(entries[0].scope)
    assert parsed[0].aliases == ["zzz"]


def test_an_unchanged_render_is_not_rewritten(tmp_path):
    entries = [_entry("aaa")]
    assert write_manifest(tmp_path, entries) is True
    assert write_manifest(tmp_path, entries) is False


def test_a_concurrent_change_is_refused_rather_than_clobbered(tmp_path):
    write_manifest(tmp_path, [_entry("aaa")])
    _, raw = load_manifest(tmp_path)

    manifest_path(tmp_path).write_text(
        render_manifest([_entry("aaa"), _entry("bbb")]), encoding="utf-8"
    )

    with pytest.raises(ManifestConflictError, match="changed since"):
        write_manifest(tmp_path, [_entry("aaa", reason="mine")], expected_raw=raw)


def test_an_empty_store_does_not_erase_a_committed_file(tmp_path):
    """The recovery path is deleting the index; it must not delete the file."""
    write_manifest(tmp_path, [_entry("aaa")])

    with pytest.raises(ManifestConflictError, match="Import it first"):
        write_manifest(tmp_path, [])

    assert load_manifest(tmp_path)[0]
    assert write_manifest(tmp_path, [], allow_empty=True) is True


def test_a_newer_format_is_refused_rather_than_downgraded(tmp_path):
    path = manifest_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"version: {MANIFEST_VERSION + 1}\ndecisions: []\n", encoding="utf-8")

    with pytest.raises(ManifestConflictError, match="newer repowise"):
        load_manifest(tmp_path)


def test_a_malformed_version_is_a_conflict_not_a_traceback(tmp_path):
    path = manifest_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("version: one\ndecisions: []\n", encoding="utf-8")

    with pytest.raises(ManifestConflictError, match="non-numeric"):
        load_manifest(tmp_path)


# ---------------------------------------------------------------------------
# Store <-> file
# ---------------------------------------------------------------------------


async def test_only_accepted_decisions_are_exported(async_session, tmp_path):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(
        async_session, repo.id, [_dict("Accepted"), _dict("Candidate")]
    )
    rows = {
        r.title: r
        for r in (
            (
                await async_session.execute(
                    select(DecisionRecord).where(DecisionRecord.repository_id == repo.id)
                )
            )
            .scalars()
            .all()
        )
    }
    await accept_decision(async_session, rows["Accepted"], accepter="tester")

    count, changed = await export_manifest(async_session, repo.id, tmp_path)

    assert (count, changed) == (1, True)
    entries, _ = load_manifest(tmp_path)
    assert [e.title for e in entries] == ["Accepted"]
    assert entries[0].accepted_by == "tester"


async def test_export_import_export_is_a_fixed_point(async_session, tmp_path):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_dict("Round trip")])
    rec = (
        (
            await async_session.execute(
                select(DecisionRecord).where(DecisionRecord.repository_id == repo.id)
            )
        )
        .scalars()
        .one()
    )
    await accept_decision(async_session, rec, accepter="tester")

    await export_manifest(async_session, repo.id, tmp_path)
    first = manifest_path(tmp_path).read_text(encoding="utf-8")

    outcome = await import_manifest(async_session, repo.id, tmp_path)
    assert outcome.unchanged == [rec.id]

    await export_manifest(async_session, repo.id, tmp_path)
    assert manifest_path(tmp_path).read_text(encoding="utf-8") == first


async def test_the_file_creates_and_accepts_a_decision_the_store_lacks(
    async_session, tmp_path
):
    """A colleague's commit is the authority; the store is the copy."""
    repo = await insert_repo(async_session)
    write_manifest(tmp_path, [_entry("f" * 32)])

    outcome = await import_manifest(async_session, repo.id, tmp_path)

    assert outcome.created == ["f" * 32]
    rec = await async_session.get(DecisionRecord, "f" * 32)
    assert rec is not None and rec.status == "active"
    acceptance = await latest_acceptance(async_session, rec.id)
    assert acceptance.artifact == ".repowise/decisions.yaml"


async def test_a_dry_run_import_writes_nothing(async_session, tmp_path):
    repo = await insert_repo(async_session)
    write_manifest(tmp_path, [_entry("f" * 32)])

    outcome = await import_manifest(async_session, repo.id, tmp_path, dry_run=True)

    assert outcome.created == ["f" * 32]
    assert await async_session.get(DecisionRecord, "f" * 32) is None


async def test_an_edited_entry_is_reconciled_rather_than_reported_unchanged(
    async_session, tmp_path
):
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_dict("Edited elsewhere")])
    rec = (
        (
            await async_session.execute(
                select(DecisionRecord).where(DecisionRecord.repository_id == repo.id)
            )
        )
        .scalars()
        .one()
    )
    await accept_decision(async_session, rec, accepter="tester")
    await export_manifest(async_session, repo.id, tmp_path)

    entries, _ = load_manifest(tmp_path)
    write_manifest(
        tmp_path,
        [
            replace(
                entries[0],
                reason="a colleague corrected this",
                scope=["src/corrected.py"],
            )
        ],
    )

    outcome = await import_manifest(async_session, repo.id, tmp_path)

    assert outcome.reaffirmed == [rec.id]
    acceptance = await latest_acceptance(async_session, rec.id)
    assert acceptance.reason == "a colleague corrected this"
    assert acceptance.scope_json == '["src/corrected.py"]'


async def test_an_edited_entry_is_rescored_from_its_evidence_not_from_the_file(
    async_session, tmp_path
):
    """The file exports accepted decisions, and most of those were mined.

    Scoring such a record from the file's two fields would fight
    ``reconcile_decision_confidence``, which re-derives it from the evidence
    on the next index: the value would oscillate and ``updated_at`` would
    churn forever. The file only supplies the score when it is the whole
    record.
    """
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(
        async_session,
        repo.id,
        [_dict("Mined then edited", context="the situation", consequences=["a cost"])],
    )
    rec = (
        (
            await async_session.execute(
                select(DecisionRecord).where(DecisionRecord.repository_id == repo.id)
            )
        )
        .scalars()
        .one()
    )
    await accept_decision(async_session, rec, accepter="tester")
    scored_from_evidence = rec.confidence
    await export_manifest(async_session, repo.id, tmp_path)

    entries, _ = load_manifest(tmp_path)
    write_manifest(tmp_path, [replace(entries[0], reason="a colleague corrected this")])
    await import_manifest(async_session, repo.id, tmp_path)
    await async_session.refresh(rec)

    # The file sees 2 of 5 body fields; the record still has 4 and its
    # evidence rows. Scoring from the file would land somewhere else.
    assert rec.confidence == scored_from_evidence
    assert rec.confidence != compute_confidence(
        rank_for_source("session"), 1, "exact", filled_fields=2
    )


async def test_a_file_only_decision_is_scored_on_the_ladder_not_at_one(
    async_session, tmp_path
):
    """A decision the file is the only record of has no evidence to derive
    from, so the import scores it: full rank credit, never the old 1.0."""
    repo = await insert_repo(async_session)
    write_manifest(
        tmp_path,
        [
            ManifestDecision(
                id="f" * 32,
                title="Only in the file",
                decision="Route through the client",
                reason="one place to change the retry policy",
                scope=["src/app.py"],
                accepted_at="2026-01-01T00:00:00+00:00",
                accepted_by="tester",
                evidence=[],
                source="cli",
            )
        ],
    )

    await import_manifest(async_session, repo.id, tmp_path)

    rec = (
        (
            await async_session.execute(
                select(DecisionRecord).where(DecisionRecord.repository_id == repo.id)
            )
        )
        .scalars()
        .one()
    )
    assert rec.confidence == compute_confidence(rank_for_source("cli"), 1, "exact")
    assert rec.confidence < 1.0


async def test_an_entry_missing_a_reason_is_skipped_not_guessed_at(
    async_session, tmp_path
):
    repo = await insert_repo(async_session)
    write_manifest(tmp_path, [_entry("f" * 32, reason="")])

    outcome = await import_manifest(async_session, repo.id, tmp_path)

    assert outcome.skipped == [("f" * 32, "no reason recorded")]
    assert await async_session.get(DecisionRecord, "f" * 32) is None


async def test_a_withdrawn_decision_exports_as_withdrawn(async_session, tmp_path):
    """Otherwise a colleague's import would undo the dismissal."""
    repo = await insert_repo(async_session)
    await bulk_upsert_decisions(async_session, repo.id, [_dict("Withdrawn")])
    rec = (
        (
            await async_session.execute(
                select(DecisionRecord).where(DecisionRecord.repository_id == repo.id)
            )
        )
        .scalars()
        .one()
    )
    await accept_decision(async_session, rec, accepter="tester")
    await dismiss_candidate(async_session, rec, accepter="tester")

    await export_manifest(async_session, repo.id, tmp_path)

    entries, _ = load_manifest(tmp_path)
    assert [e.currency for e in entries] == ["dismissed"]
